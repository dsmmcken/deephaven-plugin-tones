"""
``use_table_tones_listener`` — sonify a ticking table.

The hook listens to the table server-side (``use_table_listener``), turns each
new row into a tone event, and queues the send onto the render thread
(``use_render_queue``) because listeners fire on a background thread.

Row → sound translation lives here in Python: the value→pitch input range, the
loudness→velocity/duration curve, the per-row effect-param positions and the
chord/sequence trigger cells are all resolved before the event is sent, so each
event is self-contained JSON. Only the musical mapping itself (scale
quantisation, param output ranges, scheduling) happens on the client.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from ._config import (
    _DEFAULT_CHORUS,
    _DEFAULT_DELAY,
    _DEFAULT_DISTORTION,
    _DEFAULT_LIMITER_THRESHOLD,
    _DEFAULT_PINGPONG,
    TONES_EVENT,
    ColumnInput,
    FilterRolloff,
    FilterType,
    Instrument,
    NoteInput,
    ParamInput,
    TableMode,
    ToneTime,
    VoiceOverride,
    _resolve_envelope,
    augment_with_ranges,
    build_chord_trigger,
    build_config,
    build_mappings,
    build_param_mappings,
    build_sequence_trigger,
    resolve_param,
    resolve_pitch,
    validate_columns,
    validate_config_enums,
)

if TYPE_CHECKING:
    from deephaven.table import Table

# Ceiling on how many rows one tick may sonify in mode="all". A tick that adds
# thousands of rows would otherwise queue thousands of notes; the newest rows
# are the interesting ones.
_MAX_ROWS_PER_TICK = 32

# Loudness → velocity and note-length output ranges. A quiet row still sounds
# (0.3) and a short note is still audible (0.15s).
_VELOCITY_RANGE = (0.3, 1.0)
_DURATION_RANGE = (0.15, 0.9)


# ---------------------------------------------------------------------------
# Cell helpers (pure — no engine types, so they unit-test without a server)
# ---------------------------------------------------------------------------


def _num(raw: Any) -> float | None:
    """Coerce a cell to a float, or ``None`` when it isn't a usable number."""
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def _normalize01(
    value: float, lo: float, hi: float, empty_default: float = 0.0
) -> float:
    """Clamp *value* to its 0..1 position in ``[lo, hi]``; flat span → default."""
    span = hi - lo
    t = empty_default if span == 0 else (value - lo) / span
    return min(1.0, max(0.0, t))


def _lerp(t: float, lo: float, hi: float) -> float:
    return lo + t * (hi - lo)


def _is_truthy_cell(raw: Any) -> bool:
    """
    A cell gates a trigger when it's true / non-zero / a non-empty, non-"false"
    string / a non-empty array — so a notes column can itself be the gate.
    """
    if raw is None:
        return False
    if isinstance(raw, str):
        s = raw.strip()
        return s != "" and s.lower() != "false"
    if hasattr(raw, "__len__"):
        return len(raw) > 0
    try:
        return float(raw) != 0
    except (TypeError, ValueError):
        return bool(raw)


def _to_note_list(cell: Any) -> list[str] | None:
    """
    Parse a cell into a flat note list: an array is used as-is, a string is
    split on whitespace/commas. ``None`` when there are no notes.
    """
    if cell is None:
        return None
    if isinstance(cell, str):
        parts = [s for s in cell.replace(",", " ").split() if s]
    elif hasattr(cell, "__len__"):
        parts = [str(x).strip() for x in cell if str(x).strip()]
    else:
        return None
    return parts or None


def _to_chord_list(cell: Any) -> list[list[str]] | None:
    """
    Parse a cell into a list of chords. Accepts a nested array (progression), a
    flat array (one chord), or a string where chords split on ``|`` / ``;`` and
    notes within a chord on whitespace/commas.
    """
    if cell is None:
        return None
    chords: list[list[str]]
    if isinstance(cell, str):
        segments = cell.replace(";", "|").split("|")
        chords = [_to_note_list(seg) or [] for seg in segments]
    elif hasattr(cell, "__len__"):
        items = list(cell)
        if not items:
            return None
        if isinstance(items[0], str) or not hasattr(items[0], "__len__"):
            chords = [_to_note_list(items) or []]
        else:
            chords = [_to_note_list(c) or [] for c in items]
    else:
        return None
    chords = [c for c in chords if c]
    return chords or None


def _tracked_range(
    ranges: dict[str, list[float]], key: str, value: float
) -> tuple[float, float]:
    """
    Running min/max for *key*, widened by *value*. The fallback when a channel
    has no explicit range and no server range columns (see [[auto-range]]).
    """
    rr = ranges.get(key)
    if rr is None:
        ranges[key] = [value, value]
        return value, value
    if value < rr[0]:
        rr[0] = value
    if value > rr[1]:
        rr[1] = value
    return rr[0], rr[1]


def _channel_range(
    row: Mapping[str, Any],
    channel: Mapping[str, Any],
    ranges: dict[str, list[float]],
    key: str,
    value: float,
) -> tuple[float, float]:
    """
    Resolve a channel's input span for this row: an explicit ``range`` wins,
    then the server-side live min/max columns, then the running min/max.
    """
    explicit = channel.get("range")
    if explicit:
        return float(explicit[0]), float(explicit[1])
    min_col, max_col = channel.get("minColumn"), channel.get("maxColumn")
    if min_col and max_col:
        lo, hi = _num(row.get(min_col)), _num(row.get(max_col))
        if lo is not None and hi is not None:
            return lo, hi
    return _tracked_range(ranges, key, value)


# ---------------------------------------------------------------------------
# Row → events
# ---------------------------------------------------------------------------


def _tone_event(
    row: Mapping[str, Any],
    plan: Mapping[str, Any],
    ranges: dict[str, list[float]],
) -> dict[str, Any] | None:
    """Build the value→pitch event for one row, or ``None`` if it has no pitch."""
    mappings = plan["mappings"]
    pitch_channel = mappings["pitch"]
    value = _num(row.get(pitch_channel["column"]))
    if value is None:
        return None

    overrides: dict[str, Any] = {}
    # An explicit pitch range already sits in config.valueRange; otherwise send
    # this row's live span so the client quantises against the true extent.
    if plan["config"].get("valueRange") is None:
        lo, hi = _channel_range(row, pitch_channel, ranges, "pitch", value)
        overrides["valueRange"] = [lo, hi]

    velocity = 1.0
    duration: float | None = None
    loudness_channel = mappings.get("velocity")
    if loudness_channel is not None:
        loudness = _num(row.get(loudness_channel["column"]))
        if loudness is not None:
            # velocity and duration share one span — the same loudness column.
            lo, hi = _channel_range(row, loudness_channel, ranges, "loudness", loudness)
            t = _normalize01(loudness, lo, hi, 0.5)
            velocity = _lerp(t, *_VELOCITY_RANGE)
            duration = _lerp(t, *_DURATION_RANGE)

    voice_channel = mappings.get("voice")
    if voice_channel is not None:
        cell = row.get(voice_channel["column"])
        key = str(cell).strip() if cell is not None else ""
        voice = voice_channel["voices"].get(key, voice_channel.get("default"))
        if voice:
            overrides.update(voice)

    event: dict[str, Any] = {
        "op": "tone",
        "config": plan["config"],
        "value": value,
        "velocity": velocity,
        "overrides": overrides,
    }
    if duration is not None:
        event["duration"] = duration
    params = _row_params(row, plan, ranges)
    if params:
        event["params"] = params
    return event


def _row_params(
    row: Mapping[str, Any],
    plan: Mapping[str, Any],
    ranges: dict[str, list[float]],
) -> dict[str, Any] | None:
    """
    Resolve this row's data-driven effect params to ``{path: {t, min?, max?}}``.
    ``t`` is the column's normalised 0..1 position; the client maps it into the
    param's output range (its own source of truth for those).
    """
    channels = plan.get("param_mappings")
    if not channels:
        return None
    out: dict[str, Any] = {}
    for path, channel in channels.items():
        value = _num(row.get(channel["column"]))
        if value is None:
            continue
        lo, hi = _channel_range(row, channel, ranges, f"param:{path}", value)
        entry: dict[str, Any] = {"t": _normalize01(value, lo, hi, 0.5)}
        if "min" in channel:
            entry["min"] = channel["min"]
        if "max" in channel:
            entry["max"] = channel["max"]
        out[path] = entry
    return out or None


def events_for_row(
    row: Mapping[str, Any],
    plan: Mapping[str, Any],
    ranges: dict[str, list[float]],
) -> list[dict[str, Any]]:
    """
    Translate one new row into the tone events it triggers: a mapped note when
    a ``pitch`` column is configured, plus any chord/sequence trigger whose gate
    cell is truthy. Most rows of a trigger-only table produce nothing.
    """
    events: list[dict[str, Any]] = []
    config = plan["config"]

    if plan.get("mappings"):
        event = _tone_event(row, plan, ranges)
        if event is not None:
            events.append(event)

    chord_trigger = plan.get("chord_trigger")
    if chord_trigger and _is_truthy_cell(row.get(chord_trigger["column"])):
        notes_column = chord_trigger.get("notesColumn")
        chords = _to_chord_list(row.get(notes_column)) if notes_column else None
        events.append(
            {
                "op": "chordSequence",
                "config": config,
                "chords": chords or chord_trigger["chords"],
                "gap": chord_trigger["gap"],
                "duration": chord_trigger["duration"],
            }
        )

    sequence_trigger = plan.get("sequence_trigger")
    if sequence_trigger and _is_truthy_cell(row.get(sequence_trigger["column"])):
        notes_column = sequence_trigger.get("notesColumn")
        from_cell = _to_note_list(row.get(notes_column)) if notes_column else None
        notes = (
            [{"note": n} for n in from_cell] if from_cell else sequence_trigger["notes"]
        )
        events.append(
            {
                "op": "sequence",
                "config": config,
                "notes": notes,
                "gap": sequence_trigger["gap"],
                "envelope": None,
            }
        )

    return events


def rows_from_update(
    update: Any, columns: Sequence[str], limit: int
) -> list[dict[str, Any]]:
    """
    Read the rows added by this tick as plain dicts, newest last, at most
    *limit* of them. Only ``added`` rows sonify: an in-place modification isn't
    a new event, and replaying history on subscribe would flood the browser.
    """
    try:
        added = update.added(cols=list(columns))
    except Exception:  # noqa: BLE001 — a tick with no matching columns is silent
        return []
    if not added:
        return []
    size = min(len(v) for v in added.values())
    if size == 0:
        return []
    start = max(0, size - limit)
    return [{col: added[col][i] for col in added} for i in range(start, size)]


# ---------------------------------------------------------------------------
# The hook
# ---------------------------------------------------------------------------


def use_table_tones_listener(
    table: Table,
    *,
    # Instrument / voice
    instrument: Instrument = "sine",
    polyphony: int = 8,
    envelope_attack: float | None = None,
    envelope_decay: float | None = None,
    envelope_sustain: float | None = None,
    envelope_release: float | None = None,
    detune: ParamInput = 0,
    portamento: float = 0,
    # Effects — each ParamInput accepts a number (static), a column-name str
    # (data-driven), or (col, lo, hi) (data-driven + output range).
    filter: bool = True,  # noqa: A002 — effect on/off toggle
    filter_type: FilterType = "lowpass",
    filter_frequency: ParamInput = 2200,
    filter_q: ParamInput = 1,
    filter_rolloff: FilterRolloff = -24,
    reverb: bool = True,
    reverb_decay: float = 3,
    reverb_wet: ParamInput = 0.3,
    reverb_predelay: float = 0.01,
    delay: bool = False,
    delay_time: ToneTime | None = None,
    delay_feedback: ParamInput | None = None,
    delay_wet: ParamInput | None = None,
    distortion: bool = False,
    distortion_amount: float = 0.4,
    distortion_wet: ParamInput | None = None,
    chorus: bool = False,
    chorus_frequency: float = 1.5,
    chorus_depth: float = 0.7,
    chorus_wet: ParamInput | None = None,
    ping_pong: bool = False,
    ping_pong_time: ToneTime = "8n",
    ping_pong_feedback: ParamInput | None = None,
    ping_pong_wet: ParamInput | None = None,
    limiter: bool = True,
    limiter_threshold: float = _DEFAULT_LIMITER_THRESHOLD,
    volume: float = -8,
    pan: ParamInput = 0,
    # Value→pitch mapping
    scale: str | Sequence[int] = "pentatonic",
    root: str = "C3",
    octaves: int = 3,
    descending: bool = False,
    # Tick behaviour
    mode: TableMode = "last",
    rate_limit_ms: int = 60,
    # Value→pitch sonify. `pitch` alone maps one column to pitch; add
    # `loudness`/`voice` for a multi-dimensional "duet". Each names a COLUMN;
    # "Col" or (col, lo, hi) where (lo, hi) clamps the INPUT data domain.
    pitch: ColumnInput | None = None,
    loudness: ColumnInput | None = None,
    voice: str | None = None,
    voices: Mapping[Any, VoiceOverride] | None = None,
    voice_default: VoiceOverride | None = None,
    # Chord trigger
    chord_column: str | None = None,
    chords: Sequence[Sequence[str]] | None = None,
    chord_gap: ToneTime = "4n",
    chord_duration: ToneTime = "2n",
    chord_notes_column: str | None = None,
    # Sequence trigger — the play_sequence analogue
    sequence_column: str | None = None,
    sequence_notes: Sequence[NoteInput] | None = None,
    sequence_gap: ToneTime = 0,
    sequence_notes_column: str | None = None,
) -> None:
    """
    Render hook that sonifies a ticking ``Table``.

    Call it at the top of a ``@ui.component``; it renders nothing. As each tick
    adds rows, the mapped columns turn into sound. Only *added* rows sonify —
    existing history is never replayed.

    Args:
        table: The Deephaven ``Table`` to sonify on tick.
        instrument: Tone.js synth type — ``"sine"`` | ``"triangle"`` |
            ``"square"`` | ``"sawtooth"`` | ``"fm"`` | ``"am"`` | ``"membrane"``
            | ``"pluck"`` | ``"monosynth"`` | ``"duosynth"`` | ``"metal"``.
        polyphony: Maximum simultaneous voices. Default ``8``.
        envelope_attack, envelope_decay, envelope_sustain, envelope_release:
            ADSR envelope, in seconds (sustain is a 0–1 level). Static only.
        detune: Global detune in cents. **Data-driven.**
        portamento: Glide time between notes in seconds. Default ``0``.
        filter: ``True`` (default) enables the filter node; ``False`` disables.
        filter_type: ``"lowpass"`` | ``"highpass"`` | … Static only.
        filter_frequency: Cutoff in Hz. Default ``2200``. **Data-driven** (Hz,
            log-scaled output by default).
        filter_q: Resonance. Default ``1``. **Data-driven.**
        filter_rolloff: ``-12`` | ``-24`` | ``-48`` | ``-96``. Static only.
        reverb: ``True`` (default) enables the reverb node; ``False`` disables.
        reverb_decay: Tail length in seconds. Default ``3``. Static only.
        reverb_wet: Wet/dry mix 0–1. Default ``0.3``. **Data-driven.**
        reverb_predelay: Pre-delay in seconds. Default ``0.01``. Static only.
        delay: ``True`` enables the feedback-delay node — setting any
            ``delay_*`` param also enables it.
        delay_time: Delay time (``"8n"`` or seconds). Static only.
        delay_feedback: Feedback 0–~0.9. Default ``0.2``. **Data-driven.**
        delay_wet: Wet/dry mix 0–1. Default ``0.1``. **Data-driven.**
        distortion: ``True`` enables a waveshaper distortion node.
        distortion_amount: Distortion amount 0–1. Static only.
        distortion_wet: Wet/dry mix 0–1. **Data-driven.**
        chorus: ``True`` enables a stereo chorus node.
        chorus_frequency: LFO rate in Hz. Static only.
        chorus_depth: Modulation depth 0–1. Static only.
        chorus_wet: Wet/dry mix 0–1. **Data-driven.**
        ping_pong: ``True`` enables a stereo ping-pong delay node.
        ping_pong_time: Delay time. Static only.
        ping_pong_feedback: Feedback 0–~0.9. **Data-driven.**
        ping_pong_wet: Wet/dry mix 0–1. **Data-driven.**
        limiter: ``True`` (default) inserts a brick-wall limiter on the master
            bus so loud bursts don't clip.
        limiter_threshold: Limiter ceiling in dBFS. Default ``-1``.
        volume: Master volume in dB. Default ``-8``.
        pan: Stereo position, ``-1`` … ``1``. **Data-driven.**
        scale: Scale name (``"pentatonic"``, ``"major"``, ``"minor"``,
            ``"chromatic"``) or explicit semitone intervals, e.g.
            ``[0, 2, 4, 7, 9]``.
        root: Bottom of the pitch range as a note name, e.g. ``"C3"``.
        octaves: Octaves spanned by the value→pitch mapping.
        descending: When ``True``, higher values map to lower pitches.
        mode: ``"last"`` (one sound per tick, from the newest row) or ``"all"``
            (every row the tick added, newest ``32`` at most).
        rate_limit_ms: Minimum gap between sounds in ``"last"`` mode. Default
            ``60``. Ignored by ``"all"``.
        pitch: The numeric column mapped to pitch (scale-quantised via
            ``scale``/``root``/``octaves``). ``pitch`` alone is a
            one-dimensional sonify; add ``loudness``/``voice`` for a
            multi-dimensional "duet". ``"Col"`` auto-ranges against the table's
            live min/max; ``("Col", lo, hi)`` clamps the input domain.
        loudness: Numeric column mapped to loudness AND note length (bigger
            value = louder + longer). Same ``"Col"`` / ``("Col", lo, hi)`` forms.
        voice: Categorical column (e.g. a ``BUY``/``SELL`` side) whose value
            selects the instrument/voice for each row.
        voices: Map of ``voice`` cell value → a flat override dict, e.g.
            ``{"BUY": {"instrument": "pluck"}, "SELL": {"instrument":
            "sawtooth", "envelope_attack": 0.01}}``. Override keys mirror the
            flat param names; any you don't name keep the base value.
            ``voice``/``voices`` (and ``loudness``) only apply alongside
            ``pitch``.
        voice_default: Flat override applied to rows whose ``voice`` cell
            matches no key in ``voices``.
        chord_column: Trigger column for chord mode. On each new row where this
            column is truthy, ``chords`` plays as a progression. Use
            ``mode="all"`` so every flagged row fires.
        chords: The progression — a list of chords, each a list of note names.
            Defaults to a pleasant I-V-vi-IV in C.
        chord_gap: Onset spacing between chords. Default ``"4n"``.
        chord_duration: How long each chord rings. Default ``"2n"``.
        chord_notes_column: Column whose per-row cell supplies the chord(s) — a
            ``String[]`` (one chord), a ``String`` like ``"C4,E4,G4 | G3,B3,D4"``
            (chords split on ``|``/``;``), or a ``String[][]``. Given without
            ``chord_column`` it also acts as the trigger (fires when non-empty).
        sequence_column: Trigger column for a melodic sequence. On each new
            truthy row ``sequence_notes`` plays as a timed melody.
        sequence_notes: The melody — same note forms as
            ``tones.play_sequence``. Defaults to an ascending C arpeggio.
        sequence_gap: Extra silence after each note. Default ``0``.
        sequence_notes_column: Column whose per-row cell supplies the melody — a
            ``String[]`` or a ``String`` like ``"C5 E5 G5 C6"``. Given without
            ``sequence_column`` it also acts as the trigger.

    Example::

        @ui.component
        def market_sounds(prices):
            use_table_tones_listener(prices, pitch="Price", scale="pentatonic")
            return ui.table(prices)
    """
    from deephaven.ui import (
        use_memo,
        use_ref,
        use_render_queue,
        use_send_event,
        use_table_listener,
    )

    # --- resolve flat overloaded params → (static baseline, per-row channel) --
    # A number stays static (in config); a column name / (col, lo, hi) becomes a
    # data-driven channel modulated live around the static baseline.
    detune_v, detune_ch = resolve_param(detune, 0)
    filter_frequency_v, filter_frequency_ch = resolve_param(filter_frequency, 2200)
    filter_q_v, filter_q_ch = resolve_param(filter_q, 1)
    reverb_wet_v, reverb_wet_ch = resolve_param(reverb_wet, 0.3)
    delay_feedback_v, delay_feedback_ch = resolve_param(
        delay_feedback, _DEFAULT_DELAY["feedback"]
    )
    delay_wet_v, delay_wet_ch = resolve_param(delay_wet, _DEFAULT_DELAY["wet"])
    pan_v, pan_ch = resolve_param(pan, 0)
    distortion_wet_v, distortion_wet_ch = resolve_param(
        distortion_wet, _DEFAULT_DISTORTION["wet"]
    )
    chorus_wet_v, chorus_wet_ch = resolve_param(chorus_wet, _DEFAULT_CHORUS["wet"])
    ping_pong_wet_v, ping_pong_wet_ch = resolve_param(
        ping_pong_wet, _DEFAULT_PINGPONG["wet"]
    )
    ping_pong_feedback_v, ping_pong_feedback_ch = resolve_param(
        ping_pong_feedback, _DEFAULT_PINGPONG["feedback"]
    )

    param_channels = {
        "detune": detune_ch,
        "filter_frequency": filter_frequency_ch,
        "filter_q": filter_q_ch,
        "reverb_wet": reverb_wet_ch,
        "delay_feedback": delay_feedback_ch,
        "delay_wet": delay_wet_ch,
        "pan": pan_ch,
        "distortion_wet": distortion_wet_ch,
        "chorus_wet": chorus_wet_ch,
        "ping_pong_wet": ping_pong_wet_ch,
        "ping_pong_feedback": ping_pong_feedback_ch,
    }
    param_mappings = build_param_mappings(param_channels)

    # pitch / loudness name a COLUMN; a 3-tuple clamps the INPUT data domain.
    pitch_col, pitch_range = resolve_pitch(pitch)
    loudness_col, loudness_range = resolve_pitch(loudness)

    # --- validate column-name props up front ------------------------------
    referenced: list[tuple[str, str | None]] = [
        ("pitch", pitch_col),
        ("loudness", loudness_col),
        ("voice", voice),
        ("chord_column", chord_column),
        ("chord_notes_column", chord_notes_column),
        ("sequence_column", sequence_column),
        ("sequence_notes_column", sequence_notes_column),
    ]
    referenced.extend(
        (kwarg, channel["column"])
        for kwarg, channel in param_channels.items()
        if channel is not None
    )
    validate_columns("use_table_tones_listener", table, referenced)
    validate_config_enums(
        "use_table_tones_listener",
        instrument,
        filter_type,
        filter_rolloff,
        voices,
        mode=mode,
    )

    mappings = build_mappings(
        pitch=pitch_col,
        loudness=loudness_col,
        loudness_range=loudness_range,
        voice=voice,
        voices=voices,
        voice_default=voice_default,
        base_envelope=_resolve_envelope(
            envelope_attack, envelope_decay, envelope_sustain, envelope_release
        ),
    )

    chord_trigger = build_chord_trigger(
        chord_column=chord_column,
        chords=chords,
        chord_gap=chord_gap,
        chord_duration=chord_duration,
        chord_notes_column=chord_notes_column,
    )

    sequence_trigger = build_sequence_trigger(
        sequence_column=sequence_column,
        sequence_notes=sequence_notes,
        sequence_gap=sequence_gap,
        sequence_notes_column=sequence_notes_column,
    )

    # --- server-side AUTO range (default) ---------------------------------
    # Any channel whose range is unset gets live min/max columns via agg + a
    # keyless natural_join, so row 1 already scales against the true global
    # span. An explicit range opts out.
    range_cols: list[str] = []
    if pitch_col is not None and pitch_range is None:
        range_cols.append(pitch_col)
    if loudness_col is not None and loudness_range is None:
        range_cols.append(loudness_col)
    range_cols.extend(
        channel["column"] for channel in param_channels.values() if channel is not None
    )

    # use_memo opens a LivenessScope around the call, so the derived agg_by /
    # natural_join tables are released when the table or the columns change.
    augmented_table, range_names = use_memo(
        lambda: augment_with_ranges(table, range_cols),
        [table, tuple(range_cols)],
    )

    if range_names:
        if mappings is not None and pitch_col in range_names:
            mn, mx = range_names[pitch_col]
            mappings["pitch"]["minColumn"] = mn
            mappings["pitch"]["maxColumn"] = mx
        if mappings is not None and loudness_col in range_names:
            mn, mx = range_names[loudness_col]
            for channel_name in ("velocity", "duration"):
                if channel_name in mappings:
                    mappings[channel_name]["minColumn"] = mn
                    mappings[channel_name]["maxColumn"] = mx
        for channel in (param_mappings or {}).values():
            if channel["column"] in range_names:
                mn, mx = range_names[channel["column"]]
                channel["minColumn"] = mn
                channel["maxColumn"] = mx

    config = build_config(
        {
            "instrument": instrument,
            "polyphony": polyphony,
            "envelope_attack": envelope_attack,
            "envelope_decay": envelope_decay,
            "envelope_sustain": envelope_sustain,
            "envelope_release": envelope_release,
            "detune": detune_v,
            "portamento": portamento,
            "filter": filter,
            "filter_type": filter_type,
            "filter_frequency": filter_frequency_v,
            "filter_q": filter_q_v,
            "filter_rolloff": filter_rolloff,
            "reverb": reverb,
            "reverb_decay": reverb_decay,
            "reverb_wet": reverb_wet_v,
            "reverb_predelay": reverb_predelay,
            "delay": delay,
            "delay_time": delay_time,
            "delay_feedback": delay_feedback_v if delay_feedback is not None else None,
            "delay_wet": delay_wet_v if delay_wet is not None else None,
            "distortion": distortion,
            "distortion_amount": distortion_amount,
            "distortion_wet": distortion_wet_v,
            "chorus": chorus,
            "chorus_frequency": chorus_frequency,
            "chorus_depth": chorus_depth,
            "chorus_wet": chorus_wet_v,
            "ping_pong": ping_pong,
            "ping_pong_time": ping_pong_time,
            "ping_pong_feedback": ping_pong_feedback_v,
            "ping_pong_wet": ping_pong_wet_v,
            "limiter": limiter,
            "limiter_threshold": limiter_threshold,
            "volume": volume,
            "pan": pan_v,
            "scale": scale,
            "root": root,
            "octaves": octaves,
            "value_range": pitch_range,
            "descending": descending,
        }
    )

    plan: dict[str, Any] = {
        "config": config,
        "mappings": mappings,
        "param_mappings": param_mappings,
        "chord_trigger": chord_trigger,
        "sequence_trigger": sequence_trigger,
        "mode": mode,
        "rate_limit_ms": rate_limit_ms,
    }
    columns = _plan_columns(plan)
    # Recreate the listener whenever the sound or the columns it reads change.
    plan_key = json.dumps(plan, sort_keys=True, default=str)

    send_event = use_send_event()
    render_queue = use_render_queue()
    # Running ranges + rate-limit clock, per mounted listener.
    ranges_ref = use_ref({})
    last_ms_ref = use_ref(0.0)

    def on_update(update: Any, is_replay: bool) -> None:
        limit = 1 if mode == "last" else _MAX_ROWS_PER_TICK
        rows = rows_from_update(update, columns, limit)
        if not rows:
            return
        if mode == "last" and rate_limit_ms:
            now_ms = time.monotonic() * 1000
            if now_ms - last_ms_ref.current < rate_limit_ms:
                return
            last_ms_ref.current = now_ms
        events = [
            event
            for row in rows
            for event in events_for_row(row, plan, ranges_ref.current)
        ]
        if not events:
            return
        # Listeners run off the render thread, where there's no event context.
        render_queue(lambda: _send_all(send_event, events))

    use_table_listener(augmented_table, on_update, [augmented_table, plan_key])


def _send_all(send_event: Any, events: Sequence[dict[str, Any]]) -> None:
    for event in events:
        send_event(TONES_EVENT, event)


def _plan_columns(plan: Mapping[str, Any]) -> list[str]:
    """Every column the listener reads per row, including the range columns."""
    columns: list[str] = []

    def add(name: str | None) -> None:
        if name and name not in columns:
            columns.append(name)

    for channel in (plan.get("mappings") or {}).values():
        add(channel.get("column"))
        add(channel.get("minColumn"))
        add(channel.get("maxColumn"))
    for channel in (plan.get("param_mappings") or {}).values():
        add(channel.get("column"))
        add(channel.get("minColumn"))
        add(channel.get("maxColumn"))
    for trigger in (plan.get("chord_trigger"), plan.get("sequence_trigger")):
        if trigger:
            add(trigger.get("column"))
            add(trigger.get("notesColumn"))
    return columns

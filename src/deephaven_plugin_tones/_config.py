"""
Shared, server-testable helpers behind the two public entry points (``tones``
and ``use_table_tones_listener``).

Everything here is pure Python with no Deephaven render context: the public
type aliases, the flat-kwargs → nested ``ToneConfig`` builder the client
consumes, the overloaded number/column param resolution, note normalisation,
validation, and the live server-side min/max range augmentation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Literal, TypedDict, get_args

if TYPE_CHECKING:
    from deephaven.table import Table

# ---------------------------------------------------------------------------
# Event name — MUST match the JS `eventMapping` key exactly.
# ---------------------------------------------------------------------------
TONES_EVENT = "deephaven_plugin_tones.event"

# ---------------------------------------------------------------------------
# Public type aliases (re-exported from the package __init__)
# ---------------------------------------------------------------------------

#: Tone.js synth types accepted by ``instrument`` (top-level and per-voice).
Instrument = Literal[
    "sine",
    "triangle",
    "square",
    "sawtooth",
    "fm",
    "am",
    "membrane",
    "pluck",
    "monosynth",
    "duosynth",
    "metal",
]

#: Filter curves accepted by ``filter_type``.
FilterType = Literal["lowpass", "highpass", "bandpass", "notch", "allpass", "peaking"]

#: Filter rolloffs (dB/octave) accepted by ``filter_rolloff``.
FilterRolloff = Literal[-12, -24, -48, -96]

#: Row-delivery modes for table sonification: most-recent row vs. every row.
TableMode = Literal["last", "all"]

#: A Tone.js time value: a note string like ``"8n"`` or plain seconds.
ToneTime = str | float


class MidiNote(TypedDict):
    """The explicit-MIDI pitch form: ``{"midi": 60}``."""

    midi: int


#: A single pitch: a note name (``"C4"``), Hz (number), or ``{"midi": 60}``.
NoteValue = str | float | MidiNote

#: One sequence item: a bare :data:`NoteValue`, a list of them (a chord),
#: ``None`` (a rest), or a tuple attaching a duration and optionally a velocity
#: to any of those — ``("C5", "16n")`` / ``(["C4", "E4"], 0.5, 0.8)``.
NoteInput = (
    NoteValue
    | list[NoteValue]
    | None
    | tuple[NoteValue | list[NoteValue] | None]
    | tuple[NoteValue | list[NoteValue] | None, ToneTime]
    | tuple[NoteValue | list[NoteValue] | None, ToneTime, float]
)

#: An overloaded numeric effect param for ``use_table_tones_listener``: a number
#: (static), a column name (data-driven, output range defaulted per param), or
#: ``(col, lo, hi)`` (data-driven with an explicit OUTPUT range).
ParamInput = float | str | tuple[str] | tuple[str, float, float]

#: A ``pitch`` / ``loudness`` column for ``use_table_tones_listener``: ``"Col"``
#: (input range auto-tracked) or ``("Col", lo, hi)`` clamping the INPUT domain.
ColumnInput = str | tuple[str] | tuple[str, float, float]


class VoiceOverride(TypedDict, total=False):
    """
    A flat per-voice config override for ``use_table_tones_listener(voices=...,
    voice_default=...)``. Keys mirror the flat sound-param names; any key left
    out keeps the base config's value. A ``False`` effect toggle disables that
    node for the voice.
    """

    instrument: Instrument
    polyphony: int
    envelope_attack: float
    envelope_decay: float
    envelope_sustain: float
    envelope_release: float
    detune: float
    portamento: float
    volume: float
    pan: float
    filter: bool
    reverb: bool
    delay: bool
    distortion: bool
    chorus: bool
    ping_pong: bool


# ---------------------------------------------------------------------------
# Config builder
# ---------------------------------------------------------------------------

# Default envelope — used to fill any envelope_* flat kwarg left unset (None).
_DEFAULT_ENVELOPE: dict[str, Any] = {
    "attack": 0.02,
    "decay": 0.10,
    "sustain": 0.6,
    "release": 1.2,
}


def _resolve_envelope(
    envelope_attack: float | None,
    envelope_decay: float | None,
    envelope_sustain: float | None,
    envelope_release: float | None,
) -> dict[str, Any]:
    """
    Resolve the flat ``envelope_*`` kwargs into a complete ADSR dict, filling
    each unset (``None``) stage from the pleasant default. Shared by
    ``_build_config`` and the ``voices`` override translation (which needs the
    full base envelope because the client shallow-merges voice overrides).
    """
    return {
        "attack": (
            envelope_attack
            if envelope_attack is not None
            else _DEFAULT_ENVELOPE["attack"]
        ),
        "decay": (
            envelope_decay if envelope_decay is not None else _DEFAULT_ENVELOPE["decay"]
        ),
        "sustain": (
            envelope_sustain
            if envelope_sustain is not None
            else _DEFAULT_ENVELOPE["sustain"]
        ),
        "release": (
            envelope_release
            if envelope_release is not None
            else _DEFAULT_ENVELOPE["release"]
        ),
    }


# Tone.js FeedbackDelay defaults — used when delay is enabled but a sub-param
# is left unset (None). Kept here so the static defaults live next to the
# other effect defaults rather than being implicit on the client.
_DEFAULT_DELAY: dict[str, Any] = {
    "delayTime": "8n",
    "feedback": 0.2,
    "wet": 0.1,
}

# Tier 1 effect defaults — Tone.js Distortion / Chorus / PingPongDelay. Each is
# enabled by its own on/off toggle (default off); the sub-params below fill any
# stage left unset, mirroring how _DEFAULT_DELAY backs the delay node.
_DEFAULT_DISTORTION: dict[str, Any] = {
    "amount": 0.4,
    "wet": 1.0,
}
_DEFAULT_CHORUS: dict[str, Any] = {
    "frequency": 1.5,
    "depth": 0.7,
    "wet": 0.5,
}
_DEFAULT_PINGPONG: dict[str, Any] = {
    "delayTime": "8n",
    "feedback": 0.2,
    "wet": 0.5,
}

# Master limiter threshold (dBFS) — a brick-wall on the master bus that keeps
# loud bursts (high polyphony / many simultaneous rows) from clipping.
_DEFAULT_LIMITER_THRESHOLD: float = -1


def _build_config(
    *,
    instrument: str,
    polyphony: int,
    envelope_attack: float | None,
    envelope_decay: float | None,
    envelope_sustain: float | None,
    envelope_release: float | None,
    detune: float,
    portamento: float,
    filter: bool,  # noqa: A002 — effect on/off toggle
    filter_type: str,
    filter_frequency: float,
    filter_q: float,
    filter_rolloff: int,
    reverb: bool,
    reverb_decay: float,
    reverb_wet: float,
    reverb_predelay: float,
    delay: bool,
    delay_time: ToneTime | None,
    delay_feedback: float | None,
    delay_wet: float | None,
    volume: float,
    pan: float,
    scale: str | Sequence[int],
    root: str,
    octaves: int,
    value_range: Sequence[float] | None,
    descending: bool,
    distortion: bool = False,
    distortion_amount: float | None = None,
    distortion_wet: float | None = None,
    chorus: bool = False,
    chorus_frequency: float | None = None,
    chorus_depth: float | None = None,
    chorus_wet: float | None = None,
    ping_pong: bool = False,
    ping_pong_time: ToneTime | None = None,
    ping_pong_feedback: float | None = None,
    ping_pong_wet: float | None = None,
    limiter: bool = True,
    limiter_threshold: float = _DEFAULT_LIMITER_THRESHOLD,
) -> dict[str, Any]:
    """
    Build the ``config`` dict with **verbatim camelCase keys** as required by
    the client contract.

    The public API is FLAT (``filter_frequency=`` rather than
    ``filter={"frequency": ...}``); this function re-assembles the flat kwargs
    into the nested ``envelope`` / ``filter`` / ``reverb`` / ``delay`` sub-dicts
    the client config expects. The ``filter`` / ``reverb`` / ``delay`` booleans
    toggle each effect on/off (a disabled effect serialises as ``None``).

    Static values passed here are the per-row *baseline* for any param that is
    also data-driven (see ``build_param_mappings``): the client modulates the
    live node around this value.
    """
    resolved_envelope = _resolve_envelope(
        envelope_attack, envelope_decay, envelope_sustain, envelope_release
    )

    resolved_filter: dict[str, Any] | None = (
        {
            "type": filter_type,
            "frequency": filter_frequency,
            "q": filter_q,
            "rolloff": filter_rolloff,
        }
        if filter
        else None
    )

    resolved_reverb: dict[str, Any] | None = (
        {
            "decay": reverb_decay,
            "wet": reverb_wet,
            "preDelay": reverb_predelay,
        }
        if reverb
        else None
    )

    resolved_delay: dict[str, Any] | None = (
        {
            "delayTime": (
                delay_time if delay_time is not None else _DEFAULT_DELAY["delayTime"]
            ),
            "feedback": (
                delay_feedback
                if delay_feedback is not None
                else _DEFAULT_DELAY["feedback"]
            ),
            "wet": delay_wet if delay_wet is not None else _DEFAULT_DELAY["wet"],
        }
        if delay
        else None
    )

    resolved_distortion: dict[str, Any] | None = (
        {
            "amount": (
                distortion_amount
                if distortion_amount is not None
                else _DEFAULT_DISTORTION["amount"]
            ),
            "wet": (
                distortion_wet
                if distortion_wet is not None
                else _DEFAULT_DISTORTION["wet"]
            ),
        }
        if distortion
        else None
    )

    resolved_chorus: dict[str, Any] | None = (
        {
            "frequency": (
                chorus_frequency
                if chorus_frequency is not None
                else _DEFAULT_CHORUS["frequency"]
            ),
            "depth": (
                chorus_depth if chorus_depth is not None else _DEFAULT_CHORUS["depth"]
            ),
            "wet": chorus_wet if chorus_wet is not None else _DEFAULT_CHORUS["wet"],
        }
        if chorus
        else None
    )

    resolved_ping_pong: dict[str, Any] | None = (
        {
            "delayTime": (
                ping_pong_time
                if ping_pong_time is not None
                else _DEFAULT_PINGPONG["delayTime"]
            ),
            "feedback": (
                ping_pong_feedback
                if ping_pong_feedback is not None
                else _DEFAULT_PINGPONG["feedback"]
            ),
            "wet": (
                ping_pong_wet if ping_pong_wet is not None else _DEFAULT_PINGPONG["wet"]
            ),
        }
        if ping_pong
        else None
    )

    # Master limiter: a single shared node on the master bus (the client owns the
    # one instance); a dict carries its threshold, None disables it.
    resolved_limiter: dict[str, Any] | None = (
        {"threshold": limiter_threshold} if limiter else None
    )

    vr: list[float] | None = None
    if value_range is not None:
        vr = [float(v) for v in value_range]

    return {
        "instrument": instrument,
        "polyphony": polyphony,
        "envelope": resolved_envelope,
        "detune": detune,
        "portamento": portamento,
        "filter": resolved_filter,
        "reverb": resolved_reverb,
        "delay": resolved_delay,
        "distortion": resolved_distortion,
        "chorus": resolved_chorus,
        "pingPongDelay": resolved_ping_pong,
        "limiter": resolved_limiter,
        "volume": volume,
        "pan": pan,
        "scale": scale if isinstance(scale, str) else list(scale),
        "root": root,
        "octaves": octaves,
        "valueRange": vr,
        "descending": descending,
    }


# Every flat sound option, with its default. This is the single source of truth
# for what `Tones(...)` accepts, what a per-call override may name, and what
# `use_table_tones_listener` forwards into `_build_config`.
SOUND_DEFAULTS: dict[str, Any] = {
    "instrument": "sine",
    "polyphony": 8,
    "envelope_attack": None,
    "envelope_decay": None,
    "envelope_sustain": None,
    "envelope_release": None,
    "detune": 0,
    "portamento": 0,
    "filter": True,
    "filter_type": "lowpass",
    "filter_frequency": 2200,
    "filter_q": 1,
    "filter_rolloff": -24,
    "reverb": True,
    "reverb_decay": 3,
    "reverb_wet": 0.3,
    "reverb_predelay": 0.01,
    "delay": False,
    "delay_time": None,
    "delay_feedback": None,
    "delay_wet": None,
    "distortion": False,
    "distortion_amount": None,
    "distortion_wet": None,
    "chorus": False,
    "chorus_frequency": None,
    "chorus_depth": None,
    "chorus_wet": None,
    "ping_pong": False,
    "ping_pong_time": None,
    "ping_pong_feedback": None,
    "ping_pong_wet": None,
    "limiter": True,
    "limiter_threshold": _DEFAULT_LIMITER_THRESHOLD,
    "volume": -8,
    "pan": 0,
    "scale": "pentatonic",
    "root": "C3",
    "octaves": 3,
    "value_range": None,
    "descending": False,
}


def _delay_enabled(
    delay: bool,
    delay_time: Any,
    delay_feedback: Any,
    delay_wet: Any,
) -> bool:
    """The delay node turns on when explicitly enabled OR any ``delay_*`` is set."""
    return (
        bool(delay)
        or delay_time is not None
        or delay_feedback is not None
        or delay_wet is not None
    )


def build_config(options: Mapping[str, Any]) -> dict[str, Any]:
    """
    Build the client ``config`` from a full flat options mapping (every
    :data:`SOUND_DEFAULTS` key present). Enables the delay node implicitly when
    any ``delay_*`` sub-param is set.
    """
    o = dict(SOUND_DEFAULTS)
    o.update(options)
    o["delay"] = _delay_enabled(
        o["delay"], o["delay_time"], o["delay_feedback"], o["delay_wet"]
    )
    return _build_config(**o)


def validate_option_names(fn: str, names: Sequence[str]) -> None:
    """Raise ``ValueError`` naming any override kwarg that isn't a sound option."""
    unknown = [n for n in names if n not in SOUND_DEFAULTS]
    if unknown:
        valid = ", ".join(sorted(SOUND_DEFAULTS))
        raise ValueError(
            f"{fn}(): unknown sound option(s) {', '.join(sorted(unknown))}. Valid options: {valid}."
        )


# ---------------------------------------------------------------------------
# Flat overloaded-param resolution
# ---------------------------------------------------------------------------
#
# A NUMERIC sound param may be given three ways in table mode:
#   number          → a static value
#   "ColumnName"    → data-driven: the column drives the param, its live
#                     min/max as INPUT, the param's default OUTPUT range
#   ("Col", lo, hi) → data-driven with an explicit OUTPUT range [lo, hi]
#
# ``resolve_param`` splits such a value into (static_baseline, channel). The
# static baseline always goes into ``config`` (so a data-driven param still has
# a sensible value the client modulates around); ``channel`` (or None) is the
# per-row mapping. Output-range defaults live on the CLIENT, next to the nodes —
# Python only forwards an explicit ``{min, max}`` when the tuple form is used.

# Public kwarg name → client param-path it modulates. Only these numeric params
# are continuously modulatable per-event (live Tone signal on a chain node);
# everything else (envelope_*, reverb_decay, filter_type, …) is static-only.
PARAM_PATHS: dict[str, str] = {
    "detune": "detune",
    "filter_frequency": "filter.frequency",
    "filter_q": "filter.q",
    "reverb_wet": "reverb.wet",
    "delay_feedback": "delay.feedback",
    "delay_wet": "delay.wet",
    "pan": "pan",
    "distortion_wet": "distortion.wet",
    "chorus_wet": "chorus.wet",
    "ping_pong_wet": "pingPong.wet",
    "ping_pong_feedback": "pingPong.feedback",
}


def resolve_param(
    value: ParamInput | None,
    default: float,
) -> tuple[float, dict[str, Any] | None]:
    """
    Split an overloaded numeric param into ``(static_baseline, channel)``.

    * ``None``            → ``(default, None)``
    * ``"Col"`` (str)     → ``(default, {"column": "Col"})``
    * ``("Col", lo, hi)`` → ``(default, {"column": "Col", "min": lo, "max": hi})``
    * number              → ``(number, None)``
    """
    if value is None:
        return default, None
    if isinstance(value, str):
        return default, {"column": value}
    if isinstance(value, (list, tuple)):
        parts = list(value)
        if len(parts) == 1:
            return default, {"column": parts[0]}
        if len(parts) == 3:
            return default, {
                "column": parts[0],
                "min": float(parts[1]),
                "max": float(parts[2]),
            }
        raise ValueError(
            f"a data-driven param must be a column name, a 1-tuple (column,), or a 3-tuple (column, lo, hi); got {value!r}"
        )
    return value, None


def resolve_pitch(
    value: ColumnInput | None,
) -> tuple[str | None, list[float] | None]:
    """
    Resolve ``pitch`` / ``loudness`` (which name a COLUMN). Unlike effect
    params, a 3-tuple here sets the INPUT clamp (the data domain), not an output
    range — pitch maps into the musical scale, loudness into velocity/duration.

    * ``"Col"``           → ``("Col", None)``  (input range auto)
    * ``("Col", lo, hi)`` → ``("Col", [lo, hi])``
    * ``None``            → ``(None, None)``
    """
    if value is None:
        return None, None
    if isinstance(value, str):
        return value, None
    if isinstance(value, (list, tuple)):
        parts: list[Any] = list(value)
        if len(parts) == 1:
            return parts[0], None
        if len(parts) == 3:
            return parts[0], [float(parts[1]), float(parts[2])]
        raise ValueError(
            f"a pitch/loudness column must be a column name, a 1-tuple (column,), or a 3-tuple (column, lo, hi); got {value!r}"
        )
    return None, None


def build_param_mappings(
    channels: dict[str, dict[str, Any] | None],
) -> dict[str, Any] | None:
    """
    Collapse the per-param channels into ``{client_path: {column, min?, max?}}``.
    Returns ``None`` when no numeric param is data-driven. The client owns the
    default OUTPUT range per path; the INPUT span is the column's live min/max.
    """
    out: dict[str, Any] = {}
    for kwarg, channel in channels.items():
        if channel is not None:
            out[PARAM_PATHS[kwarg]] = channel
    return out or None


# ---------------------------------------------------------------------------
# Server-side auto-range (live min/max via agg + natural_join)
# ---------------------------------------------------------------------------
#
# Mirrors how deephaven.ui's ``ui.table`` databar/heatmap derive a column's
# range: aggregate a single-row global min/max, then NATURAL JOIN it (with no
# join keys) back onto the source so every row carries the current min/max.
# Because both the aggregation and the join are live Deephaven tables, the
# range tracks the data as a ticking table grows — no manual range needed.
#
# This is why range args DEFAULT TO AUTO: when a channel's range is left unset
# we attach these columns and the listener scales each row to the true global
# span from the first aggregation cycle, instead of a cold-start running
# min/max over only the rows it has seen. See [[auto-range]].

RANGE_MIN_SUFFIX = "_tones_min"
RANGE_MAX_SUFFIX = "_tones_max"


def augment_with_ranges(
    table: Table | None,
    cols: Sequence[str],
) -> tuple[Table | None, dict[str, tuple[str, str]]]:
    """
    Return ``(augmented_table, {col: (min_col, max_col)})``.

    For each column in *cols*, append live ``<col>_tones_min`` /
    ``<col>_tones_max`` columns holding the table-wide min/max (broadcast to
    every row via a keyless ``natural_join``).  On ANY failure — no table,
    engine import error, a table type that can't be joined (e.g. a tree
    table) — returns ``(table, {})`` so the caller falls back to a running
    min/max over the rows it has seen.  Never raises.
    """
    if table is None or not cols:
        return table, {}

    try:
        from deephaven import agg  # local import: engine only present at runtime
    except Exception:  # noqa: BLE001 — degrade gracefully if engine absent
        return table, {}

    uniq = list(dict.fromkeys(cols))  # de-dupe, preserve order
    aggs = []
    joins: list[str] = []
    names: dict[str, tuple[str, str]] = {}
    for col in uniq:
        mn = f"{col}{RANGE_MIN_SUFFIX}"
        mx = f"{col}{RANGE_MAX_SUFFIX}"
        aggs.append(agg.min_(f"{mn} = {col}"))
        aggs.append(agg.max_(f"{mx} = {col}"))
        joins.extend((mn, mx))
        names[col] = (mn, mx)

    try:
        range_table = table.agg_by(aggs)  # no `by=` → single global row
        augmented = table.natural_join(range_table, on=[], joins=joins)
    except Exception:  # noqa: BLE001 — fall back to a running min/max
        return table, {}

    return augmented, names


# ---------------------------------------------------------------------------
# Column-name validation (helpful errors for typo'd column props)
# ---------------------------------------------------------------------------
#
# Column-name props are otherwise only resolved per row inside the table
# listener, where an unknown name would silently produce no sound. This guard
# closes that gap: when a real table is supplied and its column list is
# introspectable, a referenced name that isn't present raises a ``ValueError``
# naming the offender and listing the available columns. It is best-effort — if
# the column list can't be determined (engine absent, or a table type without
# ``column_names``), validation is skipped rather than risking a false positive.


def validate_columns(
    fn: str,
    table: Table | None,
    referenced: Sequence[tuple[str, str | None]],
) -> None:
    """
    Raise ``ValueError`` if any referenced column name is absent from *table*.

    Args:
        fn: Calling function name, for the error message.
        table: The source ``Table`` (the user's, pre-augmentation), or ``None``.
        referenced: ``(role, column_name)`` pairs, where ``role`` is the kwarg
            that named the column. ``None`` column names are ignored.
    """
    if table is None:
        return
    pairs = [(role, col) for role, col in referenced if col]
    if not pairs:
        return
    try:
        available = list(table.column_names)
    except Exception:  # noqa: BLE001 — can't introspect → skip (no false positives)
        return
    available_set = set(available)
    missing = [(role, col) for role, col in pairs if col not in available_set]
    if missing:
        miss_desc = ", ".join(f"{role}={col!r}" for role, col in missing)
        raise ValueError(
            f"{fn}(): unknown column(s) {miss_desc}. Available columns: {available}."
        )


# Enumerated config values. These are passed straight through to Tone.js, where
# an unknown value fails silently in the browser. The Literal aliases above
# catch typos statically; validating here turns the dynamic case into an
# actionable ValueError instead.
_INSTRUMENTS: tuple[str, ...] = get_args(Instrument)
_FILTER_TYPES: tuple[str, ...] = get_args(FilterType)
_FILTER_ROLLOFFS: tuple[int, ...] = get_args(FilterRolloff)
_TABLE_MODES: tuple[str, ...] = get_args(TableMode)


def validate_config_enums(
    fn: str,
    instrument: str,
    filter_type: str,
    filter_rolloff: int,
    voices: Mapping[Any, VoiceOverride] | None = None,
    mode: str | None = None,
) -> None:
    """
    Raise ``ValueError`` for an out-of-range ``instrument`` / ``filter_type`` /
    ``filter_rolloff`` / ``mode`` (and any per-voice ``instrument`` override),
    naming the offender and the valid set. A ``None`` *mode* skips the mode
    check (only table mode has one).
    """
    if instrument not in _INSTRUMENTS:
        raise ValueError(
            f"{fn}(): unknown instrument {instrument!r}. Choose one of: {', '.join(_INSTRUMENTS)}."
        )
    if filter_type not in _FILTER_TYPES:
        raise ValueError(
            f"{fn}(): unknown filter_type {filter_type!r}. Choose one of: {', '.join(_FILTER_TYPES)}."
        )
    if filter_rolloff not in _FILTER_ROLLOFFS:
        raise ValueError(
            f"{fn}(): unknown filter_rolloff {filter_rolloff!r}. Choose one of: {', '.join(str(r) for r in _FILTER_ROLLOFFS)}."
        )
    if mode is not None and mode not in _TABLE_MODES:
        raise ValueError(
            f"{fn}(): unknown mode {mode!r}. Choose one of: {', '.join(_TABLE_MODES)}."
        )
    for key, override in (voices or {}).items():
        inst = override.get("instrument")
        if inst is not None and inst not in _INSTRUMENTS:
            raise ValueError(
                f"{fn}(): unknown instrument {inst!r} in voices[{key!r}]. Choose one of: {', '.join(_INSTRUMENTS)}."
            )


# ---------------------------------------------------------------------------
# Trigger builders
# ---------------------------------------------------------------------------

# A pleasant, resolved progression: I - V - vi - IV in C (the "four chords"),
# voiced low-to-mid so it sounds full without muddiness.
DEFAULT_CHORDS: list[list[str]] = [
    ["C4", "E4", "G4"],  # I   (C major)
    ["G3", "B3", "D4"],  # V   (G major)
    ["A3", "C4", "E4"],  # vi  (A minor)
    ["F3", "A3", "C4"],  # IV  (F major)
]


def build_chord_trigger(
    chord_column: str | None,
    chords: Sequence[Sequence[str]] | None,
    chord_gap: ToneTime,
    chord_duration: ToneTime,
    chord_notes_column: str | None,
) -> dict[str, Any] | None:
    """
    Build the chord-trigger spec, or ``None`` when neither a trigger column nor
    a per-row chord column is given. On each new row whose gate column is
    truthy, a chord progression plays: from ``chord_notes_column``'s cell when
    provided (a ``String[]`` = one chord, or a delimited ``String``), otherwise
    the static ``chords``. If only ``chord_notes_column`` is given it doubles as
    the gate (fires when its cell is non-empty).
    """
    gate = chord_column or chord_notes_column
    if gate is None:
        return None
    progression = chords if chords is not None else DEFAULT_CHORDS
    ct: dict[str, Any] = {
        "column": gate,
        "chords": [list(c) for c in progression],
        "gap": chord_gap,
        "duration": chord_duration,
    }
    if chord_notes_column is not None:
        ct["notesColumn"] = chord_notes_column
    return ct


# A pleasant ascending major arpeggio (a confirm-ish flourish).
DEFAULT_SEQUENCE: list[str] = ["C5", "E5", "G5", "C6"]


def build_sequence_trigger(
    sequence_column: str | None,
    sequence_notes: Sequence[NoteInput] | None,
    sequence_gap: ToneTime,
    sequence_notes_column: str | None,
) -> dict[str, Any] | None:
    """
    Build the sequence-trigger spec — the table analogue of
    ``tones.play_sequence``. On each new row whose gate column is truthy a timed
    melody plays: from ``sequence_notes_column``'s cell when provided (a
    ``String[]`` or a ``"C5 E5 G5"`` string), otherwise the static
    ``sequence_notes``. ``None`` when neither a trigger nor a notes column is
    given. If only ``sequence_notes_column`` is given it doubles as the gate.
    """
    gate = sequence_column or sequence_notes_column
    if gate is None:
        return None
    notes = sequence_notes if sequence_notes is not None else DEFAULT_SEQUENCE
    normalized = normalize_sequence_notes(notes, "16n", 0.9)
    st: dict[str, Any] = {
        "column": gate,
        "notes": normalized,
        "gap": sequence_gap,
    }
    if sequence_notes_column is not None:
        st["notesColumn"] = sequence_notes_column
    return st


# ---------------------------------------------------------------------------
# Multi-dimensional mapping builder
# ---------------------------------------------------------------------------

# Flat voice-override keys → the simple scalar client-config keys they map to
# 1:1 (same name on the client). Envelope is handled separately (it nests).
_VOICE_PASSTHROUGH: tuple[str, ...] = (
    "instrument",
    "polyphony",
    "detune",
    "portamento",
    "volume",
    "pan",
)

_VOICE_ENVELOPE_KEYS: dict[str, str] = {
    "envelope_attack": "attack",
    "envelope_decay": "decay",
    "envelope_sustain": "sustain",
    "envelope_release": "release",
}

# Flat effect-toggle kwarg → client config key for the on/off node a ``False``
# disables per voice. Most map 1:1; ``ping_pong`` → ``pingPongDelay``.
_VOICE_EFFECT_TOGGLES: dict[str, str] = {
    "filter": "filter",
    "reverb": "reverb",
    "delay": "delay",
    "distortion": "distortion",
    "chorus": "chorus",
    "ping_pong": "pingPongDelay",
}


def voice_override_to_config(
    flat: Mapping[str, Any],
    base_envelope: dict[str, Any],
) -> dict[str, Any]:
    """
    Translate one flat voice-override dict into the ``Partial<ToneConfig>`` the
    client merges onto the base config for that row's voice.

    Because the client **shallow-merges** voice overrides, any envelope override
    is filled out from *base_envelope* so the unspecified ADSR stages aren't
    dropped. A ``False`` effect toggle disables that node for the voice; a
    ``True`` (or absent) toggle inherits the base node.
    """
    out: dict[str, Any] = {}
    for key in _VOICE_PASSTHROUGH:
        if key in flat:
            out[key] = flat[key]
    if any(k in flat for k in _VOICE_ENVELOPE_KEYS):
        env = dict(base_envelope)
        for flat_key, short in _VOICE_ENVELOPE_KEYS.items():
            if flat_key in flat:
                env[short] = flat[flat_key]
        out["envelope"] = env
    for flat_key, config_key in _VOICE_EFFECT_TOGGLES.items():
        if flat.get(flat_key) is False:
            out[config_key] = None
    return out


def build_mappings(
    pitch: str | None,
    loudness: str | None,
    loudness_range: Sequence[float] | None,
    voice: str | None,
    voices: Mapping[Any, VoiceOverride] | None,
    base_envelope: dict[str, Any] | None = None,
    voice_default: VoiceOverride | None = None,
) -> dict[str, Any] | None:
    """
    Build the value→pitch channel mapping, or ``None`` when no ``pitch`` column
    is given (trigger-only mode).

    Each channel names the COLUMN that drives it. ``loudness`` drives BOTH
    velocity (loudness) and note length. ``range`` left as ``None`` means the
    live min/max applies (see [[auto-range]]). ``voice`` is a categorical column
    whose cell value selects a per-side config override from ``voices``.
    ``voice_default`` is the override applied to rows whose ``voice`` cell
    matches no entry in ``voices``.
    """
    if pitch is None:
        return None

    mappings: dict[str, Any] = {"pitch": {"column": pitch}}

    if loudness is not None:
        lr = list(loudness_range) if loudness_range is not None else None
        # Loudness → velocity AND note length (bigger value = louder + longer).
        mappings["velocity"] = {"column": loudness, "range": lr}
        mappings["duration"] = {"column": loudness, "range": lr}

    if voice is not None:
        env = base_envelope if base_envelope is not None else _DEFAULT_ENVELOPE
        voice_map: dict[str, Any] = {
            "column": voice,
            "voices": {
                str(key): voice_override_to_config(override, env)
                for key, override in (voices or {}).items()
            },
        }
        if voice_default is not None:
            voice_map["default"] = voice_override_to_config(voice_default, env)
        mappings["voice"] = voice_map

    return mappings


# ---------------------------------------------------------------------------
# Sequence note normalisation
# ---------------------------------------------------------------------------

# Accepted input shapes for a single sequence item — the ``NoteValue`` /
# ``NoteInput`` aliases at the top of this module:
#   "C5"                 → note name
#   440.0                → Hz
#   {"midi": 60}         → explicit MIDI note value
#   ["C4", "E4", "G4"]    → a LIST is a chord (its notes sound together)
#   None                 → a rest (silent, still takes its duration)
#   ("C5", "16n")        → a TUPLE attaches a duration to any of the above
#   ("C5", "16n", 0.9)   → …and a velocity


def normalize_sequence_notes(
    notes: Sequence[NoteInput],
    default_duration: ToneTime,
    default_velocity: float,
) -> list[dict[str, Any]]:
    """
    Normalise a heterogeneous notes list into a uniform list of dicts with keys
    ``note``, ``duration``, ``velocity``.

    A *tuple* attaches a duration (and optionally a velocity) to its first
    element, so ``(["C4", "E4"], 0.5)`` is a half-second chord. A *list* is a
    chord at the default duration. ``None`` is a rest: silent, but it still
    occupies its duration.
    """
    result: list[dict[str, Any]] = []
    for item in notes:
        if isinstance(item, tuple):
            parts: list[Any] = list(item)
            note = parts[0]
            duration = parts[1] if len(parts) > 1 else default_duration
            velocity = float(parts[2]) if len(parts) > 2 else default_velocity
        else:
            note = item
            duration = default_duration
            velocity = default_velocity
        result.append(
            {
                "note": list(note) if isinstance(note, list) else note,
                "duration": duration,
                "velocity": velocity,
            }
        )
    return result


def envelope_from_flat(
    attack: float | None,
    decay: float | None,
    sustain: float | None,
    release: float | None,
) -> dict[str, Any] | None:
    """
    Collect the flat per-call ADSR kwargs into a (possibly partial) envelope
    override dict, or ``None`` when none are given. The client deep-merges this
    onto the base envelope, so a partial dict is fine — only named stages change.
    """
    env: dict[str, Any] = {}
    if attack is not None:
        env["attack"] = attack
    if decay is not None:
        env["decay"] = decay
    if sustain is not None:
        env["sustain"] = sustain
    if release is not None:
        env["release"] = release
    return env or None

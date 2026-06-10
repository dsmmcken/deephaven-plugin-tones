"""
Pure-Python implementation of the two public entry points for
deephaven-plugin-tones: ``use_tones`` (manual triggers) and ``table_tones``
(declarative table sonification).

Design:
  * ``use_tones(...)`` is a **render hook** for the imperative case — called at
    the top of a ``@ui.component``.  It calls ``use_state`` / ``use_ref`` to
    hold the event queue and monotonic id counter across renders, then returns a
    ``Tones`` NamedTuple ``(audio, audio_control)``: ``audio`` is a
    ``TonesElement`` you place in the tree (mounting boots the audio engine) and
    ``audio_control`` is a ``TonesControl`` whose methods (``play``,
    ``play_chord``, …) you call from handlers. The two are separate so the hook
    returns plain values, the way React/deephaven.ui hooks are expected to.
  * ``table_tones(...)`` is a declarative **element factory** (like ``ui.table``)
    for auto-sonifying a ticking ``Table``.  It returns a bare ``TonesElement``
    you drop into the tree — no control handle, since it emits no manual events.
  * ``TonesElement`` subclasses ``BaseElement`` so it is *placeable in the render
    tree*; it carries only props (no methods).  ``TonesControl`` is a plain
    object whose trigger methods close over the stable ``set_events`` /
    ``id_ref`` obtained from the hooks.
  * Pure helper functions (``_make_play_event``, ``_normalize_sequence_notes``,
    ``_bounded_append``, ``_build_config``, ``_resolve_envelope``, and the
    ``_DEFAULT_*`` config constants) are module-level so they can be unit-tested
    without a running Deephaven server.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, NamedTuple

from deephaven.ui.elements import BaseElement
from deephaven.ui.hooks import use_memo, use_ref, use_state

# ---------------------------------------------------------------------------
# Element name — MUST match the JS pluginsElementMap key exactly.
# ---------------------------------------------------------------------------
_ELEMENT_NAME = "deephaven_plugin_tones.deephaven_plugin_tones_component"

# ---------------------------------------------------------------------------
# Bounded deque helpers
# ---------------------------------------------------------------------------

_MAX_EVENTS: int = 64


def _bounded_append(
    prev: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    ev: dict[str, Any],
) -> tuple[dict[str, Any], ...]:
    """Return a new tuple of at most _MAX_EVENTS events with *ev* appended."""
    return (*prev, ev)[-_MAX_EVENTS:]


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
    envelope_attack: int | float | None,
    envelope_decay: int | float | None,
    envelope_sustain: int | float | None,
    envelope_release: int | float | None,
) -> dict[str, Any]:
    """
    Resolve the flat ``envelope_*`` kwargs into a complete ADSR dict, filling
    each unset (``None``) stage from the pleasant default. Shared by
    ``_build_config`` and the ``voices`` override translation (which needs the
    full base envelope because the client shallow-merges voice overrides).
    """
    return {
        "attack": envelope_attack
        if envelope_attack is not None
        else _DEFAULT_ENVELOPE["attack"],
        "decay": envelope_decay
        if envelope_decay is not None
        else _DEFAULT_ENVELOPE["decay"],
        "sustain": envelope_sustain
        if envelope_sustain is not None
        else _DEFAULT_ENVELOPE["sustain"],
        "release": envelope_release
        if envelope_release is not None
        else _DEFAULT_ENVELOPE["release"],
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
_DEFAULT_LIMITER_THRESHOLD: int | float = -1


def _build_config(
    *,
    instrument: str,
    polyphony: int,
    envelope_attack: int | float | None,
    envelope_decay: int | float | None,
    envelope_sustain: int | float | None,
    envelope_release: int | float | None,
    detune: int | float,
    portamento: int | float,
    filter: bool,  # noqa: A002 — effect on/off toggle
    filter_type: str,
    filter_frequency: int | float,
    filter_q: int | float,
    filter_rolloff: int,
    reverb: bool,
    reverb_decay: int | float,
    reverb_wet: int | float,
    reverb_predelay: int | float,
    delay: bool,
    delay_time: str | int | float | None,
    delay_feedback: int | float | None,
    delay_wet: int | float | None,
    volume: int | float,
    pan: int | float,
    scale: str | list[int],
    root: str,
    octaves: int,
    value_range: list[float] | tuple[float, float] | None,
    descending: bool,
    # Tier 1 effects — each defaults so existing callers/tests need not pass them.
    distortion: bool = False,
    distortion_amount: int | float | None = None,
    distortion_wet: int | float | None = None,
    chorus: bool = False,
    chorus_frequency: int | float | None = None,
    chorus_depth: int | float | None = None,
    chorus_wet: int | float | None = None,
    ping_pong: bool = False,
    ping_pong_time: str | int | float | None = None,
    ping_pong_feedback: int | float | None = None,
    ping_pong_wet: int | float | None = None,
    limiter: bool = True,
    limiter_threshold: int | float = _DEFAULT_LIMITER_THRESHOLD,
) -> dict[str, Any]:
    """
    Build the ``config`` dict with **verbatim camelCase keys** as required by
    the locked contract.

    The public API is now FLAT (``filter_frequency=`` rather than
    ``filter={"frequency": ...}``); this function re-assembles the flat kwargs
    into the nested ``envelope`` / ``filter`` / ``reverb`` / ``delay`` sub-dicts
    the client config still expects, so the client contract is unchanged. The
    ``filter`` / ``reverb`` / ``delay`` booleans toggle each effect on/off (a
    disabled effect serialises as ``None``).

    Static values passed here are the per-row *baseline* for any param that is
    also data-driven (see ``_build_param_mappings``): the client modulates the
    live node around this value.
    """
    # Envelope: flat kwargs → dict, falling back to the pleasant default per key.
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
            "delayTime": delay_time
            if delay_time is not None
            else _DEFAULT_DELAY["delayTime"],
            "feedback": delay_feedback
            if delay_feedback is not None
            else _DEFAULT_DELAY["feedback"],
            "wet": delay_wet if delay_wet is not None else _DEFAULT_DELAY["wet"],
        }
        if delay
        else None
    )

    resolved_distortion: dict[str, Any] | None = (
        {
            "amount": distortion_amount
            if distortion_amount is not None
            else _DEFAULT_DISTORTION["amount"],
            "wet": distortion_wet
            if distortion_wet is not None
            else _DEFAULT_DISTORTION["wet"],
        }
        if distortion
        else None
    )

    resolved_chorus: dict[str, Any] | None = (
        {
            "frequency": chorus_frequency
            if chorus_frequency is not None
            else _DEFAULT_CHORUS["frequency"],
            "depth": chorus_depth
            if chorus_depth is not None
            else _DEFAULT_CHORUS["depth"],
            "wet": chorus_wet if chorus_wet is not None else _DEFAULT_CHORUS["wet"],
        }
        if chorus
        else None
    )

    resolved_ping_pong: dict[str, Any] | None = (
        {
            "delayTime": ping_pong_time
            if ping_pong_time is not None
            else _DEFAULT_PINGPONG["delayTime"],
            "feedback": ping_pong_feedback
            if ping_pong_feedback is not None
            else _DEFAULT_PINGPONG["feedback"],
            "wet": ping_pong_wet
            if ping_pong_wet is not None
            else _DEFAULT_PINGPONG["wet"],
        }
        if ping_pong
        else None
    )

    # Master limiter: a single shared node on the master bus (the client owns the
    # one instance); a dict carries its threshold, None disables it.
    resolved_limiter: dict[str, Any] | None = (
        {"threshold": limiter_threshold} if limiter else None
    )

    # value_range: accept tuple or list, serialise as list
    vr: list[float] | None = None
    if value_range is not None:
        vr = list(value_range)

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
        "scale": scale,
        "root": root,
        "octaves": octaves,
        "valueRange": vr,
        "descending": descending,
    }


# ---------------------------------------------------------------------------
# Flat overloaded-param resolution
# ---------------------------------------------------------------------------
#
# A NUMERIC sound param may be given three ways:
#   number          → a static value
#   "ColumnName"    → data-driven: the client maps that column to the param,
#                     auto-tracking the column's running min/max as INPUT and
#                     mapping into the param's default OUTPUT range
#   ("Col", lo, hi) → data-driven with an explicit OUTPUT range [lo, hi]
#
# ``_resolve_param`` splits such a value into (static_baseline, channel). The
# static baseline always goes into ``config`` (so a data-driven param still has
# a sensible value the client modulates around); ``channel`` (or None) is the
# per-row mapping. Output-range defaults live on the CLIENT, next to the nodes —
# Python only forwards an explicit ``{min, max}`` when the tuple form is used.

# Public kwarg name → client param-path it modulates. Only these numeric params
# are continuously modulatable per-event (live Tone signal on a chain node);
# everything else (envelope_*, reverb_decay, filter_type, …) is static-only.
_PARAM_PATHS: dict[str, str] = {
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


def _resolve_param(
    value: Any,
    default: int | float,
) -> tuple[int | float, dict[str, Any] | None]:
    """
    Split an overloaded numeric param into ``(static_baseline, channel)``.

    Every caller passes a non-``None`` *default*, so the static baseline is
    always a number (the client modulates the live node around it).

    * ``None``            → ``(default, None)``
    * ``"Col"`` (str)    → ``(default, {"column": "Col"})``
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
    # plain number
    return value, None


def _resolve_pitch(
    value: str | tuple[Any, ...] | list[Any] | None,
) -> tuple[str | None, list[float] | None]:
    """
    Resolve ``pitch`` / ``loudness`` (which name a COLUMN). Unlike effect
    params, a 3-tuple here sets the INPUT clamp (the data domain), not an output
    range — pitch maps into the musical scale, loudness into velocity/duration.

    * ``"Col"``            → ``("Col", None)``  (input range auto)
    * ``("Col", lo, hi)`` → ``("Col", [lo, hi])``
    * ``None``             → ``(None, None)``
    """
    if value is None:
        return None, None
    if isinstance(value, str):
        return value, None
    if isinstance(value, (list, tuple)):
        parts = list(value)
        if len(parts) == 1:
            return parts[0], None
        if len(parts) == 3:
            return parts[0], [float(parts[1]), float(parts[2])]
        raise ValueError(
            f"a pitch/loudness column must be a column name, a 1-tuple (column,), or a 3-tuple (column, lo, hi); got {value!r}"
        )
    return None, None


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


def _build_param_mappings(
    channels: dict[str, dict[str, Any] | None],
) -> dict[str, Any] | None:
    """
    Collapse the per-param channels into the ``param_mappings`` prop:
    ``{client_path: {column, min?, max?}}``. Returns ``None`` when no numeric
    param is data-driven. The client owns the default OUTPUT range per path and
    auto-tracks each column's running min/max as the INPUT span.
    """
    out: dict[str, Any] = {}
    for kwarg, channel in channels.items():
        if channel is not None:
            out[_PARAM_PATHS[kwarg]] = channel
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
# we attach these columns and the client scales to the true global span from
# the first aggregation cycle, instead of a cold-start client-side running
# min/max. See [[auto-range]].

_RANGE_MIN_SUFFIX = "_tones_min"
_RANGE_MAX_SUFFIX = "_tones_max"


def _augment_with_ranges(
    table: Any,
    cols: Sequence[str],
) -> tuple[Any, dict[str, tuple[str, str]]]:
    """
    Return ``(augmented_table, {col: (min_col, max_col)})``.

    For each column in *cols*, append live ``<col>_tones_min`` /
    ``<col>_tones_max`` columns holding the table-wide min/max (broadcast to
    every row via a keyless ``natural_join``).  On ANY failure — no table,
    engine import error, a table type that can't be joined (e.g. a tree
    table) — returns ``(table, {})`` so the caller falls back to client-side
    auto-ranging.  Never raises.
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
        mn = f"{col}{_RANGE_MIN_SUFFIX}"
        mx = f"{col}{_RANGE_MAX_SUFFIX}"
        aggs.append(agg.min_(f"{mn} = {col}"))
        aggs.append(agg.max_(f"{mx} = {col}"))
        joins.extend((mn, mx))
        names[col] = (mn, mx)

    try:
        range_table = table.agg_by(aggs)  # no `by=` → single global row
        augmented = table.natural_join(range_table, on=[], joins=joins)
    except Exception:  # noqa: BLE001 — fall back to client running min/max
        return table, {}

    return augmented, names


# ---------------------------------------------------------------------------
# Column-name validation (helpful errors for typo'd column props)
# ---------------------------------------------------------------------------
#
# Column-name props (``column``, ``pitch``, data-driven param columns, trigger
# columns, …) are otherwise forwarded to the client verbatim: the engine's own
# "Unknown column" error during auto-range is swallowed by
# ``_augment_with_ranges`` (its broad except powers the tree-table / no-engine
# fallback), and the param/trigger columns never touch the engine server-side
# at all. The net effect is that a typo'd column name is silently accepted and
# only fails later in the browser, with nothing pointing back at the typo.
#
# This guard closes that gap: when a real table is supplied and its column list
# is introspectable, a referenced name that isn't present raises a ``ValueError``
# naming the offender and listing the available columns (mirroring the engine's
# ``NoSuchColumnException`` message). It is best-effort — if the column list
# can't be determined (engine absent, or a table type without ``column_names``),
# validation is skipped rather than risking a false positive, matching the
# graceful-degradation philosophy of ``_augment_with_ranges``.


def _validate_columns(
    table: Any,
    referenced: Sequence[tuple[str, str | None]],
) -> None:
    """
    Raise ``ValueError`` if any referenced column name is absent from *table*.

    Args:
        table: The source ``Table`` (the user's, pre-augmentation), or ``None``.
        referenced: ``(role, column_name)`` pairs, where ``role`` is the kwarg
            that named the column (used only for the error message). ``None``
            column names are ignored.

    Best-effort: returns silently when *table* is ``None``, when nothing is
    referenced, or when the table's column list can't be introspected. Only the
    unambiguous typo case — a known column list that lacks a referenced name —
    raises.
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
            f"table_tones(): unknown column(s) {miss_desc}. Available columns: {available}."
        )


# Enumerated config values. These are passed straight through to Tone.js, where
# an unknown value fails silently in the browser (the failure mode AGENTS.md's
# Debugging section calls out). Validating them at render time — alongside the
# column check above — turns that into an actionable ValueError instead.
_INSTRUMENTS: tuple[str, ...] = (
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
)
_FILTER_TYPES: tuple[str, ...] = (
    "lowpass",
    "highpass",
    "bandpass",
    "notch",
    "allpass",
    "peaking",
)
_FILTER_ROLLOFFS: tuple[int, ...] = (-12, -24, -48, -96)


def _validate_config_enums(
    fn: str,
    instrument: str,
    filter_type: str,
    filter_rolloff: int,
    voices: dict[str, dict[str, Any]] | None = None,
) -> None:
    """
    Raise ``ValueError`` for an out-of-range ``instrument`` / ``filter_type`` /
    ``filter_rolloff`` (and any per-voice ``instrument`` override), naming the
    offender and the valid set. *fn* is the calling function name for the message.
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
    for key, override in (voices or {}).items():
        inst = override.get("instrument")
        if inst is not None and inst not in _INSTRUMENTS:
            raise ValueError(
                f"{fn}(): unknown instrument {inst!r} in voices[{key!r}]. Choose one of: {', '.join(_INSTRUMENTS)}."
            )


# ---------------------------------------------------------------------------
# Chord-trigger builder
# ---------------------------------------------------------------------------

# A pleasant, resolved progression: I - V - vi - IV in C (the "four chords"),
# voiced low-to-mid so it sounds full without muddiness.
_DEFAULT_CHORDS: list[list[str]] = [
    ["C4", "E4", "G4"],  # I   (C major)
    ["G3", "B3", "D4"],  # V   (G major)
    ["A3", "C4", "E4"],  # vi  (A minor)
    ["F3", "A3", "C4"],  # IV  (F major)
]


def _build_chord_trigger(
    chord_column: str | None,
    chords: list[list[str]] | None,
    chord_gap: str,
    chord_duration: str,
    chord_notes_column: str | None,
) -> dict[str, Any] | None:
    """
    Build the ``chord_trigger`` prop, or ``None`` when neither a trigger column
    nor a per-row chord column is given. On each new row whose gate column is
    truthy, the client plays a chord progression: from ``chord_notes_column``'s
    cell when provided (a ``String[]`` = one chord, or a delimited ``String``),
    otherwise the static ``chords``. If only ``chord_notes_column`` is given it
    doubles as the gate (fires when its cell is non-empty).
    """
    gate = chord_column or chord_notes_column
    if gate is None:
        return None
    progression = chords if chords is not None else _DEFAULT_CHORDS
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
_DEFAULT_SEQUENCE: list[str] = ["C5", "E5", "G5", "C6"]


def _build_sequence_trigger(
    sequence_column: str | None,
    sequence_notes: Sequence[Any] | None,
    sequence_gap: str,
    sequence_notes_column: str | None,
) -> dict[str, Any] | None:
    """
    Build the ``sequence_trigger`` prop — the table analogue of
    ``play_sequence``. On each new row whose gate column is truthy, the client
    plays a timed melody: from ``sequence_notes_column``'s cell when provided (a
    ``String[]`` or a ``"C5 E5 G5"`` string), otherwise the static
    ``sequence_notes``. ``None`` when neither a trigger nor a notes column is
    given. If only ``sequence_notes_column`` is given it doubles as the gate.
    Static notes accept the same heterogeneous forms as ``play_sequence``.
    """
    gate = sequence_column or sequence_notes_column
    if gate is None:
        return None
    notes = sequence_notes if sequence_notes is not None else _DEFAULT_SEQUENCE
    normalized = _normalize_sequence_notes(notes, "16n", 0.9)
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


def _voice_override_to_config(
    flat: dict[str, Any],
    base_envelope: dict[str, Any],
) -> dict[str, Any]:
    """
    Translate one flat voice-override dict into the ``Partial<ToneConfig>`` the
    client merges onto the base config for that row's voice.

    The override keys mirror the flat ``table_tones`` param names
    (``instrument``, ``envelope_attack`` …, ``filter`` / ``reverb`` / ``delay`` /
    ``distortion`` / ``chorus`` / ``ping_pong`` toggles). Because the client
    **shallow-merges** voice overrides, any envelope override is filled out from
    *base_envelope* so the unspecified ADSR stages aren't dropped. A ``False``
    effect toggle disables that node for the voice; a ``True`` (or absent) toggle
    inherits the base node.
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


def _build_mappings(
    pitch: str | None,
    loudness: str | None,
    loudness_range: list[float] | tuple[float, float] | None,
    voice: str | None,
    voices: dict[str, dict[str, Any]] | None,
    base_envelope: dict[str, Any] | None = None,
    voice_default: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """
    Build the ``mappings`` prop for value→pitch table sonification, or ``None``
    when no ``pitch`` column is given (trigger-only / event mode).

    Each channel names the COLUMN that drives it.  ``loudness`` drives BOTH
    velocity (loudness) and note length.  ``range`` is left as ``None`` =>
    the client auto-tracks the column's running min/max (see [[auto-range]]):
    every range-needing channel defaults to AUTO.  ``voice`` is a categorical
    column whose cell value selects a per-side config override from ``voices``
    (each value a flat override dict; see ``_voice_override_to_config``).
    ``voice_default`` is the flat override applied to rows whose ``voice`` cell
    matches no entry in ``voices`` (falls back to the base config when unset).
    """
    if pitch is None:
        return None

    mappings: dict[str, Any] = {"pitch": {"column": pitch}}

    if loudness is not None:
        lr = list(loudness_range) if loudness_range is not None else None
        # Volume → loudness AND note length (bigger trade = louder + longer).
        mappings["velocity"] = {"column": loudness, "range": lr}
        mappings["duration"] = {"column": loudness, "range": lr}

    if voice is not None:
        env = base_envelope if base_envelope is not None else _DEFAULT_ENVELOPE
        voice_map: dict[str, Any] = {
            "column": voice,
            "voices": {
                str(key): _voice_override_to_config(override, env)
                for key, override in (voices or {}).items()
            },
        }
        if voice_default is not None:
            voice_map["default"] = _voice_override_to_config(voice_default, env)
        mappings["voice"] = voice_map

    return mappings


# ---------------------------------------------------------------------------
# Event dict builders (pure, server-testable)
# ---------------------------------------------------------------------------


def _make_play_event(
    eid: int,
    note: str | int | float | dict[str, Any],
    duration: str,
    velocity: float,
) -> dict[str, Any]:
    """Build a ``play`` event dict."""
    return {
        "id": eid,
        "op": "play",
        "note": note,
        "duration": duration,
        "velocity": velocity,
    }


def _make_chord_event(
    eid: int,
    notes: list[Any],
    duration: str,
    velocity: float,
) -> dict[str, Any]:
    """Build a ``chord`` event dict."""
    return {
        "id": eid,
        "op": "chord",
        "notes": list(notes),
        "duration": duration,
        "velocity": velocity,
    }


def _make_value_event(
    eid: int,
    value: float,
    overrides: dict[str, Any],
) -> dict[str, Any]:
    """Build a ``value`` event dict."""
    return {
        "id": eid,
        "op": "value",
        "value": float(value),
        "overrides": overrides,
    }


def _make_sequence_event(
    eid: int,
    notes: list[dict[str, Any]],
    gap: str,
    envelope: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build a ``sequence`` event dict."""
    return {
        "id": eid,
        "op": "sequence",
        "notes": notes,
        "gap": gap,
        "envelope": envelope,
    }


def _make_stop_event(eid: int) -> dict[str, Any]:
    """Build a ``stop`` event dict."""
    return {"id": eid, "op": "stop"}


def _make_set_volume_event(eid: int, db: int | float) -> dict[str, Any]:
    """Build a ``setVolume`` event dict."""
    return {"id": eid, "op": "setVolume", "db": db}


# ---------------------------------------------------------------------------
# Sequence note normalisation
# ---------------------------------------------------------------------------

# Accepted input shapes for a single sequence item. A bare *note value* is a
# pitch name, Hz, or the one explicit-MIDI form ``{"midi": 60}``; wrap it in a
# tuple to attach a per-note duration / velocity:
#   "C5"                 → note name
#   440.0                → Hz
#   {"midi": 60}         → explicit MIDI note value
#   ("C5", "16n")        → note + duration
#   ("C5", "16n", 0.9)   → note + duration + velocity
NoteValue = str | int | float | dict[str, Any]
NoteInput = NoteValue | tuple[Any, ...]


def _normalize_sequence_notes(
    notes: Sequence[NoteInput],
    default_duration: str,
    default_velocity: float,
) -> list[dict[str, Any]]:
    """
    Normalise a heterogeneous notes list into a uniform list of dicts with
    keys ``note``, ``duration``, ``velocity``.

    Accepted input forms per item:
    * a bare note value (``"C5"``, Hz, or ``{"midi": 60}``) → fills in the
      default duration & velocity.
    * ``("C5",)``                   → note only, fill defaults
    * ``("C5", "16n")``             → note + duration, fill velocity
    * ``("C5", "16n", 0.8)``        → full

    A tuple's first element is the note value (so ``({"midi": 60}, "16n")``
    works); a non-tuple item is itself the note value.
    """
    result: list[dict[str, Any]] = []
    for item in notes:
        if isinstance(item, (list, tuple)):
            parts = list(item)
            note = parts[0]
            duration = parts[1] if len(parts) > 1 else default_duration
            velocity = float(parts[2]) if len(parts) > 2 else default_velocity
            result.append({"note": note, "duration": duration, "velocity": velocity})
        else:
            # bare note value: str / Hz / {"midi": N}
            result.append(
                {
                    "note": item,
                    "duration": default_duration,
                    "velocity": default_velocity,
                }
            )
    return result


def _envelope_from_flat(
    attack: int | float | None,
    decay: int | float | None,
    sustain: int | float | None,
    release: int | float | None,
) -> dict[str, Any] | None:
    """
    Collect the flat per-call ADSR kwargs into a (possibly partial) envelope
    override dict, or ``None`` when none are given. The client deep-merges this
    onto the base envelope for the sequence, so a partial dict is fine — only
    the stages you name change.
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


# ---------------------------------------------------------------------------
# Value overrides: only scale/root/octaves/valueRange/descending are valid
# ---------------------------------------------------------------------------

_OVERRIDE_KEYS: frozenset[str] = frozenset(
    {"scale", "root", "octaves", "valueRange", "descending"}
)


def _build_overrides(**kwargs: Any) -> dict[str, Any]:
    """
    Filter and normalise play_value overrides to only the keys the client
    understands (camelCase).  Accept snake_case ``value_range`` as an alias.
    """
    out: dict[str, Any] = {}
    for k, v in kwargs.items():
        if k == "value_range":
            out["valueRange"] = list(v) if v is not None else None
        elif k in _OVERRIDE_KEYS:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# Element + control + result types
# ---------------------------------------------------------------------------


class TonesElement(BaseElement):
    """
    The renderable, zero-DOM audio element. Place it in the component tree to
    mount the (invisible) audio engine — it contributes no layout.

    Carries only props; it has no trigger methods. ``use_tones`` returns one as
    ``Tones.audio``; ``table_tones`` returns one directly. Do **not** instantiate
    directly — use ``use_tones(...)`` or ``table_tones(...)``.
    """


class TonesControl:
    """
    The imperative audio handle returned as ``Tones.audio_control`` by
    ``use_tones``. Call its methods (``play``, ``play_chord``, …) from any
    handler in scope; each enqueues an event that the mounted ``TonesElement``
    forwards to the engine.

    Created by ``use_tones`` — its methods close over the stable ``set_events`` /
    ``id_ref`` from that hook, so the handle stays valid across renders.
    """

    def __init__(
        self,
        set_events: Callable[..., None],
        id_ref: Any,
    ) -> None:
        self._set_events = set_events
        self._id_ref = id_ref

    def play(
        self,
        note: str | int | float | dict[str, Any],
        duration: str = "8n",
        velocity: float = 1.0,
    ) -> None:
        """
        Play a single note.

        Args:
            note: Pitch as ``"C4"``, Hz (number), or ``{"midi": 60}``.
            duration: Tone.js duration string, default ``"8n"``.
            velocity: 0.0–1.0, default 1.0.
        """
        self._emit(_make_play_event(self._next_id(), note, duration, velocity))

    def play_chord(
        self,
        notes: list[Any],
        duration: str = "2n",
        velocity: float = 1.0,
    ) -> None:
        """
        Play multiple notes simultaneously.

        Args:
            notes: List of pitches (same accepted forms as ``play``).
            duration: Tone.js duration string, default ``"2n"``.
            velocity: 0.0–1.0, default 1.0.
        """
        self._emit(_make_chord_event(self._next_id(), notes, duration, velocity))

    def play_value(self, value: float, **overrides: Any) -> None:
        """
        Map a numeric value to a pitch using the configured
        scale/root/octaves/valueRange/descending, then play it.

        Args:
            value: Numeric value to sonify.
            **overrides: Override subset of scale/root/octaves/
                         value_range/valueRange/descending for this call only.
        """
        self._emit(
            _make_value_event(
                self._next_id(), float(value), _build_overrides(**overrides)
            )
        )

    def play_sequence(
        self,
        notes: Sequence[NoteInput],
        gap: str = "16n",
        duration: str = "8n",
        velocity: float = 0.9,
        attack: int | float | None = None,
        decay: int | float | None = None,
        sustain: int | float | None = None,
        release: int | float | None = None,
    ) -> None:
        """
        Play a timed melody / arpeggio.  Self-terminating — don't follow it with
        ``stop()``.

        Args:
            notes: Heterogeneous note list (see module docstring).
            gap: Onset interval between notes (Tone.js time).
            duration: Default per-note duration if not specified per note.
            velocity: Default per-note velocity if not specified per note.
            attack, decay, sustain, release: Optional ADSR overrides for this
                call (e.g. a plucky earcon). Each ``None`` keeps the base
                envelope's value; only the stages you name change.
        """
        envelope = _envelope_from_flat(attack, decay, sustain, release)
        normalized = _normalize_sequence_notes(notes, duration, velocity)
        self._emit(_make_sequence_event(self._next_id(), normalized, gap, envelope))

    def stop(self) -> None:
        """Stop all currently-playing sounds immediately."""
        self._emit(_make_stop_event(self._next_id()))

    def set_volume(self, db: int | float) -> None:
        """
        Adjust master volume.

        Args:
            db: New volume in dB (e.g. -6 for half-amplitude).
        """
        self._emit(_make_set_volume_event(self._next_id(), db))

    # ------------------------------------------------------------------
    # Internal helpers (not part of the public API)
    # ------------------------------------------------------------------

    def _next_id(self) -> int:
        self._id_ref.current += 1
        return self._id_ref.current

    def _emit(self, ev: dict[str, Any]) -> None:
        self._set_events(lambda prev: _bounded_append(prev, ev))


class Tones(NamedTuple):
    """
    What ``use_tones`` returns: the element to mount plus the control handle.

    Destructure it — ``audio, audio_control = use_tones(...)`` — or reach for a
    field by name (``use_tones(...).audio``).
    """

    audio: TonesElement
    audio_control: TonesControl


# ---------------------------------------------------------------------------
# Public entry points: use_tones (triggers) + table_tones (sonify)
# ---------------------------------------------------------------------------


def use_tones(
    instrument: str = "sine",
    polyphony: int = 8,
    envelope_attack: int | float | None = None,
    envelope_decay: int | float | None = None,
    envelope_sustain: int | float | None = None,
    envelope_release: int | float | None = None,
    detune: int | float = 0,
    portamento: int | float = 0,
    filter: bool = True,  # noqa: A002 — effect on/off toggle
    filter_type: str = "lowpass",
    filter_frequency: int | float = 2200,
    filter_q: int | float = 1,
    filter_rolloff: int = -24,
    reverb: bool = True,
    reverb_decay: int | float = 3,
    reverb_wet: int | float = 0.3,
    reverb_predelay: int | float = 0.01,
    delay: bool = False,
    delay_time: str | int | float | None = None,
    delay_feedback: int | float | None = None,
    delay_wet: int | float | None = None,
    distortion: bool = False,
    distortion_amount: int | float | None = None,
    distortion_wet: int | float | None = None,
    chorus: bool = False,
    chorus_frequency: int | float | None = None,
    chorus_depth: int | float | None = None,
    chorus_wet: int | float | None = None,
    ping_pong: bool = False,
    ping_pong_time: str | int | float | None = None,
    ping_pong_feedback: int | float | None = None,
    ping_pong_wet: int | float | None = None,
    limiter: bool = True,
    limiter_threshold: int | float = _DEFAULT_LIMITER_THRESHOLD,
    volume: int | float = -8,
    pan: int | float = 0,
    scale: str | list[int] = "pentatonic",
    root: str = "C3",
    octaves: int = 3,
    descending: bool = False,
) -> Tones:
    """
    Render hook for manual audio triggers. Returns ``(audio, audio_control)``.

    Call it at the **top** of a ``@ui.component`` (like any render hook), then:
    place ``audio`` in the render tree (mounting it boots the invisible audio
    engine — it takes no layout space), and call methods on ``audio_control``
    (``play``, ``play_chord``, ``play_sequence``, ``play_value``, ``stop``,
    ``set_volume``) from any handler in scope.

    Every param configures the synth / effects chain / value→pitch mapping and
    is **static** (set once per render). To auto-sonify a ticking ``Table``
    instead, use ``table_tones`` (which also adds the data-driven column
    overload for the effect params).

    Args:
        instrument: Tone.js synth type — ``"sine"`` | ``"triangle"`` |
            ``"square"`` | ``"sawtooth"`` | ``"fm"`` | ``"am"`` | ``"membrane"``
            | ``"pluck"`` | ``"monosynth"`` | ``"duosynth"`` | ``"metal"``.
        polyphony: Maximum simultaneous voices. Default ``8``.
        envelope_attack, envelope_decay, envelope_sustain, envelope_release:
            ADSR envelope, in seconds (sustain is a 0–1 level). Each ``None``
            keeps the pleasant sine default.
        detune: Global detune in cents. Default ``0``.
        portamento: Glide time between notes in seconds. Default ``0``.
        filter: ``True`` (default) enables the lowpass filter node; ``False``
            disables it.
        filter_type: ``"lowpass"`` | ``"highpass"`` | ``"bandpass"`` |
            ``"notch"`` | ``"allpass"`` | ``"peaking"``.
        filter_frequency: Cutoff in Hz. Default ``2200``.
        filter_q: Resonance. Default ``1``.
        filter_rolloff: ``-12`` | ``-24`` | ``-48`` | ``-96``.
        reverb: ``True`` (default) enables the reverb node; ``False`` disables.
        reverb_decay: Tail length in seconds. Default ``3``.
        reverb_wet: Wet/dry mix 0–1. Default ``0.3``.
        reverb_predelay: Pre-delay in seconds. Default ``0.01``.
        delay: ``True`` enables the feedback-delay node. Default ``False`` —
            setting any ``delay_*`` param also enables it.
        delay_time: Delay time (Tone.js note string like ``"8n"`` or seconds).
        delay_feedback: Feedback 0–~0.9. Default ``0.2``.
        delay_wet: Wet/dry mix 0–1. Default ``0.1``.
        distortion: ``True`` enables a waveshaper distortion node. Default
            ``False``.
        distortion_amount: Distortion amount 0–1. Default ``0.4``.
        distortion_wet: Wet/dry mix 0–1. Default ``1.0`` (fully wet).
        chorus: ``True`` enables a stereo chorus node. Default ``False``.
        chorus_frequency: LFO rate in Hz. Default ``1.5``.
        chorus_depth: Modulation depth 0–1. Default ``0.7``.
        chorus_wet: Wet/dry mix 0–1. Default ``0.5``.
        ping_pong: ``True`` enables a stereo ping-pong delay node. Default
            ``False``.
        ping_pong_time: Delay time (Tone.js note string like ``"8n"`` or
            seconds). Default ``"8n"``.
        ping_pong_feedback: Feedback 0–~0.9. Default ``0.2``.
        ping_pong_wet: Wet/dry mix 0–1. Default ``0.5``.
        limiter: ``True`` (default) inserts a brick-wall limiter on the master
            bus so loud bursts don't clip; ``False`` removes it.
        limiter_threshold: Limiter ceiling in dBFS. Default ``-1``.
        volume: Master volume in dB. Default ``-8``.
        pan: Stereo position, ``-1`` (left) … ``1`` (right). Default ``0``.
        scale: Scale name (``"pentatonic"``, ``"major"``, ``"minor"``,
            ``"chromatic"``) or an explicit list of semitone intervals, e.g.
            ``[0, 2, 4, 7, 9]`` — used by ``play_value``.
        root: Bottom of the value→pitch range as a note name, e.g. ``"C3"``.
        octaves: Octaves spanned by the value→pitch mapping.
        descending: When ``True``, higher values map to lower pitches.

    Returns:
        A ``Tones`` named tuple ``(audio, audio_control)``.

    Example::

        @ui.component
        def tone_buttons():
            audio, audio_control = use_tones(instrument="sine", reverb_wet=0.25)
            return ui.flex(
                audio,
                # A "confirm" earcon is just a short play_sequence.
                ui.button(
                    "Confirm",
                    on_press=lambda _e: audio_control.play_sequence(
                        ["C5", "E5", "G5", "C6"],
                        gap="16n",
                        duration="16n",
                        attack=0.005,
                        decay=0.12,
                        sustain=0.0,
                        release=0.25,
                    ),
                ),
                ui.button("Stop", on_press=lambda _e: audio_control.stop()),
            )
    """
    events, set_events = use_state(())
    id_ref = use_ref(0)

    _validate_config_enums("use_tones", instrument, filter_type, filter_rolloff)
    delay_on = _delay_enabled(delay, delay_time, delay_feedback, delay_wet)

    config = _build_config(
        instrument=instrument,
        polyphony=polyphony,
        envelope_attack=envelope_attack,
        envelope_decay=envelope_decay,
        envelope_sustain=envelope_sustain,
        envelope_release=envelope_release,
        detune=detune,
        portamento=portamento,
        filter=filter,
        filter_type=filter_type,
        filter_frequency=filter_frequency,
        filter_q=filter_q,
        filter_rolloff=filter_rolloff,
        reverb=reverb,
        reverb_decay=reverb_decay,
        reverb_wet=reverb_wet,
        reverb_predelay=reverb_predelay,
        delay=delay_on,
        delay_time=delay_time,
        delay_feedback=delay_feedback,
        delay_wet=delay_wet,
        distortion=distortion,
        distortion_amount=distortion_amount,
        distortion_wet=distortion_wet,
        chorus=chorus,
        chorus_frequency=chorus_frequency,
        chorus_depth=chorus_depth,
        chorus_wet=chorus_wet,
        ping_pong=ping_pong,
        ping_pong_time=ping_pong_time,
        ping_pong_feedback=ping_pong_feedback,
        ping_pong_wet=ping_pong_wet,
        limiter=limiter,
        limiter_threshold=limiter_threshold,
        volume=volume,
        pan=pan,
        scale=scale,
        root=root,
        octaves=octaves,
        value_range=None,
        descending=descending,
    )

    element = TonesElement(
        _ELEMENT_NAME,
        config=config,
        events=list(events),
        mode="last",
        rate_limit_ms=60,
    )
    control = use_memo(
        lambda: TonesControl(set_events, id_ref),
        [set_events, id_ref],
    )
    return Tones(audio=element, audio_control=control)


def table_tones(
    table: Any,
    *,
    # Instrument / voice
    instrument: str = "sine",
    polyphony: int = 8,
    envelope_attack: int | float | None = None,
    envelope_decay: int | float | None = None,
    envelope_sustain: int | float | None = None,
    envelope_release: int | float | None = None,
    detune: int | float | str | tuple[Any, ...] = 0,
    portamento: int | float = 0,
    # Effects — each numeric param accepts a number (static), a column-name str
    # (data-driven), or (col, lo, hi) (data-driven + output range).
    filter: bool = True,  # noqa: A002 — effect on/off toggle
    filter_type: str = "lowpass",
    filter_frequency: int | float | str | tuple[Any, ...] = 2200,
    filter_q: int | float | str | tuple[Any, ...] = 1,
    filter_rolloff: int = -24,
    reverb: bool = True,
    reverb_decay: int | float = 3,
    reverb_wet: int | float | str | tuple[Any, ...] = 0.3,
    reverb_predelay: int | float = 0.01,
    delay: bool = False,
    delay_time: str | int | float | None = None,
    delay_feedback: int | float | str | tuple[Any, ...] | None = None,
    delay_wet: int | float | str | tuple[Any, ...] | None = None,
    distortion: bool = False,
    distortion_amount: int | float = 0.4,
    distortion_wet: int | float | str | tuple[Any, ...] | None = None,
    chorus: bool = False,
    chorus_frequency: int | float = 1.5,
    chorus_depth: int | float = 0.7,
    chorus_wet: int | float | str | tuple[Any, ...] | None = None,
    ping_pong: bool = False,
    ping_pong_time: str | int | float = "8n",
    ping_pong_feedback: int | float | str | tuple[Any, ...] | None = None,
    ping_pong_wet: int | float | str | tuple[Any, ...] | None = None,
    limiter: bool = True,
    limiter_threshold: int | float = _DEFAULT_LIMITER_THRESHOLD,
    volume: int | float = -8,
    pan: int | float | str | tuple[Any, ...] = 0,
    # Value→pitch mapping
    scale: str | list[int] = "pentatonic",
    root: str = "C3",
    octaves: int = 3,
    descending: bool = False,
    # Table behaviour
    mode: str = "last",
    rate_limit_ms: int = 60,
    # Table mode — value→pitch sonify. `pitch` alone maps one column to pitch;
    # add `loudness`/`voice` for a multi-dimensional "duet". Each names a COLUMN;
    # "Col" or (col, lo, hi) where (lo, hi) clamps the INPUT data domain.
    pitch: str | tuple[Any, ...] | None = None,
    loudness: str | tuple[Any, ...] | None = None,
    voice: str | None = None,
    voices: dict[str, dict[str, Any]] | None = None,
    voice_default: dict[str, Any] | None = None,
    # Table mode (chord trigger)
    chord_column: str | None = None,
    chords: list[list[str]] | None = None,
    chord_gap: str = "4n",
    chord_duration: str = "2n",
    chord_notes_column: str | None = None,
    # Table mode (sequence trigger — the play_sequence analogue)
    sequence_column: str | None = None,
    sequence_notes: Sequence[Any] | None = None,
    sequence_gap: str = "16n",
    sequence_notes_column: str | None = None,
) -> TonesElement:
    """
    Declarative element factory that auto-sonifies a ticking ``Table``.

    Returns a bare ``TonesElement`` — place it in the render tree (mounting it
    boots the invisible audio engine; it takes no layout space). As each tick
    delivers rows, the client turns the mapped columns into sound. There is no
    control handle: a sonifier emits no manual triggers. For manual ``play(...)``
    use ``use_tones`` instead.

    Like any ``@ui.component`` element it runs render hooks internally, so call
    it during render (inline in the tree is fine), not conditionally.

    Args:
        table: The Deephaven ``Table`` to auto-sonify on tick.
        instrument: Tone.js synth type.
            ``"sine"`` | ``"triangle"`` | ``"square"`` | ``"sawtooth"`` |
            ``"fm"`` | ``"am"`` | ``"membrane"`` | ``"pluck"`` |
            ``"monosynth"`` | ``"duosynth"`` | ``"metal"``.
        polyphony: Maximum simultaneous voices. Default ``8``.
        envelope_attack, envelope_decay, envelope_sustain, envelope_release:
            ADSR envelope, in seconds (sustain is a 0–1 level). Each ``None``
            keeps the pleasant sine default. Static only.
        detune: Global detune in cents. Default ``0``. **Data-driven:** pass a
            column name (or ``(col, lo, hi)``) to modulate per row.
        portamento: Glide time between notes in seconds. Default ``0``.
        filter: ``True`` (default) enables the lowpass filter node; ``False``
            disables it.
        filter_type: Filter type — ``"lowpass"`` | ``"highpass"`` | … Static.
        filter_frequency: Cutoff in Hz. Default ``2200``. **Data-driven** (Hz,
            log-scaled output by default).
        filter_q: Resonance. Default ``1``. **Data-driven.**
        filter_rolloff: ``-12`` | ``-24`` | ``-48`` | ``-96``. Static only.
        reverb: ``True`` (default) enables the reverb node; ``False`` disables.
        reverb_decay: Tail length in seconds. Default ``3``. Static only
            (changing it rebuilds the impulse response).
        reverb_wet: Wet/dry mix 0–1. Default ``0.3``. **Data-driven.**
        reverb_predelay: Pre-delay in seconds. Default ``0.01``. Static only.
        delay: ``True`` enables the feedback-delay node. Default ``False`` — but
            setting any ``delay_*`` param also enables it.
        delay_time: Delay time (Tone.js note string like ``"8n"`` or seconds).
            Static only.
        delay_feedback: Feedback 0–~0.9. Default ``0.2``. **Data-driven.**
        delay_wet: Wet/dry mix 0–1. Default ``0.1``. **Data-driven.**
        distortion: ``True`` enables a waveshaper distortion node. Default
            ``False``.
        distortion_amount: Distortion amount 0–1. Default ``0.4``. Static only.
        distortion_wet: Wet/dry mix 0–1. Default ``1.0``. **Data-driven.**
        chorus: ``True`` enables a stereo chorus node. Default ``False``.
        chorus_frequency: LFO rate in Hz. Default ``1.5``. Static only.
        chorus_depth: Modulation depth 0–1. Default ``0.7``. Static only.
        chorus_wet: Wet/dry mix 0–1. Default ``0.5``. **Data-driven.**
        ping_pong: ``True`` enables a stereo ping-pong delay node. Default
            ``False``.
        ping_pong_time: Delay time (Tone.js note string or seconds). Default
            ``"8n"``. Static only.
        ping_pong_feedback: Feedback 0–~0.9. Default ``0.2``. **Data-driven.**
        ping_pong_wet: Wet/dry mix 0–1. Default ``0.5``. **Data-driven.**
        limiter: ``True`` (default) inserts a brick-wall limiter on the master
            bus so loud bursts don't clip; ``False`` removes it.
        limiter_threshold: Limiter ceiling in dBFS. Default ``-1``.
        volume: Master volume in dB. Default ``-8``.
        pan: Stereo position, ``-1`` (left) … ``1`` (right). Default ``0``
            (center). **Data-driven.**
        scale: Scale name (``"pentatonic"``, ``"major"``, ``"minor"``,
            ``"chromatic"``) or an explicit list of semitone intervals
            e.g. ``[0, 2, 4, 7, 9]``.
        root: Bottom of the pitch range as a note name, e.g. ``"C3"``.
        octaves: Number of octaves spanned by the value→pitch mapping.
        descending: When ``True``, higher values map to lower pitches.

    Numeric-param overload: a plain number is a static value; a column-name
    string is data-driven (the client auto-tracks the column's running min/max
    and maps it into the param's default output range); ``(col, lo, hi)`` is
    data-driven with an explicit output range. Only ``detune``,
    ``filter_frequency``, ``filter_q``, ``reverb_wet``, ``delay_feedback``,
    ``delay_wet`` and ``pan`` are data-driven (live Tone signals); the rest are
    static. To data-drive timbre categorically (e.g. swap instrument), use
    ``voice`` / ``voices``.
        mode: ``"last"`` (most-recent row) or ``"all"`` (every new row).
            BLINK tables (e.g. from ``table_publisher``) are auto-detected via
            the JSAPI — ``"last"`` plays one sound per tick, ``"all"`` plays
            every row delivered in that tick (a tick may add several rows).
        rate_limit_ms: Client-side rate-limit in ms for table-tick
            sonification. Default ``60``.
        pitch: The numeric column mapped to pitch (scale-quantised via
            ``scale``/``root``/``octaves``). ``pitch`` alone is a one-dimensional
            sonify (the common case — "turn this column into sound"); add
            ``loudness``/``voice`` for a multi-dimensional "duet". Accepts
            ``"Col"`` (input range auto) or ``("Col", lo, hi)`` to clamp the
            input data domain.
        loudness: Numeric column mapped to loudness AND note length (bigger
            value = louder + longer). Accepts ``"Col"`` or ``("Col", lo, hi)``
            (``lo, hi`` clamp the input domain; ``None`` = auto running min/max).
        voice: Categorical column (e.g. a ``BUY``/``SELL`` side) whose value
            selects the instrument/voice for each row.
        voices: Map of ``voice`` cell value → a flat override dict, e.g.
            ``{"BUY": {"instrument": "pluck"}, "SELL": {"instrument":
            "sawtooth", "envelope_attack": 0.01}}``. Override keys mirror the
            flat param names — ``instrument``, ``polyphony``,
            ``envelope_attack``/``_decay``/``_sustain``/``_release``,
            ``detune``, ``portamento``, ``volume``, ``pan``, and
            ``filter``/``reverb``/``delay``/``distortion``/``chorus``/``ping_pong``
            on-off toggles. Any stage you don't
            name keeps the base value; unmatched cell values fall back to the
            base config (or to ``voice_default`` if given). ``voice``/``voices``
            (and ``loudness``) only take effect alongside ``pitch``.
        voice_default: A flat override dict (same shape as a ``voices`` entry)
            applied to rows whose ``voice`` cell matches no key in ``voices``.
            Defaults to the base config when unset. Only used alongside
            ``voice``.
        chord_column: Trigger column for chord mode. On each new row where this
            column is truthy (a boolean / non-zero / non-empty cell), the client
            plays ``chords`` as a progression. Other rows stay silent. Use
            ``mode="all"`` so every flagged row fires.
        chords: The progression to play — a list of chords, each a list of note
            names (e.g. ``[["C4","E4","G4"], ["G3","B3","D4"]]``). Defaults to a
            pleasant I-V-vi-IV in C.
        chord_gap: Onset spacing between chords (Tone.js time). Default ``"4n"``.
        chord_duration: Per-chord length (Tone.js time). Default ``"2n"``.
        chord_notes_column: Optional column whose per-row cell supplies the
            chord(s) to play — a ``String[]`` (one chord), a ``String`` like
            ``"C4,E4,G4 | G3,B3,D4"`` (chords split on ``|``/``;``), or a
            ``String[][]``. Overrides ``chords`` per row. If given without
            ``chord_column`` it also acts as the trigger (fires when non-empty).
        sequence_column: Trigger column for a melodic SEQUENCE (the table
            analogue of ``play_sequence``). On each new truthy row the client
            plays ``sequence_notes`` as a timed melody/arpeggio. Use
            ``mode="all"``. Can be combined with ``chord_column``.
        sequence_notes: The melody to play — same heterogeneous note forms as
            ``play_sequence`` (``"C5"``, ``("C5", "8n")``, ``("C5", "8n", 0.8)``).
            Defaults to a pleasant ascending C arpeggio.
        sequence_gap: Onset spacing between notes (Tone.js time). Default
            ``"16n"``.
        sequence_notes_column: Optional column whose per-row cell supplies the
            melody — a ``String[]`` or a ``String`` like ``"C5 E5 G5 C6"``.
            Overrides ``sequence_notes`` per row. If given without
            ``sequence_column`` it also acts as the trigger.

    Returns:
        A ``TonesElement`` to place in the render tree.

    Example::

        @ui.component
        def market_sounds(prices):
            return ui.flex(
                table_tones(prices, pitch="Price", scale="pentatonic"),
                ui.table(prices),
                direction="row",
            )
    """
    # --- resolve flat overloaded params → (static baseline, per-row channel) --
    # A number stays static (in config); a column name / (col, lo, hi) becomes a
    # data-driven channel the client modulates live around the static baseline.
    detune_v, detune_ch = _resolve_param(detune, 0)
    filter_frequency_v, filter_frequency_ch = _resolve_param(filter_frequency, 2200)
    filter_q_v, filter_q_ch = _resolve_param(filter_q, 1)
    reverb_wet_v, reverb_wet_ch = _resolve_param(reverb_wet, 0.3)
    delay_feedback_v, delay_feedback_ch = _resolve_param(
        delay_feedback, _DEFAULT_DELAY["feedback"]
    )
    delay_wet_v, delay_wet_ch = _resolve_param(delay_wet, _DEFAULT_DELAY["wet"])
    pan_v, pan_ch = _resolve_param(pan, 0)
    distortion_wet_v, distortion_wet_ch = _resolve_param(
        distortion_wet, _DEFAULT_DISTORTION["wet"]
    )
    chorus_wet_v, chorus_wet_ch = _resolve_param(chorus_wet, _DEFAULT_CHORUS["wet"])
    ping_pong_wet_v, ping_pong_wet_ch = _resolve_param(
        ping_pong_wet, _DEFAULT_PINGPONG["wet"]
    )
    ping_pong_feedback_v, ping_pong_feedback_ch = _resolve_param(
        ping_pong_feedback, _DEFAULT_PINGPONG["feedback"]
    )

    param_mappings = _build_param_mappings(
        {
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
    )

    delay_on = _delay_enabled(delay, delay_time, delay_feedback, delay_wet)

    # pitch / loudness name a COLUMN; a 3-tuple clamps the INPUT data domain.
    # `pitch` drives the value→pitch mapping for both a one-dimensional sonify
    # (pitch alone) and the multi-dimensional duet (pitch + loudness + voice).
    pitch_col, pitch_range = _resolve_pitch(pitch)
    loudness_col, loudness_range = _resolve_pitch(loudness)
    value_range = pitch_range
    # ----------------------------------------------------------------------

    # --- validate column-name props up front ------------------------------
    # Catch typo'd column names with a helpful Python error before they slip
    # through to the client (where they'd fail silently).
    _referenced: list[tuple[str, str | None]] = [
        ("pitch", pitch_col),
        ("loudness", loudness_col),
        ("voice", voice),
        ("chord_column", chord_column),
        ("chord_notes_column", chord_notes_column),
        ("sequence_column", sequence_column),
        ("sequence_notes_column", sequence_notes_column),
    ]
    for _kw, _ch in (
        ("detune", detune_ch),
        ("filter_frequency", filter_frequency_ch),
        ("filter_q", filter_q_ch),
        ("reverb_wet", reverb_wet_ch),
        ("delay_feedback", delay_feedback_ch),
        ("delay_wet", delay_wet_ch),
        ("pan", pan_ch),
        ("distortion_wet", distortion_wet_ch),
        ("chorus_wet", chorus_wet_ch),
        ("ping_pong_wet", ping_pong_wet_ch),
        ("ping_pong_feedback", ping_pong_feedback_ch),
    ):
        if _ch is not None:
            _referenced.append((_kw, _ch.get("column")))
    _validate_columns(table, _referenced)
    _validate_config_enums(
        "table_tones", instrument, filter_type, filter_rolloff, voices
    )
    # ----------------------------------------------------------------------

    mappings = _build_mappings(
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

    chord_trigger = _build_chord_trigger(
        chord_column=chord_column,
        chords=chords,
        chord_gap=chord_gap,
        chord_duration=chord_duration,
        chord_notes_column=chord_notes_column,
    )

    sequence_trigger = _build_sequence_trigger(
        sequence_column=sequence_column,
        sequence_notes=sequence_notes,
        sequence_gap=sequence_gap,
        sequence_notes_column=sequence_notes_column,
    )

    # --- server-side AUTO range (default) ---------------------------------
    # Any range left unset => attach live min/max columns via agg + keyless
    # natural_join, so the client scales to the true global span. Explicit
    # ranges opt OUT (no columns attached for that channel).
    range_cols: list[str] = []
    if mappings is not None:
        if pitch_col is not None and value_range is None:
            range_cols.append(pitch_col)
        if loudness_col is not None and loudness_range is None:
            range_cols.append(loudness_col)

    # Memoise so the derived join isn't rebuilt every render (only when the
    # table or the set of auto-ranged columns changes). use_memo opens a
    # LivenessScope around this call, so the derived agg_by/natural_join tables
    # are owned by the RenderContext and released when these deps change — no
    # manual close needed.
    augmented_table, range_names = use_memo(
        lambda: _augment_with_ranges(table, range_cols),
        [table, tuple(range_cols)],
    )

    # range_cols is only populated in multi-mode (mappings present), so any
    # auto-range columns attach to the mappings' pitch / velocity / duration.
    if range_names and mappings is not None:
        if pitch_col in range_names:
            mn, mx = range_names[pitch_col]
            mappings["pitch"]["minColumn"] = mn
            mappings["pitch"]["maxColumn"] = mx
        if loudness_col is not None and loudness_col in range_names:
            mn, mx = range_names[loudness_col]
            for _ch in ("velocity", "duration"):
                if _ch in mappings:
                    mappings[_ch]["minColumn"] = mn
                    mappings[_ch]["maxColumn"] = mx

    config = _build_config(
        instrument=instrument,
        polyphony=polyphony,
        envelope_attack=envelope_attack,
        envelope_decay=envelope_decay,
        envelope_sustain=envelope_sustain,
        envelope_release=envelope_release,
        detune=detune_v,
        portamento=portamento,
        filter=filter,
        filter_type=filter_type,
        filter_frequency=filter_frequency_v,
        filter_q=filter_q_v,
        filter_rolloff=filter_rolloff,
        reverb=reverb,
        reverb_decay=reverb_decay,
        reverb_wet=reverb_wet_v,
        reverb_predelay=reverb_predelay,
        delay=delay_on,
        delay_time=delay_time,
        delay_feedback=delay_feedback_v,
        delay_wet=delay_wet_v,
        distortion=distortion,
        distortion_amount=distortion_amount,
        distortion_wet=distortion_wet_v,
        chorus=chorus,
        chorus_frequency=chorus_frequency,
        chorus_depth=chorus_depth,
        chorus_wet=chorus_wet_v,
        ping_pong=ping_pong,
        ping_pong_time=ping_pong_time,
        ping_pong_feedback=ping_pong_feedback_v,
        ping_pong_wet=ping_pong_wet_v,
        limiter=limiter,
        limiter_threshold=limiter_threshold,
        volume=volume,
        pan=pan_v,
        scale=scale,
        root=root,
        octaves=octaves,
        value_range=value_range,
        descending=descending,
    )

    return TonesElement(
        _ELEMENT_NAME,
        config=config,
        events=[],
        table=augmented_table,
        mode=mode,
        rate_limit_ms=rate_limit_ms,
        mappings=mappings,
        param_mappings=param_mappings,
        chord_trigger=chord_trigger,
        sequence_trigger=sequence_trigger,
    )

"""
Unit tests for the ``table_tones(...)`` factory's prop construction across each
table sonification mode (single-column, multi-dimensional duet, chord/sequence
triggers), plus the ``use_tones(...)`` trigger hook's (element, control) return.

Like ``test_tones_events``, these install lightweight stubs for the
``deephaven.ui`` hierarchy so the component module imports without a live
Deephaven server. The stubbed ``use_state`` returns ``(initial, MagicMock)`` and
``use_memo`` evaluates eagerly. ``_augment_with_ranges`` degrades to
``(table, {})`` because the engine ``deephaven.agg`` import fails under the
stubs — exactly its no-engine fallback.
"""

from __future__ import annotations

import importlib
import sys
import types
import unittest.mock as mock


def _install_stubs() -> None:
    """Minimal stub modules for the deephaven.ui hierarchy (idempotent)."""
    dh = sys.modules.setdefault("deephaven", types.ModuleType("deephaven"))

    ui = types.ModuleType("deephaven.ui")
    dh.ui = ui  # type: ignore[attr-defined]
    sys.modules.setdefault("deephaven.ui", ui)

    elements_mod = types.ModuleType("deephaven.ui.elements")

    class _BaseElement:
        def __init__(self, name: str, /, **props):  # type: ignore[override]
            self._name = name
            self._props = props

    elements_mod.BaseElement = _BaseElement  # type: ignore[attr-defined]
    sys.modules.setdefault("deephaven.ui.elements", elements_mod)

    hooks_mod = types.ModuleType("deephaven.ui.hooks")

    class _Ref:
        def __init__(self, current):
            self.current = current

    def _use_state(initial=None):
        return initial, mock.MagicMock()

    def _use_ref(initial=None):
        return _Ref(initial)

    def _use_memo(func, _deps):
        return func()

    hooks_mod.use_state = _use_state  # type: ignore[attr-defined]
    hooks_mod.use_ref = _use_ref  # type: ignore[attr-defined]
    hooks_mod.use_memo = _use_memo  # type: ignore[attr-defined]
    sys.modules.setdefault("deephaven.ui.hooks", hooks_mod)


_install_stubs()

_comp = importlib.import_module(
    "deephaven_plugin_tones.deephaven_plugin_tones_component"
)
table_tones = _comp.table_tones
use_tones = _comp.use_tones


# ---------------------------------------------------------------------------
# table_tones() factory — prop construction for each mode
# ---------------------------------------------------------------------------


class TestFactoryProps:
    def test_pitch_only_props(self):
        # A one-dimensional sonify: `pitch` alone builds a pitch-only mapping.
        # table_tones returns the element directly (no control handle).
        tbl = object()
        element = table_tones(tbl, pitch="V", mode="all")
        p = element._props
        assert p["mappings"]["pitch"]["column"] == "V"
        assert p["mode"] == "all"
        # No engine under stubs → augment falls back to the original table.
        assert p["table"] is tbl

    def test_multi_mode_props(self):
        element = table_tones(
            object(),
            pitch="Price",
            loudness="Size",
            voice="Side",
            voices={"BUY": {"instrument": "pluck"}},
        )
        m = element._props["mappings"]
        assert m["pitch"]["column"] == "Price"
        # loudness drives BOTH velocity and duration
        assert m["velocity"]["column"] == "Size"
        assert m["duration"]["column"] == "Size"
        assert m["voice"]["column"] == "Side"
        # Flat voice override passes through (no envelope_* → no envelope added).
        assert m["voice"]["voices"]["BUY"] == {"instrument": "pluck"}

    def test_voice_override_flat_envelope_filled_from_base(self):
        # A flat envelope_* override fills the rest of the ADSR from the base
        # envelope, since the client shallow-merges voice overrides.
        element = table_tones(
            object(),
            pitch="Price",
            voice="Side",
            voices={"SELL": {"instrument": "sawtooth", "envelope_attack": 0.01}},
            envelope_decay=0.5,
        )
        sell = element._props["mappings"]["voice"]["voices"]["SELL"]
        assert sell["instrument"] == "sawtooth"
        assert sell["envelope"] == {
            "attack": 0.01,  # overridden
            "decay": 0.5,  # from base (envelope_decay)
            "sustain": 0.6,  # base default
            "release": 1.2,  # base default
        }

    def test_chord_and_sequence_trigger_props(self):
        element = table_tones(
            object(),
            chord_column="Buy",
            sequence_column="Signal",
        )
        p = element._props
        assert p["chord_trigger"]["column"] == "Buy"
        assert p["sequence_trigger"]["column"] == "Signal"

    def test_pitch_tuple_sets_value_range(self):
        # pitch=("Col", lo, hi) clamps the input domain.
        element = table_tones(object(), pitch=("Degree", 0, 6))
        p = element._props
        assert p["mappings"]["pitch"]["column"] == "Degree"
        assert p["config"]["valueRange"] == [0.0, 6.0]

    def test_data_driven_param_builds_param_mappings(self):
        element = table_tones(
            object(),
            pitch="V",
            reverb_wet="Volatility",
            filter_frequency=("Vol", 200, 6000),
        )
        pm = element._props["param_mappings"]
        assert pm["reverb.wet"] == {"column": "Volatility"}
        assert pm["filter.frequency"] == {"column": "Vol", "min": 200.0, "max": 6000.0}
        # static baseline still lands in config
        assert element._props["config"]["reverb"]["wet"] == 0.3

    def test_new_effects_static_and_data_driven(self):
        # Tier 1 effects: static enable lands in config; a column overload builds
        # a param mapping while the static baseline stays in config.
        element = table_tones(
            object(),
            pitch="V",
            distortion=True,
            distortion_wet="Vol",
            chorus=True,
            chorus_wet=("C", 0, 1),
            ping_pong=True,
            ping_pong_feedback=0.4,
        )
        p = element._props
        assert p["config"]["distortion"]["amount"] == 0.4  # static baseline
        assert p["config"]["pingPongDelay"]["feedback"] == 0.4
        pm = p["param_mappings"]
        assert pm["distortion.wet"] == {"column": "Vol"}
        assert pm["chorus.wet"] == {"column": "C", "min": 0.0, "max": 1.0}

    def test_new_instruments_accepted(self):
        for inst in ("monosynth", "duosynth", "metal"):
            element = table_tones(object(), pitch="V", instrument=inst)
            assert element._props["config"]["instrument"] == inst

    def test_limiter_on_by_default(self):
        element = table_tones(object(), pitch="V")
        assert element._props["config"]["limiter"] == {"threshold": -1}


# ---------------------------------------------------------------------------
# use_tones() factory — trigger-only element + control handle
# ---------------------------------------------------------------------------


class TestUseTones:
    def test_returns_element_and_control(self):
        result = use_tones(instrument="sine")
        # NamedTuple: destructure or attribute-access.
        audio, audio_control = result
        assert result.audio is audio
        assert result.audio_control is audio_control
        assert audio._props["config"]["instrument"] == "sine"
        # Trigger-only: no table-mode props on the element.
        for key in ("table", "mappings", "param_mappings", "chord_trigger"):
            assert key not in audio._props
        for method in ("play", "play_chord", "play_value", "play_sequence", "stop"):
            assert callable(getattr(audio_control, method))

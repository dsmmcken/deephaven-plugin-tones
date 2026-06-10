"""
Unit tests for the pure event-construction and normalisation helpers in
``deephaven_plugin_tones_component``.

These tests import ONLY the module-level free functions — they do NOT import
``deephaven.ui`` (which requires a live Deephaven server), so they run safely
in a plain pytest environment.
"""

from __future__ import annotations

import importlib
import sys
import types
import unittest.mock as mock

import pytest

# ---------------------------------------------------------------------------
# Lightweight stubs so we can import the component module without a server.
# We patch sys.modules before the import so the component module never tries
# to contact a Deephaven server.
# ---------------------------------------------------------------------------


def _install_stubs() -> None:
    """Install minimal stub modules for deephaven.ui hierarchy."""

    # deephaven stub
    dh = types.ModuleType("deephaven")
    sys.modules.setdefault("deephaven", dh)

    # deephaven.ui stub
    ui = types.ModuleType("deephaven.ui")
    dh.ui = ui  # type: ignore[attr-defined]
    sys.modules.setdefault("deephaven.ui", ui)

    # deephaven.ui.elements
    elements_mod = types.ModuleType("deephaven.ui.elements")

    class _BaseElement:
        """Minimal BaseElement stub that stores props."""

        def __init__(self, name: str, /, **props):  # type: ignore[override]
            self._name = name
            self._props = props

    elements_mod.BaseElement = _BaseElement  # type: ignore[attr-defined]
    sys.modules.setdefault("deephaven.ui.elements", elements_mod)

    # deephaven.ui.hooks
    hooks_mod = types.ModuleType("deephaven.ui.hooks")

    class _Ref:
        def __init__(self, current):
            self.current = current

    # Stubs that simply return (initial, setter) without server round-trips.
    # Each call gets a fresh pair so that hook ordering is respected in the
    # component module (though these unit tests exercise the free helpers, not
    # use_tones()/table_tones() directly).
    def _use_state(initial=None):
        return initial, mock.MagicMock()

    def _use_ref(initial=None):
        return _Ref(initial)

    def _use_memo(func, _deps):
        # Eager evaluation is fine for tests (no memoisation needed).
        return func()

    hooks_mod.use_state = _use_state  # type: ignore[attr-defined]
    hooks_mod.use_ref = _use_ref  # type: ignore[attr-defined]
    hooks_mod.use_memo = _use_memo  # type: ignore[attr-defined]
    sys.modules.setdefault("deephaven.ui.hooks", hooks_mod)


_install_stubs()

# Now we can safely import the module-level helpers.
# We import the module directly so we can reach the private helpers without
# triggering any component/server code.
_comp = importlib.import_module(
    "deephaven_plugin_tones.deephaven_plugin_tones_component"
)

_bounded_append = _comp._bounded_append
_build_config = _comp._build_config
_build_overrides = _comp._build_overrides
_make_chord_event = _comp._make_chord_event
_make_play_event = _comp._make_play_event
_make_sequence_event = _comp._make_sequence_event
_make_set_volume_event = _comp._make_set_volume_event
_make_stop_event = _comp._make_stop_event
_make_value_event = _comp._make_value_event
_normalize_sequence_notes = _comp._normalize_sequence_notes
_envelope_from_flat = _comp._envelope_from_flat
_resolve_envelope = _comp._resolve_envelope
_voice_override_to_config = _comp._voice_override_to_config
_build_mappings = _comp._build_mappings
_build_chord_trigger = _comp._build_chord_trigger
_DEFAULT_CHORDS = _comp._DEFAULT_CHORDS
_build_sequence_trigger = _comp._build_sequence_trigger
_DEFAULT_SEQUENCE = _comp._DEFAULT_SEQUENCE
_resolve_param = _comp._resolve_param
_resolve_pitch = _comp._resolve_pitch
_build_param_mappings = _comp._build_param_mappings
_augment_with_ranges = _comp._augment_with_ranges
_validate_columns = _comp._validate_columns
_validate_config_enums = _comp._validate_config_enums
_RANGE_MIN_SUFFIX = _comp._RANGE_MIN_SUFFIX
_RANGE_MAX_SUFFIX = _comp._RANGE_MAX_SUFFIX
_MAX_EVENTS = _comp._MAX_EVENTS

# ---------------------------------------------------------------------------
# _bounded_append
# ---------------------------------------------------------------------------


class TestBoundedAppend:
    def test_appends_single_event(self):
        ev = {"id": 1, "op": "play"}
        result = _bounded_append((), ev)
        assert result == (ev,)

    def test_keeps_most_recent_64(self):
        base: tuple = ()
        for i in range(_MAX_EVENTS + 10):
            ev = {"id": i, "op": "play"}
            base = _bounded_append(base, ev)
        assert len(base) == _MAX_EVENTS
        # oldest events were dropped; last element has the highest id
        assert base[-1]["id"] == _MAX_EVENTS + 10 - 1
        assert base[0]["id"] == 10  # first 10 were dropped

    def test_preserves_order(self):
        events: tuple = ()
        for i in range(5):
            events = _bounded_append(events, {"id": i, "op": "stop"})
        ids = [e["id"] for e in events]
        assert ids == [0, 1, 2, 3, 4]

    def test_accepts_list_input(self):
        result = _bounded_append([], {"id": 1, "op": "stop"})
        assert len(result) == 1


# ---------------------------------------------------------------------------
# _make_play_event
# ---------------------------------------------------------------------------


class TestMakePlayEvent:
    def test_string_note(self):
        ev = _make_play_event(1, "C4", "8n", 1.0)
        assert ev == {
            "id": 1,
            "op": "play",
            "note": "C4",
            "duration": "8n",
            "velocity": 1.0,
        }

    def test_hz_note(self):
        ev = _make_play_event(2, 440.0, "16n", 0.8)
        assert ev["note"] == 440.0
        assert ev["op"] == "play"

    def test_midi_dict_note(self):
        ev = _make_play_event(3, {"midi": 60}, "4n", 0.7)
        assert ev["note"] == {"midi": 60}

    def test_id_is_monotonic(self):
        ev1 = _make_play_event(7, "C4", "8n", 1.0)
        ev2 = _make_play_event(8, "D4", "8n", 1.0)
        assert ev2["id"] == ev1["id"] + 1


# ---------------------------------------------------------------------------
# _make_chord_event
# ---------------------------------------------------------------------------


class TestMakeChordEvent:
    def test_shape(self):
        ev = _make_chord_event(5, ["C4", "E4", "G4"], "2n", 1.0)
        assert ev == {
            "id": 5,
            "op": "chord",
            "notes": ["C4", "E4", "G4"],
            "duration": "2n",
            "velocity": 1.0,
        }

    def test_notes_is_list_copy(self):
        original = ["C4", "E4"]
        ev = _make_chord_event(1, original, "2n", 0.9)
        original.append("G4")
        assert len(ev["notes"]) == 2  # not affected by mutation


# ---------------------------------------------------------------------------
# _make_value_event
# ---------------------------------------------------------------------------


class TestMakeValueEvent:
    def test_basic(self):
        ev = _make_value_event(10, 42.0, {})
        assert ev == {"id": 10, "op": "value", "value": 42.0, "overrides": {}}

    def test_float_coercion(self):
        ev = _make_value_event(1, 7, {})  # int input
        assert isinstance(ev["value"], float)

    def test_overrides_passed_through(self):
        ov = {"scale": "major", "root": "D3"}
        ev = _make_value_event(1, 3.14, ov)
        assert ev["overrides"] == ov


# ---------------------------------------------------------------------------
# _make_sequence_event
# ---------------------------------------------------------------------------


class TestMakeSequenceEvent:
    def test_basic(self):
        notes = [{"note": "C5", "duration": "16n", "velocity": 0.9}]
        ev = _make_sequence_event(9, notes, "16n", None)
        assert ev == {
            "id": 9,
            "op": "sequence",
            "notes": notes,
            "gap": "16n",
            "envelope": None,
        }

    def test_envelope_override(self):
        env = {"attack": 0.005, "decay": 0.12, "sustain": 0.0, "release": 0.25}
        ev = _make_sequence_event(1, [], "8n", env)
        assert ev["envelope"] == env


# ---------------------------------------------------------------------------
# _make_stop_event
# ---------------------------------------------------------------------------


class TestMakeStopEvent:
    def test_shape(self):
        ev = _make_stop_event(11)
        assert ev == {"id": 11, "op": "stop"}


# ---------------------------------------------------------------------------
# _make_set_volume_event
# ---------------------------------------------------------------------------


class TestMakeSetVolumeEvent:
    def test_shape(self):
        ev = _make_set_volume_event(12, -6)
        assert ev == {"id": 12, "op": "setVolume", "db": -6}

    def test_float_db(self):
        ev = _make_set_volume_event(1, -12.5)
        assert ev["db"] == -12.5


# ---------------------------------------------------------------------------
# _normalize_sequence_notes
# ---------------------------------------------------------------------------


class TestNormalizeSequenceNotes:
    def test_string_names(self):
        result = _normalize_sequence_notes(["C5", "E5", "G5"], "8n", 0.9)
        assert result == [
            {"note": "C5", "duration": "8n", "velocity": 0.9},
            {"note": "E5", "duration": "8n", "velocity": 0.9},
            {"note": "G5", "duration": "8n", "velocity": 0.9},
        ]

    def test_tuples_with_duration(self):
        result = _normalize_sequence_notes([("C5", "16n"), ("E5", "4n")], "8n", 0.9)
        assert result[0] == {"note": "C5", "duration": "16n", "velocity": 0.9}
        assert result[1] == {"note": "E5", "duration": "4n", "velocity": 0.9}

    def test_tuples_full(self):
        result = _normalize_sequence_notes([("C5", "16n", 0.7)], "8n", 0.9)
        assert result[0]["velocity"] == 0.7

    def test_midi_dict_is_a_note_value(self):
        # A bare dict is the {"midi": N} note value, not a {note, duration} spec.
        result = _normalize_sequence_notes([{"midi": 60}], "4n", 0.5)
        assert result[0] == {"note": {"midi": 60}, "duration": "4n", "velocity": 0.5}

    def test_midi_dict_in_tuple(self):
        # The note value can be a {"midi": N} dict inside a (note, duration) tuple.
        result = _normalize_sequence_notes([({"midi": 60}, "16n")], "8n", 0.9)
        assert result[0] == {"note": {"midi": 60}, "duration": "16n", "velocity": 0.9}

    def test_mixed_forms(self):
        notes = ["C5", ("E5", "16n"), ("G5", "8n", 1.0)]
        result = _normalize_sequence_notes(notes, "8n", 0.9)
        assert len(result) == 3
        assert result[0]["note"] == "C5"
        assert result[1]["duration"] == "16n"
        assert result[2]["velocity"] == 1.0

    def test_hz_pitch(self):
        result = _normalize_sequence_notes([440.0], "8n", 0.9)
        assert result[0]["note"] == 440.0


# ---------------------------------------------------------------------------
# Earcon RECIPES — earcons are no longer methods; they're short play_sequence
# calls. This pins the documented recipe (the snippet shown in README/SKILL).
# ---------------------------------------------------------------------------

_PLUCK_ENVELOPE = {"attack": 0.005, "decay": 0.12, "sustain": 0.0, "release": 0.25}


class TestEarconRecipes:
    def test_confirm_recipe_builds_sequence(self):
        # audio_control.play_sequence(["C5","E5","G5","C6"], gap="16n",
        #   duration="16n", attack=0.005, decay=0.12, sustain=0.0, release=0.25)
        normalized = _normalize_sequence_notes(["C5", "E5", "G5", "C6"], "16n", 0.9)
        ev = _make_sequence_event(1, normalized, "16n", _PLUCK_ENVELOPE)
        assert ev["op"] == "sequence"
        assert ev["notes"][0] == {"note": "C5", "duration": "16n", "velocity": 0.9}
        assert ev["notes"][-1] == {"note": "C6", "duration": "16n", "velocity": 0.9}
        assert ev["gap"] == "16n"
        assert ev["envelope"]["attack"] == 0.005

    def test_flat_kwargs_build_the_pluck_envelope(self):
        # The flat play_sequence kwargs assemble the same dict the recipe used.
        assert _envelope_from_flat(0.005, 0.12, 0.0, 0.25) == _PLUCK_ENVELOPE


# ---------------------------------------------------------------------------
# _envelope_from_flat — flat play_sequence ADSR kwargs → (partial) dict
# ---------------------------------------------------------------------------


class TestEnvelopeFromFlat:
    def test_all_none_is_none(self):
        assert _envelope_from_flat(None, None, None, None) is None

    def test_partial_keeps_only_named_stages(self):
        # The client deep-merges the sequence envelope, so a partial dict is fine.
        assert _envelope_from_flat(0.01, None, None, 0.4) == {
            "attack": 0.01,
            "release": 0.4,
        }


# ---------------------------------------------------------------------------
# _resolve_envelope — flat ADSR kwargs → COMPLETE dict (unset stages filled)
# ---------------------------------------------------------------------------


class TestResolveEnvelope:
    def test_all_none_yields_full_default(self):
        # Unlike _envelope_from_flat, this always returns every stage so callers
        # (config build, voice-override translation) get a complete envelope.
        assert _resolve_envelope(None, None, None, None) == {
            "attack": 0.02,
            "decay": 0.10,
            "sustain": 0.6,
            "release": 1.2,
        }

    def test_named_stage_overrides_default(self):
        assert _resolve_envelope(0.5, None, None, None) == {
            "attack": 0.5,
            "decay": 0.10,
            "sustain": 0.6,
            "release": 1.2,
        }


# ---------------------------------------------------------------------------
# _voice_override_to_config — flat voice override → Partial<ToneConfig>
# ---------------------------------------------------------------------------


class TestVoiceOverrideToConfig:
    _BASE_ENV = {"attack": 0.02, "decay": 0.10, "sustain": 0.6, "release": 1.2}

    def test_instrument_only_passthrough(self):
        out = _voice_override_to_config({"instrument": "pluck"}, self._BASE_ENV)
        assert out == {"instrument": "pluck"}

    def test_envelope_filled_from_base(self):
        # A flat envelope_* override emits a COMPLETE envelope (client shallow-
        # merges voice overrides), filling unspecified stages from the base.
        out = _voice_override_to_config(
            {"instrument": "sawtooth", "envelope_attack": 0.12}, self._BASE_ENV
        )
        assert out["instrument"] == "sawtooth"
        assert out["envelope"] == {
            "attack": 0.12,
            "decay": 0.10,
            "sustain": 0.6,
            "release": 1.2,
        }

    def test_false_toggle_disables_effect(self):
        out = _voice_override_to_config({"reverb": False}, self._BASE_ENV)
        assert out == {"reverb": None}

    def test_true_toggle_inherits_base(self):
        # True (or absent) inherits the base node — nothing emitted for it.
        out = _voice_override_to_config({"filter": True, "pan": -0.5}, self._BASE_ENV)
        assert "filter" not in out
        assert out["pan"] == -0.5


# ---------------------------------------------------------------------------
# _build_config — camelCase keys
# ---------------------------------------------------------------------------


# Flat kwargs that build_config now takes (all keyword-only). Helper supplies
# sensible defaults so each test overrides only what it cares about.
_BUILD_CONFIG_DEFAULTS = dict(
    instrument="sine",
    polyphony=8,
    envelope_attack=None,
    envelope_decay=None,
    envelope_sustain=None,
    envelope_release=None,
    detune=0,
    portamento=0,
    filter=True,
    filter_type="lowpass",
    filter_frequency=2200,
    filter_q=1,
    filter_rolloff=-24,
    reverb=True,
    reverb_decay=3,
    reverb_wet=0.3,
    reverb_predelay=0.01,
    delay=False,
    delay_time=None,
    delay_feedback=None,
    delay_wet=None,
    volume=-8,
    pan=0,
    scale="pentatonic",
    root="C3",
    octaves=3,
    value_range=None,
    descending=False,
)


def _cfg(**overrides):
    return _build_config(**{**_BUILD_CONFIG_DEFAULTS, **overrides})


class TestBuildConfig:
    def test_camel_case_keys_present(self):
        cfg = _cfg()
        # Top-level camelCase keys per contract
        assert "valueRange" in cfg
        assert "value_range" not in cfg

    def test_defaults(self):
        cfg = _cfg()
        assert cfg["instrument"] == "sine"
        assert cfg["polyphony"] == 8
        assert cfg["detune"] == 0
        assert cfg["portamento"] == 0
        assert cfg["volume"] == -8
        assert cfg["pan"] == 0
        assert cfg["scale"] == "pentatonic"
        assert cfg["root"] == "C3"
        assert cfg["octaves"] == 3
        assert cfg["valueRange"] is None
        assert cfg["descending"] is False

    def test_envelope_defaults_filled(self):
        cfg = _cfg()
        assert cfg["envelope"]["attack"] == 0.02
        assert cfg["envelope"]["sustain"] == 0.6

    def test_envelope_flat_overrides(self):
        cfg = _cfg(envelope_attack=0.5, envelope_release=2.0)
        assert cfg["envelope"]["attack"] == 0.5
        assert cfg["envelope"]["release"] == 2.0
        # unspecified keys keep the pleasant default
        assert cfg["envelope"]["sustain"] == 0.6

    def test_filter_flat_assembled(self):
        cfg = _cfg(
            filter_type="highpass", filter_frequency=800, filter_q=4, filter_rolloff=-12
        )
        assert cfg["filter"] == {
            "type": "highpass",
            "frequency": 800,
            "q": 4,
            "rolloff": -12,
        }

    def test_reverb_flat_assembled(self):
        cfg = _cfg(reverb_decay=2, reverb_wet=0.5, reverb_predelay=0.02)
        assert cfg["reverb"] == {"decay": 2, "wet": 0.5, "preDelay": 0.02}

    def test_delay_off_by_default(self):
        assert _cfg()["delay"] is None

    def test_delay_assembled_with_camel_key(self):
        cfg = _cfg(delay=True, delay_time="8n", delay_feedback=0.3, delay_wet=0.2)
        assert cfg["delay"] == {"delayTime": "8n", "feedback": 0.3, "wet": 0.2}
        assert "delay_time" not in cfg["delay"]

    def test_delay_defaults_filled_when_enabled(self):
        cfg = _cfg(delay=True)
        assert cfg["delay"]["delayTime"] == "8n"
        assert cfg["delay"]["feedback"] == 0.2
        assert cfg["delay"]["wet"] == 0.1

    def test_value_range_as_list(self):
        cfg = _cfg(value_range=(0.0, 100.0))
        assert cfg["valueRange"] == [0.0, 100.0]
        assert isinstance(cfg["valueRange"], list)

    def test_filter_disabled(self):
        cfg = _cfg(filter=False, reverb=False)
        assert cfg["filter"] is None
        assert cfg["reverb"] is None

    def test_explicit_scale_list(self):
        cfg = _cfg(scale=[0, 2, 4, 7, 9], root="D2", octaves=2, descending=True)
        assert cfg["scale"] == [0, 2, 4, 7, 9]
        assert cfg["descending"] is True


# ---------------------------------------------------------------------------
# _build_config — Tier 1 new effects (distortion / chorus / ping-pong / limiter)
# ---------------------------------------------------------------------------


class TestBuildConfigNewEffects:
    def test_distortion_off_by_default(self):
        assert _cfg()["distortion"] is None

    def test_distortion_assembled(self):
        cfg = _cfg(distortion=True, distortion_amount=0.6, distortion_wet=0.8)
        assert cfg["distortion"] == {"amount": 0.6, "wet": 0.8}

    def test_distortion_defaults_filled_when_enabled(self):
        cfg = _cfg(distortion=True)
        assert cfg["distortion"] == {"amount": 0.4, "wet": 1.0}

    def test_chorus_off_by_default(self):
        assert _cfg()["chorus"] is None

    def test_chorus_assembled(self):
        cfg = _cfg(chorus=True, chorus_frequency=2.0, chorus_depth=0.5, chorus_wet=0.4)
        assert cfg["chorus"] == {"frequency": 2.0, "depth": 0.5, "wet": 0.4}

    def test_chorus_defaults_filled_when_enabled(self):
        cfg = _cfg(chorus=True)
        assert cfg["chorus"] == {"frequency": 1.5, "depth": 0.7, "wet": 0.5}

    def test_ping_pong_off_by_default(self):
        assert _cfg()["pingPongDelay"] is None

    def test_ping_pong_assembled_with_camel_key(self):
        cfg = _cfg(
            ping_pong=True,
            ping_pong_time="8n",
            ping_pong_feedback=0.3,
            ping_pong_wet=0.5,
        )
        assert cfg["pingPongDelay"] == {"delayTime": "8n", "feedback": 0.3, "wet": 0.5}
        assert "ping_pong_time" not in cfg["pingPongDelay"]

    def test_ping_pong_defaults_filled_when_enabled(self):
        cfg = _cfg(ping_pong=True)
        assert cfg["pingPongDelay"] == {"delayTime": "8n", "feedback": 0.2, "wet": 0.5}

    def test_limiter_on_by_default(self):
        # Master clip-protection is on unless explicitly disabled.
        assert _cfg()["limiter"] == {"threshold": -1}

    def test_limiter_threshold_passthrough(self):
        assert _cfg(limiter_threshold=-3)["limiter"] == {"threshold": -3}

    def test_limiter_disabled(self):
        assert _cfg(limiter=False)["limiter"] is None


# ---------------------------------------------------------------------------
# _resolve_param / _resolve_pitch / _build_param_mappings — flat overloads
# ---------------------------------------------------------------------------


class TestResolveParam:
    def test_number_is_static(self):
        assert _resolve_param(0.5, 0.3) == (0.5, None)

    def test_none_uses_default(self):
        assert _resolve_param(None, 0.3) == (0.3, None)

    def test_string_is_column_with_default_baseline(self):
        static, channel = _resolve_param("Vol", 0.3)
        assert static == 0.3
        assert channel == {"column": "Vol"}

    def test_tuple_sets_output_range(self):
        static, channel = _resolve_param(("Vol", 200, 6000), 2200)
        assert static == 2200
        assert channel == {"column": "Vol", "min": 200.0, "max": 6000.0}

    def test_one_tuple_is_column_only(self):
        # No explicit range → defaults applied client-side.
        _, channel = _resolve_param(("Vol",), 1)
        assert channel == {"column": "Vol"}

    def test_two_tuple_raises(self):
        # A 2-tuple is a likely typo for (col, lo, hi) — fail loud, don't drop it.
        with pytest.raises(ValueError, match="1-tuple"):
            _resolve_param(("Vol", 200), 1)


class TestResolvePitch:
    def test_string_no_range(self):
        assert _resolve_pitch("Price") == ("Price", None)

    def test_tuple_is_input_clamp(self):
        assert _resolve_pitch(("Price", 0, 100)) == ("Price", [0.0, 100.0])

    def test_none(self):
        assert _resolve_pitch(None) == (None, None)

    def test_two_tuple_raises(self):
        with pytest.raises(ValueError, match="1-tuple"):
            _resolve_pitch(("Price", 100))


class TestBuildParamMappings:
    def test_none_when_all_static(self):
        assert _build_param_mappings({"pan": None, "detune": None}) is None

    def test_maps_kwarg_to_client_path(self):
        pm = _build_param_mappings(
            {
                "reverb_wet": {"column": "Vol"},
                "filter_frequency": {"column": "Vol", "min": 200.0, "max": 6000.0},
                "pan": None,
            }
        )
        assert pm == {
            "reverb.wet": {"column": "Vol"},
            "filter.frequency": {"column": "Vol", "min": 200.0, "max": 6000.0},
        }

    def test_new_effect_param_paths_registered(self):
        # Tier 1 data-driven wet/feedback channels for the new effects.
        assert _comp._PARAM_PATHS["distortion_wet"] == "distortion.wet"
        assert _comp._PARAM_PATHS["chorus_wet"] == "chorus.wet"
        assert _comp._PARAM_PATHS["ping_pong_wet"] == "pingPong.wet"
        assert _comp._PARAM_PATHS["ping_pong_feedback"] == "pingPong.feedback"


# ---------------------------------------------------------------------------
# _build_overrides
# ---------------------------------------------------------------------------


class TestBuildOverrides:
    def test_empty(self):
        assert _build_overrides() == {}

    def test_camel_case_passthrough(self):
        ov = _build_overrides(scale="major", root="D3", octaves=2)
        assert ov == {"scale": "major", "root": "D3", "octaves": 2}

    def test_value_range_alias(self):
        ov = _build_overrides(value_range=(0, 100))
        assert ov == {"valueRange": [0, 100]}

    def test_unknown_keys_dropped(self):
        ov = _build_overrides(scale="major", instrument="fm")  # instrument not valid
        assert "instrument" not in ov
        assert "scale" in ov

    def test_descending(self):
        ov = _build_overrides(descending=True)
        assert ov == {"descending": True}


# ---------------------------------------------------------------------------
# _build_mappings — multi-dimensional mapping prop
# ---------------------------------------------------------------------------


class TestBuildMappings:
    def test_no_pitch_returns_none(self):
        assert _build_mappings(None, "Vol", None, "Side", {}) is None

    def test_pitch_only(self):
        m = _build_mappings("Price", None, None, None, None)
        assert m == {"pitch": {"column": "Price"}}

    def test_loudness_drives_velocity_and_duration(self):
        m = _build_mappings("Price", "Volume", None, None, None)
        assert m["velocity"] == {"column": "Volume", "range": None}
        assert m["duration"] == {"column": "Volume", "range": None}

    def test_explicit_loudness_range_passes_through(self):
        m = _build_mappings("Price", "Volume", (40, 100), None, None)
        assert m["velocity"]["range"] == [40, 100]
        assert m["duration"]["range"] == [40, 100]

    def test_voice_carries_instrument_map(self):
        voices = {"BUY": {"instrument": "pluck"}, "SELL": {"instrument": "sawtooth"}}
        m = _build_mappings("Price", None, None, "Side", voices)
        assert m["voice"]["column"] == "Side"
        assert m["voice"]["voices"] == voices

    def test_voice_default_translated_into_mapping(self):
        m = _build_mappings(
            "Price", None, None, "Side", {}, voice_default={"instrument": "pluck"}
        )
        assert m["voice"]["default"] == {"instrument": "pluck"}

    def test_no_voice_default_key_when_unset(self):
        m = _build_mappings("Price", None, None, "Side", {})
        assert "default" not in m["voice"]


# ---------------------------------------------------------------------------
# _validate_config_enums — actionable errors for out-of-range enum values
# ---------------------------------------------------------------------------


class TestValidateConfigEnums:
    def test_valid_config_passes(self):
        # No exception for in-range values (incl. a valid per-voice instrument).
        _validate_config_enums(
            "table_tones", "sawtooth", "lowpass", -24, {"BUY": {"instrument": "pluck"}}
        )

    def test_bad_instrument_raises(self):
        with pytest.raises(ValueError, match="instrument 'sin'"):
            _validate_config_enums("use_tones", "sin", "lowpass", -24)

    def test_bad_filter_type_raises(self):
        with pytest.raises(ValueError, match="filter_type 'lopass'"):
            _validate_config_enums("use_tones", "sine", "lopass", -24)

    def test_bad_filter_rolloff_raises(self):
        with pytest.raises(ValueError, match="filter_rolloff -30"):
            _validate_config_enums("use_tones", "sine", "lowpass", -30)

    def test_bad_voice_instrument_raises(self):
        with pytest.raises(ValueError, match=r"voices\['BUY'\]"):
            _validate_config_enums(
                "table_tones", "sine", "lowpass", -24, {"BUY": {"instrument": "nope"}}
            )

    def test_new_instruments_pass(self):
        # Tier 1 instruments validate (all extend Monophonic → PolySynth-wrappable).
        for inst in ("monosynth", "duosynth", "metal"):
            _validate_config_enums("use_tones", inst, "lowpass", -24)


# ---------------------------------------------------------------------------
# _voice_override_to_config — Tier 1 new-effect per-voice toggles
# ---------------------------------------------------------------------------


class TestVoiceOverrideNewEffectToggles:
    _BASE_ENV = {"attack": 0.02, "decay": 0.10, "sustain": 0.6, "release": 1.2}

    def test_distortion_false_disables(self):
        out = _voice_override_to_config({"distortion": False}, self._BASE_ENV)
        assert out == {"distortion": None}

    def test_chorus_false_disables(self):
        out = _voice_override_to_config({"chorus": False}, self._BASE_ENV)
        assert out == {"chorus": None}

    def test_ping_pong_false_maps_to_camel_config_key(self):
        # Flat kwarg is `ping_pong`; client config key is `pingPongDelay`.
        out = _voice_override_to_config({"ping_pong": False}, self._BASE_ENV)
        assert out == {"pingPongDelay": None}


# ---------------------------------------------------------------------------
# _build_chord_trigger — chord progression on a trigger column
# ---------------------------------------------------------------------------


class TestBuildChordTrigger:
    def test_no_column_returns_none(self):
        assert _build_chord_trigger(None, None, "4n", "2n", None) is None

    def test_defaults_to_four_chords(self):
        ct = _build_chord_trigger("IsChord", None, "4n", "2n", None)
        assert ct["column"] == "IsChord"
        assert ct["chords"] == _DEFAULT_CHORDS
        assert ct["gap"] == "4n"
        assert ct["duration"] == "2n"
        assert "notesColumn" not in ct

    def test_custom_chords_copied(self):
        chords = [["C4", "E4"], ["G4", "B4"]]
        ct = _build_chord_trigger("Trig", chords, "8n", "4n", None)
        assert ct["chords"] == chords
        # Each chord is copied (not the same list object).
        assert ct["chords"][0] is not chords[0]

    def test_notes_column_sets_notes_column_and_gate(self):
        ct = _build_chord_trigger("IsChord", None, "4n", "2n", "Chord")
        assert ct["column"] == "IsChord"  # explicit trigger remains the gate
        assert ct["notesColumn"] == "Chord"

    def test_notes_column_doubles_as_gate(self):
        ct = _build_chord_trigger(None, None, "4n", "2n", "Chord")
        assert ct["column"] == "Chord"  # notes column is the gate when no trigger
        assert ct["notesColumn"] == "Chord"


# ---------------------------------------------------------------------------
# _build_sequence_trigger — melodic sequence on a trigger column
# ---------------------------------------------------------------------------


class TestBuildSequenceTrigger:
    def test_no_column_returns_none(self):
        assert _build_sequence_trigger(None, None, "16n", None) is None

    def test_default_sequence_is_normalised(self):
        st = _build_sequence_trigger("Sparkle", None, "16n", None)
        assert st["column"] == "Sparkle"
        assert st["gap"] == "16n"
        # Normalised to {note, duration, velocity} dicts.
        assert [n["note"] for n in st["notes"]] == _DEFAULT_SEQUENCE
        assert all({"note", "duration", "velocity"} <= set(n) for n in st["notes"])
        assert "notesColumn" not in st

    def test_custom_notes_accept_heterogeneous_forms(self):
        st = _build_sequence_trigger(
            "S", ["C5", ("E5", "8n"), ("G5", "8n", 1.0)], "8n", None
        )
        notes = st["notes"]
        assert notes[0]["note"] == "C5"
        assert notes[1] == {"note": "E5", "duration": "8n", "velocity": 0.9}
        assert notes[2] == {"note": "G5", "duration": "8n", "velocity": 1.0}

    def test_notes_column_doubles_as_gate(self):
        st = _build_sequence_trigger(None, None, "16n", "Motif")
        assert st["column"] == "Motif"
        assert st["notesColumn"] == "Motif"


# ---------------------------------------------------------------------------
# _augment_with_ranges — server-side auto range (agg + natural_join)
# ---------------------------------------------------------------------------


class _FakeTable:
    """Records agg_by / natural_join calls so we can assert the query shape
    without a live engine."""

    def __init__(self):
        self.agg_specs = None
        self.join_args = None

    def agg_by(self, aggs, by=None):
        self.agg_specs = aggs
        return ("RANGE_TABLE", aggs)

    def natural_join(self, table, on, joins):
        self.join_args = {"table": table, "on": on, "joins": joins}
        return ("AUGMENTED", joins)


class TestAugmentWithRanges:
    def test_none_table_is_noop(self):
        assert _augment_with_ranges(None, ["X"]) == (None, {})

    def test_no_cols_is_noop(self):
        t = _FakeTable()
        assert _augment_with_ranges(t, []) == (t, {})

    def test_builds_join_and_names(self, monkeypatch):
        # Stub `from deephaven import agg` with a recording shim.
        agg_mod = types.ModuleType("deephaven.agg")
        agg_mod.min_ = lambda spec: ("min", spec)  # type: ignore[attr-defined]
        agg_mod.max_ = lambda spec: ("max", spec)  # type: ignore[attr-defined]
        dh_mod = sys.modules["deephaven"]
        monkeypatch.setattr(dh_mod, "agg", agg_mod, raising=False)
        monkeypatch.setitem(sys.modules, "deephaven.agg", agg_mod)

        t = _FakeTable()
        augmented, names = _augment_with_ranges(t, ["Price", "Volume", "Price"])

        # De-duped to two columns, each with a min + max name.
        assert names == {
            "Price": (f"Price{_RANGE_MIN_SUFFIX}", f"Price{_RANGE_MAX_SUFFIX}"),
            "Volume": (f"Volume{_RANGE_MIN_SUFFIX}", f"Volume{_RANGE_MAX_SUFFIX}"),
        }
        # Keyless natural_join broadcasts the single agg row to every row.
        assert t.join_args["on"] == []
        assert t.join_args["joins"] == [
            f"Price{_RANGE_MIN_SUFFIX}",
            f"Price{_RANGE_MAX_SUFFIX}",
            f"Volume{_RANGE_MIN_SUFFIX}",
            f"Volume{_RANGE_MAX_SUFFIX}",
        ]
        assert augmented[0] == "AUGMENTED"

    def test_engine_failure_falls_back_to_original(self, monkeypatch):
        # agg import present, but agg_by raises → must return (table, {}).
        agg_mod = types.ModuleType("deephaven.agg")
        agg_mod.min_ = lambda spec: spec  # type: ignore[attr-defined]
        agg_mod.max_ = lambda spec: spec  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "deephaven.agg", agg_mod)

        class _Boom(_FakeTable):
            def agg_by(self, aggs, by=None):
                raise RuntimeError("no engine")

        t = _Boom()
        assert _augment_with_ranges(t, ["Price"]) == (t, {})


# ---------------------------------------------------------------------------
# _validate_columns — helpful errors for typo'd column-name props
# ---------------------------------------------------------------------------


class _SchemaTable:
    """A table stub exposing a ``column_names`` list, like a real Table."""

    def __init__(self, names):
        self.column_names = list(names)


class TestValidateColumns:
    def test_none_table_is_noop(self):
        # No table → nothing to validate (trigger-only handle).
        _validate_columns(None, [("column", "Whatever")])

    def test_no_referenced_columns_is_noop(self):
        t = _SchemaTable(["Price"])
        _validate_columns(t, [])
        _validate_columns(t, [("pitch", None), ("loudness", None)])

    def test_valid_columns_pass(self):
        t = _SchemaTable(["Timestamp", "Price", "Vol"])
        _validate_columns(t, [("column", "Price"), ("reverb_wet", "Vol")])

    def test_unknown_column_raises_with_role_and_available(self):
        t = _SchemaTable(["Timestamp", "Price", "Vol"])
        try:
            _validate_columns(t, [("column", "Nope")])
        except ValueError as e:
            msg = str(e)
            assert "column='Nope'" in msg  # names the offending kwarg
            assert "Price" in msg and "Vol" in msg  # lists available columns
        else:
            raise AssertionError("expected ValueError for unknown column")

    def test_reports_all_missing_columns(self):
        t = _SchemaTable(["Price"])
        try:
            _validate_columns(t, [("pitch", "BadA"), ("loudness", "BadB")])
        except ValueError as e:
            assert "pitch='BadA'" in str(e)
            assert "loudness='BadB'" in str(e)
        else:
            raise AssertionError("expected ValueError listing both columns")

    def test_uninspectable_table_skips_silently(self):
        # A table type whose column_names access raises → skip (no false
        # positive), mirroring _augment_with_ranges graceful degradation.
        class _Opaque:
            @property
            def column_names(self):
                raise RuntimeError("not available on this table type")

        _validate_columns(_Opaque(), [("column", "Anything")])

    def test_object_without_column_names_skips(self):
        # A bare object (no column_names attr at all) → skip silently.
        _validate_columns(object(), [("column", "Anything")])


# ---------------------------------------------------------------------------
# table_tones() factory — column validation wired into the element factory
# ---------------------------------------------------------------------------


class TestFactoryColumnValidation:
    """End-to-end through the table_tones() factory (with stubbed deephaven.ui),
    using a schema-bearing table stub so validation actually runs."""

    def test_bad_pitch_column_raises(self):
        t = _SchemaTable(["Price"])
        try:
            _comp.table_tones(t, pitch="Nope")
        except ValueError as e:
            assert "pitch='Nope'" in str(e)
        else:
            raise AssertionError("expected ValueError")

    def test_bad_data_driven_param_column_raises(self):
        t = _SchemaTable(["Price"])
        try:
            _comp.table_tones(t, pitch="Price", reverb_wet="Nope")
        except ValueError as e:
            assert "reverb_wet='Nope'" in str(e)
        else:
            raise AssertionError("expected ValueError")

    def test_bad_trigger_column_raises(self):
        t = _SchemaTable(["Price"])
        try:
            _comp.table_tones(t, chord_column="Nope", mode="all")
        except ValueError as e:
            assert "chord_column='Nope'" in str(e)
        else:
            raise AssertionError("expected ValueError")

    def test_valid_columns_do_not_raise(self):
        t = _SchemaTable(["Price", "Vol", "Side"])
        _comp.table_tones(t, pitch="Price", loudness="Vol", voice="Side")

    def test_use_tones_has_no_columns_to_validate(self):
        # The trigger hook takes no table/column kwargs — nothing to validate.
        audio, audio_control = _comp.use_tones(instrument="sine")
        assert audio._props["config"]["instrument"] == "sine"


# ---------------------------------------------------------------------------
# Monotonic id test — simulated id_ref usage
# ---------------------------------------------------------------------------


class TestMonotonicId:
    def test_ids_are_strictly_increasing(self):
        """Simulate the id_ref counter used by Tones._next_id."""

        class FakeRef:
            current = 0

        ref = FakeRef()

        def next_id():
            ref.current += 1
            return ref.current

        ids = [next_id() for _ in range(10)]
        assert ids == list(range(1, 11))
        assert len(set(ids)) == len(ids), "IDs must be unique"

    def test_ids_start_at_1(self):
        class FakeRef:
            current = 0

        ref = FakeRef()
        ref.current += 1
        assert ref.current == 1

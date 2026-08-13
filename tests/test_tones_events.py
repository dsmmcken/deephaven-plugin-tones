"""
Unit tests for the pure config/note helpers in ``deephaven_plugin_tones._config``
and the event payloads built by ``tones``.

Nothing here needs a Deephaven server: ``_config`` imports no engine modules,
and ``tones`` only reaches for ``deephaven.ui`` when a trigger fires, which the
fake event context below intercepts.
"""

from __future__ import annotations

import sys
import types

import pytest

from deephaven_plugin_tones import Tones, TonesError, tones
from deephaven_plugin_tones._config import (
    DEFAULT_CHORDS,
    DEFAULT_SEQUENCE,
    PARAM_PATHS,
    RANGE_MAX_SUFFIX,
    RANGE_MIN_SUFFIX,
    SOUND_DEFAULTS,
    TONES_EVENT,
    _build_config,
    _resolve_envelope,
    augment_with_ranges,
    build_chord_trigger,
    build_config,
    build_mappings,
    build_param_mappings,
    build_sequence_trigger,
    envelope_from_flat,
    normalize_sequence_notes,
    resolve_param,
    resolve_pitch,
    validate_columns,
    validate_config_enums,
    voice_override_to_config,
)

# ---------------------------------------------------------------------------
# Fake event context: `tones` calls `use_send_event()` from deephaven.ui at
# trigger time, so a stub module is enough to capture the payloads.
# ---------------------------------------------------------------------------


@pytest.fixture
def sent(monkeypatch):
    """Capture every (name, payload) that a `tones` call sends."""
    captured: list[tuple[str, dict]] = []

    ui = types.ModuleType("deephaven.ui")
    ui.use_send_event = lambda: lambda name, params: captured.append((name, params))
    monkeypatch.setitem(sys.modules, "deephaven.ui", ui)
    return captured


@pytest.fixture
def no_context(monkeypatch):
    """A deephaven.ui whose use_send_event raises, as it does off-render."""
    ui = types.ModuleType("deephaven.ui")

    def _boom():
        raise RuntimeError("No context set")

    ui.use_send_event = _boom
    monkeypatch.setitem(sys.modules, "deephaven.ui", ui)


def _config_args(**overrides):
    """Full kwargs for the low-level _build_config, with test-friendly defaults."""
    args = {
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
        "volume": -8,
        "pan": 0,
        "scale": "pentatonic",
        "root": "C3",
        "octaves": 3,
        "value_range": None,
        "descending": False,
    }
    args.update(overrides)
    return args


# ---------------------------------------------------------------------------
# tones triggers — the event payloads sent to the client
# ---------------------------------------------------------------------------


class TestTonesTriggers:
    def test_play_payload(self, sent):
        tones.play("C4")
        name, payload = sent[-1]
        assert name == TONES_EVENT
        assert payload["op"] == "play"
        assert payload["note"] == "C4"
        assert payload["duration"] == "8n"
        assert payload["velocity"] == 1.0
        assert payload["config"]["instrument"] == "sine"

    def test_play_accepts_hz_and_midi(self, sent):
        tones.play(440)
        assert sent[-1][1]["note"] == 440
        tones.play({"midi": 60})
        assert sent[-1][1]["note"] == {"midi": 60}

    def test_chord_payload(self, sent):
        tones.play_chord(["C4", "E4", "G4"], duration="4n", velocity=0.5)
        payload = sent[-1][1]
        assert payload["op"] == "chord"
        assert payload["notes"] == ["C4", "E4", "G4"]
        assert payload["duration"] == "4n"
        assert payload["velocity"] == 0.5

    def test_sequence_payload_normalises_notes(self, sent):
        tones.play_sequence(["C5", ("E5", "8n"), ("G5", "8n", 0.4)])
        payload = sent[-1][1]
        assert payload["op"] == "sequence"
        assert payload["gap"] == 0
        assert payload["notes"][0] == {"note": "C5", "duration": "8n", "velocity": 0.9}
        assert payload["notes"][2]["velocity"] == 0.4

    def test_sequence_envelope_override(self, sent):
        tones.play_sequence(["C5"], attack=0.005, sustain=0.0)
        assert sent[-1][1]["envelope"] == {"attack": 0.005, "sustain": 0.0}

    def test_sequence_without_envelope_kwargs_sends_none(self, sent):
        tones.play_sequence(["C5"])
        assert sent[-1][1]["envelope"] is None

    def test_chords_payload(self, sent):
        tones.play_chords([["C4", "E4"], ["G3", "B3"]], gap="8n")
        payload = sent[-1][1]
        assert payload["op"] == "chordSequence"
        assert payload["chords"] == [["C4", "E4"], ["G3", "B3"]]
        assert payload["gap"] == "8n"

    def test_value_payload(self, sent):
        tones.play_value(42)
        payload = sent[-1][1]
        assert payload["op"] == "value"
        assert payload["value"] == 42.0
        assert payload["config"]["valueRange"] is None

    def test_value_mapping_overrides_land_in_config(self, sent):
        tones.play_value(1, scale="major", root="D3", octaves=2, value_range=(0, 100))
        config = sent[-1][1]["config"]
        assert config["scale"] == "major"
        assert config["root"] == "D3"
        assert config["octaves"] == 2
        assert config["valueRange"] == [0.0, 100.0]

    def test_per_call_sound_override(self, sent):
        tones.play("C4", instrument="pluck", reverb_wet=0.9)
        config = sent[-1][1]["config"]
        assert config["instrument"] == "pluck"
        assert config["reverb"]["wet"] == 0.9
        # The module-level default is untouched by a per-call override.
        tones.play("C4")
        assert sent[-1][1]["config"]["instrument"] == "sine"

    def test_unknown_override_raises(self):
        with pytest.raises(ValueError, match="unknown sound option"):
            tones.play("C4", instrumnet="pluck")

    def test_bad_instrument_raises(self):
        with pytest.raises(ValueError, match="unknown instrument"):
            tones.play("C4", instrument="sin")

    def test_off_render_thread_raises_tones_error(self, no_context):
        with pytest.raises(TonesError, match="use_render_queue"):
            tones.play("C4")


class TestConfigure:
    def test_returns_a_new_instance_with_merged_options(self, sent):
        bell = tones.configure(instrument="metal", volume=-20)
        bell.play("C6")
        config = sent[-1][1]["config"]
        assert config["instrument"] == "metal"
        assert config["volume"] == -20
        # Unnamed options are inherited from the source instance.
        assert config["root"] == "C3"

    def test_source_is_unchanged(self, sent):
        tones.configure(instrument="metal")
        tones.play("C4")
        assert sent[-1][1]["config"]["instrument"] == "sine"

    def test_chains(self, sent):
        tones.configure(instrument="fm").configure(volume=-3).play("C4")
        config = sent[-1][1]["config"]
        assert config["instrument"] == "fm"
        assert config["volume"] == -3

    def test_unknown_option_raises(self):
        with pytest.raises(ValueError, match="unknown sound option"):
            tones.configure(instrumnet="fm")

    def test_options_property_is_a_copy(self):
        opts = tones.options
        opts["instrument"] = "metal"
        assert tones.options["instrument"] == "sine"

    def test_constructor_covers_every_sound_option(self):
        # SOUND_DEFAULTS is the contract for overrides, so it must match the
        # constructor exactly or an override would be rejected or dropped.
        assert set(Tones().options) == set(SOUND_DEFAULTS)


# ---------------------------------------------------------------------------
# normalize_sequence_notes
# ---------------------------------------------------------------------------


class TestNormalizeSequenceNotes:
    def test_bare_notes_get_defaults(self):
        out = normalize_sequence_notes(["C5", "E5"], "16n", 0.8)
        assert out == [
            {"note": "C5", "duration": "16n", "velocity": 0.8},
            {"note": "E5", "duration": "16n", "velocity": 0.8},
        ]

    def test_tuple_sets_duration_then_velocity(self):
        out = normalize_sequence_notes([("C5", "4n"), ("E5", "4n", 0.5)], "16n", 0.9)
        assert out[0] == {"note": "C5", "duration": "4n", "velocity": 0.9}
        assert out[1] == {"note": "E5", "duration": "4n", "velocity": 0.5}

    def test_list_item_is_a_chord(self):
        out = normalize_sequence_notes([["C4", "E4", "G4"]], "8n", 1.0)
        assert out[0]["note"] == ["C4", "E4", "G4"]

    def test_chord_with_duration(self):
        out = normalize_sequence_notes([(["C4", "E4"], 0.5)], "8n", 1.0)
        assert out[0] == {"note": ["C4", "E4"], "duration": 0.5, "velocity": 1.0}

    def test_none_is_a_rest_that_keeps_its_duration(self):
        out = normalize_sequence_notes([(None, 0.25)], "8n", 1.0)
        assert out[0] == {"note": None, "duration": 0.25, "velocity": 1.0}

    def test_hz_and_midi_notes(self):
        out = normalize_sequence_notes([440, {"midi": 60}], "8n", 1.0)
        assert out[0]["note"] == 440
        assert out[1]["note"] == {"midi": 60}


class TestEnvelopeFromFlat:
    def test_all_none_is_none(self):
        assert envelope_from_flat(None, None, None, None) is None

    def test_only_named_stages_present(self):
        assert envelope_from_flat(0.01, None, 0.0, None) == {
            "attack": 0.01,
            "sustain": 0.0,
        }


class TestResolveEnvelope:
    def test_fills_every_stage_from_the_default(self):
        assert _resolve_envelope(None, None, None, None) == {
            "attack": 0.02,
            "decay": 0.10,
            "sustain": 0.6,
            "release": 1.2,
        }

    def test_named_stage_wins(self):
        assert _resolve_envelope(0.5, None, None, None)["attack"] == 0.5


# ---------------------------------------------------------------------------
# Config assembly
# ---------------------------------------------------------------------------


class TestBuildConfig:
    def test_camel_case_keys(self):
        config = _build_config(**_config_args())
        assert "pingPongDelay" in config
        assert "valueRange" in config
        assert config["reverb"]["preDelay"] == 0.01

    def test_envelope_defaults_and_overrides(self):
        assert _build_config(**_config_args())["envelope"]["attack"] == 0.02
        assert (
            _build_config(**_config_args(envelope_attack=0.5))["envelope"]["attack"]
            == 0.5
        )

    def test_filter_assembled_and_disablable(self):
        assert _build_config(**_config_args())["filter"] == {
            "type": "lowpass",
            "frequency": 2200,
            "q": 1,
            "rolloff": -24,
        }
        assert _build_config(**_config_args(filter=False))["filter"] is None

    def test_delay_off_by_default_and_defaults_filled(self):
        assert _build_config(**_config_args())["delay"] is None
        delay = _build_config(**_config_args(delay=True))["delay"]
        assert delay == {"delayTime": "8n", "feedback": 0.2, "wet": 0.1}

    def test_new_effects_off_by_default(self):
        config = _build_config(**_config_args())
        assert config["distortion"] is None
        assert config["chorus"] is None
        assert config["pingPongDelay"] is None

    def test_new_effect_defaults_filled_when_enabled(self):
        config = _build_config(
            **_config_args(distortion=True, chorus=True, ping_pong=True)
        )
        assert config["distortion"] == {"amount": 0.4, "wet": 1.0}
        assert config["chorus"] == {"frequency": 1.5, "depth": 0.7, "wet": 0.5}
        assert config["pingPongDelay"] == {
            "delayTime": "8n",
            "feedback": 0.2,
            "wet": 0.5,
        }

    def test_limiter_on_by_default(self):
        assert _build_config(**_config_args())["limiter"] == {"threshold": -1}
        assert _build_config(**_config_args(limiter=False))["limiter"] is None

    def test_sequences_serialise_to_lists(self):
        config = _build_config(
            **_config_args(scale=(0, 2, 4, 7, 9), value_range=(0, 100))
        )
        assert config["scale"] == [0, 2, 4, 7, 9]
        assert config["valueRange"] == [0.0, 100.0]


class TestBuildConfigFromOptions:
    def test_defaults_round_trip(self):
        assert build_config({})["instrument"] == "sine"

    def test_any_delay_param_enables_the_node(self):
        assert build_config({"delay_wet": 0.4})["delay"]["wet"] == 0.4
        assert build_config({"delay_time": "4n"})["delay"]["delayTime"] == "4n"


# ---------------------------------------------------------------------------
# Overloaded numeric params / column inputs
# ---------------------------------------------------------------------------


class TestResolveParam:
    def test_number_is_static(self):
        assert resolve_param(0.5, 0.3) == (0.5, None)

    def test_none_uses_default(self):
        assert resolve_param(None, 0.3) == (0.3, None)

    def test_column_keeps_the_default_as_baseline(self):
        assert resolve_param("Vol", 0.3) == (0.3, {"column": "Vol"})

    def test_tuple_sets_the_output_range(self):
        assert resolve_param(("Vol", 0.1, 0.9), 0.3) == (
            0.3,
            {"column": "Vol", "min": 0.1, "max": 0.9},
        )

    def test_two_tuple_raises(self):
        with pytest.raises(ValueError, match="3-tuple"):
            resolve_param(("Vol", 0.1), 0.3)


class TestResolvePitch:
    def test_string_auto_ranges(self):
        assert resolve_pitch("Price") == ("Price", None)

    def test_tuple_clamps_the_input_domain(self):
        assert resolve_pitch(("Price", 0, 100)) == ("Price", [0.0, 100.0])

    def test_none(self):
        assert resolve_pitch(None) == (None, None)

    def test_two_tuple_raises(self):
        with pytest.raises(ValueError, match="3-tuple"):
            resolve_pitch(("Price", 0))


class TestBuildParamMappings:
    def test_none_when_all_static(self):
        assert build_param_mappings({"reverb_wet": None}) is None

    def test_maps_kwarg_to_client_path(self):
        mappings = build_param_mappings(
            {"reverb_wet": {"column": "Vol"}, "filter_frequency": {"column": "Hz"}}
        )
        assert mappings == {
            "reverb.wet": {"column": "Vol"},
            "filter.frequency": {"column": "Hz"},
        }

    def test_new_effect_paths_registered(self):
        assert PARAM_PATHS["distortion_wet"] == "distortion.wet"
        assert PARAM_PATHS["chorus_wet"] == "chorus.wet"
        assert PARAM_PATHS["ping_pong_wet"] == "pingPong.wet"
        assert PARAM_PATHS["ping_pong_feedback"] == "pingPong.feedback"


# ---------------------------------------------------------------------------
# Mappings + voices
# ---------------------------------------------------------------------------


class TestBuildMappings:
    def test_no_pitch_returns_none(self):
        assert build_mappings(None, "Vol", None, "Side", {}) is None

    def test_pitch_only(self):
        assert build_mappings("Price", None, None, None, None) == {
            "pitch": {"column": "Price"}
        }

    def test_loudness_drives_velocity_and_duration(self):
        mappings = build_mappings("Price", "Volume", None, None, None)
        assert mappings["velocity"] == {"column": "Volume", "range": None}
        assert mappings["duration"] == {"column": "Volume", "range": None}

    def test_explicit_loudness_range_passes_through(self):
        mappings = build_mappings("Price", "Volume", (40, 100), None, None)
        assert mappings["velocity"]["range"] == [40, 100]

    def test_voice_carries_instrument_map(self):
        voices = {"BUY": {"instrument": "pluck"}, "SELL": {"instrument": "sawtooth"}}
        mappings = build_mappings("Price", None, None, "Side", voices)
        assert mappings["voice"]["column"] == "Side"
        assert mappings["voice"]["voices"] == voices

    def test_voice_default_only_present_when_given(self):
        assert "default" not in build_mappings("P", None, None, "Side", {})["voice"]
        mappings = build_mappings(
            "P", None, None, "Side", {}, voice_default={"instrument": "pluck"}
        )
        assert mappings["voice"]["default"] == {"instrument": "pluck"}


class TestVoiceOverrideToConfig:
    _BASE_ENV = {"attack": 0.02, "decay": 0.10, "sustain": 0.6, "release": 1.2}

    def test_instrument_passthrough(self):
        assert voice_override_to_config({"instrument": "pluck"}, self._BASE_ENV) == {
            "instrument": "pluck"
        }

    def test_envelope_filled_from_base(self):
        # The client shallow-merges, so an override must carry every stage.
        out = voice_override_to_config({"envelope_attack": 0.5}, self._BASE_ENV)
        assert out["envelope"] == {
            "attack": 0.5,
            "decay": 0.10,
            "sustain": 0.6,
            "release": 1.2,
        }

    def test_false_toggles_disable_effects(self):
        assert voice_override_to_config({"reverb": False}, self._BASE_ENV) == {
            "reverb": None
        }
        assert voice_override_to_config({"ping_pong": False}, self._BASE_ENV) == {
            "pingPongDelay": None
        }

    def test_true_toggle_inherits_base(self):
        assert voice_override_to_config({"reverb": True}, self._BASE_ENV) == {}


# ---------------------------------------------------------------------------
# Trigger specs
# ---------------------------------------------------------------------------


class TestBuildChordTrigger:
    def test_no_column_returns_none(self):
        assert build_chord_trigger(None, None, "4n", "2n", None) is None

    def test_defaults_to_four_chords(self):
        trigger = build_chord_trigger("IsChord", None, "4n", "2n", None)
        assert trigger["column"] == "IsChord"
        assert trigger["chords"] == DEFAULT_CHORDS
        assert "notesColumn" not in trigger

    def test_custom_chords_are_copied(self):
        chords = [["C4", "E4"], ["G4", "B4"]]
        trigger = build_chord_trigger("Trig", chords, "8n", "4n", None)
        assert trigger["chords"] == chords
        assert trigger["chords"][0] is not chords[0]

    def test_notes_column_doubles_as_gate(self):
        trigger = build_chord_trigger(None, None, "4n", "2n", "Chord")
        assert trigger["column"] == "Chord"
        assert trigger["notesColumn"] == "Chord"

    def test_explicit_gate_wins_over_notes_column(self):
        trigger = build_chord_trigger("IsChord", None, "4n", "2n", "Chord")
        assert trigger["column"] == "IsChord"
        assert trigger["notesColumn"] == "Chord"


class TestBuildSequenceTrigger:
    def test_no_column_returns_none(self):
        assert build_sequence_trigger(None, None, 0, None) is None

    def test_default_sequence_is_normalised(self):
        trigger = build_sequence_trigger("Sparkle", None, 0, None)
        assert [n["note"] for n in trigger["notes"]] == DEFAULT_SEQUENCE
        assert all({"note", "duration", "velocity"} <= set(n) for n in trigger["notes"])

    def test_custom_notes_accept_heterogeneous_forms(self):
        trigger = build_sequence_trigger(
            "S", ["C5", ("E5", "8n"), ("G5", "8n", 1.0)], "8n", None
        )
        assert trigger["notes"][1] == {"note": "E5", "duration": "8n", "velocity": 0.9}
        assert trigger["notes"][2]["velocity"] == 1.0

    def test_notes_column_doubles_as_gate(self):
        trigger = build_sequence_trigger(None, None, 0, "Motif")
        assert trigger["column"] == "Motif"
        assert trigger["notesColumn"] == "Motif"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidateConfigEnums:
    def test_valid_config_passes(self):
        validate_config_enums(
            "fn", "sawtooth", "lowpass", -24, {"BUY": {"instrument": "pluck"}}
        )

    def test_bad_instrument_raises(self):
        with pytest.raises(ValueError, match="instrument 'sin'"):
            validate_config_enums("fn", "sin", "lowpass", -24)

    def test_bad_filter_type_raises(self):
        with pytest.raises(ValueError, match="filter_type 'lopass'"):
            validate_config_enums("fn", "sine", "lopass", -24)

    def test_bad_rolloff_raises(self):
        with pytest.raises(ValueError, match="filter_rolloff -30"):
            validate_config_enums("fn", "sine", "lowpass", -30)

    def test_bad_mode_raises(self):
        with pytest.raises(ValueError, match="mode 'every'"):
            validate_config_enums("fn", "sine", "lowpass", -24, mode="every")

    def test_bad_voice_instrument_raises(self):
        with pytest.raises(ValueError, match=r"voices\['BUY'\]"):
            validate_config_enums(
                "fn", "sine", "lowpass", -24, {"BUY": {"instrument": "nope"}}
            )

    def test_new_instruments_pass(self):
        for instrument in ("monosynth", "duosynth", "metal"):
            validate_config_enums("fn", instrument, "lowpass", -24)


class _SchemaTable:
    """A table stub exposing a ``column_names`` list, like a real Table."""

    def __init__(self, names):
        self.column_names = list(names)


class TestValidateColumns:
    def test_none_table_is_noop(self):
        validate_columns("fn", None, [("pitch", "Whatever")])

    def test_valid_columns_pass(self):
        validate_columns(
            "fn", _SchemaTable(["Price", "Vol"]), [("pitch", "Price"), ("x", "Vol")]
        )

    def test_unknown_column_names_role_and_available(self):
        with pytest.raises(ValueError) as excinfo:
            validate_columns("fn", _SchemaTable(["Price", "Vol"]), [("pitch", "Nope")])
        message = str(excinfo.value)
        assert "pitch='Nope'" in message
        assert "Price" in message and "Vol" in message

    def test_reports_every_missing_column(self):
        with pytest.raises(ValueError) as excinfo:
            validate_columns(
                "fn", _SchemaTable(["Price"]), [("pitch", "A"), ("loudness", "B")]
            )
        assert "pitch='A'" in str(excinfo.value)
        assert "loudness='B'" in str(excinfo.value)

    def test_uninspectable_table_skips_silently(self):
        class _Opaque:
            @property
            def column_names(self):
                raise RuntimeError("not available on this table type")

        validate_columns("fn", _Opaque(), [("pitch", "Anything")])
        validate_columns("fn", object(), [("pitch", "Anything")])


# ---------------------------------------------------------------------------
# augment_with_ranges — server-side auto range (agg + natural_join)
# ---------------------------------------------------------------------------


class _FakeTable:
    """Records agg_by / natural_join calls so the query shape can be asserted."""

    def __init__(self):
        self.agg_specs = None
        self.join_args = None

    def agg_by(self, aggs, by=None):
        self.agg_specs = aggs
        return ("RANGE_TABLE", aggs)

    def natural_join(self, table, on, joins):
        self.join_args = {"table": table, "on": on, "joins": joins}
        return ("AUGMENTED", joins)


@pytest.fixture
def fake_agg(monkeypatch):
    agg = types.ModuleType("deephaven.agg")
    agg.min_ = lambda spec: ("min", spec)
    agg.max_ = lambda spec: ("max", spec)
    deephaven = sys.modules.get("deephaven") or types.ModuleType("deephaven")
    monkeypatch.setitem(sys.modules, "deephaven", deephaven)
    monkeypatch.setattr(deephaven, "agg", agg, raising=False)
    monkeypatch.setitem(sys.modules, "deephaven.agg", agg)
    return agg


class TestAugmentWithRanges:
    def test_none_table_is_noop(self):
        assert augment_with_ranges(None, ["X"]) == (None, {})

    def test_no_cols_is_noop(self):
        table = _FakeTable()
        assert augment_with_ranges(table, []) == (table, {})

    def test_builds_keyless_join_and_names(self, fake_agg):
        table = _FakeTable()
        augmented, names = augment_with_ranges(table, ["Price", "Volume", "Price"])
        assert names == {
            "Price": (f"Price{RANGE_MIN_SUFFIX}", f"Price{RANGE_MAX_SUFFIX}"),
            "Volume": (f"Volume{RANGE_MIN_SUFFIX}", f"Volume{RANGE_MAX_SUFFIX}"),
        }
        assert table.join_args["on"] == []
        assert table.join_args["joins"] == [
            f"Price{RANGE_MIN_SUFFIX}",
            f"Price{RANGE_MAX_SUFFIX}",
            f"Volume{RANGE_MIN_SUFFIX}",
            f"Volume{RANGE_MAX_SUFFIX}",
        ]
        assert augmented[0] == "AUGMENTED"

    def test_engine_failure_falls_back_to_the_original_table(self, fake_agg):
        class _Boom(_FakeTable):
            def agg_by(self, aggs, by=None):
                raise RuntimeError("no engine")

        table = _Boom()
        assert augment_with_ranges(table, ["Price"]) == (table, {})

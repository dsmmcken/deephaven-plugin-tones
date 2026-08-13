"""
Unit tests for the table→sound translation in
``deephaven_plugin_tones.table_tones``: cell parsing, range resolution, the
per-row events, and the hook's wiring (listener registration, rate limiting,
render-queue hand-off).

The hook's deephaven.ui hooks are stubbed, so none of this needs a server.
"""

from __future__ import annotations

import sys
import types

import pytest

from deephaven_plugin_tones._config import TONES_EVENT, build_config
from deephaven_plugin_tones.table_tones import (
    _channel_range,
    _is_truthy_cell,
    _normalize01,
    _num,
    _plan_columns,
    _to_chord_list,
    _to_note_list,
    _tracked_range,
    events_for_row,
    rows_from_update,
    use_table_tones_listener,
)

_CONFIG = build_config({})


def _plan(**overrides):
    plan = {
        "config": _CONFIG,
        "mappings": None,
        "param_mappings": None,
        "chord_trigger": None,
        "sequence_trigger": None,
        "mode": "all",
        "rate_limit_ms": 0,
    }
    plan.update(overrides)
    return plan


# ---------------------------------------------------------------------------
# Cell helpers
# ---------------------------------------------------------------------------


class TestNum:
    @pytest.mark.parametrize(
        ("raw", "expected"), [(1, 1.0), (2.5, 2.5), ("3", 3.0), (True, 1.0)]
    )
    def test_coerces(self, raw, expected):
        assert _num(raw) == expected

    @pytest.mark.parametrize("raw", [None, "abc", float("nan"), object()])
    def test_unusable_values_are_none(self, raw):
        assert _num(raw) is None


class TestNormalize01:
    def test_position_in_span(self):
        assert _normalize01(5, 0, 10) == 0.5

    def test_clamped(self):
        assert _normalize01(-1, 0, 10) == 0.0
        assert _normalize01(11, 0, 10) == 1.0

    def test_flat_span_uses_the_fallback(self):
        assert _normalize01(5, 5, 5) == 0.0
        assert _normalize01(5, 5, 5, 0.5) == 0.5


class TestIsTruthyCell:
    @pytest.mark.parametrize("raw", [True, 1, -2, "yes", "C4 E4", ["C4"]])
    def test_truthy(self, raw):
        assert _is_truthy_cell(raw) is True

    @pytest.mark.parametrize("raw", [None, False, 0, "", "   ", "false", "FALSE", []])
    def test_falsy(self, raw):
        assert _is_truthy_cell(raw) is False


class TestToNoteList:
    def test_splits_on_whitespace_and_commas(self):
        assert _to_note_list("C5 E5, G5") == ["C5", "E5", "G5"]

    def test_array_used_as_is(self):
        assert _to_note_list(["C5", " E5 "]) == ["C5", "E5"]

    def test_empty_is_none(self):
        assert _to_note_list("") is None
        assert _to_note_list([]) is None
        assert _to_note_list(None) is None


class TestToChordList:
    def test_string_splits_chords_then_notes(self):
        assert _to_chord_list("C4,E4,G4 | G3 B3 D4") == [
            ["C4", "E4", "G4"],
            ["G3", "B3", "D4"],
        ]

    def test_semicolon_also_separates_chords(self):
        assert _to_chord_list("C4,E4;G3,B3") == [["C4", "E4"], ["G3", "B3"]]

    def test_flat_array_is_one_chord(self):
        assert _to_chord_list(["C4", "E4", "G4"]) == [["C4", "E4", "G4"]]

    def test_nested_array_is_a_progression(self):
        assert _to_chord_list([["C4", "E4"], ["G3", "B3"]]) == [
            ["C4", "E4"],
            ["G3", "B3"],
        ]

    def test_empty_is_none(self):
        assert _to_chord_list("") is None
        assert _to_chord_list([]) is None
        assert _to_chord_list(None) is None


# ---------------------------------------------------------------------------
# Range resolution
# ---------------------------------------------------------------------------


class TestChannelRange:
    def test_explicit_range_wins(self):
        ranges: dict[str, list[float]] = {}
        channel = {
            "column": "P",
            "range": [0, 100],
            "minColumn": "mn",
            "maxColumn": "mx",
        }
        assert _channel_range({"mn": 5, "mx": 6}, channel, ranges, "pitch", 50) == (
            0.0,
            100.0,
        )
        assert ranges == {}

    def test_server_columns_used_when_no_explicit_range(self):
        row = {"P": 5, "P_tones_min": 1, "P_tones_max": 9}
        channel = {
            "column": "P",
            "minColumn": "P_tones_min",
            "maxColumn": "P_tones_max",
        }
        assert _channel_range(row, channel, {}, "pitch", 5) == (1.0, 9.0)

    def test_falls_back_to_a_running_range(self):
        ranges: dict[str, list[float]] = {}
        channel = {"column": "P"}
        assert _channel_range({}, channel, ranges, "pitch", 5) == (5.0, 5.0)
        assert _channel_range({}, channel, ranges, "pitch", 9) == (5.0, 9.0)
        assert _channel_range({}, channel, ranges, "pitch", 1) == (1.0, 9.0)

    def test_null_range_cells_fall_back(self):
        row = {"P_tones_min": None, "P_tones_max": None}
        channel = {
            "column": "P",
            "minColumn": "P_tones_min",
            "maxColumn": "P_tones_max",
        }
        assert _channel_range(row, channel, {}, "pitch", 7) == (7.0, 7.0)


class TestTrackedRange:
    def test_widens_in_place_per_key(self):
        ranges: dict[str, list[float]] = {}
        _tracked_range(ranges, "a", 3)
        _tracked_range(ranges, "b", 10)
        assert _tracked_range(ranges, "a", 4) == (3, 4)
        assert ranges["b"] == [10, 10]


# ---------------------------------------------------------------------------
# Row → events
# ---------------------------------------------------------------------------


class TestToneEvents:
    def test_pitch_only_row(self):
        plan = _plan(mappings={"pitch": {"column": "Price"}})
        (event,) = events_for_row({"Price": 42}, plan, {})
        assert event["op"] == "tone"
        assert event["value"] == 42.0
        assert event["velocity"] == 1.0
        assert "duration" not in event
        assert event["config"] is _CONFIG

    def test_non_numeric_pitch_is_silent(self):
        plan = _plan(mappings={"pitch": {"column": "Price"}})
        assert events_for_row({"Price": None}, plan, {}) == []

    def test_auto_range_is_sent_as_a_value_range_override(self):
        plan = _plan(mappings={"pitch": {"column": "Price"}})
        ranges: dict[str, list[float]] = {}
        events_for_row({"Price": 10}, plan, ranges)
        (event,) = events_for_row({"Price": 20}, plan, ranges)
        assert event["overrides"]["valueRange"] == [10.0, 20.0]

    def test_explicit_pitch_range_stays_in_config(self):
        # A pitch range given up front already lives in config.valueRange, so
        # the per-row override must not shadow it.
        plan = _plan(
            config=build_config({"value_range": [0, 100]}),
            mappings={"pitch": {"column": "Price"}},
        )
        (event,) = events_for_row({"Price": 42}, plan, {})
        assert "valueRange" not in event["overrides"]
        assert plan["config"]["valueRange"] == [0.0, 100.0]

    def test_loudness_drives_velocity_and_duration(self):
        plan = _plan(
            mappings={
                "pitch": {"column": "Price"},
                "velocity": {"column": "Vol", "range": [0, 100]},
                "duration": {"column": "Vol", "range": [0, 100]},
            }
        )
        (quiet,) = events_for_row({"Price": 1, "Vol": 0}, plan, {})
        (loud,) = events_for_row({"Price": 1, "Vol": 100}, plan, {})
        assert quiet["velocity"] == pytest.approx(0.3)
        assert loud["velocity"] == pytest.approx(1.0)
        assert quiet["duration"] == pytest.approx(0.15)
        assert loud["duration"] == pytest.approx(0.9)

    def test_voice_selects_an_override(self):
        plan = _plan(
            mappings={
                "pitch": {"column": "Price"},
                "voice": {
                    "column": "Side",
                    "voices": {"BUY": {"instrument": "pluck"}},
                    "default": {"instrument": "sawtooth"},
                },
            }
        )
        (buy,) = events_for_row({"Price": 1, "Side": "BUY"}, plan, {})
        (other,) = events_for_row({"Price": 1, "Side": "SELL"}, plan, {})
        assert buy["overrides"]["instrument"] == "pluck"
        assert other["overrides"]["instrument"] == "sawtooth"

    def test_voice_cell_is_trimmed(self):
        plan = _plan(
            mappings={
                "pitch": {"column": "Price"},
                "voice": {"column": "Side", "voices": {"BUY": {"instrument": "pluck"}}},
            }
        )
        (event,) = events_for_row({"Price": 1, "Side": " BUY "}, plan, {})
        assert event["overrides"]["instrument"] == "pluck"

    def test_data_driven_params_send_a_normalised_position(self):
        plan = _plan(
            mappings={"pitch": {"column": "Price"}},
            param_mappings={"reverb.wet": {"column": "Wet", "min": 0.1, "max": 0.9}},
        )
        ranges: dict[str, list[float]] = {}
        events_for_row({"Price": 1, "Wet": 0}, plan, ranges)
        (event,) = events_for_row({"Price": 1, "Wet": 10}, plan, ranges)
        assert event["params"] == {"reverb.wet": {"t": 1.0, "min": 0.1, "max": 0.9}}

    def test_params_absent_when_no_channels(self):
        plan = _plan(mappings={"pitch": {"column": "Price"}})
        (event,) = events_for_row({"Price": 1}, plan, {})
        assert "params" not in event


class TestTriggerEvents:
    def test_chord_trigger_fires_on_a_truthy_cell(self):
        plan = _plan(
            chord_trigger={
                "column": "IsChord",
                "chords": [["C4", "E4"]],
                "gap": "4n",
                "duration": "2n",
            }
        )
        assert events_for_row({"IsChord": False}, plan, {}) == []
        (event,) = events_for_row({"IsChord": True}, plan, {})
        assert event["op"] == "chordSequence"
        assert event["chords"] == [["C4", "E4"]]
        assert event["gap"] == "4n"

    def test_chord_notes_column_overrides_the_static_chords(self):
        plan = _plan(
            chord_trigger={
                "column": "Chord",
                "notesColumn": "Chord",
                "chords": [["C4"]],
                "gap": "4n",
                "duration": "2n",
            }
        )
        (event,) = events_for_row({"Chord": "F3,A3 | G3,B3"}, plan, {})
        assert event["chords"] == [["F3", "A3"], ["G3", "B3"]]

    def test_sequence_trigger_fires_on_a_truthy_cell(self):
        notes = [{"note": "C5", "duration": "16n", "velocity": 0.9}]
        plan = _plan(
            sequence_trigger={"column": "Sparkle", "notes": notes, "gap": 0},
        )
        assert events_for_row({"Sparkle": False}, plan, {}) == []
        (event,) = events_for_row({"Sparkle": True}, plan, {})
        assert event["op"] == "sequence"
        assert event["notes"] == notes
        assert event["envelope"] is None

    def test_sequence_notes_column_overrides_the_static_notes(self):
        plan = _plan(
            sequence_trigger={
                "column": "Phrase",
                "notesColumn": "Phrase",
                "notes": [{"note": "C5"}],
                "gap": 0,
            }
        )
        (event,) = events_for_row({"Phrase": "E5 G5"}, plan, {})
        assert event["notes"] == [{"note": "E5"}, {"note": "G5"}]

    def test_pitch_and_triggers_combine_on_one_row(self):
        plan = _plan(
            mappings={"pitch": {"column": "Price"}},
            chord_trigger={
                "column": "IsChord",
                "chords": [["C4"]],
                "gap": "4n",
                "duration": "2n",
            },
        )
        events = events_for_row({"Price": 1, "IsChord": True}, plan, {})
        assert [event["op"] for event in events] == ["tone", "chordSequence"]


# ---------------------------------------------------------------------------
# Update → rows
# ---------------------------------------------------------------------------


class _Update:
    def __init__(self, added):
        self._added = added
        self.requested_cols = None

    def added(self, cols=None):
        self.requested_cols = cols
        return {col: self._added[col] for col in (cols or self._added)}


class TestRowsFromUpdate:
    def test_reads_the_requested_columns(self):
        update = _Update({"A": [1, 2, 3], "B": ["x", "y", "z"]})
        rows = rows_from_update(update, ["A"], 10)
        assert rows == [{"A": 1}, {"A": 2}, {"A": 3}]
        assert update.requested_cols == ["A"]

    def test_keeps_the_newest_rows_within_the_limit(self):
        update = _Update({"A": [1, 2, 3, 4]})
        assert rows_from_update(update, ["A"], 1) == [{"A": 4}]
        assert rows_from_update(update, ["A"], 2) == [{"A": 3}, {"A": 4}]

    def test_empty_tick_is_no_rows(self):
        assert rows_from_update(_Update({"A": []}), ["A"], 10) == []
        assert rows_from_update(_Update({}), [], 10) == []

    def test_unknown_column_is_silent(self):
        class _Boom:
            def added(self, cols=None):
                raise RuntimeError("no such column")

        assert rows_from_update(_Boom(), ["A"], 10) == []


class TestPlanColumns:
    def test_collects_every_column_the_listener_reads(self):
        plan = _plan(
            mappings={
                "pitch": {
                    "column": "Price",
                    "minColumn": "Price_tones_min",
                    "maxColumn": "Price_tones_max",
                },
                "velocity": {"column": "Vol"},
                "duration": {"column": "Vol"},
                "voice": {"column": "Side", "voices": {}},
            },
            param_mappings={"reverb.wet": {"column": "Wet"}},
            chord_trigger={"column": "IsChord", "notesColumn": "Chord"},
            sequence_trigger={"column": "Phrase", "notesColumn": "Phrase"},
        )
        assert _plan_columns(plan) == [
            "Price",
            "Price_tones_min",
            "Price_tones_max",
            "Vol",
            "Side",
            "Wet",
            "IsChord",
            "Chord",
            "Phrase",
        ]


# ---------------------------------------------------------------------------
# The hook — wiring, validation, rate limiting
# ---------------------------------------------------------------------------


class _Ref:
    def __init__(self, current):
        self.current = current


class _FakeTable:
    """A table stub that also plays the part of its own augmented copy."""

    def __init__(self, names):
        self.column_names = list(names)

    def agg_by(self, aggs, by=None):
        raise RuntimeError("no engine")  # → fall back to running ranges


class _UiStub(types.ModuleType):
    """Records what the hook registered so the test can drive the listener."""

    def __init__(self):
        super().__init__("deephaven.ui")
        self.listener = None
        self.listener_deps = None
        self.sent: list[tuple[str, dict]] = []
        self.queued: list = []

        self.use_memo = lambda fn, _deps: fn()
        self.use_ref = _Ref
        self.use_send_event = lambda: self._send
        self.use_render_queue = lambda: self._queue
        self.use_table_listener = self._listen

    def _send(self, name, params):
        self.sent.append((name, params))

    def _queue(self, fn):
        self.queued.append(fn)
        fn()  # the render thread runs it immediately in these tests

    def _listen(self, table, listener, deps, *args, **kwargs):
        self.listener = listener
        self.listener_deps = deps


@pytest.fixture
def ui(monkeypatch):
    stub = _UiStub()
    monkeypatch.setitem(sys.modules, "deephaven.ui", stub)
    return stub


class TestHook:
    def test_registers_a_listener_and_sends_on_tick(self, ui):
        table = _FakeTable(["Price"])
        use_table_tones_listener(table, pitch="Price", mode="all")
        assert ui.listener is not None

        ui.listener(_Update({"Price": [1, 2]}), False)
        assert [name for name, _ in ui.sent] == [TONES_EVENT, TONES_EVENT]
        assert [params["value"] for _, params in ui.sent] == [1.0, 2.0]

    def test_sends_through_the_render_queue(self, ui):
        # Listeners run off the render thread, where there is no event context.
        use_table_tones_listener(_FakeTable(["Price"]), pitch="Price")
        ui.listener(_Update({"Price": [1]}), False)
        assert len(ui.queued) == 1

    def test_last_mode_sonifies_only_the_newest_row(self, ui):
        use_table_tones_listener(
            _FakeTable(["Price"]), pitch="Price", mode="last", rate_limit_ms=0
        )
        ui.listener(_Update({"Price": [1, 2, 3]}), False)
        assert [params["value"] for _, params in ui.sent] == [3.0]

    def test_rate_limit_drops_ticks(self, ui):
        use_table_tones_listener(
            _FakeTable(["Price"]), pitch="Price", mode="last", rate_limit_ms=10_000
        )
        ui.listener(_Update({"Price": [1]}), False)
        ui.listener(_Update({"Price": [2]}), False)
        assert len(ui.sent) == 1

    def test_rate_limit_does_not_apply_to_all_mode(self, ui):
        use_table_tones_listener(
            _FakeTable(["Price"]), pitch="Price", mode="all", rate_limit_ms=10_000
        )
        ui.listener(_Update({"Price": [1]}), False)
        ui.listener(_Update({"Price": [2]}), False)
        assert len(ui.sent) == 2

    def test_silent_rows_send_nothing(self, ui):
        use_table_tones_listener(
            _FakeTable(["IsChord"]), chord_column="IsChord", mode="all"
        )
        ui.listener(_Update({"IsChord": [False, False]}), False)
        assert ui.sent == []

    def test_listener_deps_track_the_sound(self, ui):
        table = _FakeTable(["Price"])
        use_table_tones_listener(table, pitch="Price", instrument="fm")
        fm_deps = ui.listener_deps
        use_table_tones_listener(table, pitch="Price", instrument="pluck")
        assert ui.listener_deps != fm_deps

    def test_data_driven_param_column_reaches_the_event(self, ui):
        use_table_tones_listener(
            _FakeTable(["Price", "Wet"]), pitch="Price", reverb_wet="Wet", mode="all"
        )
        ui.listener(_Update({"Price": [1, 2], "Wet": [0, 10]}), False)
        assert ui.sent[-1][1]["params"]["reverb.wet"]["t"] == 1.0

    def test_unknown_column_raises(self, ui):
        with pytest.raises(ValueError, match="pitch='Nope'"):
            use_table_tones_listener(_FakeTable(["Price"]), pitch="Nope")

    def test_unknown_trigger_column_raises(self, ui):
        with pytest.raises(ValueError, match="chord_column='Nope'"):
            use_table_tones_listener(_FakeTable(["Price"]), chord_column="Nope")

    def test_unknown_mode_raises(self, ui):
        with pytest.raises(ValueError, match="unknown mode"):
            use_table_tones_listener(_FakeTable(["Price"]), pitch="Price", mode="every")

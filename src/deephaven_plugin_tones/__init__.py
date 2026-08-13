"""
deephaven-plugin-tones — Python API.

Public surface::

    from deephaven import ui
    from deephaven_plugin_tones import tones, use_table_tones_listener


    @ui.component
    def my_panel():
        # Manual triggers: call `tones` from any handler.
        return ui.button("Play C4", on_press=lambda _e: tones.play("C4"))


    @ui.component
    def market_sounds(prices):
        # Table sonification: a hook that listens and sounds each new row.
        use_table_tones_listener(prices, pitch="Price")
        return ui.table(prices)
"""

from ._config import (
    ColumnInput,
    FilterRolloff,
    FilterType,
    Instrument,
    MidiNote,
    NoteInput,
    NoteValue,
    ParamInput,
    TableMode,
    ToneTime,
    VoiceOverride,
)
from .table_tones import use_table_tones_listener
from .tones import Tones, TonesError, tones

__all__ = [
    # entry points
    "tones",
    "use_table_tones_listener",
    "Tones",
    "TonesError",
    # type aliases for annotating user code
    "Instrument",
    "FilterType",
    "FilterRolloff",
    "TableMode",
    "ToneTime",
    "NoteValue",
    "NoteInput",
    "MidiNote",
    "ParamInput",
    "ColumnInput",
    "VoiceOverride",
]

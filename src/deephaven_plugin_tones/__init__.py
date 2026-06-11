"""
deephaven-plugin-tones — Python API.

Public surface::

    from deephaven_plugin_tones import use_tones, table_tones


    @ui.component
    def my_panel():
        # Manual triggers: a hook returning (element, control).
        audio, audio_control = use_tones(instrument="sine")
        return ui.flex(
            audio,
            ui.button("Play C4", on_press=lambda _e: audio_control.play("C4")),
        )


    @ui.component
    def market_sounds(prices):
        # Declarative table sonification: a bare element to mount.
        return ui.flex(table_tones(prices, pitch="Price"), ui.table(prices))
"""

from .deephaven_plugin_tones_component import (
    ColumnInput,
    FilterRolloff,
    FilterType,
    Instrument,
    MidiNote,
    NoteInput,
    NoteValue,
    ParamInput,
    TableMode,
    Tones,
    TonesControl,
    TonesElement,
    ToneTime,
    VoiceOverride,
    table_tones,
    use_tones,
)

__all__ = [
    # entry points + result/handle types
    "use_tones",
    "table_tones",
    "Tones",
    "TonesElement",
    "TonesControl",
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

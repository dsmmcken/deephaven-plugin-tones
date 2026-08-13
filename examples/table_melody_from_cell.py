"""
Melody-From-A-Cell Demo -- deephaven-plugin-tones
=================================================
Each ROW carries a little melodic phrase in a column, and the plugin plays that
phrase (as a timed run) when the row ticks. The notes live in the data, not in
the plugin config -- this is ``sequence_notes_column`` (the play_sequence
analogue, driven per row).

* ``Phrase`` is a ``String`` cell like ``"C5 E5 G5 C6"`` (notes split on spaces
  or commas). An empty string is a rest.
* ``use_table_tones_listener(sequence_notes_column="Phrase", mode="all")`` -- the
  notes column doubles as the trigger, so any row with a non-empty ``Phrase``
  plays that run; empty rows are silent.
* ``ui.table(..., format_=ui.TableFormat(if_="Phrase.length() > 0",
  background_color="green"))`` -- highlights the rows that will sound.

The table cycles a handful of arpeggios (up, down, and two other shapes) with a
rest, so you hear a rolling sequence of little flourishes as it ticks.

**Audio note**: browsers require a user interaction before audio can play. Opening or clicking anywhere in the panel satisfies this -- there is no separate button.
"""

from deephaven import time_table, ui

from deephaven_plugin_tones import use_table_tones_listener

# ---------------------------------------------------------------------------
# Ticking table -- one phrase per second, cycling a few note runs + a rest.
# ---------------------------------------------------------------------------
_ticking = time_table("PT1S").update(
    [
        "Step = ii % 5",
        # 0: C major arpeggio up, 1: ...and back down, 2: D minor up,
        # 3: G major there-and-back, 4: rest.
        "Phrase = Step == 0 ? `C5 E5 G5 C6` : Step == 1 ? `C6 G5 E5 C5` : Step == 2 ? `D5 F5 A5 D6` : Step == 3 ? `G4 B4 D5 G5 D5 B4` : ``",
    ]
)


@ui.component
def melody_from_cell_panel():
    """Layout: the live table with green phrase rows, sonified by the hook."""
    use_table_tones_listener(
        _ticking,
        mode="all",  # evaluate every new row
        sequence_notes_column="Phrase",  # per-row melody; doubles as trigger
        # Bright, plucky bell-ish voice for the flourishes.
        instrument="triangle",
        envelope_attack=0.002,
        envelope_decay=0.2,
        envelope_sustain=0.0,
        envelope_release=0.3,
        reverb_decay=2.0,
        reverb_wet=0.25,
        reverb_predelay=0.01,
        volume=-11,
        rate_limit_ms=0,
    )

    return ui.flex(
        ui.table(
            _ticking,
            format_=ui.TableFormat(if_="Phrase.length() > 0", background_color="green"),
        ),
        direction="row",
        flex="1",
    )


# Top-level assignment -- surfaces this as an openable panel in the Web IDE.
table_melody_from_cell = melody_from_cell_panel()

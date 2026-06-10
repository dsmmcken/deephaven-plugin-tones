"""
Column-Driven Chord Progression -- deephaven-plugin-tones
=========================================================
Instead of one fixed chord set, each ROW carries the chord(s) it should play, in
a column. The table walks a I-V-vi-IV progression (C - G - Am - F) with two
rests, one step per second, and you hear it as the table ticks.

This shows the optional ``chord_notes_column`` (and its sequence sibling
``sequence_notes_column``): a column whose per-row cell supplies the notes. A
single cell can hold **one chord** or a **whole progression**:

* ``"C4,E4,G4"``                 -- one chord (notes split on commas/whitespace).
* ``"F3,A3,C4 | G3,B3,D4"``      -- several chords in one cell, split on ``|``;
  ``chord_gap`` spaces them into a cadence within that single tick.
* ``""`` (empty string)          -- a rest (no chord this tick).

``table_tones(chord_notes_column="Chord", mode="all")`` -- the notes column
doubles as the trigger, so any row with a non-empty ``Chord`` plays it; empty
rows are silent. (You can still pass a separate boolean ``chord_column`` gate
and/or a static ``chords`` fallback.) ``ui.table(..., format_=...)`` highlights
the rows that will sound.

**Audio note**: browsers require a user interaction before audio can play. Opening or clicking anywhere in the panel satisfies this -- there is no separate button.
"""

from deephaven import time_table, ui

from deephaven_plugin_tones import table_tones

# ---------------------------------------------------------------------------
# Ticking table -- one step per second, cycling I-V-vi-IV with two rests.
# Steps 0-2 carry a single chord each; step 3 packs a IV->V turnaround into one
# cell (chords split on `|`), demonstrating both the single-chord and the
# whole-progression-in-a-cell forms. `ii` is the implicit row index.
# ---------------------------------------------------------------------------
_ticking = time_table("PT1S").update(
    [
        "Step = ii % 6",
        # Step 3 packs IV -> V into one cell; steps 4 & 5 are rests (empty string).
        "Chord = Step == 0 ? `C4,E4,G4` : Step == 1 ? `G3,B3,D4` : Step == 2 ? `A3,C4,E4` : Step == 3 ? `F3,A3,C4 | G3,B3,D4` : ``",
    ]
)


@ui.component
def chord_progression_panel():
    """Layout: invisible table_tones() engine + the live table with blue chord rows."""
    return ui.flex(
        table_tones(
            table=_ticking,
            mode="all",  # evaluate every new row
            chord_notes_column="Chord",  # per-row chord(s); doubles as the trigger
            chord_gap="4n",  # space multi-chord cells into a cadence
            chord_duration="2n",  # each chord rings for a half note
            # Warm pad-ish voice.
            instrument="triangle",
            envelope_attack=0.04,
            envelope_decay=0.3,
            envelope_sustain=0.6,
            envelope_release=1.4,
            reverb_decay=3.0,
            reverb_wet=0.3,
            reverb_predelay=0.01,
            volume=-12,
            rate_limit_ms=0,
        ),
        # Highlight the rows that carry a chord (non-empty Chord string).
        ui.table(
            _ticking,
            format_=ui.TableFormat(if_="Chord.length() > 0", background_color="blue"),
        ),
        direction="row",
        flex="1",
    )


# Top-level assignment -- surfaces this as an openable panel in the Web IDE.
table_chord_progression = chord_progression_panel()

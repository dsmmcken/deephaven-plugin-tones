"""
Chords-From-A-Cell Demo -- deephaven-plugin-tones
=================================================
Like the chord-trigger "1 in 15 ticks fires a pleasant series of chords" demo,
but the chord progression lives in the DATA: a single cell holds the whole
cadence, and different rows can carry different progressions.

* ``Chords`` is a ``String`` cell holding a progression -- chords separated by
  ``|``, notes within a chord by commas, e.g.
  ``"C4,E4,G4 | G3,B3,D4 | A3,C4,E4 | F3,A3,C4"`` (a I-V-vi-IV cadence). Empty
  string = no chord this tick.
* Roughly 1 in 15 rows is dealt one of two progressions (a "bright" I-V-vi-IV
  and a "wistful" vi-IV-I-V); the rest are empty.
* ``table_tones(chord_notes_column="Chords", mode="all")`` -- the notes column
  doubles as the trigger, so a row plays its progression whenever ``Chords`` is
  non-empty. ``chord_gap`` spaces the chords out into a cadence.
* ``ui.table(..., format_=ui.TableFormat(if_="Chords.length() > 0",
  background_color="blue"))`` -- highlights the rows that will sound.

**Audio note**: browsers require a user interaction before audio can play. Opening or clicking anywhere in the panel satisfies this -- there is no separate button.
"""

from deephaven import time_table, ui

from deephaven_plugin_tones import table_tones

# Two progressions, each a full cadence packed into one string (chords split on
# `|`, notes on commas). Picking between them per row shows that the *data*
# chooses the music.
_BRIGHT = "C4,E4,G4 | G3,B3,D4 | A3,C4,E4 | F3,A3,C4"  # I  - V  - vi - IV
_WISTFUL = "A3,C4,E4 | F3,A3,C4 | C4,E4,G4 | G3,B3,D4"  # vi - IV - I  - V

# ---------------------------------------------------------------------------
# Ticking table -- ~1/15 rows carry a progression, chosen by a coin flip.
# ---------------------------------------------------------------------------
_ticking = time_table("PT1S").update(
    [
        "Roll = Math.random()",
        # ~1/15 chance to fire; when firing, pick a progression by a second flip.
        f"Chords = Roll < (1.0 / 15.0) ? (Roll < (1.0 / 30.0) ? `{_BRIGHT}` : `{_WISTFUL}`) : ``",
    ]
)


@ui.component
def chords_from_cell_panel():
    """Layout: invisible table_tones() engine + the live table with blue chord rows."""
    return ui.flex(
        table_tones(
            table=_ticking,
            mode="all",  # evaluate every new row
            chord_notes_column="Chords",  # per-row progression; doubles as trigger
            chord_gap="4n",  # space the chords into a cadence
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
        ui.table(
            _ticking,
            format_=ui.TableFormat(if_="Chords.length() > 0", background_color="blue"),
        ),
        direction="row",
        flex="1",
    )


# Top-level assignment -- surfaces this as an openable panel in the Web IDE.
table_chords_from_cell = chords_from_cell_panel()

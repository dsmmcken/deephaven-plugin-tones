"""
Chord-Trigger Demo -- deephaven-plugin-tones
============================================
A table that ticks once a second; roughly 1 in 15 ticks fires a pleasant chord
PROGRESSION (I-V-vi-IV in C). The matching row is highlighted blue in the table
so you can SEE which ticks will sound.

**How it works**

* ``IsChord = Math.random() < 1.0/15.0`` -- each new row independently has a
  ~1/15 chance of being a "chord row" (a boolean column, fixed once the row is
  created).
* ``table_tones(chord_column="IsChord", mode="all")`` -- on every new row whose
  ``IsChord`` is true, the plugin plays the ``chords`` progression as a timed
  cadence. All other rows are silent.
* ``ui.table(..., format_=ui.TableFormat(if_="IsChord", background_color="blue"))``
  -- colours the whole row blue exactly when ``IsChord`` is true. (``if_`` is a
  Deephaven boolean expression; with no ``cols`` the format applies to the row.)

The default progression is the "four chords" (C - G - Am - F): warm, resolved,
and pleasant no matter when it fires. Pass ``chords=[[...], ...]`` to use your
own (each chord is a list of note names).

**Audio note**: browsers require a user interaction before audio can play. Opening or clicking anywhere in the panel satisfies this -- there is no separate button.
"""

from deephaven import time_table, ui

from deephaven_plugin_tones import table_tones

# ---------------------------------------------------------------------------
# Ticking table -- one row per second; ~1/15 rows flagged as chord rows.
# ---------------------------------------------------------------------------
# Math.random() is Java's; evaluated once per row, so IsChord is stable per row.
_ticking = time_table("PT1S").update(
    [
        "Beat = ii",
        "IsChord = Math.random() < (1.0 / 15.0)",
    ]
)


@ui.component
def chord_trigger_panel():
    """Layout: invisible table_tones() engine + the live table with blue chord rows."""
    return ui.flex(
        table_tones(
            table=_ticking,
            mode="all",  # evaluate every new row for the trigger
            chord_column="IsChord",  # truthy row -> play the progression
            # chords=... defaults to a pleasant I-V-vi-IV in C.
            chord_gap="4n",  # spacing between chords
            chord_duration="2n",  # each chord rings for a half note
            # Warm, soft pad-ish voice for the chords.
            instrument="triangle",
            envelope_attack=0.03,
            envelope_decay=0.3,
            envelope_sustain=0.6,
            envelope_release=1.4,
            reverb_decay=3.0,
            reverb_wet=0.3,
            reverb_predelay=0.01,
            volume=-12,
            rate_limit_ms=0,
        ),
        # Live table -- chord rows are highlighted blue (if_ applies to the row).
        ui.table(
            _ticking,
            format_=ui.TableFormat(if_="IsChord", background_color="blue"),
        ),
        direction="row",
        flex="1",
    )


# Top-level assignment -- surfaces this as an openable panel in the Web IDE.
table_chord_trigger = chord_trigger_panel()

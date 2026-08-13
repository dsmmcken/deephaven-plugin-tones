"""
Sequence-Trigger Demo -- deephaven-plugin-tones
===============================================
The melodic cousin of ``table_chord_trigger.py``. A table ticks once a second;
roughly 1 in 12 ticks fires a little ascending ARPEGGIO (a timed melody), and
the matching row is highlighted green.

This is the table analogue of ``tones.play_sequence``: ``chord_column`` plays
simultaneous chords; ``sequence_column`` plays notes in time. Both fire on a
truthy trigger column and can be combined in one
``use_table_tones_listener(...)`` call (different trigger columns).

* ``Sparkle = Math.random() < 1.0/12.0`` -- ~1/12 of rows are flagged.
* ``use_table_tones_listener(sequence_column="Sparkle", mode="all")`` -- each
  flagged row plays the ``sequence_notes`` arpeggio; other rows are silent.
* ``ui.table(..., format_=ui.TableFormat(if_="Sparkle", background_color="green"))``
  -- colours the flagged rows green.

The default ``sequence_notes`` is an ascending C arpeggio (C5-E5-G5-C6); pass
your own to play any motif (same note forms as ``tones.play_sequence``).

**Audio note**: browsers require a user interaction before audio can play. Opening or clicking anywhere in the panel satisfies this -- there is no separate button.
"""

from deephaven import time_table, ui

from deephaven_plugin_tones import use_table_tones_listener

# ---------------------------------------------------------------------------
# Ticking table -- one row per second; ~1/12 rows flagged for an arpeggio.
# ---------------------------------------------------------------------------
_ticking = time_table("PT1S").update(
    [
        "Beat = ii",
        "Sparkle = Math.random() < (1.0 / 12.0)",
    ]
)


@ui.component
def sequence_trigger_panel():
    """Layout: the live table with green motif rows, sonified by the hook."""
    use_table_tones_listener(
        _ticking,
        mode="all",  # evaluate every new row for the trigger
        sequence_column="Sparkle",  # truthy row -> play the arpeggio
        # sequence_notes=... defaults to an ascending C arpeggio.
        # Bright, plucky bell-ish voice for the flourish.
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
            format_=ui.TableFormat(if_="Sparkle", background_color="green"),
        ),
        direction="row",
        flex="1",
    )


# Top-level assignment -- surfaces this as an openable panel in the Web IDE.
table_sequence_trigger = sequence_trigger_panel()

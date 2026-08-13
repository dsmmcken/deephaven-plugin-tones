"""
Jingle Jukebox -- deephaven-plugin-tones
========================================
A panel of short tunes, each one a single ``tones.play_sequence`` call.

Open this file in the Deephaven Web IDE and pick a jingle, or press *Surprise
me* for a random one.

**How a jingle is written**

``play_sequence`` plays its notes in order, each one lasting its own
``duration``, so the note list *is* the rhythm:

* ``"E4"`` -- a note at the call's default duration.
* ``("E4", 0.4)`` -- that note, held 0.4 seconds.
* ``(None, 0.25)`` -- a REST: silent, but it still takes 0.25 seconds.
* ``["E4", "G#4", "B4"]`` -- a LIST is a chord; its notes sound together.
* ``(["E4", "G#4", "B4"], 0.35)`` -- a chord with its own length.

``gap`` adds a little silence after every note (articulation) on top of its
duration; the default ``0`` runs the notes together legato.

Each jingle picks its own voice by passing sound options to the call
(``instrument=``, ``volume=``, ...), so one import covers everything.

**Audio note**: the browser requires a user gesture before audio can play.
The first press unlocks audio; every press after that sounds immediately.
"""

import random

from deephaven import ui

from deephaven_plugin_tones import tones


def _fate():
    """Beethoven, Symphony No. 5 -- the three-short-one-long "fate" motif."""
    tones.play_sequence(
        [
            ("G4", 0.15),
            ("G4", 0.15),
            ("G4", 0.15),
            ("Eb4", 0.8),
            (None, 0.25),
            ("F4", 0.15),
            ("F4", 0.15),
            ("F4", 0.15),
            ("D4", 1.0),
        ],
        gap=0.03,
        instrument="triangle",
        volume=-9,
    )


def _ode_to_joy():
    """Beethoven, Symphony No. 9 -- the opening phrase of the Ode to Joy."""
    tones.play_sequence(
        [
            "E4",
            "E4",
            "F4",
            "G4",
            "G4",
            "F4",
            "E4",
            "D4",
            "C4",
            "C4",
            "D4",
            "E4",
            ("E4", 0.42),
            ("D4", 0.28),
            ("D4", 0.7),
        ],
        duration=0.28,
        gap=0.02,
        instrument="fm",
        volume=-10,
    )


def _fur_elise():
    """Beethoven -- the alternating figure that opens Fur Elise."""
    tones.play_sequence(
        [
            "E5",
            "D#5",
            "E5",
            "D#5",
            "E5",
            "B4",
            "D5",
            "C5",
            ("A4", 0.4),
            (None, 0.12),
            "C4",
            "E4",
            "A4",
            ("B4", 0.4),
        ],
        duration=0.16,
        gap=0.01,
        instrument="triangle",
        envelope_attack=0.005,
        envelope_decay=0.3,
        envelope_sustain=0.1,
        envelope_release=0.4,
        volume=-10,
    )


def _twinkle():
    """Twinkle Twinkle Little Star -- the first two phrases."""
    tones.play_sequence(
        [
            "C4",
            "C4",
            "G4",
            "G4",
            "A4",
            "A4",
            ("G4", 0.6),
            "F4",
            "F4",
            "E4",
            "E4",
            "D4",
            "D4",
            ("C4", 0.6),
        ],
        duration=0.3,
        gap=0.02,
        instrument="triangle",
        envelope_attack=0.002,
        envelope_decay=0.28,
        envelope_sustain=0.0,
        envelope_release=0.3,
        volume=-10,
    )


def _westminster():
    """The Westminster chime -- the quarter-hour phrase from Big Ben."""
    tones.play_sequence(
        ["E4", "C4", "D4", ("G3", 1.1), (None, 0.3), "G3", "D4", "E4", ("C4", 1.4)],
        duration=0.45,
        gap=0.04,
        instrument="metal",
        envelope_attack=0.001,
        envelope_decay=1.4,
        envelope_sustain=0.0,
        envelope_release=1.6,
        reverb_decay=4,
        reverb_wet=0.4,
        volume=-16,
    )


def _fanfare():
    """An original brass-ish fanfare: a rising call answered by a chord."""
    tones.play_sequence(
        [
            ("C4", 0.18),
            ("E4", 0.18),
            ("G4", 0.18),
            ("C5", 0.5),
            (None, 0.15),
            (["C4", "E4", "G4", "C5"], 1.2),
        ],
        gap=0.02,
        instrument="sawtooth",
        envelope_attack=0.02,
        envelope_decay=0.15,
        envelope_sustain=0.6,
        envelope_release=0.5,
        filter_frequency=1600,
        volume=-13,
    )


# Display name -> the function that plays it. Order sets the picker order.
_JINGLES = {
    "Fate motif (Beethoven 5)": _fate,
    "Ode to Joy": _ode_to_joy,
    "Fur Elise": _fur_elise,
    "Twinkle Twinkle": _twinkle,
    "Westminster chime": _westminster,
    "Fanfare": _fanfare,
}


@ui.component
def jukebox():
    """A picker of jingles, a Play button, and a random pick."""
    selected, set_selected = ui.use_state(next(iter(_JINGLES)))

    return ui.flex(
        ui.picker(
            *[ui.item(name, key=name) for name in _JINGLES],
            selected_key=selected,
            on_change=set_selected,
            label="Jingle",
            width="size-3000",
        ),
        ui.button(
            "Play",
            variant="accent",
            on_press=lambda _e: _JINGLES[selected](),
        ),
        ui.button(
            "Surprise me",
            variant="primary",
            style="outline",
            on_press=lambda _e: _JINGLES[random.choice(list(_JINGLES))](),
        ),
        direction="row",
        align_items="end",
        gap="size-100",
        margin="size-200",
    )


# Top-level assignment -- surfaces this as an openable panel in the Web IDE.
jingle_jukebox = jukebox()

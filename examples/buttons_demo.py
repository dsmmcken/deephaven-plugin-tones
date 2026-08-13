"""
Tone Buttons Demo -- deephaven-plugin-tones
==========================================
Interactive demonstration of the ``tones`` trigger API.

Open this file in the Deephaven Web IDE.  The panel ``tone_buttons_demo``
appears in the panel list and can be opened in any layout.

**What each button does**

* **C / E / G** -- plays a single note (C4, E4, or G4) with a sine voice.
* **Chord C-E-G** -- plays all three notes simultaneously.
* **Arpeggio** -- plays a short rising arpeggio (C4->E4->G4->B4) via
  ``play_sequence``.
* **Progression** -- plays four chords in turn via ``play_chords``.
* **Confirm / Error / Notify** -- "earcons" built directly from
  ``play_sequence`` with a plucky envelope (success C5->E5->G5->C6, failure
  C5->G4->Eb4, info C5->G5). There are no preset earcon methods -- an earcon is
  just a short sequence, so you compose whatever fits.
* **Bell** -- the same ``play`` call on a *configured copy* of ``tones``, to show
  how a named voice is made once and reused.

``tones`` is imported and called directly: there is no hook to call and nothing
to mount in the render tree.  Every call may also carry per-call sound options
(``tones.play("C4", instrument="pluck")``).

**Audio note**: the browser requires a user gesture before audio can play.
Click *any* button once to unlock audio; subsequent presses play immediately.
"""

from deephaven import ui

from deephaven_plugin_tones import tones

# A configured copy: same API, different voice. Made once at import time.
_bell = tones.configure(
    instrument="metal",
    envelope_attack=0.001,
    envelope_decay=1.2,
    envelope_sustain=0.0,
    envelope_release=1.5,
    reverb_decay=4,
    reverb_wet=0.4,
    volume=-14,
)


# An "earcon" is just a short play_sequence with a plucky envelope, set via the
# flat attack/decay/sustain/release kwargs.
def _confirm():
    tones.play_sequence(
        ["C5", "E5", "G5", "C6"],
        duration="16n",
        attack=0.005,
        decay=0.12,
        sustain=0.0,
        release=0.25,
    )


def _error():
    tones.play_sequence(
        ["C5", "G4", "Eb4"],
        duration="8n",
        attack=0.005,
        decay=0.12,
        sustain=0.0,
        release=0.25,
    )


def _notify():
    tones.play_sequence(
        ["C5", "G5"],
        duration="16n",
        attack=0.005,
        decay=0.12,
        sustain=0.0,
        release=0.25,
    )


# Map each action_group item key to the zero-arg call it triggers.
_ACTIONS = {
    "c": lambda: tones.play("C4"),
    "e": lambda: tones.play("E4"),
    "g": lambda: tones.play("G4"),
    "chord": lambda: tones.play_chord(["C4", "E4", "G4"]),
    "arpeggio": lambda: tones.play_sequence(
        ["C4", "E4", "G4", "B4"],
        duration="16n",
        velocity=0.9,
    ),
    "progression": lambda: tones.play_chords(
        [
            ["C4", "E4", "G4"],
            ["G3", "B3", "D4"],
            ["A3", "C4", "E4"],
            ["F3", "A3", "C4"],
        ]
    ),
    "confirm": _confirm,
    "error": _error,
    "notify": _notify,
    "bell": lambda: _bell.play("C6", duration="2n"),
}


@ui.component
def tone_buttons():
    """
    A single-panel component with a ``ui.action_group`` of tone-triggering
    buttons.

    The buttons are a single ``ui.action_group``: each ``ui.item`` carries a
    ``key``, and the one ``on_action(key)`` handler dispatches to the matching
    call.  With no ``selection_mode`` the group acts as a row of action buttons
    (nothing stays "selected").
    """

    # on_action receives the pressed item's key. Handlers run on the render
    # thread, which is where `tones` needs to be called from.
    def on_action(key):
        _ACTIONS[key]()

    return ui.action_group(
        # ---- individual notes ----
        ui.item("C", key="c"),
        ui.item("E", key="e"),
        ui.item("G", key="g"),
        # ---- chord + progression ----
        ui.item("Chord C-E-G", key="chord"),
        ui.item("Progression", key="progression"),
        # ---- sequence: rising arpeggio via play_sequence (not a preset) ----
        ui.item("Arpeggio", key="arpeggio"),
        # ---- earcons: short play_sequence recipes (see defs above) ----
        ui.item("Confirm", key="confirm"),
        ui.item("Error", key="error"),
        ui.item("Notify", key="notify"),
        # ---- a configured copy of `tones` ----
        ui.item("Bell", key="bell"),
        on_action=on_action,
    )


# Top-level assignment -- Deephaven Web IDE surfaces any top-level variable as
# an openable panel.  Opening ``tone_buttons_demo`` renders the component.
tone_buttons_demo = tone_buttons()

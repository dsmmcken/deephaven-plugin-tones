"""
Tone Buttons Demo -- deephaven-plugin-tones
==========================================
Interactive demonstration of the ``use_tones`` trigger hook.

Open this file in the Deephaven Web IDE.  The panel ``tone_buttons_demo``
appears in the panel list and can be opened in any layout.

**What each button does**

* **C / E / G** -- plays a single note (C4, E4, or G4) with a sine voice.
* **Chord C-E-G** -- plays all three notes simultaneously.
* **Arpeggio** -- plays a short rising arpeggio (C4->E4->G4->B4) via
  ``play_sequence``.
* **Confirm / Error / Notify** -- "earcons" built directly from
  ``play_sequence`` with a plucky envelope (success C5->E5->G5->C6, failure
  C5->G4->Eb4, info C5->G5). There are no preset earcon methods — an earcon is
  just a short sequence, so you compose whatever fits.
* **Stop** -- ``audio_control.stop()`` cuts all sustained sound immediately.

The buttons are a single ``ui.action_group``: each ``ui.item`` carries a ``key``,
and the one ``on_action(key)`` handler dispatches to the matching control method.
With no ``selection_mode`` the group acts as a row of action buttons (nothing
stays "selected").

**Audio note**: the browser requires a user gesture before audio can play.
Click *any* button once to unlock audio; subsequent presses play immediately.
"""

from deephaven import ui

from deephaven_plugin_tones import use_tones


@ui.component
def tone_buttons():
    """
    A single-panel component with a ``ui.action_group`` of tone-triggering buttons.

    ``audio, audio_control = use_tones(...)`` is called at the top of the
    component (render-hook pattern).  ``audio`` is placed inside the ``ui.flex``
    tree -- it renders **nothing** (zero DOM, zero layout space) but mounts the
    audio engine.  The action group's ``on_action`` handler dispatches each
    item ``key`` to a method on ``audio_control``.
    """
    audio, audio_control = use_tones(
        instrument="sine",
        reverb_decay=2,
        reverb_wet=0.25,
        reverb_predelay=0.01,
        volume=-8,
        envelope_attack=0.02,
        envelope_decay=0.10,
        envelope_sustain=0.6,
        envelope_release=1.2,
    )

    # An "earcon" is just a short play_sequence with a plucky envelope, set via
    # the flat attack/decay/sustain/release kwargs.
    def confirm():
        audio_control.play_sequence(
            ["C5", "E5", "G5", "C6"],
            gap="16n",
            duration="16n",
            attack=0.005,
            decay=0.12,
            sustain=0.0,
            release=0.25,
        )

    def error():
        audio_control.play_sequence(
            ["C5", "G4", "Eb4"],
            gap="16n",
            duration="8n",
            attack=0.005,
            decay=0.12,
            sustain=0.0,
            release=0.25,
        )

    def notify():
        audio_control.play_sequence(
            ["C5", "G5"],
            gap="8n",
            duration="16n",
            attack=0.005,
            decay=0.12,
            sustain=0.0,
            release=0.25,
        )

    # Map each action_group item key to the zero-arg control call it triggers.
    actions = {
        "c": lambda: audio_control.play("C4"),
        "e": lambda: audio_control.play("E4"),
        "g": lambda: audio_control.play("G4"),
        "chord": lambda: audio_control.play_chord(["C4", "E4", "G4"]),
        "arpeggio": lambda: audio_control.play_sequence(
            ["C4", "E4", "G4", "B4"],
            gap="16n",
            duration="8n",
            velocity=0.9,
        ),
        "confirm": confirm,
        "error": error,
        "notify": notify,
        "stop": audio_control.stop,
    }

    # on_action receives the pressed item's key (selection_mode is None, so the
    # group behaves as action buttons rather than a selection control).
    def on_action(key):
        actions[key]()

    return [
        # audio is invisible -- it mounts the engine with no layout impact
        audio,
        ui.action_group(
            # ---- individual notes ----
            ui.item("C", key="c"),
            ui.item("E", key="e"),
            ui.item("G", key="g"),
            # ---- chord ----
            ui.item("Chord C-E-G", key="chord"),
            # ---- sequence: rising arpeggio via play_sequence (not a preset) ----
            ui.item("Arpeggio", key="arpeggio"),
            # ---- earcons: short play_sequence recipes (see defs above) ----
            ui.item("Confirm", key="confirm"),
            ui.item("Error", key="error"),
            ui.item("Notify", key="notify"),
            # ---- stop ----
            ui.item("Stop", key="stop"),
            on_action=on_action,
        ),
    ]


# Top-level assignment -- Deephaven Web IDE surfaces any top-level variable as
# an openable panel.  Opening ``tone_buttons_demo`` renders the component.
tone_buttons_demo = tone_buttons()

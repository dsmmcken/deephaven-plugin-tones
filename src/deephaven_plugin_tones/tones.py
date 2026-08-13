"""
``tones`` — the trigger API.

``tones`` is a module-level object you import and call directly; there is no
element to mount and no hook to call first::

    from deephaven_plugin_tones import tones

    ui.button("Play", on_press=lambda _e: tones.play("C4"))

Each call sends a ``deephaven_plugin_tones.event`` to the browser, where the
plugin's ``eventMapping`` handler drives Tone.js. Sounds are self-terminating —
there is nothing to stop.

Every call carries its own sound config, so per-call overrides work
(``tones.play("C4", instrument="pluck")``) and a configured copy is one call
away (``bell = tones.configure(instrument="metal")``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ._config import (
    TONES_EVENT,
    ColumnInput,  # noqa: F401 — re-exported for annotating user code
    FilterRolloff,
    FilterType,
    Instrument,
    NoteInput,
    NoteValue,
    ToneTime,
    build_config,
    envelope_from_flat,
    normalize_sequence_notes,
    validate_config_enums,
    validate_option_names,
)


class TonesError(Exception):
    """Raised when a tone is triggered from somewhere it can't reach the client."""


def _send(payload: dict[str, Any]) -> None:
    """
    Send one tone event to the client.

    ``use_send_event`` must be called on the render thread — i.e. while a
    ``@ui.component`` renders or from a handler it triggered. Calling from a
    background thread (a table listener, a worker) has no event context, so
    re-raise with a pointer to ``use_render_queue``.
    """
    from deephaven.ui import use_send_event

    try:
        send_event = use_send_event()
    except Exception as e:  # noqa: BLE001 — NoContextException is internal to ui
        raise TonesError(
            "Tones must be triggered from the render thread — while a component renders or from an event handler it triggered. To trigger from a background thread (e.g. a table listener), queue it with the `use_render_queue` hook."
        ) from e
    send_event(TONES_EVENT, payload)


class Tones:
    """
    A configured sound. The module-level :data:`tones` is an instance with the
    defaults; :meth:`configure` returns a copy with some options changed.

    Every constructor arg is a *sound option* and may also be passed to any
    trigger method as a per-call override.

    Args:
        instrument: Tone.js synth type — ``"sine"`` | ``"triangle"`` |
            ``"square"`` | ``"sawtooth"`` | ``"fm"`` | ``"am"`` | ``"membrane"``
            | ``"pluck"`` | ``"monosynth"`` | ``"duosynth"`` | ``"metal"``.
        polyphony: Maximum simultaneous voices. Default ``8``.
        envelope_attack, envelope_decay, envelope_sustain, envelope_release:
            ADSR envelope, in seconds (sustain is a 0–1 level). Each ``None``
            keeps the pleasant default.
        detune: Global detune in cents. Default ``0``.
        portamento: Glide time between notes in seconds. Default ``0``.
        filter: ``True`` (default) enables the filter node; ``False`` disables.
        filter_type: ``"lowpass"`` | ``"highpass"`` | ``"bandpass"`` |
            ``"notch"`` | ``"allpass"`` | ``"peaking"``.
        filter_frequency: Cutoff in Hz. Default ``2200``.
        filter_q: Resonance. Default ``1``.
        filter_rolloff: ``-12`` | ``-24`` | ``-48`` | ``-96``.
        reverb: ``True`` (default) enables the reverb node; ``False`` disables.
        reverb_decay: Tail length in seconds. Default ``3``.
        reverb_wet: Wet/dry mix 0–1. Default ``0.3``.
        reverb_predelay: Pre-delay in seconds. Default ``0.01``.
        delay: ``True`` enables the feedback-delay node. Default ``False`` —
            setting any ``delay_*`` param also enables it.
        delay_time: Delay time (Tone.js note string like ``"8n"`` or seconds).
        delay_feedback: Feedback 0–~0.9. Default ``0.2``.
        delay_wet: Wet/dry mix 0–1. Default ``0.1``.
        distortion: ``True`` enables a waveshaper distortion node.
        distortion_amount: Distortion amount 0–1. Default ``0.4``.
        distortion_wet: Wet/dry mix 0–1. Default ``1.0``.
        chorus: ``True`` enables a stereo chorus node.
        chorus_frequency: LFO rate in Hz. Default ``1.5``.
        chorus_depth: Modulation depth 0–1. Default ``0.7``.
        chorus_wet: Wet/dry mix 0–1. Default ``0.5``.
        ping_pong: ``True`` enables a stereo ping-pong delay node.
        ping_pong_time: Delay time. Default ``"8n"``.
        ping_pong_feedback: Feedback 0–~0.9. Default ``0.2``.
        ping_pong_wet: Wet/dry mix 0–1. Default ``0.5``.
        limiter: ``True`` (default) inserts a brick-wall limiter on the master
            bus so loud bursts don't clip.
        limiter_threshold: Limiter ceiling in dBFS. Default ``-1``.
        volume: Master volume in dB. Default ``-8``.
        pan: Stereo position, ``-1`` (left) … ``1`` (right). Default ``0``.
        scale: Scale for :meth:`play_value` — ``"pentatonic"``, ``"major"``,
            ``"minor"``, ``"chromatic"``, or a list of semitone intervals.
        root: Bottom of the value→pitch range, e.g. ``"C3"``.
        octaves: Octaves spanned by the value→pitch mapping.
        value_range: ``[lo, hi]`` input domain for :meth:`play_value`. ``None``
            tracks the values seen so far.
        descending: When ``True``, higher values map to lower pitches.

    Example::

        from deephaven_plugin_tones import tones

        bell = tones.configure(instrument="metal", reverb_wet=0.5)
        ui.button("Ding", on_press=lambda _e: bell.play("C6"))
    """

    def __init__(
        self,
        instrument: Instrument = "sine",
        polyphony: int = 8,
        envelope_attack: float | None = None,
        envelope_decay: float | None = None,
        envelope_sustain: float | None = None,
        envelope_release: float | None = None,
        detune: float = 0,
        portamento: float = 0,
        filter: bool = True,  # noqa: A002 — effect on/off toggle
        filter_type: FilterType = "lowpass",
        filter_frequency: float = 2200,
        filter_q: float = 1,
        filter_rolloff: FilterRolloff = -24,
        reverb: bool = True,
        reverb_decay: float = 3,
        reverb_wet: float = 0.3,
        reverb_predelay: float = 0.01,
        delay: bool = False,
        delay_time: ToneTime | None = None,
        delay_feedback: float | None = None,
        delay_wet: float | None = None,
        distortion: bool = False,
        distortion_amount: float | None = None,
        distortion_wet: float | None = None,
        chorus: bool = False,
        chorus_frequency: float | None = None,
        chorus_depth: float | None = None,
        chorus_wet: float | None = None,
        ping_pong: bool = False,
        ping_pong_time: ToneTime | None = None,
        ping_pong_feedback: float | None = None,
        ping_pong_wet: float | None = None,
        limiter: bool = True,
        limiter_threshold: float = -1,
        volume: float = -8,
        pan: float = 0,
        scale: str | Sequence[int] = "pentatonic",
        root: str = "C3",
        octaves: int = 3,
        value_range: Sequence[float] | None = None,
        descending: bool = False,
    ) -> None:
        self._options: dict[str, Any] = {
            "instrument": instrument,
            "polyphony": polyphony,
            "envelope_attack": envelope_attack,
            "envelope_decay": envelope_decay,
            "envelope_sustain": envelope_sustain,
            "envelope_release": envelope_release,
            "detune": detune,
            "portamento": portamento,
            "filter": filter,
            "filter_type": filter_type,
            "filter_frequency": filter_frequency,
            "filter_q": filter_q,
            "filter_rolloff": filter_rolloff,
            "reverb": reverb,
            "reverb_decay": reverb_decay,
            "reverb_wet": reverb_wet,
            "reverb_predelay": reverb_predelay,
            "delay": delay,
            "delay_time": delay_time,
            "delay_feedback": delay_feedback,
            "delay_wet": delay_wet,
            "distortion": distortion,
            "distortion_amount": distortion_amount,
            "distortion_wet": distortion_wet,
            "chorus": chorus,
            "chorus_frequency": chorus_frequency,
            "chorus_depth": chorus_depth,
            "chorus_wet": chorus_wet,
            "ping_pong": ping_pong,
            "ping_pong_time": ping_pong_time,
            "ping_pong_feedback": ping_pong_feedback,
            "ping_pong_wet": ping_pong_wet,
            "limiter": limiter,
            "limiter_threshold": limiter_threshold,
            "volume": volume,
            "pan": pan,
            "scale": scale,
            "root": root,
            "octaves": octaves,
            "value_range": value_range,
            "descending": descending,
        }

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def configure(self, **overrides: Any) -> Tones:
        """
        Return a copy with some sound options changed — the way to keep a named
        voice around::

            bass = tones.configure(instrument="fm", root="C1", volume=-4)
        """
        validate_option_names("configure", list(overrides))
        return Tones(**{**self._options, **overrides})

    @property
    def options(self) -> dict[str, Any]:
        """The flat sound options behind this instance (a copy)."""
        return dict(self._options)

    def _config(self, overrides: Mapping[str, Any]) -> dict[str, Any]:
        """Build the client config, merging this call's option overrides."""
        validate_option_names("tones", list(overrides))
        merged = {**self._options, **overrides}
        validate_config_enums(
            "tones",
            merged["instrument"],
            merged["filter_type"],
            merged["filter_rolloff"],
        )
        return build_config(merged)

    # ------------------------------------------------------------------
    # Triggers
    # ------------------------------------------------------------------

    def play(
        self,
        note: NoteValue,
        duration: ToneTime = "8n",
        velocity: float = 1.0,
        **overrides: Any,
    ) -> None:
        """
        Play a single note.

        Args:
            note: Pitch as ``"C4"``, Hz (number), or ``{"midi": 60}``.
            duration: Tone.js time (note string or seconds), default ``"8n"``.
            velocity: 0.0–1.0, default 1.0.
            **overrides: Any sound option, for this call only.
        """
        _send(
            {
                "op": "play",
                "config": self._config(overrides),
                "note": note,
                "duration": duration,
                "velocity": velocity,
            }
        )

    def play_chord(
        self,
        notes: Sequence[NoteValue],
        duration: ToneTime = "2n",
        velocity: float = 1.0,
        **overrides: Any,
    ) -> None:
        """
        Play multiple notes simultaneously.

        Args:
            notes: Pitches to sound together (same forms as :meth:`play`).
            duration: Tone.js time (note string or seconds), default ``"2n"``.
            velocity: 0.0–1.0, default 1.0.
            **overrides: Any sound option, for this call only.
        """
        _send(
            {
                "op": "chord",
                "config": self._config(overrides),
                "notes": list(notes),
                "duration": duration,
                "velocity": velocity,
            }
        )

    def play_sequence(
        self,
        notes: Sequence[NoteInput],
        gap: ToneTime = 0,
        duration: ToneTime = "8n",
        velocity: float = 0.9,
        attack: float | None = None,
        decay: float | None = None,
        sustain: float | None = None,
        release: float | None = None,
        **overrides: Any,
    ) -> None:
        """
        Play a melody: each note starts when the previous one's ``duration``
        (plus ``gap``) has elapsed, so per-note durations set the rhythm.

        Args:
            notes: Note list. Each item is a note (``"C5"``, Hz, or
                ``{"midi": 60}``), a ``(note, duration)`` or
                ``(note, duration, velocity)`` tuple, or ``None`` for a REST
                (``(None, 0.5)`` rests for half a second).
            gap: Extra silence after each note, on top of its duration —
                articulation. Default ``0`` (notes butt up against each other).
            duration: Default length for notes that don't carry their own.
            velocity: Default velocity for notes that don't carry their own.
            attack, decay, sustain, release: ADSR overrides for this call only
                (e.g. ``attack=0.005, sustain=0.0`` for a plucky earcon). Each
                ``None`` keeps the base envelope's value.
            **overrides: Any sound option, for this call only.
        """
        _send(
            {
                "op": "sequence",
                "config": self._config(overrides),
                "notes": normalize_sequence_notes(notes, duration, velocity),
                "gap": gap,
                "envelope": envelope_from_flat(attack, decay, sustain, release),
            }
        )

    def play_chords(
        self,
        chords: Sequence[Sequence[NoteValue]],
        gap: ToneTime = "4n",
        duration: ToneTime = "2n",
        velocity: float = 0.8,
        **overrides: Any,
    ) -> None:
        """
        Play a chord progression — each chord struck ``gap`` after the previous.

        Args:
            chords: Chords to play in turn, e.g.
                ``[["C4","E4","G4"], ["G3","B3","D4"]]``.
            gap: Onset spacing between chords. Default ``"4n"``.
            duration: How long each chord rings. Default ``"2n"``.
            velocity: 0.0–1.0, default 0.8.
            **overrides: Any sound option, for this call only.
        """
        _send(
            {
                "op": "chordSequence",
                "config": self._config(overrides),
                "chords": [list(c) for c in chords],
                "gap": gap,
                "duration": duration,
                "velocity": velocity,
            }
        )

    def play_value(
        self,
        value: float,
        *,
        scale: str | Sequence[int] | None = None,
        root: str | None = None,
        octaves: int | None = None,
        value_range: Sequence[float] | None = None,
        descending: bool | None = None,
        **overrides: Any,
    ) -> None:
        """
        Map a number to a pitch on the configured scale, then play it.

        Args:
            value: The number to sonify.
            scale, root, octaves, value_range, descending: Per-call overrides of
                the value→pitch mapping; each ``None`` keeps the configured
                value. Without a ``value_range`` the client scales against the
                span of values it has seen so far.
            **overrides: Any other sound option, for this call only.
        """
        mapping = {
            "scale": scale,
            "root": root,
            "octaves": octaves,
            "value_range": value_range,
            "descending": descending,
        }
        merged = {**overrides, **{k: v for k, v in mapping.items() if v is not None}}
        _send(
            {
                "op": "value",
                "config": self._config(merged),
                "value": float(value),
            }
        )


#: The ready-to-use default sound. Import and call it directly.
tones = Tones()

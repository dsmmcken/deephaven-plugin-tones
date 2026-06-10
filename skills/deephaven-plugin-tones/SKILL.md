---
name: deephaven-plugin-tones
description: >-
  Add sound to a Deephaven Web IDE panel (@ui.component): button-click sounds,
  success/failure earcons, or live musical sonification of a ticking Deephaven
  table. Wraps Tone.js behind two Python entry points — use_tones() (a hook for
  manual triggers) and table_tones() (a declarative element that auto-sonifies a
  table). Use this skill whenever the user is building a deephaven.ui /
  @ui.component panel and mentions audio, sound, sonification, earcons, audio
  feedback, beeps, audible alerts, playing notes/chords/melodies, or turning
  table data or ticks into sound — even if they never name the plugin. Also use
  it whenever editing code that imports deephaven_plugin_tones or calls
  use_tones(...) / table_tones(...).
---

# deephaven-plugin-tones

This plugin wraps Tone.js and gives a Deephaven Web IDE panel (`@ui.component`) two Python entry
points for sound:

- **`use_tones(...)`** — a render hook for **manual triggers** (button-click sounds, earcons). It
  returns `(audio, audio_control)`: place `audio` in the tree, call `audio_control.play(...)` from
  handlers.
- **`table_tones(...)`** — a declarative **element** that **auto-sonifies a ticking table**. Drop it
  in the tree like `ui.table(...)`; it makes sound on every tick. No handle — it has nothing to
  trigger manually.

---

## The minimal pattern (manual triggers)

```python
from deephaven import ui
from deephaven_plugin_tones import use_tones

@ui.component
def my_panel():
    audio, audio_control = use_tones()        # 1. call at the TOP, like any hook
    return ui.flex(
        audio,                                 # 2. place audio in the tree (invisible)
        ui.button("Play", on_press=lambda _e: audio_control.play("C4")),  # 3. trigger
    )

panel = my_panel()
```

Three rules:

1. **Call `use_tones()` at the top of the component** (it's a render hook — uses `use_state` /
   `use_ref` internally, so it must run unconditionally).
2. **Place the returned `audio` element in the render tree** — mounting it initialises the audio
   engine client-side. It's invisible (zero layout space).
3. **Call methods on `audio_control` from any handler** in scope — buttons, text-field changes,
   callbacks, background threads.

---

## Trigger from a button

```python
@ui.component
def audio_buttons():
    audio, audio_control = use_tones(instrument="sine", volume=-8)
    return ui.flex(
        audio,
        ui.button("C", on_press=lambda _e: audio_control.play("C4")),
        ui.button("Chord", on_press=lambda _e: audio_control.play_chord(["C4", "E4", "G4"])),
        # "earcons" are just short play_sequence calls (see next section)
        ui.button("OK", on_press=lambda _e: audio_control.play_sequence(
            ["C5", "E5", "G5", "C6"], gap="16n", duration="16n",
            attack=0.005, decay=0.12, sustain=0.0, release=0.25)),
        ui.button("Stop", on_press=lambda _e: audio_control.stop()),
        direction="row",
    )
```

**Gesture gotcha:** The browser suspends its `AudioContext` until the user performs a real click.
The **first button press** satisfies this requirement — you may not hear sound on that very first
press. The second press onwards always works. There is nothing you need to code; it is automatic.

---

## Make a pleasant earcon

There are no preset earcon methods — an earcon is just a short `play_sequence` with a plucky
envelope (set via the flat `attack`/`decay`/`sustain`/`release` kwargs), so you compose whatever
fits. The classic three:

```python
# success — rising C5→E5→G5→C6, bright
audio_control.play_sequence(["C5", "E5", "G5", "C6"], gap="16n", duration="16n",
                            attack=0.005, decay=0.12, sustain=0.0, release=0.25)

# failure — falling C5→G4→Eb4, minor
audio_control.play_sequence(["C5", "G4", "Eb4"], gap="16n", duration="8n",
                            attack=0.005, decay=0.12, sustain=0.0, release=0.25)

# info — two-note C5→G5 ping
audio_control.play_sequence(["C5", "G5"], gap="8n", duration="16n",
                            attack=0.005, decay=0.12, sustain=0.0, release=0.25)

# …or any custom figure
audio_control.play_sequence(
    ["D4", "F#4", "A4", "D5"],   # notes (strings, Hz, or {"midi": N})
    gap="16n",                    # time between note onsets
    duration="16n",               # per-note sustain
    attack=0.005, decay=0.12, sustain=0.0, release=0.25,  # plucky ADSR
)
# Sequences are self-terminating — do not call audio_control.stop() after them.
```

The `attack`/`decay`/`sustain`/`release` kwargs override only the stages you name (the rest keep
the base envelope from `use_tones(...)`).

### Translating descriptive requests into sound

Users describe sounds in plain language ("a pleasant confirmation tone", "an ominous warning").
Compose from these dimensions rather than asking for note names:

| User says…                 | Reach for                                                                                       |
| -------------------------- | ------------------------------------------------------------------------------------------------ |
| pleasant, success, done    | rising major/pentatonic figure (`C5 E5 G5 C6`), plucky envelope (`attack=0.005, sustain=0.0`)   |
| error, failure, ominous    | falling minor figure (`C5 G4 Eb4`), low register, longer `duration`, optionally `instrument="sawtooth"` |
| subtle, soft, ping, notify | one or two short high notes (`C5 G5`), `volume` lowered (e.g. `-16`), short release             |
| urgent, alarm, attention   | fast repeated notes (small `gap` like `"32n"`), `"square"`/`"sawtooth"`, mid-high register      |
| calm, ambient, dreamy      | `"sine"`/`"triangle"`, slow `envelope_attack`/`_release`, `reverb_wet` up (0.4–0.6), pentatonic |
| plucky, percussive         | `attack=0.005, decay=0.1, sustain=0.0` (or `instrument="pluck"`/`"membrane"`)                   |
| retro, chiptune, game-like | `"square"` or `"triangle"`, `filter=False` or high cutoff, tight envelope, no reverb            |

---

## Sonify a ticking table

```python
from deephaven_plugin_tones import table_tones

@ui.component
def market_sounds(ticking_table):
    return ui.flex(
        table_tones(
            ticking_table,        # the table to sonify (first positional arg)
            pitch="Price",        # numeric column to map to pitch
            scale="pentatonic",   # "pentatonic"|"major"|"minor"|"chromatic"
            root="C3",            # lowest note
            octaves=3,            # pitch range spans 3 octaves
            descending=False,     # True = higher value -> lower pitch
            rate_limit_ms=100,    # throttle in ms (default 60)
        ),                        # ← returns an element; inline it like ui.table
        ui.table(ticking_table),  # the visible table — takes all space
        direction="row",
    )
```

`table_tones(...)` returns a bare element — inline it directly in the tree (just like
`ui.table(...)`); there's no handle to assign. It renders no UI of its own and makes sound on each
tick.

**Table-only page gesture:** Because the plugin renders no UI, audio relies on the browser's sticky
user activation — once the user has interacted with the page (opening/clicking the panel counts),
audio unlocks and ticks drive it from then on. In a brand-new tab, one click anywhere in the panel
unlocks audio.

To re-target a different table or column, just re-render `table_tones(...)` with the new
`table`/`pitch=` (e.g. drive them from component state). There is no separate runtime rebind call —
the construction kwargs are live across renders.

---

## Parameter reference

`use_tones(...)` and `table_tones(...)` **share** the synth / effects / value→pitch params below.
`table_tones(...)` adds the table-mode params (and the data-driven overload) in the following
section. Pass only what you need; all have the defaults shown.

```python
# shared synth / effects / mapping config (both entry points)
instrument="sine", polyphony=8,
envelope_attack=0.02, envelope_decay=0.1, envelope_sustain=0.6, envelope_release=1.2,
detune=0, portamento=0,
# effects chain:  PolySynth → Distortion → Filter → Delay → PingPong → Reverb → Panner → master(Limiter) → out
filter=True, filter_type="lowpass", filter_frequency=2200, filter_q=1, filter_rolloff=-24,
reverb=True, reverb_decay=3, reverb_wet=0.3, reverb_predelay=0.01,
delay=False, delay_time="8n", delay_feedback=0.2, delay_wet=0.1,
distortion=False, distortion_amount=0.4, distortion_wet=1.0,
chorus=False, chorus_frequency=1.5, chorus_depth=0.7, chorus_wet=0.5,
ping_pong=False, ping_pong_time="8n", ping_pong_feedback=0.2, ping_pong_wet=0.5,
limiter=True, limiter_threshold=-1,
volume=-8, pan=0,
# value → pitch mapping (used by play_value and by table_tones' pitch column)
scale="pentatonic", root="C3", octaves=3, descending=False,
```

### Instrument / voice

| Param              | Type — default | Description                                                                                                                          |
| ------------------ | -------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `instrument`       | str — `"sine"` | Synth type: `"sine"` `"triangle"` `"square"` `"sawtooth"` `"fm"` `"am"` `"membrane"` `"pluck"` `"monosynth"` `"duosynth"` `"metal"`. |
| `polyphony`        | int — `8`      | Max simultaneous voices.                                                                                                             |
| `envelope_attack`  | float — `0.02` | ADSR attack, seconds (fade-in). `None` keeps default.                                                                                |
| `envelope_decay`   | float — `0.10` | ADSR decay, seconds (fall to sustain).                                                                                               |
| `envelope_sustain` | float — `0.6`  | ADSR sustain **level** (0–1), held while the note is on.                                                                             |
| `envelope_release` | float — `1.2`  | ADSR release, seconds (fade-out after note off).                                                                                     |
| `detune`           | num — `0`      | Global detune in cents (100 = one semitone).                                                                                         |
| `portamento`       | num — `0`      | Glide time in seconds between consecutive notes.                                                                                     |

### Effects chain (`PolySynth → Distortion → Filter → Delay → PingPong → Reverb → Panner → master(Limiter) → out`)

| Param                | Type — default    | Description                                                                                                          |
| -------------------- | ----------------- | -------------------------------------------------------------------------------------------------------------------- |
| `filter`             | bool — `True`     | Enable the filter node (`False` bypasses it).                                                                        |
| `filter_type`        | str — `"lowpass"` | `"lowpass"` `"highpass"` `"bandpass"` `"notch"` `"allpass"` `"peaking"`.                                             |
| `filter_frequency`   | num — `2200`      | Cutoff in Hz.                                                                                                        |
| `filter_q`           | num — `1`         | Resonance / Q.                                                                                                       |
| `filter_rolloff`     | int — `-24`       | Slope, dB/octave: `-12` `-24` `-48` `-96`.                                                                           |
| `reverb`             | bool — `True`     | Enable the reverb node (`False` bypasses).                                                                           |
| `reverb_decay`       | num — `3`         | Tail length, seconds.                                                                                                |
| `reverb_wet`         | num — `0.3`       | Wet/dry mix, 0–1.                                                                                                    |
| `reverb_predelay`    | num — `0.01`      | Pre-delay before the tail, seconds.                                                                                  |
| `delay`              | bool — `False`    | Enable feedback-delay. **Auto-enabled if any `delay_*` is set.**                                                     |
| `delay_time`         | str/num — `"8n"`  | Delay time: Tone.js note string (`"8n"`) or seconds.                                                                 |
| `delay_feedback`     | num — `0.2`       | Feedback amount, 0–~0.9 (higher = more repeats).                                                                     |
| `delay_wet`          | num — `0.1`       | Wet/dry mix, 0–1.                                                                                                    |
| `distortion`         | bool — `False`    | Enable a waveshaper distortion node.                                                                                 |
| `distortion_amount`  | num — `0.4`       | Distortion amount, 0–1 (static).                                                                                     |
| `distortion_wet`     | num — `1.0`       | Wet/dry mix, 0–1.                                                                                                    |
| `chorus`             | bool — `False`    | Enable a stereo chorus node.                                                                                         |
| `chorus_frequency`   | num — `1.5`       | Chorus LFO rate in Hz (static).                                                                                      |
| `chorus_depth`       | num — `0.7`       | Chorus modulation depth, 0–1 (static).                                                                               |
| `chorus_wet`         | num — `0.5`       | Wet/dry mix, 0–1.                                                                                                    |
| `ping_pong`          | bool — `False`    | Enable a stereo ping-pong delay node.                                                                                |
| `ping_pong_time`     | str/num — `"8n"`  | Ping-pong delay time: note string or seconds (static).                                                               |
| `ping_pong_feedback` | num — `0.2`       | Feedback amount, 0–~0.9.                                                                                             |
| `ping_pong_wet`      | num — `0.5`       | Wet/dry mix, 0–1.                                                                                                    |
| `limiter`            | bool — `True`     | Brick-wall limiter on the **master bus** (shared across voices) so loud/wet passages don't clip. `False` removes it. |
| `limiter_threshold`  | num — `-1`        | Limiter ceiling in dBFS.                                                                                             |
| `volume`             | num — `-8`        | Master volume in dB (0 = full, more negative = quieter).                                                             |
| `pan`                | num — `0`         | Stereo position, `-1` (left) … `1` (right).                                                                          |

### Value → pitch mapping

| Param        | Type — default            | Description                                                                                        |
| ------------ | ------------------------- | -------------------------------------------------------------------------------------------------- |
| `scale`      | str/list — `"pentatonic"` | `"pentatonic"` `"major"` `"minor"` `"chromatic"`, or an explicit interval list like `[0,2,4,7,9]`. |
| `root`       | str — `"C3"`              | Lowest note of the pitch range (bottom of the mapping).                                            |
| `octaves`    | int — `3`                 | Number of octaves the value→pitch range spans.                                                     |
| `descending` | bool — `False`            | `True` maps higher values to **lower** pitches.                                                    |

---

## `table_tones(...)` — table-mode params

These are **`table_tones`-only** (a manual `use_tones` handle has no table to drive). `table` is the
first positional argument.

**The numeric-param overload.** Inside `table_tones`, every numeric effect param marked **DD** below
accepts three forms:

| Form              | Meaning                                                                                                         |
| ----------------- | --------------------------------------------------------------------------------------------------------------- |
| `0.3` (number)    | static value                                                                                                    |
| `"Vol"` (str)     | data-driven — auto-tracks the column's running min/max as input, maps into the param's **default** output range |
| `("Vol", lo, hi)` | data-driven with an explicit **output** range `lo..hi`                                                          |

Only live Tone signals can be data-driven: `detune`, `filter_frequency`, `filter_q`, `reverb_wet`,
`delay_feedback`, `delay_wet`, `pan`, `distortion_wet`, `chorus_wet`, `ping_pong_wet`,
`ping_pong_feedback`. Everything else is static (can't change per-note without
rebuilding the graph). For `pitch`/`loudness` the tuple instead clamps the **input** domain (they
map into the musical scale / dynamics, not a raw param). To vary timbre _categorically_ (swap
instrument per row), use `voice` + `voices`.

### Table mode — shared (all table modes)

| Param           | Type — default                   | Description                                                                                                     |
| --------------- | -------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `table`         | Table — _(required, positional)_ | A Deephaven `Table` to auto-sonify on tick.                                                                     |
| `mode`          | str — `"last"`                   | `"last"` = one sound per tick (most-recent row); `"all"` = every new row that tick. Blink tables auto-detected. |
| `rate_limit_ms` | int — `60`                       | Client-side throttle (ms) for table-tick sonification.                                                          |

### Table mode — value → pitch sonify

`pitch` alone is a one-dimensional sonify ("turn this column into sound"). Add `loudness`/`voice` for a multi-dimensional "duet". Note that `loudness`, `voice`, and `voices` only take effect **alongside `pitch`** — with no `pitch` column no value→pitch mappings are emitted and they are silently ignored.

| Param           | Type — default       | Description                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| --------------- | -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pitch`         | str / tuple — `None` | Numeric column → pitch (scale-quantised). `"Col"` (auto input range) or `("Col", lo, hi)` to clamp the input domain.                                                                                                                                                                                                                                                                                                                                                                                                                |
| `loudness`      | str / tuple — `None` | Numeric column → loudness **and** note length (bigger = louder + longer). Tuple clamps input.                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `voice`         | str — `None`         | Categorical column whose cell value selects the per-row instrument/voice (looked up in `voices`).                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `voices`        | dict — `None`        | Map of `voice` cell value → **flat** override dict, e.g. `{"BUY": {"instrument": "pluck"}, "SELL": {"instrument": "sawtooth", "envelope_attack": 0.01}}`. Override keys mirror the flat param names (`instrument`, `polyphony`, `envelope_attack`/`_decay`/`_sustain`/`_release`, `detune`, `portamento`, `volume`, `pan`, and `filter`/`reverb`/`delay`/`distortion`/`chorus`/`ping_pong` on-off toggles). Any stage you don't name keeps the base value; unmatched cell values fall back to the base config (or `voice_default`). |
| `voice_default` | dict — `None`        | A flat override dict (same shape as a `voices` entry) applied to rows whose `voice` cell matches no key in `voices`. Defaults to the base config when unset. Only used alongside `voice`.                                                                                                                                                                                                                                                                                                                                           |

### Table mode — chord trigger

| Param                | Type — default | Description                                                                                                                                                                                                                         |
| -------------------- | -------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `chord_column`       | str — `None`   | Trigger column: on each new truthy row (bool / non-zero / non-empty), play `chords` as a progression. Other rows stay silent. Use `mode="all"`.                                                                                     |
| `chords`             | list — `None`  | Progression: list of chords, each a list of note names, e.g. `[["C4","E4","G4"], ["G3","B3","D4"]]`. Default: I-V-vi-IV in C.                                                                                                       |
| `chord_gap`          | str — `"4n"`   | Onset spacing between chords (Tone.js time).                                                                                                                                                                                        |
| `chord_duration`     | str — `"2n"`   | Per-chord length (Tone.js time).                                                                                                                                                                                                    |
| `chord_notes_column` | str — `None`   | Per-row column supplying the chord(s): a `String[]` (one chord), a `String` like `"C4,E4,G4 \| G3,B3,D4"` (chords split on `\|`/`;`), or `String[][]`. Overrides `chords`. Acts as the trigger itself if `chord_column` is omitted. |

### Table mode — sequence trigger (the `play_sequence` analogue)

| Param                   | Type — default | Description                                                                                                                                                          |
| ----------------------- | -------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `sequence_column`       | str — `None`   | Trigger column: on each new truthy row, play `sequence_notes` as a timed melody/arpeggio. Use `mode="all"`. Combinable with `chord_column`.                          |
| `sequence_notes`        | seq — `None`   | Melody — same note forms as `play_sequence` (`"C5"`, `("C5","8n")`, `("C5","8n",0.8)`). Default: ascending C arpeggio.                                               |
| `sequence_gap`          | str — `"16n"`  | Onset spacing between notes (Tone.js time).                                                                                                                          |
| `sequence_notes_column` | str — `None`   | Per-row column supplying the melody: a `String[]` or `String` like `"C5 E5 G5 C6"`. Overrides `sequence_notes`. Acts as the trigger if `sequence_column` is omitted. |

---

## `audio_control` — full method cheat-sheet

`audio_control` is the second element of the `use_tones()` tuple.

```python
audio, audio_control = use_tones()

# Play a single note
audio_control.play("C4")
audio_control.play(440.0)               # Hz
audio_control.play({"midi": 60})        # MIDI
audio_control.play("C4", duration="4n", velocity=0.8)

# Play multiple notes at once
audio_control.play_chord(["C4", "E4", "G4"])
audio_control.play_chord(["C4", "E4", "G4"], duration="4n")

# Map a numeric value to a pitch, then play
audio_control.play_value(42.5)
audio_control.play_value(price, scale="minor", descending=True)

# Play a timed arpeggio / melody (self-terminating)
audio_control.play_sequence(["C4", "E4", "G4", "C5"])
audio_control.play_sequence([("C4","8n"), ("E4","16n")], gap="16n")
# Earcons = short play_sequence calls (no preset methods); e.g. a success chime
# with a plucky ADSR via the flat attack/decay/sustain/release kwargs:
audio_control.play_sequence(["C5","E5","G5","C6"], gap="16n", duration="16n",
                            attack=0.005, decay=0.12, sustain=0.0, release=0.25)

# Control
audio_control.stop()              # stop all sounds immediately
audio_control.set_volume(-12)     # live volume change (dB)
```

---

## Note and duration formats

`note` accepts: `"C4"` (name), `440.0` (Hz), `{"midi": 60}` (MIDI).

A sequence note item is a bare note value, or a tuple to attach timing: `("C5", "8n")` (note +
duration) or `("C5", "8n", 0.8)` (note + duration + velocity).

`duration` accepts Tone.js note values: `"16n"` `"8n"` `"4n"` `"2n"` `"1n"` (dotted: `"4n."`,
triplet: `"8t"`), or a number of seconds.

---

## Install

Install from PyPI into the Python environment that runs the Deephaven server, then restart it
(the plugin registers itself automatically):

```sh
pip install deephaven-plugin-tones
```

Plugin import:

```python
from deephaven_plugin_tones import use_tones, table_tones
```

---

## Key facts to remember

- `use_tones()` is a **render hook** — call it unconditionally at the top of `@ui.component`. It
  returns `(audio, audio_control)`: place `audio` in the tree, call `audio_control` methods.
- `table_tones(...)` is a **declarative element** — inline it in the tree like `ui.table(...)`. It
  returns the element directly (no tuple, no handle); it auto-sonifies the table on each tick.
- **One `AudioContext` per tab**: all tones elements share the same engine. Different
  instruments each get their own voice chain and can play simultaneously.
- **User gesture required**: audio unlocks on the browser's sticky user activation (any in-page
  interaction). The plugin renders no UI — table-only pages rely on the user having clicked the
  panel; one click anywhere unlocks audio in a fresh tab.
- Event queue is capped at 64 entries. `audio_control` methods are safe to call from background
  threads.
- **Unknown column names raise `ValueError` at render time** (listing the table's available
  columns) — column typos surface as a server-side render error, not a silent browser failure.
- **Sequences are self-terminating** — do not call `stop()` after a `play_sequence` (earcon).

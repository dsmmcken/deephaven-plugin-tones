---
name: deephaven-plugin-tones
description: >-
  Add sound to a Deephaven Web IDE panel (@ui.component): button-click sounds,
  success/failure earcons, jingles, or live musical sonification of a ticking
  Deephaven table. Wraps Tone.js behind two Python entry points — tones (an
  object you call directly to play notes, chords, melodies and progressions) and
  use_table_tones_listener() (a hook that sonifies a ticking table). Use this
  skill whenever the user is building a deephaven.ui / @ui.component panel and
  mentions audio, sound, sonification, earcons, audio feedback, beeps, audible
  alerts, playing notes/chords/melodies, or turning table data or ticks into
  sound — even if they never name the plugin. Also use it whenever editing code
  that imports deephaven_plugin_tones or calls tones.* /
  use_table_tones_listener(...).
---

# deephaven-plugin-tones

This plugin wraps Tone.js and gives a Deephaven Web IDE panel (`@ui.component`) two Python entry
points for sound:

- **`tones`** — an object you import and call directly for **manual triggers** (button-click
  sounds, earcons, jingles). No hook to call, nothing to mount.
- **`use_table_tones_listener(...)`** — a **render hook** that **sonifies a ticking table**. Call it
  at the top of a component; it renders nothing and makes sound as rows arrive.

Each trigger sends one event to the browser, where the plugin plays it. Sounds are
self-terminating — there is no stop.

---

## The minimal pattern (manual triggers)

```python
from deephaven import ui
from deephaven_plugin_tones import tones

@ui.component
def my_panel():
    return ui.button("Play", on_press=lambda _e: tones.play("C4"))

panel = my_panel()
```

Two rules:

1. **Call `tones` from the render thread** — while a component renders, or from a handler it
   triggered (`on_press`, `on_change`, …). That is the normal case and needs no ceremony.
2. **From a background thread** (a table listener, a worker), queue it with `ui.use_render_queue`.
   Calling `tones` directly off the render thread raises `TonesError`.

```python
@ui.component
def alerting(table):
    render_queue = ui.use_render_queue()

    def on_update(update, is_replay):
        render_queue(lambda: tones.play("C5"))   # listener runs off the render thread

    ui.use_table_listener(table, on_update, [table])
    return ui.table(table)
```

---

## Trigger from a button

```python
@ui.component
def audio_buttons():
    return ui.flex(
        ui.button("C", on_press=lambda _e: tones.play("C4")),
        ui.button("Chord", on_press=lambda _e: tones.play_chord(["C4", "E4", "G4"])),
        # "earcons" are just short play_sequence calls (see next section)
        ui.button("OK", on_press=lambda _e: tones.play_sequence(
            ["C5", "E5", "G5", "C6"], duration="16n",
            attack=0.005, decay=0.12, sustain=0.0, release=0.25)),
        direction="row",
    )
```

**Gesture gotcha:** The browser suspends its `AudioContext` until the user performs a real click.
The **first button press** satisfies this requirement — you may not hear sound on that very first
press. The second press onwards always works. There is nothing you need to code; it is automatic.

---

## Per-call sound options, and named voices

Every sound option (`instrument`, `reverb_wet`, `volume`, … — the full list is in
[Parameter reference](#parameter-reference)) can be passed to **any** trigger, for that call only:

```python
tones.play("C4", instrument="pluck", reverb_wet=0.5)
```

For a voice you use repeatedly, make a configured copy once and call it like `tones`:

```python
bell = tones.configure(instrument="metal", envelope_decay=1.2, envelope_sustain=0.0, volume=-14)
bass = tones.configure(instrument="fm", root="C1", volume=-4)

ui.button("Ding", on_press=lambda _e: bell.play("C6"))
```

`configure()` returns a new object; the original `tones` is unchanged. A misspelled option name
raises `ValueError` listing the valid ones.

---

## Make a pleasant earcon

There are no preset earcon methods — an earcon is just a short `play_sequence` with a plucky
envelope (set via the flat `attack`/`decay`/`sustain`/`release` kwargs), so you compose whatever
fits. The classic three:

```python
# success — rising C5→E5→G5→C6, bright
tones.play_sequence(["C5", "E5", "G5", "C6"], duration="16n",
                    attack=0.005, decay=0.12, sustain=0.0, release=0.25)

# failure — falling C5→G4→Eb4, minor
tones.play_sequence(["C5", "G4", "Eb4"], duration="8n",
                    attack=0.005, decay=0.12, sustain=0.0, release=0.25)

# info — two-note C5→G5 ping
tones.play_sequence(["C5", "G5"], duration="16n",
                    attack=0.005, decay=0.12, sustain=0.0, release=0.25)
```

The `attack`/`decay`/`sustain`/`release` kwargs override only the stages you name (the rest keep the
base envelope).

### Translating descriptive requests into sound

Users describe sounds in plain language ("a pleasant confirmation tone", "an ominous warning").
Compose from these dimensions rather than asking for note names:

| User says…                 | Reach for                                                                                               |
| -------------------------- | ------------------------------------------------------------------------------------------------------- |
| pleasant, success, done    | rising major/pentatonic figure (`C5 E5 G5 C6`), plucky envelope (`attack=0.005, sustain=0.0`)           |
| error, failure, ominous    | falling minor figure (`C5 G4 Eb4`), low register, longer `duration`, optionally `instrument="sawtooth"` |
| subtle, soft, ping, notify | one or two short high notes (`C5 G5`), `volume` lowered (e.g. `-16`), short release                     |
| urgent, alarm, attention   | fast repeated notes (short `duration` like `"32n"`), `"square"`/`"sawtooth"`, mid-high register         |
| calm, ambient, dreamy      | `"sine"`/`"triangle"`, slow `envelope_attack`/`_release`, `reverb_wet` up (0.4–0.6), pentatonic         |
| plucky, percussive         | `attack=0.005, decay=0.1, sustain=0.0` (or `instrument="pluck"`/`"membrane"`)                           |
| retro, chiptune, game-like | `"square"` or `"triangle"`, `filter=False` or high cutoff, tight envelope, no reverb                    |

---

## Melodies and jingles

In `play_sequence`, **each note lasts its own `duration`**, and the next one starts when it ends —
so the note list carries the rhythm. `gap` adds extra silence after every note (articulation); it
defaults to `0`, i.e. legato.

A sequence item is one of:

| Item                          | Meaning                                                 |
| ----------------------------- | ------------------------------------------------------- |
| `"E4"`                        | a note, at the call's default `duration`                |
| `("E4", 0.4)`                 | that note, held 0.4 s (or a note value like `"8n"`)     |
| `("E4", 0.4, 0.7)`            | …with a velocity of 0.7                                 |
| `None` / `(None, 0.25)`       | a **rest** — silent, but it still takes up its duration |
| `["E4", "G#4", "B4"]`         | a **chord** (a list) — its notes sound together         |
| `(["E4", "G#4", "B4"], 0.35)` | a chord with its own length                             |

That is enough for a real tune:

```python
# Beethoven's fate motif: three shorts and a long, twice.
tones.play_sequence(
    [
        ("G4", 0.15), ("G4", 0.15), ("G4", 0.15), ("Eb4", 0.8),
        (None, 0.25),
        ("F4", 0.15), ("F4", 0.15), ("F4", 0.15), ("D4", 1.0),
    ],
    gap=0.03,
    instrument="triangle",
)

# An outro sting: one ringing chord, a pause, then four hits.
_E = ["E4", "G#4", "B4", "E5"]
tones.play_sequence(
    [(_E, 0.35), (None, 0.55), (_E, 0.12), (_E, 0.12), (_E, 0.12), (_E, 0.5)],
    gap=0.06, instrument="sawtooth",
)
```

For a plain chord **progression** (evenly spaced chords, no melody), `play_chords` is simpler — it
spaces the chords by `gap` and rings each for `duration`:

```python
tones.play_chords([["C4","E4","G4"], ["G3","B3","D4"], ["A3","C4","E4"], ["F3","A3","C4"]])
```

---

## Sonify a ticking table

```python
from deephaven_plugin_tones import use_table_tones_listener

@ui.component
def market_sounds(ticking_table):
    use_table_tones_listener(
        ticking_table,        # the table to sonify (first positional arg)
        pitch="Price",        # numeric column to map to pitch
        scale="pentatonic",   # "pentatonic"|"major"|"minor"|"chromatic"
        root="C3",            # lowest note
        octaves=3,            # pitch range spans 3 octaves
        descending=False,     # True = higher value -> lower pitch
        rate_limit_ms=100,    # min ms between sounds in mode="last" (default 60)
    )
    return ui.table(ticking_table)   # the visible table — takes all the space
```

The hook listens to the table on the **server** and sends one event per sounding row. It renders
nothing, so call it at the top of the component like any hook (unconditionally, not inside an `if`).

Only **added** rows sonify — existing history is never replayed, and an in-place modification is not
a new event.

**Auto-ranging:** with `pitch="Price"` (no explicit range) the plugin attaches live min/max columns
to the table (an aggregation joined back onto every row), so the very first row already scales
against the table's true range. Use `pitch=("Price", 0, 100)` to fix the input domain instead.

**Table-only page gesture:** Because the plugin renders no UI, audio relies on the browser's sticky
user activation — once the user has interacted with the page (opening/clicking the panel counts),
audio unlocks and ticks drive it from then on. In a brand-new tab, one click anywhere in the panel
unlocks audio.

To re-target a different table or column, re-render with new arguments (e.g. drive them from
component state); the listener is recreated when they change.

---

## Parameter reference

The **sound options** below are shared: they are the arguments of `tones.configure(...)`, may be
passed to any `tones` trigger for one call, and are also arguments of `use_table_tones_listener`.
Pass only what you need; all have the defaults shown.

```python
# sound options (per-call on any trigger, on configure(), and on the table hook)
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
# value → pitch mapping (used by play_value and by the table hook's pitch column)
scale="pentatonic", root="C3", octaves=3, value_range=None, descending=False,
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

| Param         | Type — default            | Description                                                                                        |
| ------------- | ------------------------- | -------------------------------------------------------------------------------------------------- |
| `scale`       | str/list — `"pentatonic"` | `"pentatonic"` `"major"` `"minor"` `"chromatic"`, or an explicit interval list like `[0,2,4,7,9]`. |
| `root`        | str — `"C3"`              | Lowest note of the pitch range (bottom of the mapping).                                            |
| `octaves`     | int — `3`                 | Number of octaves the value→pitch range spans.                                                     |
| `value_range` | list — `None`             | `[lo, hi]` input domain for `play_value`. `None` scales against the values seen so far.            |
| `descending`  | bool — `False`            | `True` maps higher values to **lower** pitches.                                                    |

---

## `use_table_tones_listener(...)` — table-mode params

These are **table-only** (a manual `tones` call has no table to read). `table` is the first
positional argument.

**The numeric-param overload.** In the table hook, every numeric effect param marked **DD** below
accepts three forms:

| Form              | Meaning                                                                                                |
| ----------------- | ------------------------------------------------------------------------------------------------------ |
| `0.3` (number)    | static value                                                                                           |
| `"Vol"` (str)     | data-driven — the column's live min/max is the input, mapped into the param's **default** output range |
| `("Vol", lo, hi)` | data-driven with an explicit **output** range `lo..hi`                                                 |

Only live Tone signals can be data-driven (**DD**): `detune`, `filter_frequency`, `filter_q`,
`reverb_wet`, `delay_feedback`, `delay_wet`, `pan`, `distortion_wet`, `chorus_wet`,
`ping_pong_wet`, `ping_pong_feedback`. Everything else is static (it can't change per-note without
rebuilding the graph). For `pitch`/`loudness` the tuple instead clamps the **input** domain (they
map into the musical scale / dynamics, not a raw param). To vary timbre _categorically_ (swap
instrument per row), use `voice` + `voices`.

### Table mode — shared (all table modes)

| Param           | Type — default                   | Description                                                                                           |
| --------------- | -------------------------------- | ----------------------------------------------------------------------------------------------------- |
| `table`         | Table — _(required, positional)_ | A Deephaven `Table` to sonify on tick.                                                                |
| `mode`          | str — `"last"`                   | `"last"` = one sound per tick (newest row); `"all"` = every row the tick added (newest 32 at most).   |
| `rate_limit_ms` | int — `60`                       | Minimum ms between sounds in `"last"` mode; ticks that arrive sooner are dropped. Ignored by `"all"`. |

### Table mode — value → pitch sonify

`pitch` alone is a one-dimensional sonify ("turn this column into sound"). Add `loudness`/`voice`
for a multi-dimensional "duet". Note that `loudness`, `voice`, and `voices` only take effect
**alongside `pitch`** — with no `pitch` column no value→pitch mappings are emitted and they are
silently ignored.

| Param           | Type — default       | Description                                                                                                                                                                                                                                                                                                                                                                                          |
| --------------- | -------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pitch`         | str / tuple — `None` | Numeric column → pitch (scale-quantised). `"Col"` (live auto range) or `("Col", lo, hi)` to clamp the input domain.                                                                                                                                                                                                                                                                                  |
| `loudness`      | str / tuple — `None` | Numeric column → loudness **and** note length (bigger = louder + longer). Tuple clamps input.                                                                                                                                                                                                                                                                                                        |
| `voice`         | str — `None`         | Categorical column whose cell value selects the per-row instrument/voice (looked up in `voices`).                                                                                                                                                                                                                                                                                                    |
| `voices`        | dict — `None`        | Map of `voice` cell value → **flat** override dict, e.g. `{"BUY": {"instrument": "pluck"}, "SELL": {"instrument": "sawtooth", "envelope_attack": 0.01}}`. Override keys mirror the flat param names (`instrument`, `polyphony`, `envelope_attack`/`_decay`/`_sustain`/`_release`, `detune`, `portamento`, `volume`, `pan`, and `filter`/`reverb`/`delay`/`distortion`/`chorus`/`ping_pong` toggles). |
| `voice_default` | dict — `None`        | A flat override dict (same shape as a `voices` entry) applied to rows whose `voice` cell matches no key in `voices`. Defaults to the base config when unset. Only used alongside `voice`.                                                                                                                                                                                                            |

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
| `sequence_gap`          | num — `0`      | Extra silence after each note (Tone.js time); the notes' own durations set the rhythm.                                                                               |
| `sequence_notes_column` | str — `None`   | Per-row column supplying the melody: a `String[]` or `String` like `"C5 E5 G5 C6"`. Overrides `sequence_notes`. Acts as the trigger if `sequence_column` is omitted. |

---

## `tones` — full method cheat-sheet

```python
from deephaven_plugin_tones import tones

# Play a single note
tones.play("C4")
tones.play(440.0)                       # Hz
tones.play({"midi": 60})                # MIDI
tones.play("C4", duration="4n", velocity=0.8)

# Play multiple notes at once
tones.play_chord(["C4", "E4", "G4"])
tones.play_chord(["C4", "E4", "G4"], duration="4n")

# Play a chord progression (evenly spaced)
tones.play_chords([["C4","E4","G4"], ["G3","B3","D4"]], gap="4n", duration="2n")

# Play a melody / arpeggio — each note lasts its own duration
tones.play_sequence(["C4", "E4", "G4", "C5"])
tones.play_sequence([("C4","8n"), ("E4","16n"), (None, 0.2), (["C4","E4"], 0.5)])
# Earcons = short play_sequence calls (no preset methods); a success chime with a
# plucky ADSR via the flat attack/decay/sustain/release kwargs:
tones.play_sequence(["C5","E5","G5","C6"], duration="16n",
                    attack=0.005, decay=0.12, sustain=0.0, release=0.25)

# Map a numeric value to a pitch, then play
tones.play_value(42.5)
tones.play_value(price, scale="minor", value_range=[0, 100], descending=True)

# Any sound option, for one call…
tones.play("C4", instrument="pluck", volume=-14)
# …or kept as a named voice
bell = tones.configure(instrument="metal", volume=-14)
bell.play("C6")
```

---

## Note and duration formats

`note` accepts: `"C4"` (name), `440.0` (Hz), `{"midi": 60}` (MIDI).

In a sequence, an item is a bare note value, a **list** of them (a chord), `None` (a rest), or a
tuple attaching timing to any of those: `("C5", "8n")` (+ duration) or `("C5", "8n", 0.8)`
(+ velocity).

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
from deephaven_plugin_tones import tones, use_table_tones_listener
```

---

## Key facts to remember

- `tones` is **not a hook** — import it and call it. There is no element to place in the tree and no
  handle to thread through the component.
- Call it **on the render thread** (during render or from a handler). From a background thread, wrap
  the call in `ui.use_render_queue()`; otherwise it raises `TonesError`.
- `use_table_tones_listener(...)` **is a hook** — call it unconditionally at the top of a
  `@ui.component`. It renders nothing and returns nothing.
- **Sounds are self-terminating.** There is no `stop()` and no `set_volume()`; use `volume=` (per
  call or via `configure()`) to set loudness.
- **One `AudioContext` per tab**: all events share the same engine. Different instruments each get
  their own voice chain and can play simultaneously.
- **User gesture required**: audio unlocks on the browser's sticky user activation (any in-page
  interaction). Table-only pages rely on the user having clicked the panel; one click anywhere
  unlocks audio in a fresh tab.
- **Only added rows sonify** — history is not replayed, and in-place modifications are not events.
- **Unknown column names raise `ValueError` at render time** (listing the table's available
  columns) — column typos surface as a server-side render error, not a silent browser failure.
- **A misspelled sound option raises `ValueError`** listing the valid options.

# deephaven_plugin_tones

`deephaven_plugin_tones` adds musical audio feedback to
[deephaven.ui](https://deephaven.io/community/oss/ui/) panels in the
Deephaven Web IDE. It wraps [Tone.js](https://tonejs.github.io/) behind two Python entry points:

- `tones` is an object you import and call directly — `tones.play("C4")`,
  `play_chord`, `play_chords`, `play_sequence`, `play_value`. There is no hook to call and nothing
  to mount; each call sends an event that the browser plays. Sounds are self-terminating.
- `use_table_tones_listener(...)` is a render hook that sonifies a ticking table: map a column to
  pitch (and optionally loudness or voice), or fire chord and melody triggers per row. The server
  auto-tracks each column's live min/max range and turns each new row into sound.

> [!NOTE]
> Browsers block audio until you interact with the page. Click anywhere in the browser tab
> (opening or clicking the panel counts) before you will hear sound; until then triggers play
> silently. Most users simply using the UI will unlock audio without any extra clicks.

## Install

Install from PyPI into the Python environment that runs your Deephaven server, then restart the
server:

```sh
pip install deephaven-plugin-tones
```

The plugin registers itself automatically. Once the server is back up, `tones` and
`use_table_tones_listener` are importable in any `deephaven.ui` panel.

## Use it

```python
from deephaven import ui
from deephaven_plugin_tones import tones

@ui.component
def my_panel():
    return ui.flex(
        ui.button("C", on_press=lambda _e: tones.play("C4")),
        ui.button("Chord", on_press=lambda _e: tones.play_chord(["C4", "E4", "G4"])),
        direction="row",
    )

my_panel = my_panel()                    # a top-level variable becomes an openable panel
```

Call `tones` from the render thread — while a component renders, or from a handler it triggered.
From a background thread (a table listener, a worker), queue it with `ui.use_render_queue`. The
browser unlocks audio on the first user interaction with the panel, so the very first button press
may be silent; every press after that plays immediately.

Any sound option can ride along with a single call, or be kept as a named voice:

```python
tones.play("C4", instrument="pluck", reverb_wet=0.5)

bell = tones.configure(instrument="metal", volume=-14)
bell.play("C6")
```

Sonify a ticking table with the `use_table_tones_listener(...)` hook:

```python
from deephaven_plugin_tones import use_table_tones_listener

@ui.component
def market_sounds(prices):
    use_table_tones_listener(prices, pitch="Price", scale="pentatonic", root="C3", octaves=3)
    return ui.table(prices)
```

There are no preset "earcon" methods; a success or error chime is just a short `play_sequence`
with a plucky envelope. In a sequence each note lasts its own duration, a list is a chord and
`None` is a rest — enough to play a real tune. See
[SKILL.md](./skills/deephaven-plugin-tones/SKILL.md) for the full API: every sound option, the
trigger methods, table-sonification modes, data-driven effect params, multi-dimensional "duet"
mode, and the gotchas.

## Let an AI write the sound design

This repo ships [SKILL.md](./skills/deephaven-plugin-tones/SKILL.md), an agent skill that teaches
AI coding assistants (Claude Code, etc.) the full plugin API: every parameter, the trigger methods,
table-sonification modes, and the gotchas. Install it into your own project with
[skills.sh](https://skills.sh):

```sh
npx skills add dsmmcken/deephaven-plugin-tones
```

With the skill loaded, the best way to prompt is to describe the sound you want rather than the
API calls. The plugin has no preset chimes; you build every earcon from notes, envelopes, and
timing, and a descriptive prompt lets the model do that composition for you:

- _"Play a pleasant confirmation tone when the export finishes, and a low ominous buzz if it
  fails."_
- _"Add a soft two-note ping whenever a new row matches the filter."_
- _"Sonify the `Price` column on a pentatonic scale, calm and ambient, upticks should sound
  higher."_
- _"Make the buy and sell sides sound like different instruments."_

## Examples

The [`examples/`](./examples) directory has ready-to-run panels:

| Script                                                  | Demonstrates                                            |
| ------------------------------------------------------- | ------------------------------------------------------- |
| `buttons_demo.py`                                       | Button-driven `play` / `play_chord` / earcons           |
| `jingles_jukebox.py`                                    | Tunes built from durations, chords and rests            |
| `table_tones_demo.py`                                   | Value-to-pitch table sonification (`pitch=`)            |
| `table_blink_tick.py`                                   | Blink-table tick sonification (`mode="last"` / `"all"`) |
| `table_multi_tones.py`                                  | Multi-dimensional "duet" (pitch + loudness + voice)     |
| `table_chord_trigger.py` / `table_chord_progression.py` | Chord triggers + per-row chord(s) from a table cell     |
| `table_chords_from_cell.py`                             | Whole chord progression stored in a single cell         |
| `table_sequence_trigger.py` / `table_twinkle.py`        | Melodic sequence triggers                               |
| `table_melody_from_cell.py`                             | Per-row melody from a table cell                        |
| `table_fx_showcase.py` / `table_service_health.py`      | Data-driven effect params                               |

## Documentation

- [SKILL.md](./skills/deephaven-plugin-tones/SKILL.md) is the complete, copy-pasteable API
  reference and patterns, also consumable as an agent skill by AI coding assistants.
- [AGENTS.md](./AGENTS.md) is for contributors: project layout, build/test/lint workflow
  (including how to build and run from source).

# deephaven_plugin_tones

`deephaven_plugin_tones` adds musical audio feedback to
[deephaven.ui](https://deephaven.io/community/oss/ui/) panels in the
Deephaven Web IDE. It wraps [Tone.js](https://tonejs.github.io/) behind two Python entry points:

- `use_tones(...)` is a render hook for manual triggers. It returns `(audio, audio_control)`:
  place `audio` in the tree (it renders no visible DOM and takes no layout space), then call
  `audio_control` methods (`play`, `play_chord`, `play_sequence`, `play_value`, `stop`,
  `set_volume`) from any event handler or background thread.
- `table_tones(...)` is a declarative element that sonifies a ticking table: map a column
  to pitch (and optionally loudness or voice), or fire chord and melody triggers per row. The server
  auto-tracks each column's live min/max range. Drop it in the tree like `ui.table(...)`.

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

The plugin registers itself automatically. Once the server is back up, `use_tones` and
`table_tones` are importable in any `deephaven.ui` panel.

## Use it

```python
from deephaven import ui
from deephaven_plugin_tones import use_tones

@ui.component
def my_panel():
    audio, audio_control = use_tones()   # 1. call at the top, like any render hook
    return ui.flex(
        audio,                           # 2. place audio in the tree (invisible, no layout space)
        ui.button("C", on_press=lambda _e: audio_control.play("C4")),  # 3. trigger from a handler
        ui.button("Stop", on_press=lambda _e: audio_control.stop()),
        direction="row",
    )

my_panel = my_panel()                    # a top-level variable becomes an openable panel
```

`use_tones()` returns the `audio` element to place in the tree (it mounts the audio engine without
affecting layout) and the `audio_control` handle you call from any handler. The browser unlocks
audio on the first user interaction with the panel, so the very first button press may be silent;
every press after that plays immediately.

Sonify a ticking table with `table_tones(...)`, a declarative element you inline like
`ui.table(...)`:

```python
from deephaven_plugin_tones import table_tones

@ui.component
def market_sounds(prices):
    return ui.flex(
        table_tones(prices, pitch="Price", scale="pentatonic", root="C3", octaves=3),
        ui.table(prices),
        direction="row",
    )
```

There are no preset "earcon" methods; a success or error chime is just a short `play_sequence`
with a plucky envelope. See [SKILL.md](./skills/deephaven-plugin-tones/SKILL.md) for the full API:
every `use_tones()` / `table_tones()` parameter, the data-driven effect params, multi-dimensional
"duet" mode, chord/sequence triggers, and the method cheat-sheet.

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
| `buttons_demo.py`                                       | Button-driven `play` / `play_chord` / earcons / `stop`  |
| `table_tones_demo.py`                                   | Value-to-pitch table sonification (`pitch=`)            |
| `table_blink_tick.py`                                   | BLINK-table tick sonification (`mode="last"` / `"all"`) |
| `table_multi_tones.py`                                  | Multi-dimensional "duet" (pitch + loudness + voice)     |
| `table_chord_trigger.py` / `table_chord_progression.py` | Chord triggers + per-row chord(s) from a table cell     |
| `table_chords_from_cell.py`                             | Whole chord progression stored in a single cell         |
| `table_sequence_trigger.py` / `table_twinkle.py`        | Melodic sequence triggers                               |
| `table_melody_from_cell.py`                             | Per-row melody from a table cell                        |

## Documentation

- [SKILL.md](./skills/deephaven-plugin-tones/SKILL.md) is the complete, copy-pasteable API
  reference and patterns, also consumable as an agent skill by AI coding assistants.
- [AGENTS.md](./AGENTS.md) is for contributors: project layout, build/test/lint workflow
  (including how to build and run from source).

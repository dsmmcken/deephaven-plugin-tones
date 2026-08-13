# Developing deephaven_plugin_tones

Contributor guide: project layout, and how to build, run, test, and lint. For what the plugin
_does_ and how to _use_ it, see [README.md](./README.md) and [SKILL.md](./skills/deephaven-plugin-tones/SKILL.md).

## Run / dev loop

One command builds the JS, (re)installs the wheel, and starts a Deephaven server with the repo
mounted as the data directory (PSK `iris`):

```sh
uv sync && uv run plugin_builder.py --dev
```

`--dev` is sugar for `--reinstall --js --server`. Useful variations:

| Command                                                   | When                                     |
| --------------------------------------------------------- | ---------------------------------------- |
| `uv run plugin_builder.py --reinstall --js`               | rebuild JS + reinstall (no version bump) |
| `uv run plugin_builder.py --reinstall`                    | Python-only change (skip JS build)       |
| `uv run plugin_builder.py --dev --watch`                  | rebuild + restart on file changes        |
| `uv run plugin_builder.py --dev --server-arg --port=9999` | pass args through to the server          |

`--data-dir <path>` mounts a different directory as the Deephaven data dir (default: repo root, so
`storage/notebooks` shows up in the Web IDE). `storage/` is git-ignored and regenerated from
`examples/` on each server launch — **edit `examples/`, not `storage/`**.

## Project layout

```
src/deephaven_plugin_tones/        Python package
  __init__.py                      public API: exports `tones`,
                                   `use_table_tones_listener`, `Tones`, `TonesError`
  tones.py                         the trigger API: the `Tones` class (play / play_chord /
                                   play_chords / play_sequence / play_value, per-call sound
                                   overrides, `configure()`) and the module-level `tones`
                                   instance. Each call sends one
                                   `deephaven_plugin_tones.event` via `use_send_event`.
  table_tones.py                   `use_table_tones_listener(...)`: use_table_listener +
                                   use_render_queue + use_send_event. Turns each added row
                                   into events — value→pitch, loudness→velocity/duration,
                                   voice overrides, data-driven effect params, chord and
                                   sequence triggers. Row→event helpers are module-level.
  _config.py                       shared pure helpers + public type aliases: the flat →
                                   nested `ToneConfig` builder, param/pitch resolution,
                                   note normalisation, validation, and the server-side
                                   min/max range augmentation. No engine imports, so the
                                   unit tests run without a server.
  register.py                      registers the plugin + bundled JS with Deephaven
  _js/dist/index.js                built JS bundle (git-ignored; produced by setup.py)

src/js/src/                        TypeScript source
  index.ts                         plugin entry point
  DeephavenPluginTonesPlugin.ts    registration: no element `mapping`, one `eventMapping`
                                   entry whose key must match `TONES_EVENT` in _config.py
  ToneEngine.ts                    Tone.js wrapper: one cached AudioContext/chain per
                                   instrument+fx combo; play/chord/chordSequence/sequence/
                                   value/tone ops, plus the `handleToneEvent` entry point

plugin_builder.py                  build/install/run CLI (uv build + uv pip install + server)
setup.py                           packages src/js/dist into the wheel via package_js()
examples/                          runnable panels (canonical; copied into storage/notebooks)
tests/                             see Testing below
```

The Python side never loads the JS. Every trigger sends one JSON event — `{op, config, …}` — and
the JS handler plays it; nothing persists on the client between events. Keep that payload contract
in sync across `tones.py` / `table_tones.py` (producers) and `ToneEngine.ts`'s `ToneEvent` /
`dispatchEvent` (consumer). The client owns only what Python can't compute: scale quantisation,
effect-param output ranges, and scheduling.

## Environment

Managed with [uv](https://docs.astral.sh/uv/); all deps + dev tooling are in `pyproject.toml`
(no `requirements.txt`). `uv sync` creates `.venv` with the runtime deps and the dev group
(`ruff`, `ty`, `pytest`, `deephaven-server`, `watchdog`). This is a uv _virtual_ project
(`[tool.uv] package = false`), so `uv sync` does not build the wheel — `plugin_builder.py` does.

JS deps install automatically on the first `--js` build. If you build JS directly and hit an
esbuild _"installed for another platform"_ error (e.g. macOS `node_modules` on Linux), install the
matching binary: `npm install --no-save @esbuild/<platform>@<version>` in `src/js`.

**Sharing one `node_modules` across macOS + Linux** (e.g. the repo mounted on a Mac host _and_ in
this Linux container): esbuild's platform binaries are `optionalDependencies`, so npm installs only
the current platform's. esbuild tolerates several being present and picks the right one at runtime,
so install _both_ (version must match what `vite` resolves — currently `0.16.17`). npm enforces the
`os`/`cpu` check even on explicit installs, so use `--os`/`--cpu` to force the off-platform one in:

```sh
cd src/js
npm install --no-save @esbuild/darwin-arm64@0.16.17                        # current platform
npm install --no-save --os=linux --cpu=arm64 @esbuild/linux-arm64@0.16.17  # the other platform
```

`--no-save` keeps them out of `package.json` (where an off-platform binary would break `npm ci`
with `EBADPLATFORM`); a clean `npm ci` or wiped `node_modules` drops them, so re-run the lines.

## Testing

```sh
# Python unit tests — pure helpers, `tones` payloads, row→event translation and the
# table hook's wiring (deephaven.ui stubbed). No server needed.
uv run pytest -q

# Every examples/*.py, exec'd + rendered against a REAL in-process Deephaven server,
# with its triggers fired and a live ticking-table listener checked end to end
.venv/bin/python tests/verify_examples.py

# The bundle, loaded exactly as the Deephaven web client loads it (pure Node)
node tests/loader/simulate-dh-loader.mjs

# Browser/audio engine tests (tier1, must pass; serves src/js/dist)
cd tests/e2e && npm test -- --project tier1
```

`verify_examples.py` boots a real server and drives the actual `deephaven.ui` Renderer inside an
`EventContext` (the way `ElementMessageStream` does), so it catches contract drift the stubbed unit
tests can't. tier2 e2e tests are reference-only (mostly `.skip`'d — full IDE automation is fragile).

The e2e harness loads the built bundle through `new Function(module, exports, require, …)` and
delivers payloads to the plugin's own `eventMapping` handler, so tier1 exercises the real event
path. Rebuild the bundle (`cd src/js && npm run build`) before running it.

## Lint, format, type-check

```sh
uv run ruff check .      # lint
uv run ruff format .     # format
uv run ty check src      # type check
```

JS: `cd src/js && node_modules/.bin/tsc --noEmit` (typecheck) and `npm run build` (bundle).

## Distributing

Bump the version in `pyproject.toml`, build (`uv run plugin_builder.py --reinstall --js` or
`uv build --wheel`), then upload the wheel from `dist/`:

```sh
uvx twine upload dist/*                        # PyPI
uvx twine upload --repository testpypi dist/*  # TestPyPI
```

## Debugging

- **Panel not appearing / import not found** → the plugin isn't registered. Check the console for
  `Plugins loaded:` including this plugin, or the settings panel (gear icon). Rebuild/reinstall and
  watch for errors. Confirm the Python package: `uv pip list | grep tones`.
- **Panel appears but no/odd sound** → check both Python and browser-console logs. The browser
  needs a user gesture before audio unlocks. Unknown column names raise a `ValueError` at render
  time (listing the available columns) rather than failing silently in the browser.
- **`TonesError: Tones must be triggered from the render thread`** → the call happened on a
  background thread (a table listener, a worker). Wrap it in `ui.use_render_queue()`.

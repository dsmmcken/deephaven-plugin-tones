# Developing deephaven_plugin_tones

Contributor guide: project layout, and how to build, run, test, and lint. For what the plugin
*does* and how to *use* it, see [README.md](./README.md) and [SKILL.md](./skills/deephaven-plugin-tones/SKILL.md).

## Run / dev loop

One command builds the JS, (re)installs the wheel, and starts a Deephaven server with the repo
mounted as the data directory (PSK `iris`):

```sh
uv sync && uv run plugin_builder.py --dev
```

`--dev` is sugar for `--reinstall --js --server`. Useful variations:

| Command | When |
| --- | --- |
| `uv run plugin_builder.py --reinstall --js` | rebuild JS + reinstall (no version bump) |
| `uv run plugin_builder.py --reinstall` | Python-only change (skip JS build) |
| `uv run plugin_builder.py --dev --watch` | rebuild + restart on file changes |
| `uv run plugin_builder.py --dev --server-arg --port=9999` | pass args through to the server |

`--data-dir <path>` mounts a different directory as the Deephaven data dir (default: repo root, so
`storage/notebooks` shows up in the Web IDE). `storage/` is git-ignored and regenerated from
`examples/` on each server launch — **edit `examples/`, not `storage/`**.

## Project layout

```
src/deephaven_plugin_tones/        Python package
  __init__.py                      public API: exports `use_tones`, `table_tones`,
                                   and the `Tones` / `TonesElement` / `TonesControl` types
  deephaven_plugin_tones_component.py
                                   the two entry points + element/control/result types:
                                   `use_tones(...)` (trigger hook → `Tones(audio,
                                   audio_control)`) and `table_tones(...)` (declarative
                                   element factory → `TonesElement`). `TonesElement` is the
                                   props-only renderable; `TonesControl` carries the trigger
                                   methods; both share the module-level config/mappings/trigger
                                   builders. `table_tones` also validates column-name props.
                                   Pure helper fns are module-level (server-free unit tests).
  register.py                      registers the plugin + bundled JS with Deephaven
  _js/dist/index.js                built JS bundle (git-ignored; produced by setup.py)

src/js/src/                        TypeScript/React source
  index.ts                         plugin entry point
  DeephavenPluginTonesPlugin.ts    element-map registration; key must match
                                   `_ELEMENT_NAME` in the Python component
  DeephavenPluginTonesView.tsx     the invisible React element; consumes Python props,
                                   subscribes to the table, drives the engine
  ToneEngine.ts                    Tone.js wrapper: one cached AudioContext/chain per
                                   instrument+fx combo; play/chord/sequence/value/stop ops

plugin_builder.py                  build/install/run CLI (uv build + uv pip install + server)
setup.py                           packages src/js/dist into the wheel via package_js()
examples/                          runnable panels (canonical; copied into storage/notebooks)
tests/                             see Testing below
```

The Python side never loads the JS — it emits camelCase props (the renderer camelCases prop names
and drops `None`-valued props); the View consumes them in the browser. Keep the prop contract in
sync across `deephaven_plugin_tones_component.py` and `DeephavenPluginTonesView.tsx`.

## Environment

Managed with [uv](https://docs.astral.sh/uv/); all deps + dev tooling are in `pyproject.toml`
(no `requirements.txt`). `uv sync` creates `.venv` with the runtime deps and the dev group
(`ruff`, `ty`, `pytest`, `deephaven-server`, `watchdog`). This is a uv *virtual* project
(`[tool.uv] package = false`), so `uv sync` does not build the wheel — `plugin_builder.py` does.

JS deps install automatically on the first `--js` build. If you build JS directly and hit an
esbuild *"installed for another platform"* error (e.g. macOS `node_modules` on Linux), install the
matching binary: `npm install --no-save @esbuild/<platform>@<version>` in `src/js`.

**Sharing one `node_modules` across macOS + Linux** (e.g. the repo mounted on a Mac host *and* in
this Linux container): esbuild's platform binaries are `optionalDependencies`, so npm installs only
the current platform's. esbuild tolerates several being present and picks the right one at runtime,
so install *both* (version must match what `vite` resolves — currently `0.16.17`). npm enforces the
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
# Python unit tests — pure helpers + use_tones()/table_tones() prop construction, no server needed
uv run pytest -q

# Every SKILL.md example, rendered against a REAL in-process Deephaven server
.venv/bin/python tests/verify_skill_examples.py

# Every examples/*.py, exec'd + rendered against a real server
.venv/bin/python tests/verify_examples.py

# Browser/audio engine tests (tier1, must pass; builds + serves src/js/dist)
cd tests/e2e && npm test -- --project tier1
```

The two `verify_*.py` scripts boot a real server and drive the actual `deephaven.ui` Renderer, so
they catch contract drift the stubbed unit tests can't. tier2 e2e tests are reference-only (mostly
`.skip`'d — full IDE automation is fragile).

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
  needs a user gesture before audio unlocks. Unknown column names now raise a `ValueError` at
  render time (listing the available columns) rather than failing silently in the browser.

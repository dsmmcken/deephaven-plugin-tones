"""
Run EVERY examples/*.py against a real in-process Deephaven server.

For each example: exec the file (which builds its real tables and instantiates
its top-level panel component), then fully RENDER each top-level deephaven.ui
element through the real Renderer so the hooks inside `use_tones()` /
`table_tones()` actually run.
A example "passes" if it execs and every panel renders without raising.

Run:  .venv/bin/python tests/verify_examples.py
"""

from __future__ import annotations

import os
import runpy
import sys
import traceback
from pathlib import Path

from deephaven_server import Server

_server = Server(port=10021, jvm_args=["-Xmx2g"])
_server.start()

from deephaven.execution_context import get_exec_ctx  # noqa: E402
from deephaven.ui._internal.RenderContext import RenderContext  # noqa: E402
from deephaven.ui.elements import Element  # noqa: E402
from deephaven.ui.renderer.Renderer import Renderer  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


class _Root:
    def on_change(self, u):
        u()

    def on_queue_render(self, c):
        c()

    def get_url(self):
        return ""

    def set_url(self, u):
        pass


def render(element):
    ctx = RenderContext(_Root())
    return Renderer(ctx).render(element)


def run_example(path: Path):
    """Exec the file, then render every top-level ui Element it defined."""
    with get_exec_ctx():
        ns = runpy.run_path(str(path))
        panels = [
            (name, val)
            for name, val in ns.items()
            if isinstance(val, Element) and not name.startswith("_")
        ]
        if not panels:
            raise AssertionError("no top-level deephaven.ui Element (panel) found")
        for _name, panel in panels:
            render(panel)
        return [name for name, _ in panels]


def main():
    files = sorted(EXAMPLES_DIR.glob("*.py"))
    results = []
    for f in files:
        try:
            panels = run_example(f)
            results.append((f.name, True, ", ".join(panels)))
            print(f"PASS  {f.name}  (panels: {', '.join(panels)})")
        except Exception as e:  # noqa: BLE001
            results.append((f.name, False, repr(e)))
            print(f"FAIL  {f.name}: {e!r}")
            traceback.print_exc()

    print("\n==== SUMMARY ====")
    passed = sum(1 for _, ok, _ in results if ok)
    for name, ok, info in results:
        print(
            f"  {'PASS' if ok else 'FAIL'}  {name}{('  -> ' + info) if not ok else ''}"
        )
    print(f"\n{passed}/{len(results)} examples rendered cleanly")
    # Force-exit: examples may have started background publisher threads in
    # use_effect; os._exit avoids hanging on lingering non-daemon threads.
    sys.stdout.flush()
    os._exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()

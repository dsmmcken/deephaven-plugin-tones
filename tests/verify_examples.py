"""
Run EVERY examples/*.py against a real in-process Deephaven server.

For each example: exec the file (which builds its real tables and instantiates
its top-level panel component), then fully RENDER each top-level deephaven.ui
element through the real Renderer so the hooks inside
`use_table_tones_listener()` actually run, and fire each panel's button handlers
so the `tones` triggers reach a real event context.
A example "passes" if it execs, every panel renders, and every trigger sends
without raising.

Run:  .venv/bin/python tests/verify_examples.py
"""

from __future__ import annotations

import os
import runpy
import sys
import tempfile
import traceback
from pathlib import Path

from deephaven_server import Server

# The JVM defaults to /tmp, which some sandboxes mount read-only; follow TMPDIR.
_server = Server(
    port=10021,
    jvm_args=["-Xmx2g", f"-Djava.io.tmpdir={tempfile.gettempdir()}"],
)
_server.start()

from deephaven.execution_context import get_exec_ctx  # noqa: E402
from deephaven.ui._internal.EventContext import EventContext  # noqa: E402
from deephaven.ui._internal.RenderContext import RenderContext  # noqa: E402
from deephaven.ui.elements import Element  # noqa: E402
from deephaven.ui.renderer.Renderer import Renderer  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"

# Collected by the event context below so a run reports what actually sounded.
_SENT: list[tuple[str, dict]] = []

# The server renders inside an EventContext (see ElementMessageStream), which is
# what makes `use_send_event` reachable from render and from handlers.
_EVENTS = EventContext(lambda name, params: _SENT.append((name, params)))


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


def fire_triggers(example_ns):
    """
    Call every `tones` trigger the example defines.

    The examples wire their triggers through button handlers, which the renderer
    can't press, so exercise them directly: any module-level zero-arg callable in
    the example's `_ACTIONS`/`_JINGLES` maps is a trigger.
    """
    handlers = []
    for name, value in example_ns.items():
        if name.startswith("__") or not isinstance(value, dict):
            continue
        handlers.extend(v for v in value.values() if callable(v))
    for handler in handlers:
        handler()
    return len(handlers)


def run_example(path: Path):
    """Exec the file, render every top-level ui Element, then fire its triggers."""
    with get_exec_ctx(), _EVENTS.open():
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
        fired = fire_triggers(ns)
        return [name for name, _ in panels], fired


def verify_live_listener():
    """
    End-to-end check that a real tick reaches the client: render a component
    that sonifies a ticking table, wait for a few ticks, and count the events.
    """
    import time

    from deephaven import time_table, ui

    from deephaven_plugin_tones import use_table_tones_listener

    before = len(_SENT)

    @ui.component
    def _panel(table):
        use_table_tones_listener(table, pitch="Y", mode="all", rate_limit_ms=0)
        return ui.table(table)

    with get_exec_ctx(), _EVENTS.open():
        source = time_table("PT0.2S").update(["Y = (int)(ii % 10)"])
        render(_panel(source))
        deadline = time.time() + 15
        while len(_SENT) == before and time.time() < deadline:
            time.sleep(0.2)

    sent = len(_SENT) - before
    print(f"\n{'PASS' if sent else 'FAIL'}  live listener sent {sent} tone event(s)")
    if sent:
        print(f"      first event: {_SENT[before]}")
    return sent > 0


def main():
    files = sorted(EXAMPLES_DIR.glob("*.py"))
    results = []
    for f in files:
        try:
            panels, fired = run_example(f)
            results.append((f.name, True, ", ".join(panels)))
            suffix = f", triggers: {fired}" if fired else ""
            print(f"PASS  {f.name}  (panels: {', '.join(panels)}{suffix})")
        except Exception as e:  # noqa: BLE001
            results.append((f.name, False, repr(e)))
            print(f"FAIL  {f.name}: {e!r}")
            traceback.print_exc()

    live_ok = verify_live_listener()

    print("\n==== SUMMARY ====")
    passed = sum(1 for _, ok, _ in results if ok)
    for name, ok, info in results:
        print(
            f"  {'PASS' if ok else 'FAIL'}  {name}{('  -> ' + info) if not ok else ''}"
        )
    print(f"\n{passed}/{len(results)} examples rendered cleanly")
    print(f"{len(_SENT)} tone events sent in total")
    # Force-exit: examples may have started background publisher threads in
    # use_effect; os._exit avoids hanging on lingering non-daemon threads.
    sys.stdout.flush()
    os._exit(0 if passed == len(results) and live_ok else 1)


if __name__ == "__main__":
    main()

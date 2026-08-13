"""
Irregular Blink-Table Bell Demo -- deephaven-plugin-tones
=========================================================
A *blink* table that ticks at random intervals (every 1-8 seconds), with a soft
bell sound played on each tick.

Built with ``TablePublisher`` (https://deephaven.io/core/docs/how-to-guides/table-publisher/):
a background thread sleeps a random 1-8 s, then publishes a single row.  Blink
tables only retain the rows added in the *current* update cycle (they don't grow).
``tones`` auto-detects the blink table via the JSAPI, so ``mode="last"`` plays
exactly one sound per tick (no flag needed).

The sound is a soft FM bell at a fixed pitch with a long, gently reverberant
ring-out rather than a data-driven melody.  The ``Tick`` column is a constant,
and ``pitch=("Tick", 0, 1)`` pins the pitch, so every bell sounds identical.

**Lifecycle**: the publishing thread is started inside the component via
``ui.use_effect`` and STOPPED when the panel closes (the effect's cleanup sets a
stop event, and the loop sleeps on that event so it exits promptly). Reopening
the panel starts a fresh thread. No orphaned threads keep ticking in the
background after you close the panel.

**Audio note**: browsers need a user interaction before audio can play. Opening
or clicking anywhere in the panel satisfies this -- there is no separate button.
"""

import random
import threading

from deephaven import dtypes as dht
from deephaven import empty_table, ui
from deephaven.execution_context import get_exec_ctx
from deephaven.stream.table_publisher import table_publisher

from deephaven_plugin_tones import use_table_tones_listener

# ---------------------------------------------------------------------------
# Blink table + publisher
# ---------------------------------------------------------------------------
# table_publisher returns (table, publisher). The table is a BLINK table: each
# update cycle it holds only the rows added since the last cycle. The table is
# module-level (so the panel can render it), but the THREAD that feeds it is
# owned by the component below and torn down with the panel.
_blink_table, _publisher = table_publisher(
    name="Irregular blink",
    col_defs={"Tick": dht.int32, "Timestamp": dht.Instant},
)


@ui.component
def blink_tick_panel():
    """Layout: the live blink table, sonified by the listener hook."""

    use_table_tones_listener(
        _blink_table,
        # Fixed pitch -> identical soft tick every time.
        pitch=("Tick", 0, 1),
        mode="last",  # one soft tick per publish cycle
        scale="pentatonic",
        root="C5",
        octaves=1,
        # Soft bell: FM synthesis is the classic bell timbre -- a struck
        # (instant) attack, no sustain, and a long natural ring-out.
        instrument="fm",
        envelope_attack=0.001,
        envelope_decay=1.2,
        envelope_sustain=0.0,
        envelope_release=1.5,
        # Gentle reverb adds warmth and space; no feedback delay -- a bell
        # just rings and fades, it doesn't echo rhythmically.
        reverb_decay=2.5,
        reverb_wet=0.3,
        reverb_predelay=0.02,
        volume=-12,
        rate_limit_ms=0,
    )

    def _start_publishing():
        """use_effect: spawn the publisher thread; return a cleanup that stops it."""
        stop = threading.Event()
        ctx = get_exec_ctx()  # captured on the render thread, used inside the worker

        def _loop() -> None:
            with ctx:
                # stop.wait(t) sleeps up to t seconds, returning True the instant
                # the panel closes -> the loop exits promptly (no time.sleep).
                while not stop.wait(random.uniform(1.0, 8.0)):
                    _publisher.add(
                        empty_table(1).update(["Tick = (int) 1", "Timestamp = now()"])
                    )

        threading.Thread(target=_loop, daemon=True, name="blink-tick").start()
        return stop.set  # cleanup on unmount -> stops the loop

    ui.use_effect(_start_publishing, [])

    return ui.flex(
        ui.table(_blink_table),
        direction="row",
        flex="1",
    )


# Top-level assignment -- surfaces this as an openable panel in the Web IDE.
table_blink_tick = blink_tick_panel()

"""
Table Tones Demo -- deephaven-plugin-tones
==========================================
Demonstrates automatic table sonification: a ticking table drives the pitch in
real time without any button presses.

Open this file in the Deephaven Web IDE.  The panel ``table_tones_demo``
appears in the panel list.

**How it works**

1. A ``time_table`` emits a new row every 500 ms.
2. An ``update`` formula adds column ``Y`` -- a sine wave scaled to 10-90.
   Higher values map to higher pitches (pentatonic scale, C3 root, 3 octaves).
3. ``use_table_tones_listener(ticking, pitch="Y", ...)`` listens to the table on
   the server. Each new row triggers a note.
4. ``ui.table(ticking)`` displays the live table; the hook renders nothing.

**Audio note (important)**: the browser requires a user interaction before
audio can play. This demo has *no* buttons and renders no UI of its own, so if
you open it in a brand-new tab and hear nothing, click anywhere in the panel
once -- that satisfies the browser, and ticks drive audio from then on.

**Table formula note**: ``ii`` is Deephaven's built-in implicit row-index
variable available in ``update`` expressions.  ``Math.sin`` is Java's
``Math.sin``; the ``(int)(...)`` cast truncates to an integer.  The formula
``(int)(50 + 40*Math.sin(0.4*ii))`` produces values in [10, 90], sweeping the
full pentatonic range audibly.
"""

from deephaven import time_table, ui

from deephaven_plugin_tones import use_table_tones_listener

# ---------------------------------------------------------------------------
# Ticking source table
# ---------------------------------------------------------------------------
# Emits one row every 500 ms.
# Column Y oscillates between 10 and 90 (a sinusoidal wave) so the pitch
# audibly rises and falls.  ``ii`` is the implicit 0-based row counter
# available in all Deephaven query-language update expressions.
# Math.sin is Java's java.lang.Math.sin, always available in DH formulas.
# The (int)(...) cast is standard DH query-language integer truncation.
_ticking = time_table("PT1S").update(["Y = (int)(50 + 40*Math.sin(0.4*ii))"])


@ui.component
def tone_table_panel():
    """
    Layout: the live table, with the sonification hook running alongside it.

    use_table_tones_listener() renders nothing. Audio starts once the browser
    has seen a user interaction (opening/clicking the panel is enough).
    """
    # Listens to `_ticking` server-side and plays a note on every new row,
    # mapping column Y onto the pentatonic scale.
    use_table_tones_listener(
        _ticking,
        pitch=("Y", 0, 100),
        scale="pentatonic",
        root="C3",
        octaves=3,
        descending=False,
        instrument="sine",
        reverb_decay=2,
        reverb_wet=0.20,
        reverb_predelay=0.01,
        volume=-10,
        rate_limit_ms=400,  # 500 ms ticks -- leave headroom so we don't double-fire
    )

    return ui.flex(
        ui.table(_ticking),
        direction="row",
        flex="1",
    )


# Top-level assignment -- surfaces this as an openable panel in the Web IDE.
table_tones_demo = tone_table_panel()

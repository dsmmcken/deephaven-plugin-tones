"""
Effects + Voices Showcase -- deephaven-plugin-tones
===================================================
Demonstrates the Tier-1 additions to the engine on a single live stream:

    signal -> PITCH        (scale-quantised)
    energy -> DISTORTION   (data-driven wet: busier data = grittier tone)

plus a new instrument voice (``monosynth``) routed through the new
``chorus`` and ``ping_pong`` (ping-pong delay) effects, with the master
``limiter`` (on by default) keeping the wetter, louder passages from clipping.

Open this file in the Deephaven Web IDE.  The panel ``table_fx_showcase``
appears in the panel list.

**What's new here (vs. the other examples)**

* **``instrument="monosynth"``** -- one of the three new voices (``monosynth`` /
  ``duosynth`` / ``metal``). All three wrap a polyphonic engine like the
  existing synths.
* **``distortion`` / ``chorus`` / ``ping_pong``** -- three new inline effects.
  ``chorus`` widens and thickens the tone; ``ping_pong`` adds a stereo echo;
  ``distortion`` adds grit.
* **Data-driven distortion** -- ``distortion_wet="Energy"`` maps a column to the
  distortion wet/dry mix per row (auto-ranged), so the timbre gets dirtier as
  ``Energy`` rises -- the same column-name overload the other effect params
  accept.
* **Master limiter** -- left at its default (on, -1 dBFS). With distortion fully
  wet and several effects stacked, the limiter is what keeps the master bus from
  clipping on the loud rows.

**Audio note**: the browser requires a user interaction before audio can play.
This demo has no buttons, so if you hear nothing in a fresh tab, click anywhere
in the panel once -- audio plays automatically from then on.

**Query-language notes**: ``ii`` is Deephaven's implicit 0-based row index.
``Math.sin`` / ``Math.abs`` are Java's.
"""

from deephaven import time_table, ui

from deephaven_plugin_tones import use_table_tones_listener

# ---------------------------------------------------------------------------
# Simulated live signal -- one row per second.
# ---------------------------------------------------------------------------
#   Signal : a wandering value around 100 that sweeps the pitch range.
#   Energy : a 0..1-ish "activity" level that rises and falls, driving how much
#            distortion is mixed in per row.
_stream = time_table("PT1S").update(
    [
        "Signal = (double)(100 + 30 * Math.sin(0.17 * ii) + 12 * Math.sin(0.7 * ii) + 15 * (Math.random() - 0.5))",
        "Energy = (double)(0.5 + 0.5 * Math.sin(0.23 * ii))",
    ]
)


@ui.component
def fx_showcase_panel():
    """
    Layout: the live stream, sonified by the listener hook.

    use_table_tones_listener() renders nothing; the table fills the panel.
    """
    # One note per row: Signal -> pitch, Energy -> distortion wet (data-driven),
    # routed through chorus + ping-pong delay.
    use_table_tones_listener(
        _stream,
        mode="all",
        pitch="Signal",
        # ── new instrument voice ────────────────────────────────────────
        instrument="monosynth",
        # ── new inline effects ──────────────────────────────────────────
        distortion=True,
        distortion_wet="Energy",  # data-driven: busier data = grittier
        chorus=True,
        chorus_depth=0.6,
        chorus_wet=0.4,
        ping_pong=True,
        ping_pong_time="8n",
        ping_pong_feedback=0.25,
        ping_pong_wet=0.35,
        # ── pitch mapping (auto-ranged) ─────────────────────────────────
        scale="pentatonic",
        root="C3",
        octaves=3,
        # ── space + headroom ────────────────────────────────────────────
        reverb_wet=0.2,
        volume=-9,
        # limiter is on by default (-1 dBFS) — keeps the wet rows clean.
        rate_limit_ms=0,  # mode="all": don't drop rows
    )

    return ui.flex(
        ui.table(_stream),
        direction="row",
        flex="1",
    )


# Top-level assignment -- surfaces this as an openable panel in the Web IDE.
table_fx_showcase = fx_showcase_panel()

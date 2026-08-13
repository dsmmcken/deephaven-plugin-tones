"""
Service-Health Sonification -- deephaven-plugin-tones
=====================================================
Turns a live service-metrics stream into an ambient "earcon for your dashboard":
you can hear the health of the system without watching it. Four dimensions map
onto four perceptual channels:

    requests/sec -> PITCH           (busier service = higher line)
    latency      -> ECHO TAIL       (laggy service = longer, smeared echoes)
    error rate   -> GRIT            (more errors = dirtier, more distorted tone)
    health state -> INSTRUMENT      (OK = smooth rich pad, DEGRADED = clangy alarm)

Open this file in the Deephaven Web IDE.  The panel ``table_service_health``
appears in the panel list.

**Why it reads at a glance**

Each channel is independent and intuitive, so a quick listen tells you the shape
of the system:

* **Pitch** carries the headline throughput number (requests/sec), pentatonic-
  quantised so it is never dissonant however the traffic swings.
* **Echo tail** is latency made audible: as latency climbs, the ping-pong delay
  feeds back longer and notes *smear* into each other -- exactly how a laggy
  system "feels".
* **Grit** is the error rate: a clean tone when healthy, audibly dirtier and more
  distorted as errors rise.
* **Timbre** carries the categorical health state -- a smooth, rich ``duosynth``
  pad while ``OK``, a hard, bell-like ``metal`` voice when ``DEGRADED``. You can
  tell a bad window from a good one even on a laptop speaker.

**Mapping forms shown here**

* ``pitch="Rps"`` is the bare-column form -- the client auto-tracks the column's
  running min/max.
* ``ping_pong_feedback=("Latency", 0.1, 0.55)`` and
  ``distortion_wet=("ErrorRate", 0.0, 0.85)`` are the explicit-range form: the
  column value is mapped from the data range onto that output range, keeping the
  echo feedback well short of runaway and the distortion from ever fully
  swamping the tone.
* ``voice="Health"`` switches the *instrument* per row from the ``voices`` map.

The master limiter ceiling is pulled down to ``-2`` dBFS so the loud, fully-wet
DEGRADED windows (clangy voice + grit + long echo) stay clean instead of clipping.

**Audio note**: the browser requires a user interaction before audio can play.
This demo has no buttons, so if you hear nothing in a fresh tab, click anywhere
in the panel once -- audio plays automatically from then on.

**Query-language notes**: ``ii`` is Deephaven's implicit 0-based row index.
``Math.sin`` / ``Math.max`` / ``Math.min`` are Java's. Backtick literals
(```` `OK` ````) are Deephaven query-language strings.
"""

from deephaven import time_table, ui

from deephaven_plugin_tones import use_table_tones_listener

# ---------------------------------------------------------------------------
# Simulated live service-metrics stream -- one sample per second.
# ---------------------------------------------------------------------------
#   Rps       : requests/sec, a lively wander roughly 200..1000.
#   Latency   : p99 latency 0..1 (normalised), with a slow swell so the echo
#               tail visibly lengthens and shortens over ~20 s windows.
#   ErrorRate : 0..1, mostly low with periodic spikes that drive the grit up.
#   Health    : DEGRADED when latency OR errors cross a threshold, else OK --
#               so the instrument flips to the alarm voice during bad windows.
_metrics = time_table("PT1S").update(
    [
        "Rps = (double)(600 + 350 * Math.sin(0.13 * ii) + 120 * Math.sin(0.71 * ii) + 80 * (Math.random() - 0.5))",
        "Latency = (double)Math.max(0.0, Math.min(1.0, 0.45 + 0.4 * Math.sin(0.11 * ii) + 0.1 * (Math.random() - 0.5)))",
        "ErrorRate = (double)Math.max(0.0, Math.min(1.0, 0.15 + 0.7 * Math.pow(Math.sin(0.09 * ii + 2), 6)))",
        "Health = (Latency > 0.7 || ErrorRate > 0.4) ? `DEGRADED` : `OK`",
    ]
)


@ui.component
def service_health_panel():
    """
    Layout: the live metrics, sonified by the listener hook.

    use_table_tones_listener() renders nothing; the table fills the panel.
    """
    # One note per sample: Rps -> pitch, Latency -> echo tail (ping-pong
    # feedback), ErrorRate -> distortion grit, Health -> which instrument plays.
    # Effects below are enabled globally and then driven per-row by the column
    # mappings.
    use_table_tones_listener(
        _metrics,
        mode="all",  # every sample speaks (not just the latest)
        # ── four-dimensional mapping ───────────────────────────────────
        pitch="Rps",  # throughput -> pitch (auto-ranged)
        voice="Health",  # health    -> which instrument plays
        voices={
            # OK: smooth, rich, sustained pad.
            "OK": {
                "instrument": "duosynth",
                "envelope_attack": 0.08,
                "envelope_release": 0.5,
            },
            # DEGRADED: hard, bell-like alarm voice -- cuts through.
            "DEGRADED": {"instrument": "metal"},
        },
        # ── data-driven effects (explicit-range form) ──────────────────
        ping_pong=True,
        ping_pong_time="8n",
        # latency -> echo feedback: more lag = longer, smearier tail.
        ping_pong_feedback=("Latency", 0.1, 0.55),
        ping_pong_wet=0.35,
        distortion=True,
        # error rate -> distortion wet: more errors = grittier tone.
        distortion_wet=("ErrorRate", 0.0, 0.85),
        distortion_amount=0.6,
        # subtle global width on top of the per-row effects.
        chorus=True,
        chorus_depth=0.5,
        chorus_wet=0.3,
        # ── pitch mapping (auto-ranged) ────────────────────────────────
        scale="pentatonic",
        root="C3",
        octaves=3,
        # ── space + headroom ───────────────────────────────────────────
        reverb_wet=0.2,
        volume=-9,
        # Pull the master ceiling down so the loud, fully-wet DEGRADED
        # windows stay clean.
        limiter_threshold=-2,
        rate_limit_ms=0,  # mode="all": don't drop samples
    )

    return ui.flex(
        # Live metrics table -- fills the panel.
        ui.table(_metrics),
        direction="row",
        flex="1",
    )


# Top-level assignment -- surfaces this as an openable panel in the Web IDE.
table_service_health = service_health_panel()

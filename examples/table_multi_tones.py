"""
Multi-Dimensional Table Tones Demo -- deephaven-plugin-tones
============================================================
Sonifies THREE dimensions of a live trade stream at once, as a *duet*:

    price  -> PITCH       (scale-quantised: higher price = higher note)
    volume -> LOUDNESS    + note LENGTH (bigger trade = louder + longer)
    side   -> INSTRUMENT  (BUY = plucky marimba, SELL = bowed cello)

Open this file in the Deephaven Web IDE.  The panel ``table_multi_tones``
appears in the panel list.

**Why it sounds good (and legible)**

Each data dimension gets its own *perceptual* channel, so they don't smear
together:

* **Pitch** is our most precise ordered sense -- it carries the headline
  number (price).  Quantising to a pentatonic scale means it is *never*
  dissonant, no matter what the data does.
* **Loudness + length** is the one mapping everyone intuits instantly:
  "volume" -> how hard/long the note hits.
* **Timbre (instrument)** is the natural home for a *category*.  BUY and SELL
  become two different players trading phrases -- a marimba-ish pluck vs. a
  bowed, sustained saw.  You can tell the side apart even on a mono speaker,
  no stereo required.

The result reads like a trading floor: a melodic line rising and falling with
price, hit with varying weight as size comes through, alternating between two
instruments as the market takes the bid or lifts the offer.

**Auto-ranging**: no explicit ranges are specified (``pitch="Price"`` and
``loudness="Volume"`` rather than the tuple form ``pitch=("Price", lo, hi)``), so
the client auto-tracks each column's running min/max -- pitch and loudness scale
themselves to the data as it arrives.

**Audio note**: the browser requires a user interaction before audio can play.
This demo has no buttons, so if you hear nothing in a fresh tab, click anywhere
in the panel once -- audio plays automatically from then on.

**Query-language notes**: ``ii`` is Deephaven's implicit 0-based row index.
``Math.sin`` / ``Math.abs`` are Java's.  Backtick literals (```` `BUY` ````) are
Deephaven query-language strings.
"""

from deephaven import time_table, ui

from deephaven_plugin_tones import table_tones

# ---------------------------------------------------------------------------
# Simulated live trade stream -- one trade every 500 ms.
# ---------------------------------------------------------------------------
#   Price  : a lively wander around 100. Three summed sines at unrelated
#            frequencies give big, non-repeating swings, plus a per-row random
#            jitter so consecutive trades genuinely jump -- the pitch ranges
#            roughly 40..160 and sweeps the full scale instead of drifting.
#   Volume : trade size, swinging roughly 40..100.
#   Side   : BUY / SELL, a varied ~55/45 mix driven off the row index so the
#            two instruments interleave audibly.
_trades = time_table("PT1S").update(
    [
        "Price = (double)(100 + 28 * Math.sin(0.15 * ii) + 14 * Math.sin(0.63 * ii) + 9 * Math.sin(1.9 * ii) + 18 * (Math.random() - 0.5))",
        "Volume = (int)(40 + 60 * Math.abs(Math.sin(0.5 * ii + 1)))",
        "Side = (((ii * 1327) % 100) < 55) ? `BUY` : `SELL`",
    ]
)


@ui.component
def trade_tones_panel():
    """
    Layout: table_tones() (invisible) + the live trade blotter, side by side.

    table_tones() takes zero layout space; the table fills the panel.
    """
    return ui.flex(
        # Invisible audio engine. Subscribes to `_trades` client-side and plays
        # one note per trade, mapping three columns onto three sound channels.
        table_tones(
            table=_trades,
            mode="all",  # every trade speaks (not just the latest)
            # ── three-dimensional mapping ──────────────────────────────────
            pitch="Price",  # price -> pitch
            loudness="Volume",  # volume -> loudness + note length
            voice="Side",  # side  -> which instrument plays
            voices={
                # BUY: bright, percussive, plucky -- a marimba/mallet feel.
                "BUY": {"instrument": "pluck"},
                # SELL: darker, sustained, bowed -- a cello-ish saw with a
                # slow-ish attack and real sustain. Override keys are flat.
                "SELL": {
                    "instrument": "sawtooth",
                    "envelope_attack": 0.12,
                    "envelope_decay": 0.20,
                    "envelope_sustain": 0.7,
                    "envelope_release": 0.4,
                },
            },
            # ── pitch mapping (auto-ranged: no explicit pitch range given) ──
            scale="pentatonic",
            root="C3",
            octaves=3,
            # ── shared voicing / space ──────────────────────────────────────
            reverb_decay=2.5,
            reverb_wet=0.22,
            reverb_predelay=0.01,
            volume=-9,
            rate_limit_ms=0,  # mode="all": don't drop trades
        ),
        # Live trade blotter -- fills the panel.
        ui.table(_trades),
        direction="row",
        flex="1",
    )


# Top-level assignment -- surfaces this as an openable panel in the Web IDE.
table_multi_tones = trade_tones_panel()

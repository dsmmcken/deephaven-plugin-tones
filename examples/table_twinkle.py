"""
Twinkle Twinkle Little Star -- deephaven-plugin-tones
=====================================================
Plays a recognisable TUNE as a table ticks, instead of sonifying arbitrary data.

**The idea**

A data-driven note is just ``value -> pitch``. To play a *specific* melody, make
the table emit the melody itself: one row per note, where the cell value is the
note we want. Pick a mapping so that integer values land on exact scale notes,
then each tick advances the tune by one note.

Here the ``Degree`` column holds a **major-scale degree** (0=C, 1=D, 2=E, 3=F,
4=G, 5=A, 6=B). With ``scale="major"``, ``root="C4"``, ``octaves=1`` and
``pitch=("Degree", 0, 6)``, the plugin maps degree -> note exactly:

    0->C4  1->D4  2->E4  3->F4  4->G4  5->A4  6->B4

Twinkle Twinkle in C major is therefore the degree sequence::

    C C G G A A G   ->  0 0 4 4 5 5 4   "Twinkle twinkle little star"
    F F E E D D C   ->  3 3 2 2 1 1 0   "How I wonder what you are"
    G G F F E E D   ->  4 4 3 3 2 2 1   "Up above the world so high"
    G G F F E E D   ->  4 4 3 3 2 2 1   "Like a diamond in the sky"
    (then the first two lines repeat)

A ``TablePublisher`` background thread walks the melody list and publishes one
row per beat (holding the phrase-ending notes for two beats). The table is a
BLINK table (auto-detected by ``table_tones`` via the JSAPI), so ``mode="last"`` plays
exactly one note per tick. The rhythm comes from how long the thread sleeps
between rows; the pitch comes from the ``Degree`` value.

**Lifecycle**: the melody thread is owned by the component via ``ui.use_effect``
and STOPPED when the panel closes (the cleanup sets a stop event the loop sleeps
on), so the tune doesn't keep playing in the background after you close it.

**Generalising**: the same trick plays ANY tune — swap ``_MELODY`` for another
degree+rhythm sequence (use ``scale="chromatic"`` with semitone offsets if you
need accidentals). For polyphony/chords you'd extend the plugin to accept a list
of notes per row; this single-line-melody version needs no plugin changes.

**Audio note**: browsers require a user interaction before audio can play. Opening or clicking anywhere in the panel satisfies this -- there is no separate button.
"""

import threading

from deephaven import dtypes as dht
from deephaven import empty_table, ui
from deephaven.execution_context import get_exec_ctx
from deephaven.stream.table_publisher import table_publisher

from deephaven_plugin_tones import table_tones

# Major-scale degree -> display note name (octave 4).
_NOTE_NAMES = ["C", "D", "E", "F", "G", "A", "B"]

# (degree, beats). beats=2 holds the note at the end of each phrase.
_MELODY: list[tuple[int, int]] = [
    (0, 1),
    (0, 1),
    (4, 1),
    (4, 1),
    (5, 1),
    (5, 1),
    (4, 2),  # Twinkle twinkle little star
    (3, 1),
    (3, 1),
    (2, 1),
    (2, 1),
    (1, 1),
    (1, 1),
    (0, 2),  # How I wonder what you are
    (4, 1),
    (4, 1),
    (3, 1),
    (3, 1),
    (2, 1),
    (2, 1),
    (1, 2),  # Up above the world so high
    (4, 1),
    (4, 1),
    (3, 1),
    (3, 1),
    (2, 1),
    (2, 1),
    (1, 2),  # Like a diamond in the sky
    (0, 1),
    (0, 1),
    (4, 1),
    (4, 1),
    (5, 1),
    (5, 1),
    (4, 2),  # Twinkle twinkle little star
    (3, 1),
    (3, 1),
    (2, 1),
    (2, 1),
    (1, 1),
    (1, 1),
    (0, 2),  # How I wonder what you are
]

_BEAT_SECONDS = 0.42  # tempo: seconds per beat

# ---------------------------------------------------------------------------
# Blink table + publisher (thread owned by the component, below)
# ---------------------------------------------------------------------------
_twinkle_table, _publisher = table_publisher(
    name="Twinkle",
    col_defs={"Degree": dht.int32, "Note": dht.string, "Step": dht.int32},
)


@ui.component
def twinkle_panel():
    """Layout: invisible table_tones() engine + the live melody table."""

    def _start_playing():
        """use_effect: walk the melody on a thread; cleanup stops it on close."""
        stop = threading.Event()
        ctx = get_exec_ctx()

        def _loop() -> None:
            with ctx:
                step = 0
                while True:
                    degree, beats = _MELODY[step % len(_MELODY)]
                    name = _NOTE_NAMES[degree]
                    _publisher.add(
                        empty_table(1).update(
                            [
                                f"Degree = (int) {degree}",
                                f"Note = `{name}`",
                                f"Step = (int) {step}",
                            ]
                        )
                    )
                    step += 1
                    # Interruptible beat: returns True (and we exit) when closed.
                    if stop.wait(beats * _BEAT_SECONDS):
                        break

        threading.Thread(target=_loop, daemon=True, name="twinkle").start()
        return stop.set  # cleanup on unmount -> stops the melody

    ui.use_effect(_start_playing, [])

    return ui.flex(
        table_tones(
            table=_twinkle_table,
            # Degree 0..6 -> exact major-scale notes C4..B4.
            pitch=("Degree", 0, 6),
            mode="last",  # blink table auto-detected -> one note per tick
            scale="major",
            root="C4",
            octaves=1,
            # Soft music-box-ish voice: quick mallet attack, short ring.
            instrument="triangle",
            envelope_attack=0.002,
            envelope_decay=0.28,
            envelope_sustain=0.0,
            envelope_release=0.3,
            reverb_decay=1.6,
            reverb_wet=0.25,
            reverb_predelay=0.01,
            volume=-10,
            rate_limit_ms=0,
        ),
        ui.table(_twinkle_table),
        direction="row",
        flex="1",
    )


# Top-level assignment -- surfaces this as an openable panel in the Web IDE.
table_twinkle = twinkle_panel()

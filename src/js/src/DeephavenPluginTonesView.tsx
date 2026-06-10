/**
 * DeephavenPluginTonesView
 *
 * No-DOM React component: ALWAYS returns null — it renders no UI of any kind.
 * Audio unlock relies on the browser's sticky user activation (the user has
 * interacted with the page by the time a panel is open), so engine.start()
 * succeeds without any on-screen "enable sound" affordance.
 *
 * Props (camelCase, per contract):
 *   config       – ToneConfig dict
 *   events       – list of ToneEvent objects with monotonic `id` field
 *   table        – ExportedObject (Deephaven table ref) or undefined
 *   mappings     – value→pitch mapping (pitch + optional loudness/voice)
 *   mode         – "last" | "all"
 *   rateLimitMs  – throttle interval for table tick sonification (ms)
 */

import { useEffect, useRef } from 'react';
import { useApi } from '@deephaven/jsapi-bootstrap';
import type { dh } from '@deephaven/jsapi-types';
import ToneEngine, { ToneConfig, ToneEvent, normalize01, mapParam } from './ToneEngine';

// ─── Types ────────────────────────────────────────────────────────────────────

// ExportedObject as it arrives in the prop (from Spike 2)
interface ExportedObject {
  reexport(): Promise<ExportedObject>;
  fetch(): Promise<unknown>;
  close(): void;
  type?: string | null;
}

// ─── Multi-dimensional table mapping ───────────────────────────────────────────
// Each channel names the COLUMN that drives it. `range` is an explicit
// [min, max]; when omitted (null/undefined) the View auto-tracks a running
// min/max for that column so the channel always uses the full observed span.
interface ChannelMapping {
  column: string;
  range?: [number, number] | null;
  // Server-side AUTO range: names of live columns (added by a min/max
  // natural_join) carrying the current global min/max for `column`. When
  // present these take precedence over `range` and over client running min/max.
  minColumn?: string;
  maxColumn?: string;
  // Output span the normalised 0..1 value is mapped into (channel-specific
  // defaults applied if omitted): velocity 0..1, duration seconds.
  min?: number;
  max?: number;
}

interface VoiceMapping {
  column: string;
  // Map of cell value (stringified) → partial config override (instrument,
  // envelope, …). The matching override selects that row's VOICE.
  voices: Record<string, Partial<ToneConfig>>;
  default?: Partial<ToneConfig>;
}

interface Mappings {
  // Required: which column drives PITCH (mapped via the base config's
  // scale/root/octaves/valueRange). `pitch` alone is a one-dimensional sonify;
  // add velocity/duration/voice for a multi-dimensional "duet".
  // minColumn/maxColumn enable server-side auto-range for the pitch span.
  pitch: { column: string; minColumn?: string; maxColumn?: string };
  velocity?: ChannelMapping; // loudness
  duration?: ChannelMapping; // note length (seconds)
  voice?: VoiceMapping; // categorical → instrument selection
}

// Fire a series of chords (a progression) whenever a trigger column is truthy
// on a new row. Independent of the pitch/value path — most rows stay silent.
interface ChordTrigger {
  column: string;
  // Default chord(s) when no per-row column is given (or its cell is empty).
  chords: Array<Array<string | number>>;
  // Optional column whose per-row cell supplies the chord(s): a String[] (one
  // chord), a String like "C4,E4,G4 | G3,B3,D4" (chords split by | or ;), or
  // a String[][]. Overrides `chords` for that row.
  notesColumn?: string;
  gap?: string | number; // onset spacing between chords (Tone time)
  duration?: string | number; // per-chord length (Tone time)
}

// Fire a melodic SEQUENCE (arpeggio / motif) whenever a trigger column is truthy
// on a new row — the table analogue of play_sequence.
interface SequenceTrigger {
  column: string;
  notes: Array<{ note: string | number; duration?: string | number; velocity?: number }>;
  // Optional column whose per-row cell supplies the notes: a String[] or a
  // String like "C5 E5 G5 C6". Overrides `notes` for that row.
  notesColumn?: string;
  gap?: string | number; // onset spacing between notes (Tone time)
}

// Data-driven EFFECT params: map a column to a live Tone node param (e.g.
// reverb wet, filter cutoff, pan) per row. Keyed by the param PATH the engine
// understands ('filter.frequency', 'reverb.wet', 'pan', …). Unlike pitch/
// loudness, the input domain is always the column's running min/max (no server
// range columns); `min`/`max` set the OUTPUT range (else the param's default).
interface ParamChannel {
  column: string;
  min?: number;
  max?: number;
}
type ParamMappings = Record<string, ParamChannel>;

interface DeephavenPluginTonesViewProps {
  config?: ToneConfig;
  events?: ToneEvent[];
  table?: ExportedObject;
  mode?: 'last' | 'all';
  rateLimitMs?: number;
  mappings?: Mappings | null;
  // Data-driven effect params (ride along with the per-row note path).
  paramMappings?: ParamMappings | null;
  // When set, play a chord progression on each new row whose `column` is truthy.
  chordTrigger?: ChordTrigger | null;
  // When set, play a melodic sequence on each new row whose `column` is truthy.
  sequenceTrigger?: SequenceTrigger | null;
}

// ─── Component ────────────────────────────────────────────────────────────────

export default function DeephavenPluginTonesView({
  config = {},
  events = [],
  table,
  mode = 'last',
  rateLimitMs = 100,
  mappings = null,
  paramMappings = null,
  chordTrigger = null,
  sequenceTrigger = null,
}: DeephavenPluginTonesViewProps): null {
  // Track the highest event id we have already dispatched
  const lastPlayedIdRef = useRef(-1);

  // Latest `config`, read live by the table handlers WITHOUT resubscribing.
  // `config` is a fresh object every render (built server-side), so depending on
  // it would tear down the table subscription on every unrelated re-render.
  const configRef = useRef(config);
  configRef.current = config;

  // Deephaven JSAPI (only needed for table mode — loaded lazily)
  // We call useApi() unconditionally (rules of hooks) but only use it when table is present
  let dh: typeof import('@deephaven/jsapi-types').dh | undefined;
  try {
    // eslint-disable-next-line react-hooks/rules-of-hooks
    dh = useApi();
  } catch {
    // Not inside an ApiContext.Provider — table mode won't work, but that's OK
    dh = undefined;
  }

  // ── Mount refcount ───────────────────────────────────────────────────────
  // The ToneEngine is a shared module singleton. Refcount mounts so the last
  // view to unmount disposes the engine (releasing all cached Tone nodes).
  useEffect(() => {
    ToneEngine.acquire();
    return () => { ToneEngine.release(); };
  }, []);

  // ── Event playback ─────────────────────────────────────────────────────────
  useEffect(() => {
    if (!events || events.length === 0) return undefined;

    // Sort events ascending by id and play only those with id > lastPlayedId
    const pending = [...events]
      .filter(e => e.id > lastPlayedIdRef.current)
      .sort((a, b) => a.id - b.id);

    if (pending.length === 0) return undefined;

    // Fire-and-forget: start() is idempotent. Audio unlock relies on the user
    // having interacted with the page (sticky activation) — opening/clicking the
    // panel is enough; there is no separate enable affordance.
    let cancelled = false;
    (async () => {
      await ToneEngine.start();
      for (const event of pending) {
        if (cancelled) break;
        // Read config from the ref so a runtime config change is reflected
        // without re-running (and re-playing) on every render.
        // eslint-disable-next-line no-await-in-loop
        await ToneEngine.dispatchEvent(event, configRef.current);
        lastPlayedIdRef.current = Math.max(lastPlayedIdRef.current, event.id);
      }
    })();
    return () => { cancelled = true; };
  }, [events]);

  // ── Table mode ─────────────────────────────────────────────────────────────
  // Structural keys for the object-valued props so the table effect only
  // re-subscribes when their CONTENT changes, not their (always-new) identity.
  const mappingsKey = mappings ? JSON.stringify(mappings) : '';
  const paramMappingsKey = paramMappings ? JSON.stringify(paramMappings) : '';
  const chordTriggerKey = chordTrigger ? JSON.stringify(chordTrigger) : '';
  const sequenceTriggerKey = sequenceTrigger ? JSON.stringify(sequenceTrigger) : '';
  useEffect(() => {
    // Ways to drive table mode: a value→pitch `mappings` object (pitch alone, or
    // pitch + loudness + voice + …), or a `chordTrigger`/`sequenceTrigger` that
    // fires a progression / melody on flagged rows.
    const multi = mappings && mappings.pitch ? mappings : null;
    const chordTrig = chordTrigger && chordTrigger.column ? chordTrigger : null;
    const seqTrig = sequenceTrigger && sequenceTrigger.column ? sequenceTrigger : null;
    if (!table || !dh || (!multi && !chordTrig && !seqTrig)) return undefined;
    const dhApi = dh;
    const srcTable = table;

    let cancelled = false;
    // The chain of server handles we create. ALL must be closed on teardown:
    //   reexported (ExportedObject) → fetched (live Table) → baseTable (our copy)
    // Failing to close any of these leaks a server-side export per subscribe.
    let reexported: ExportedObject | null = null;
    let fetched: dh.Table | null = null;
    // A PRIVATE, REVERSED copy of the table. We never mutate the user's table.
    let baseTable: dh.Table | null = null;
    let vp: dh.TableViewportSubscription | null = null;
    let removeUpdated: (() => void) | null = null;
    // Serializes async update handling: each EVENT_UPDATED appends to this chain
    // so notes never play out of order or race on the shared bookkeeping below.
    let queue: Promise<void> = Promise.resolve();
    let lastThrottleTime = 0;
    // Row count at the previous update — used to detect genuinely new rows.
    let lastSize = 0;
    let primed = false;
    // Whether the source is a BLINK table — detected from the JSAPI in
    // subscribe(), not a prop. Blink tables replace their rows each cycle (and
    // may add several at once), so each update carries only the fresh rows.
    let blink = false;

    // Resolved columns. Single mode uses `pitchCol`; multi mode fills the rest.
    let pitchCol: dh.Column | undefined;
    let velCol: dh.Column | undefined;
    let durCol: dh.Column | undefined;
    let voiceCol: dh.Column | undefined;
    let chordTriggerCol: dh.Column | undefined; // chord-progression trigger
    let seqTriggerCol: dh.Column | undefined; // melodic-sequence trigger
    let chordNotesCol: dh.Column | undefined; // per-row chord source (optional)
    let seqNotesCol: dh.Column | undefined; // per-row notes source (optional)
    // Server-side auto-range columns (live global min/max), if attached.
    let pitchMinCol: dh.Column | undefined;
    let pitchMaxCol: dh.Column | undefined;
    let loudMinCol: dh.Column | undefined;
    let loudMaxCol: dh.Column | undefined;
    // Data-driven effect-param channels, resolved to live columns.
    const paramCols: Array<{ path: string; col: dh.Column; min?: number; max?: number }> = [];

    // ── Strategy: REVERSE, don't chase the tail. ────────────────────────────
    // An append-only table puts new rows at the END, so "play the newest value"
    // used to require constantly re-anchoring a viewport to the moving tail —
    // fragile, and the cause of the "same tone" / "first note only" bugs.
    // Instead we apply Table.reverse() (a cheap row-order flip — NOT a value
    // sort) to a private copy. The newest row is then ALWAYS index 0, so a fixed
    // viewport [0, WINDOW-1] never has to move. rows[0] is the newest value.
    const WINDOW = 200;

    // Close every server handle + subscription we hold, in order, each guarded.
    // Idempotent (nulls as it goes) so it is safe to call from both an early
    // cancel inside subscribe() AND the effect cleanup. `fetched` is skipped when
    // it IS baseTable (copy() fell back to the fetched handle) to avoid a double
    // close.
    const closeHandles = () => {
      if (removeUpdated) { try { removeUpdated(); } catch { /* ignore */ } removeUpdated = null; }
      if (vp) { try { vp.close(); } catch { /* ignore */ } vp = null; }
      if (baseTable) { try { baseTable.close(); } catch { /* ignore */ } }
      if (fetched && fetched !== baseTable) { try { fetched.close(); } catch { /* ignore */ } }
      if (reexported) { try { reexported.close(); } catch { /* ignore */ } }
      baseTable = null;
      fetched = null;
      reexported = null;
    };

    // Per-column running min/max for any channel whose `range` is omitted, so an
    // auto-ranged channel always uses the full observed span. Channel-keyed.
    const autoRanges = new Map<string, { min: number; max: number }>();
    const norm01 = (v: number, range: [number, number] | null | undefined, key: string): number => {
      let mn: number;
      let mx: number;
      if (range) {
        [mn, mx] = range;
      } else {
        let rr = autoRanges.get(key);
        if (!rr) {
          rr = { min: v, max: v };
          autoRanges.set(key, rr);
        } else {
          if (v < rr.min) rr.min = v;
          if (v > rr.max) rr.max = v;
        }
        mn = rr.min;
        mx = rr.max;
      }
      // Dynamics (velocity/duration): a flat signal stays audibly mid (0.5).
      return normalize01(v, mn, mx, 0.5);
    };
    const lerp = (t: number, lo: number, hi: number): number => lo + t * (hi - lo);

    const num = (raw: unknown): number => (typeof raw === 'number' ? raw : Number(raw));

    // Resolve this row's data-driven effect params → {path: outputValue}. Each
    // channel's INPUT domain is the column's running min/max (auto-tracked,
    // keyed per path); the engine maps the normalised 0..1 into the param's
    // output range. Returns undefined when nothing is data-driven this row.
    const computeParams = (row: dh.Row): Record<string, number> | undefined => {
      if (paramCols.length === 0) return undefined;
      const out: Record<string, number> = {};
      for (const pc of paramCols) {
        const v = num(row.get(pc.col));
        if (Number.isNaN(v)) continue;
        const t = norm01(v, null, `param:${pc.path}`);
        out[pc.path] = mapParam(pc.path, t, pc.min, pc.max);
      }
      return Object.keys(out).length ? out : undefined;
    };

    // Read a live [min, max] off the row from its server-side range columns, or
    // null when they're absent/invalid (→ caller falls back to client running
    // min/max or a static range).
    const readRange = (
      row: dh.Row,
      minCol: dh.Column | undefined,
      maxCol: dh.Column | undefined
    ): [number, number] | null => {
      if (!minCol || !maxCol) return null;
      const lo = num(row.get(minCol));
      const hi = num(row.get(maxCol));
      if (Number.isNaN(lo) || Number.isNaN(hi)) return null;
      return [lo, hi];
    };

    // Value→pitch: read every mapped column off the row and play one note
    // whose pitch / loudness / length / voice each carry a separate column.
    const playRowMulti = async (row: dh.Row) => {
      if (!multi || !pitchCol) return;
      const pitchVal = num(row.get(pitchCol));
      if (Number.isNaN(pitchVal)) return;

      // Per-row loudness range (server auto) shared by velocity + duration.
      const loudRange = readRange(row, loudMinCol, loudMaxCol);

      // velocity + duration are both driven by the SAME loudness column, so they
      // share one running-range key ('loudness') — one observed span, not two.
      let velocity = 1;
      if (multi.velocity && velCol) {
        const vv = num(row.get(velCol));
        if (!Number.isNaN(vv)) {
          const t = norm01(vv, loudRange ?? multi.velocity.range, 'loudness');
          velocity = lerp(t, multi.velocity.min ?? 0.3, multi.velocity.max ?? 1.0);
        }
      }

      let duration: number | undefined;
      if (multi.duration && durCol) {
        const dv = num(row.get(durCol));
        if (!Number.isNaN(dv)) {
          const t = norm01(dv, loudRange ?? multi.duration.range, 'loudness');
          duration = lerp(t, multi.duration.min ?? 0.15, multi.duration.max ?? 0.9);
        }
      }

      // Categorical → voice (instrument/envelope override). Selects the chain.
      const overrides: Partial<ToneConfig> = {};
      if (multi.voice && voiceCol) {
        const key = String(row.get(voiceCol));
        Object.assign(
          overrides,
          multi.voice.voices[key] ?? multi.voice.voices[key.trim()] ?? multi.voice.default ?? {}
        );
      }
      // Pitch range from server columns (auto) overrides config.valueRange.
      const pr = readRange(row, pitchMinCol, pitchMaxCol);
      if (pr) overrides.valueRange = pr;

      await ToneEngine.tone(
        { value: pitchVal, velocity, duration, overrides, params: computeParams(row) },
        configRef.current
      );
    };

    // A cell counts as a trigger when it's true / non-zero / a non-empty,
    // non-"false" string / a non-empty array (so a notes column can itself be
    // the gate — it fires whenever it carries notes).
    const isTruthyCell = (raw: unknown): boolean =>
      raw === true ||
      (typeof raw === 'number' && raw !== 0) ||
      (typeof raw === 'string' && raw !== '' && raw.toLowerCase() !== 'false') ||
      (Array.isArray(raw) && raw.length > 0);

    // Parse a cell into a flat note list: a String[] is used as-is; a String is
    // split on whitespace/commas. Returns null when there are no notes.
    const toNoteList = (cell: unknown): string[] | null => {
      let arr: string[];
      if (Array.isArray(cell)) {
        arr = cell.map(x => String(x).trim()).filter(Boolean);
      } else if (typeof cell === 'string') {
        arr = cell.split(/[\s,]+/).map(s => s.trim()).filter(Boolean);
      } else {
        return null;
      }
      return arr.length ? arr : null;
    };

    // Parse a cell into a list of chords (each a note list). Accepts a String[][]
    // (progression), a String[] (one chord), or a String where chords are split
    // on | or ; and notes within a chord on whitespace/commas.
    const toChordList = (cell: unknown): string[][] | null => {
      let chords: string[][];
      if (Array.isArray(cell)) {
        if (cell.length === 0) return null;
        chords = Array.isArray(cell[0])
          ? (cell as unknown[][]).map(c => (Array.isArray(c) ? c.map(String) : []).filter(Boolean))
          : [(cell as unknown[]).map(String).map(s => s.trim()).filter(Boolean)];
      } else if (typeof cell === 'string') {
        chords = cell
          .split(/[|;]/)
          .map(seg => seg.split(/[\s,]+/).map(s => s.trim()).filter(Boolean));
      } else {
        return null;
      }
      chords = chords.filter(c => c.length > 0);
      return chords.length ? chords : null;
    };

    // Trigger row: fire a chord progression and/or a melodic sequence depending
    // on which trigger column(s) are truthy. When a per-row notes column is
    // configured, its cell supplies the chord(s)/notes; otherwise the static
    // default is used. Most rows are falsy → silent.
    const playTriggerRow = async (row: dh.Row) => {
      if (chordTrig && chordTriggerCol && isTruthyCell(row.get(chordTriggerCol))) {
        const fromCol = chordNotesCol ? toChordList(row.get(chordNotesCol)) : null;
        await ToneEngine.chordSequence(
          { chords: fromCol ?? chordTrig.chords, gap: chordTrig.gap, duration: chordTrig.duration },
          configRef.current
        );
      }
      if (seqTrig && seqTriggerCol && isTruthyCell(row.get(seqTriggerCol))) {
        const fromCol = seqNotesCol ? toNoteList(row.get(seqNotesCol)) : null;
        const notes = fromCol ? fromCol.map(n => ({ note: n })) : seqTrig.notes;
        await ToneEngine.sequence({ notes, gap: seqTrig.gap }, configRef.current);
      }
    };

    const hasTrigger = () => !!chordTriggerCol || !!seqTriggerCol;

    // `sizeSnapshot` is captured SYNCHRONOUSLY in the listener (before this runs
    // on the serialized queue) so it matches this event's `detail`. Reading
    // baseTable.size here instead would race: fresh ticks bump the live size
    // while earlier handlers await, collapsing `newRows` and dropping notes.
    const handleUpdate = async (detail: dh.ViewportData | undefined, sizeSnapshot: number) => {
      if (cancelled || !baseTable || (!pitchCol && !hasTrigger())) return;

      // How many of the rows in this event are genuinely new (and should sound).
      let newRows: number;
      if (blink) {
        // Blink tables don't GROW — they replace their contents each cycle, so
        // every EVENT_UPDATED already carries only the freshly-added rows. The
        // size-delta gate below would see size stay flat (e.g. 1) and silence
        // everything after the first tick, so for blink we treat the whole
        // event payload as new. (Row count is bounded by the viewport read.)
        newRows = detail?.rows?.length ?? 0;
      } else {
        const size = sizeSnapshot;
        // `primed` makes the first update sonify just the current newest value
        // (immediate feedback) instead of replaying the whole history.
        newRows = primed ? size - lastSize : Math.min(size, 1);
        lastSize = size;
        primed = true;
      }
      if (newRows <= 0) return; // update fired but no new rows — nothing to play

      if (!detail) return;
      const { rows } = detail;
      if (!rows || rows.length === 0) return;
      // Reversed table: rows[0] = newest, rows[1] = 2nd newest, …

      // Ensure the audio context is running.
      try { await ToneEngine.start(); } catch { /* ignore */ }
      if (cancelled || !baseTable || (!pitchCol && !hasTrigger())) return;

      const playOne = chordTrig || seqTrig
        ? (r: dh.Row) => playTriggerRow(r)
        : (r: dh.Row) => playRowMulti(r);

      if (mode === 'last') {
        // Throttle: at most one note per rateLimitMs.
        const now = Date.now();
        if (now - lastThrottleTime < rateLimitMs) return;
        lastThrottleTime = now;
        await playOne(rows[0]);
      } else {
        // mode === 'all': play each new row, oldest → newest. The new rows are
        // the first `newRows` entries (indices 0..newRows-1, newest first), so
        // we iterate downward to emit them in chronological order.
        const count = Math.min(newRows, rows.length);
        if (newRows > rows.length) {
          // The viewport only holds WINDOW rows; a single tick added more than
          // that, so the overflow is unreachable. Surface it rather than drop
          // silently (which would read as "played everything").
          console.warn(
            `[deephaven-plugin-tones] dropped ${newRows - rows.length} row(s) in one tick: ` +
            `exceeded viewport window (${WINDOW}). Increase WINDOW or reduce tick batch size.`
          );
        }
        for (let i = count - 1; i >= 0; i -= 1) {
          if (cancelled) break;
          // eslint-disable-next-line no-await-in-loop
          await playOne(rows[i]);
        }
      }
    };

    async function subscribe() {
      try {
        // Resolve the ExportedObject → live dh.Table. Every handle is stored on
        // the effect-scoped vars so closeHandles() (cancel branch OR cleanup)
        // closes ALL of them — reexported, fetched, and the copy below.
        reexported = await srcTable.reexport();
        if (cancelled) { closeHandles(); return; }
        fetched = (await reexported.fetch()) as dh.Table;
        if (cancelled) { closeHandles(); return; }
      } catch (e) {
        if (!cancelled) {
          console.warn('[deephaven-plugin-tones] Failed to fetch table:', e);
        }
        closeHandles();
        return;
      }

      // Auto-detect a blink table from the JSAPI (a blink table replaces its
      // rows each cycle rather than growing). No manual flag needed.
      try {
        blink = typeof fetched.isBlinkTable === 'function' && fetched.isBlinkTable();
      } catch {
        blink = false;
      }

      // Work on an independent copy so reversing it does NOT reorder the user's
      // visible ui.table. Fall back to the fetched handle if copy() is missing.
      try {
        baseTable = await fetched.copy();
        if (cancelled) { closeHandles(); return; }
      } catch {
        baseTable = fetched;
      }

      // Resolve every mapped column. Missing the pitch column is fatal; missing
      // an optional channel column just disables that channel.
      const find = (name: string): dh.Column | undefined => {
        try {
          return baseTable!.findColumn(name);
        } catch {
          console.warn('[deephaven-plugin-tones] Column not found:', name);
          return undefined;
        }
      };
      const viewportCols: dh.Column[] = [];
      if (multi) {
        pitchCol = find(multi.pitch.column);
        if (multi.pitch.minColumn) pitchMinCol = find(multi.pitch.minColumn);
        if (multi.pitch.maxColumn) pitchMaxCol = find(multi.pitch.maxColumn);
        if (multi.velocity) velCol = find(multi.velocity.column);
        if (multi.duration) durCol = find(multi.duration.column);
        if (multi.voice) voiceCol = find(multi.voice.column);
        // Loudness range columns (velocity/duration share the same source col).
        const loudMin = multi.velocity?.minColumn ?? multi.duration?.minColumn;
        const loudMax = multi.velocity?.maxColumn ?? multi.duration?.maxColumn;
        if (loudMin) loudMinCol = find(loudMin);
        if (loudMax) loudMaxCol = find(loudMax);
      }
      // Data-driven effect-param channels — resolve each path's column. These
      // ride along with the per-note paths (single + multi), so they need a
      // pitch column to attach to; a path whose node is absent no-ops on apply.
      if (paramMappings) {
        for (const path of Object.keys(paramMappings)) {
          const ch = paramMappings[path];
          const col = ch && ch.column ? find(ch.column) : undefined;
          if (col) paramCols.push({ path, col, min: ch.min, max: ch.max });
        }
      }
      // Trigger columns (either can be the only thing this view reads), plus
      // optional per-row note/chord source columns.
      if (chordTrig) {
        chordTriggerCol = find(chordTrig.column);
        if (chordTrig.notesColumn) chordNotesCol = find(chordTrig.notesColumn);
      }
      if (seqTrig) {
        seqTriggerCol = find(seqTrig.column);
        if (seqTrig.notesColumn) seqNotesCol = find(seqTrig.notesColumn);
      }
      // The viewport must carry every column we read per row.
      [
        pitchCol, velCol, durCol, voiceCol,
        pitchMinCol, pitchMaxCol, loudMinCol, loudMaxCol,
        chordTriggerCol, seqTriggerCol, chordNotesCol, seqNotesCol,
        ...paramCols.map(pc => pc.col),
      ].forEach(c => {
        if (c && !viewportCols.includes(c)) viewportCols.push(c);
      });
      // Need at least one thing to read: a pitch column or a trigger column.
      // Nothing usable → close the handles we opened rather than leak them.
      if (!pitchCol && !chordTriggerCol && !seqTriggerCol) { closeHandles(); return; }

      // Reverse (row-order flip, NOT a value sort) so the newest row is index 0.
      try {
        baseTable.applySort([dhApi.Table.reverse()]);
      } catch (e) {
        console.warn('[deephaven-plugin-tones] reverse() unsupported:', e);
      }

      removeUpdated = baseTable.addEventListener<dh.ViewportData>(
        dhApi.Table.EVENT_UPDATED,
        e => {
          if (cancelled || !baseTable) return;
          // Capture size + detail SYNCHRONOUSLY, then process on the serialized
          // queue so updates never overlap or race on the shared bookkeeping.
          const sizeSnapshot = baseTable.size;
          const { detail } = e;
          queue = queue
            .then(() => handleUpdate(detail, sizeSnapshot))
            .catch(err => console.warn('[deephaven-plugin-tones] update error:', err));
        }
      );

      // Fixed viewport over the newest WINDOW rows — restricted to mapped
      // columns. It is set ONCE and never moved: a reversed table keeps the
      // newest row at index 0 no matter how large the table grows.
      vp = baseTable.setViewport(0, WINDOW - 1, viewportCols);
    }

    subscribe();

    return () => {
      cancelled = true;
      // Closes listener, viewport, and ALL three server handles (guarded).
      closeHandles();
    };
  // The object-valued props (mappings/chord/sequence) are rebuilt fresh
  // server-side every render, so depending on them by identity would tear down
  // + rebuild the whole subscription (re-fetch, re-copy, leak) on every
  // unrelated re-render. We key on their STRUCTURE instead. `config` is
  // intentionally excluded — it's read live via configRef so a config change
  // takes effect without resubscribing. `mode`/`rateLimitMs` are stable
  // primitives, so depending on them directly is fine.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [table, mode, rateLimitMs, mappingsKey, paramMappingsKey, chordTriggerKey, sequenceTriggerKey]);

  // ── Render ────────────────────────────────────────────────────────────────
  // No DOM — the plugin takes zero layout space and renders nothing. Audio
  // unlock relies on the browser's sticky user-activation: by the time the user
  // has opened/interacted with the panel, AudioContext.resume() (called inside
  // ToneEngine.start()) succeeds. There is no separate "enable sound" button.
  return null;
}

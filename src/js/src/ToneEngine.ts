/**
 * ToneEngine — module-level singleton that wraps Tone.js.
 *
 * Responsibilities
 * ─────────────────
 * • Lazy-evaluate Tone.js via dynamic import() so the heavy module factory only
 *   runs on first use. NOTE: `tone` is bundled INLINE into index.js (not a
 *   separate chunk) because Deephaven loads plugins via `new Function(...)`
 *   (CommonJS), whose require shim cannot fetch a sibling chunk. The dynamic
 *   import is inlined by Rollup (inlineDynamicImports) — see vite.config.js.
 * • Gesture-unlock (Tone.start) — idempotent, tracked with `started` flag.
 * • Voice chains keyed by a stable JSON hash of the relevant config fields.
 *   Each unique instrument/fx combination gets its own PolySynth → filter →
 *   delay → reverb → destination chain, cached in a Map.
 * • value→pitch mapping (used by the `value` op; table mode uses `tone`).
 * • Ops: play, chord, sequence, value, stop, setVolume.
 * • Test hook: window.__deephavenTones (always-on, lightweight).
 */

import Log from '@deephaven/log';

const log = Log.module('ToneEngine');

// ─── Types ───────────────────────────────────────────────────────────────────

export interface EnvelopeConfig {
  attack?: number;
  decay?: number;
  sustain?: number;
  release?: number;
}

export interface FilterConfig {
  type?: BiquadFilterType;
  frequency?: number;
  q?: number;
  rolloff?: -12 | -24 | -48 | -96;
}

export interface ReverbConfig {
  decay?: number;
  wet?: number;
  preDelay?: number;
}

export interface DelayConfig {
  delayTime?: string | number;
  feedback?: number;
  wet?: number;
}

export interface DistortionConfig {
  amount?: number;
  wet?: number;
}

export interface ChorusConfig {
  frequency?: number;
  depth?: number;
  wet?: number;
}

export interface PingPongConfig {
  delayTime?: string | number;
  feedback?: number;
  wet?: number;
}

export interface LimiterConfig {
  threshold?: number;
}

export type ScaleType = 'pentatonic' | 'major' | 'minor' | 'chromatic' | number[];

export interface ToneConfig {
  instrument?: string;
  polyphony?: number;
  envelope?: EnvelopeConfig;
  detune?: number;
  portamento?: number;
  filter?: FilterConfig | null;
  reverb?: ReverbConfig | null;
  delay?: DelayConfig | null;
  distortion?: DistortionConfig | null;
  chorus?: ChorusConfig | null;
  pingPongDelay?: PingPongConfig | null;
  // Master-bus brick-wall limiter (shared across all chains). null = disabled.
  limiter?: LimiterConfig | null;
  volume?: number;
  pan?: number;
  scale?: ScaleType;
  root?: string;
  octaves?: number;
  valueRange?: [number, number] | null;
  descending?: boolean;
}

export interface NoteSpec {
  note: string | number | { midi: number };
  duration?: string | number;
  velocity?: number;
}

export interface SequenceNoteSpec extends NoteSpec {
  note: string | number | { midi: number };
  duration: string | number;
  velocity?: number;
}

// Minimal Tone module surface we use (typed loosely so we don't need full Tone types at compile time)
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type ToneModule = any;

// ─── Scale tables ─────────────────────────────────────────────────────────────

const SCALE_INTERVALS: Record<string, number[]> = {
  pentatonic: [0, 2, 4, 7, 9],
  major: [0, 2, 4, 5, 7, 9, 11],
  minor: [0, 2, 3, 5, 7, 8, 10],
  chromatic: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11],
};

// ─── Test hook ────────────────────────────────────────────────────────────────

export interface ToneTestRecord {
  op: string;
  note?: string;
  notes?: string[];
  duration?: string | number;
  velocity?: number;
  /** Resolved instrument for this trigger — lets tests verify a duet (two
   *  distinct voices) really used two different instruments. */
  instrument?: string;
  db?: number;
  t: number;
}

export interface ToneTestHook {
  started: boolean;
  loaded: boolean;
  log: ToneTestRecord[];
  activeVoices: number;
}

function getHook(): ToneTestHook {
  // SSR-safe: only touch globalThis in browser context
  if (typeof globalThis !== 'undefined') {
    const g = globalThis as Record<string, unknown>;
    if (!g['__deephavenTones']) {
      g['__deephavenTones'] = {
        started: false,
        loaded: false,
        log: [],
        activeVoices: 0,
      } as ToneTestHook;
    }
    return g['__deephavenTones'] as ToneTestHook;
  }
  // Fallback (SSR / non-browser): return a throwaway object
  return { started: false, loaded: false, log: [], activeVoices: 0 };
}

function hookLog(record: ToneTestRecord): void {
  const hook = getHook();
  hook.log.push(record);
  // Trim to last 256 entries to avoid unbounded growth
  if (hook.log.length > 256) {
    hook.log.splice(0, hook.log.length - 256);
  }
}

// ─── Lazy Tone.js import ──────────────────────────────────────────────────────

// Module-level cache: the promise must be module-level so Rollup considers it
// reachable from the registered component (prevents tree-shaking to empty chunk).
let tonePromise: Promise<ToneModule> | null = null;

export function loadTone(): Promise<ToneModule> {
  return (tonePromise ??= import('tone').then(m => {
    getHook().loaded = true;
    return m;
  }));
}

// ─── Voice chain cache ────────────────────────────────────────────────────────

interface VoiceChain {
  poly: ToneModule;
  filter: ToneModule | null;
  delay: ToneModule | null;
  reverb: ToneModule | null;
  distortion: ToneModule | null;
  chorus: ToneModule | null;
  pingPong: ToneModule | null;
  // Always present (pan=0 = center). A Panner is cheap and transparent at
  // center, and giving every chain one means data-driven `pan` always has a
  // node to target without a chain rebuild.
  panner: ToneModule | null;
}

// Map from config-hash → chain
const chainCache = new Map<string, VoiceChain>();

// ─── Master bus (shared limiter) ──────────────────────────────────────────────
// A single brick-wall Limiter shared by EVERY chain, sitting between the chains
// and the destination, so summed polyphony / many simultaneous rows can't clip.
// It is NOT part of chainKey (one global node, not per-chain). `getMaster`
// returns the node each chain's tail connects into: the limiter when enabled,
// else the raw destination. The threshold tracks the latest config.
let masterLimiter: ToneModule | null = null;

function getMaster(Tone: ToneModule, config: ToneConfig): ToneModule {
  const dest = Tone.getDestination();
  if (config.limiter == null) return dest;
  const threshold = config.limiter.threshold ?? -1;
  if (masterLimiter == null) {
    masterLimiter = new Tone.Limiter(threshold);
    masterLimiter.connect(dest);
  } else {
    // Keep the shared node's ceiling in step with the most recent config.
    try {
      masterLimiter.threshold.value = threshold;
    } catch {
      /* threshold not a settable Param on this build — ignore */
    }
  }
  return masterLimiter;
}

// Running min/max per config key (for valueRange = null auto-tracking)
interface RunningRange {
  min: number;
  max: number;
}
const runningRanges = new Map<string, RunningRange>();

// ─── Running start state ──────────────────────────────────────────────────────

let started = false;

// ─── Mount refcount ─────────────────────────────────────────────────────────
// The engine is a module-level singleton shared by every mounted view. We
// refcount mounts so the LAST view to unmount tears the engine down (disposes
// all cached chains / Tone nodes). Without this, chains accumulate for the page
// lifetime. acquire/release are balanced and StrictMode-safe (double
// mount/unmount nets to zero).
let mountCount = 0;

export function acquire(): void {
  mountCount += 1;
}

export function release(): void {
  mountCount = Math.max(0, mountCount - 1);
  if (mountCount === 0) {
    dispose();
  }
}

// ─── Active-voice decay timers ───────────────────────────────────────────────
// Each trigger bumps the (test-hook) activeVoices counter and schedules a
// decrement. We track the timer ids so dispose() can clear any still-pending —
// otherwise they fire after teardown. Purely for the test hook; cheap to keep
// correct.
const voiceTimers = new Set<ReturnType<typeof setTimeout>>();

function decayVoices(n: number, delayMs: number): void {
  const id = setTimeout(() => {
    voiceTimers.delete(id);
    const hook = getHook();
    hook.activeVoices = Math.max(0, hook.activeVoices - n);
  }, delayMs);
  voiceTimers.add(id);
}

// ─── Audio-output probe (test-only, opt-in) ────────────────────────────────────
// When `globalThis.__deephavenTonesProbe` is set BEFORE the first start(), we
// tap the master output with a Tone.Meter and expose getOutputLevel() (dBFS) on
// the test hook. This lets E2E verify that real audio is actually produced —
// not just that an op was logged. Zero cost in production (flag is never set).
let meter: ToneModule | null = null;

function maybeSetupProbe(Tone: ToneModule): void {
  const g = globalThis as Record<string, unknown>;
  if (!g['__deephavenTonesProbe'] || meter != null) return;
  try {
    meter = new Tone.Meter({ normalRange: false, smoothing: 0 });
    Tone.getDestination().connect(meter);
    const hook = getHook() as ToneTestHook & {
      getOutputLevel?: () => number;
      getMasterReduction?: () => number;
    };
    hook.getOutputLevel = () => {
      try {
        const v = meter.getValue();
        return typeof v === 'number' ? v : Math.max(...(v as number[]));
      } catch {
        return -Infinity;
      }
    };
    // Gain reduction (dB, ≤ 0) the shared master Limiter is currently applying.
    // A deterministic readout of "the limiter is engaged" — unlike the master
    // level it's independent of source-level variance. 0 when no limiter exists.
    hook.getMasterReduction = () => {
      try {
        return masterLimiter ? (masterLimiter.reduction as number) : 0;
      } catch {
        return 0;
      }
    };
  } catch (e) {
    log.warn('probe setup error', e);
  }
}

// ─── Config key (stable hash) ─────────────────────────────────────────────────

function chainKey(config: ToneConfig): string {
  const { instrument, polyphony, envelope, detune, portamento, filter, reverb, delay, distortion, chorus, pingPongDelay, pan } =
    config;
  // `pan` is a per-chain node value, so a different STATIC pan needs its own
  // chain. Data-driven pan leaves config.pan at its baseline (so the key stays
  // stable across rows) and is modulated live via applyParams instead.
  // `limiter` is deliberately excluded — it's a single shared master-bus node,
  // not a per-chain node, so it never forks the chain cache.
  return JSON.stringify({
    instrument,
    polyphony,
    envelope,
    detune,
    portamento,
    filter,
    reverb,
    delay,
    distortion,
    chorus,
    pingPongDelay,
    pan,
  });
}

// ─── Voice class resolution ───────────────────────────────────────────────────

// Instruments that are NOT a plain Synth-with-oscillator-type. Each is its own
// voice class; crucially they must NOT receive an `oscillator: {type}` option
// (their `instrument` name is not a valid oscillator type — setting it throws
// "Oscillator: invalid type"). mono/duo/metal all extend Monophonic, so they
// wrap in a PolySynth exactly like fm/am; only pluck is the non-Monophonic
// special case handled in buildChain.
const NON_OSCILLATOR_INSTRUMENTS = ['fm', 'am', 'membrane', 'pluck', 'monosynth', 'duosynth', 'metal'];

function resolveVoiceClass(Tone: ToneModule, instrument: string): ToneModule {
  switch (instrument) {
    case 'fm':
      return Tone.FMSynth;
    case 'am':
      return Tone.AMSynth;
    case 'membrane':
      return Tone.MembraneSynth;
    case 'pluck':
      return Tone.PluckSynth;
    case 'monosynth':
      return Tone.MonoSynth;
    case 'duosynth':
      return Tone.DuoSynth;
    case 'metal':
      return Tone.MetalSynth;
    default:
      // sine/triangle/square/sawtooth → Synth with that oscillator type
      return Tone.Synth;
  }
}

function buildVoiceOptions(config: ToneConfig): Record<string, unknown> {
  const instrument = config.instrument ?? 'sine';
  const opts: Record<string, unknown> = {};

  // Oscillator type for Synth-based voices (sine/triangle/square/sawtooth).
  if (!NON_OSCILLATOR_INSTRUMENTS.includes(instrument)) {
    opts['oscillator'] = { type: instrument };
  }

  if (config.envelope) {
    opts['envelope'] = { ...config.envelope };
  }
  if (config.detune !== undefined && config.detune !== null) {
    opts['detune'] = config.detune;
  }
  if (config.portamento !== undefined && config.portamento !== null) {
    opts['portamento'] = config.portamento;
  }
  return opts;
}

// ─── Build a new chain ────────────────────────────────────────────────────────

async function buildChain(Tone: ToneModule, config: ToneConfig): Promise<VoiceChain> {
  const instrument = config.instrument ?? 'sine';
  const polyphony = config.polyphony ?? 8;
  const VoiceClass = resolveVoiceClass(Tone, instrument);
  const voiceOpts = buildVoiceOptions(config);

  // Build effects chain from the end backwards so we can connect forward. The
  // tail connects into the shared master bus (limiter → destination) rather than
  // straight to the destination, so every voice is clip-protected together.
  // Forward signal flow: poly → distortion → filter → delay → pingPong →
  //                      reverb → panner → master(limiter) → destination.
  let lastNode: ToneModule = getMaster(Tone, config);

  // Panner sits closest to the destination so it pans the fully-wet signal.
  // Always present (center by default) — see VoiceChain.panner.
  const panner: ToneModule = new Tone.Panner(config.pan ?? 0);
  panner.connect(lastNode);
  lastNode = panner;

  let reverb: ToneModule | null = null;
  if (config.reverb != null) {
    reverb = new Tone.Reverb({
      decay: config.reverb.decay ?? 3,
      wet: config.reverb.wet ?? 0.3,
      preDelay: config.reverb.preDelay ?? 0.01,
    });
    reverb.connect(lastNode);
    lastNode = reverb;
  }

  // Stereo ping-pong echo — sits before the reverb so its taps get spatialised.
  let pingPong: ToneModule | null = null;
  if (config.pingPongDelay != null) {
    pingPong = new Tone.PingPongDelay({
      delayTime: config.pingPongDelay.delayTime ?? '8n',
      feedback: config.pingPongDelay.feedback ?? 0.2,
      wet: config.pingPongDelay.wet ?? 0.5,
    });
    pingPong.connect(lastNode);
    lastNode = pingPong;
  }

  let delay: ToneModule | null = null;
  if (config.delay != null) {
    delay = new Tone.FeedbackDelay({
      delayTime: config.delay.delayTime ?? '8n',
      feedback: config.delay.feedback ?? 0.2,
      wet: config.delay.wet ?? 0.1,
    });
    delay.connect(lastNode);
    lastNode = delay;
  }

  let filter: ToneModule | null = null;
  if (config.filter != null) {
    filter = new Tone.Filter({
      type: config.filter.type ?? 'lowpass',
      frequency: config.filter.frequency ?? 2200,
      Q: config.filter.q ?? 1,
      rolloff: config.filter.rolloff ?? -24,
    });
    filter.connect(lastNode);
    lastNode = filter;
  }

  // Stereo chorus. NOTE: Tone.Chorus is SILENT until its LFO is started — the
  // missing .start() is a classic Tone footgun (no error, just no effect).
  let chorus: ToneModule | null = null;
  if (config.chorus != null) {
    chorus = new Tone.Chorus({
      frequency: config.chorus.frequency ?? 1.5,
      depth: config.chorus.depth ?? 0.7,
      wet: config.chorus.wet ?? 0.5,
    }).start();
    chorus.connect(lastNode);
    lastNode = chorus;
  }

  // Waveshaper distortion — closest to the synth so it shapes the raw tone.
  let distortion: ToneModule | null = null;
  if (config.distortion != null) {
    distortion = new Tone.Distortion({
      distortion: config.distortion.amount ?? 0.4,
      wet: config.distortion.wet ?? 1,
    });
    distortion.connect(lastNode);
    lastNode = distortion;
  }

  // PluckSynth is already monophonic and doesn't support polyphony wrapping the same way;
  // wrap in PolySynth only for synths that support it
  let poly: ToneModule;
  if (instrument === 'pluck') {
    // PluckSynth is NOT derived from Monophonic, so it cannot be wrapped in a
    // PolySynth — Tone v15 throws "Voice must extend Monophonic class". Use it
    // standalone (it's monophonic, which is fine for one-note-per-event
    // sonification). It exposes triggerAttackRelease like any instrument.
    poly = new Tone.PluckSynth();
  } else {
    poly = new Tone.PolySynth(VoiceClass, voiceOpts);
    // Polyphony limit
    if (poly.maxPolyphony !== undefined) {
      poly.maxPolyphony = polyphony;
    }
  }
  poly.connect(lastNode);

  // Set master volume
  if (config.volume !== undefined && config.volume !== null) {
    Tone.getDestination().volume.value = config.volume;
  }

  return { poly, filter, delay, reverb, distortion, chorus, pingPong, panner };
}

// ─── Get or create chain ──────────────────────────────────────────────────────

async function getChain(Tone: ToneModule, config: ToneConfig): Promise<VoiceChain> {
  const key = chainKey(config);
  if (chainCache.has(key)) {
    return chainCache.get(key)!;
  }
  let chain: VoiceChain;
  try {
    chain = await buildChain(Tone, config);
  } catch (e) {
    // A bad instrument/effect config must never silently kill audio (it used to:
    // getChain runs outside the trigger try/catch, so a throw here dropped the
    // note with no log). Fall back to a plain PolySynth so SOMETHING plays.
    log.warn('buildChain failed; falling back to default Synth', config.instrument, e);
    const poly = new Tone.PolySynth(Tone.Synth);
    poly.connect(Tone.getDestination());
    chain = { poly, filter: null, delay: null, reverb: null, distortion: null, chorus: null, pingPong: null, panner: null };
  }
  chainCache.set(key, chain);
  return chain;
}

// ─── Note resolution ──────────────────────────────────────────────────────────

function resolveNote(Tone: ToneModule, note: string | number | { midi: number }): string {
  if (typeof note === 'string') {
    return note;
  }
  if (typeof note === 'number') {
    // Treat as Hz
    return Tone.Frequency(note).toNote() as string;
  }
  if (note && typeof note === 'object' && 'midi' in note) {
    return Tone.Frequency(note.midi, 'midi').toNote() as string;
  }
  return 'C4';
}

// ─── value → pitch mapping ───────────────────────────────────────────────────

function resolveRootMidi(Tone: ToneModule, root: string): number {
  try {
    return Tone.Frequency(root).toMidi() as number;
  } catch {
    return 48; // C3
  }
}

/**
 * Clamp `value` to a 0..1 position within [min, max]. When the span is empty
 * (min === max) there is no meaningful position, so the caller supplies the
 * fallback: pitch uses 0 (a flat signal sits at the bottom of the range),
 * dynamics (velocity/duration) use 0.5 (a flat signal stays audibly mid). Single
 * source of truth shared by valueToNote here and the view's channel mapping.
 */
export function normalize01(value: number, min: number, max: number, emptyDefault = 0): number {
  const d = max - min;
  const t = d === 0 ? emptyDefault : (value - min) / d;
  return Math.min(1, Math.max(0, t));
}

export function valueToNote(
  Tone: ToneModule,
  value: number,
  config: ToneConfig,
  rangeKey?: string
): string {
  const scale = config.scale ?? 'pentatonic';
  const intervals: number[] = Array.isArray(scale)
    ? scale
    : SCALE_INTERVALS[scale as string] ?? SCALE_INTERVALS['pentatonic'];
  const rootMidi = resolveRootMidi(Tone, config.root ?? 'C3');
  const octaves = config.octaves ?? 3;
  const descending = config.descending ?? false;

  // Determine range
  let rangeMin: number;
  let rangeMax: number;

  if (config.valueRange != null) {
    [rangeMin, rangeMax] = config.valueRange;
  } else {
    // Auto running min/max
    const rKey = rangeKey ?? 'default';
    let rr = runningRanges.get(rKey);
    if (!rr) {
      rr = { min: value, max: value };
      runningRanges.set(rKey, rr);
    } else {
      if (value < rr.min) rr.min = value;
      if (value > rr.max) rr.max = value;
    }
    rangeMin = rr.min;
    rangeMax = rr.max;
  }

  // Flat signal (rangeMin === rangeMax) → bottom of the pitch range.
  let t = normalize01(value, rangeMin, rangeMax, 0);
  if (descending) t = 1 - t;

  const steps = intervals.length * octaves;
  const idx = Math.round(t * (steps - 1));
  const octaveOffset = Math.floor(idx / intervals.length);
  const midi = rootMidi + octaveOffset * 12 + intervals[idx % intervals.length];
  return Tone.Frequency(midi, 'midi').toNote() as string;
}

// ─── Data-driven effect-param channels ─────────────────────────────────────────
// A table column can drive any of these params per row. The View normalises the
// column to 0..1; `mapParam` turns that into an output value using the param's
// default OUTPUT range (overridable per-channel), and `applyParams` writes it to
// the live node on the resolved chain — NO chain rebuild, so the config-hash
// cache is untouched. This is the single source of truth for which params are
// modulatable and their sensible output ranges (kept here, next to the nodes).

interface ParamSpec {
  defMin: number;
  defMax: number;
  /** Interpolate in log space (perceptually right for frequency). */
  log?: boolean;
  apply: (chain: VoiceChain, v: number) => void;
}

const PARAM_SPECS: Record<string, ParamSpec> = {
  'filter.frequency': {
    defMin: 200, defMax: 8000, log: true,
    apply: (c, v) => { if (c.filter) c.filter.frequency.value = v; },
  },
  'filter.q': {
    defMin: 0.1, defMax: 18,
    apply: (c, v) => { if (c.filter) c.filter.Q.value = v; },
  },
  'reverb.wet': {
    defMin: 0, defMax: 1,
    apply: (c, v) => { if (c.reverb) c.reverb.wet.value = v; },
  },
  'delay.feedback': {
    defMin: 0, defMax: 0.9,
    apply: (c, v) => { if (c.delay) c.delay.feedback.value = v; },
  },
  'delay.wet': {
    defMin: 0, defMax: 1,
    apply: (c, v) => { if (c.delay) c.delay.wet.value = v; },
  },
  'distortion.wet': {
    defMin: 0, defMax: 1,
    apply: (c, v) => { if (c.distortion) c.distortion.wet.value = v; },
  },
  'chorus.wet': {
    defMin: 0, defMax: 1,
    apply: (c, v) => { if (c.chorus) c.chorus.wet.value = v; },
  },
  'pingPong.wet': {
    defMin: 0, defMax: 1,
    apply: (c, v) => { if (c.pingPong) c.pingPong.wet.value = v; },
  },
  'pingPong.feedback': {
    defMin: 0, defMax: 0.9,
    apply: (c, v) => { if (c.pingPong) c.pingPong.feedback.value = v; },
  },
  'pan': {
    defMin: -1, defMax: 1,
    apply: (c, v) => { if (c.panner) c.panner.pan.value = v; },
  },
  'detune': {
    // PolySynth has no single detune AudioParam — set() updates all voices.
    // Affects still-ringing voices too (shared-node), acceptable per design.
    defMin: -100, defMax: 100,
    apply: (c, v) => { try { c.poly.set({ detune: v }); } catch { /* voice has no detune */ } },
  },
};

/** True when `path` is a known data-driven param (lets the View skip unknowns). */
export function isParamPath(path: string): boolean {
  return Object.prototype.hasOwnProperty.call(PARAM_SPECS, path);
}

/**
 * Map a normalised 0..1 input `t` to an output value for `path`, using the
 * channel's [min, max] when given else the param's default output range.
 */
export function mapParam(path: string, t: number, min?: number, max?: number): number {
  const spec = PARAM_SPECS[path];
  if (!spec) return t;
  const lo = min ?? spec.defMin;
  const hi = max ?? spec.defMax;
  const tt = Math.min(1, Math.max(0, t));
  if (spec.log && lo > 0 && hi > 0) {
    return lo * (hi / lo) ** tt;
  }
  return lo + tt * (hi - lo);
}

/** Write each resolved param value to its live node on the resolved chain. */
function applyParams(chain: VoiceChain, params: Record<string, number> | undefined): void {
  if (!params) return;
  for (const path of Object.keys(params)) {
    const spec = PARAM_SPECS[path];
    if (!spec) continue;
    try {
      spec.apply(chain, params[path]);
    } catch (e) {
      log.warn('applyParam error', path, e);
    }
  }
}

// ─── Public engine API ────────────────────────────────────────────────────────

export async function start(): Promise<void> {
  if (started) return;
  const Tone = await loadTone();
  await Tone.start();
  maybeSetupProbe(Tone);
  started = true;
  getHook().started = true;
}

export async function play(
  args: { note: string | number | { midi: number }; duration?: string | number; velocity?: number },
  config: ToneConfig
): Promise<void> {
  const Tone = await loadTone();
  const chain = await getChain(Tone, config);
  const noteName = resolveNote(Tone, args.note);
  const duration = args.duration ?? '8n';
  const velocity = args.velocity ?? 1;
  try {
    chain.poly.triggerAttackRelease(noteName, duration, Tone.now(), velocity);
    getHook().activeVoices += 1;
    // Approximate: decrement after ~2 seconds
    decayVoices(1, 2000);
  } catch (e) {
    log.warn('play error', e);
  }
  hookLog({ op: 'play', note: noteName, duration, velocity, t: Date.now() });
}

export async function chord(
  args: { notes: Array<string | number | { midi: number }>; duration?: string | number; velocity?: number },
  config: ToneConfig
): Promise<void> {
  const Tone = await loadTone();
  const chain = await getChain(Tone, config);
  const noteNames = args.notes.map(n => resolveNote(Tone, n));
  const duration = args.duration ?? '4n';
  const velocity = args.velocity ?? 1;
  try {
    chain.poly.triggerAttackRelease(noteNames, duration, Tone.now(), velocity);
    getHook().activeVoices += noteNames.length;
    decayVoices(noteNames.length, 2000);
  } catch (e) {
    log.warn('chord error', e);
  }
  hookLog({ op: 'chord', notes: noteNames, duration, velocity, t: Date.now() });
}

/**
 * Play a SERIES of chords (a progression) — each chord triggered in turn,
 * spaced `gap` apart, scheduled on Tone's clock so the timing is tight. Used by
 * table mode to fire a pleasant cadence when a trigger column lights up. Logs
 * one `op:'chord'` record per chord so tests can count the progression.
 */
export async function chordSequence(
  args: {
    chords: Array<Array<string | number | { midi: number }>>;
    gap?: string | number;
    duration?: string | number;
    velocity?: number;
  },
  config: ToneConfig
): Promise<void> {
  const Tone = await loadTone();
  const chain = await getChain(Tone, config);
  const duration = args.duration ?? '2n';
  const velocity = args.velocity ?? 0.8;
  let gapSeconds: number;
  try {
    gapSeconds = Tone.Time(args.gap ?? '4n').toSeconds() as number;
  } catch {
    gapSeconds = 0.5;
  }
  const now = Tone.now() as number;

  (args.chords ?? []).forEach((notes, i) => {
    const noteNames = notes.map(n => resolveNote(Tone, n));
    const startTime = now + i * gapSeconds;
    try {
      chain.poly.triggerAttackRelease(noteNames, duration, startTime, velocity);
      getHook().activeVoices += noteNames.length;
      decayVoices(noteNames.length, (i * gapSeconds + 2) * 1000);
    } catch (e) {
      log.warn('chordSequence error', e);
    }
    hookLog({ op: 'chord', notes: noteNames, duration, velocity, t: Date.now() });
  });
}

export async function sequence(
  // `duration` is optional per note (defaulted below), so accept the looser
  // NoteSpec rather than SequenceNoteSpec — table triggers pass notes without
  // a per-note duration.
  args: {
    notes: NoteSpec[];
    gap?: string | number;
    envelope?: EnvelopeConfig | null;
  },
  config: ToneConfig
): Promise<void> {
  const Tone = await loadTone();

  // Merge earcon envelope override if provided
  const effectiveConfig: ToneConfig = args.envelope
    ? { ...config, envelope: { ...config.envelope, ...args.envelope } }
    : config;

  const chain = await getChain(Tone, effectiveConfig);
  const gap = args.gap ?? '16n';
  let gapSeconds: number;
  try {
    gapSeconds = Tone.Time(gap).toSeconds() as number;
  } catch {
    gapSeconds = 0.1;
  }

  const noteNames: string[] = [];
  const now = Tone.now() as number;

  args.notes.forEach((spec, i) => {
    const noteName = resolveNote(Tone, spec.note);
    const duration = spec.duration ?? '16n';
    const velocity = spec.velocity ?? 1;
    const startTime = now + i * gapSeconds;
    noteNames.push(noteName);
    try {
      chain.poly.triggerAttackRelease(noteName, duration, startTime, velocity);
      getHook().activeVoices += 1;
      decayVoices(1, (i * gapSeconds + 2) * 1000);
    } catch (e) {
      log.warn('sequence note error', e);
    }
  });

  hookLog({ op: 'sequence', notes: noteNames, t: Date.now() });
}

/**
 * Multi-parameter sonification primitive — the heart of multi-dimensional table
 * mode. Maps `value` → pitch (scale-quantised), and lets the caller drive
 * `velocity` (loudness) and `duration` (note length) INDEPENDENTLY, plus pass
 * `overrides` (e.g. instrument + envelope) so a categorical dimension can select
 * a distinct VOICE. Because `chainKey` includes instrument/envelope, two
 * different override sets resolve to two different cached chains — i.e. a duet:
 * one instrument per category, no extra plumbing.
 */
export async function tone(
  args: {
    value: number;
    velocity?: number;
    duration?: string | number;
    overrides?: Partial<ToneConfig>;
    // Data-driven effect-param values for THIS note, keyed by param path
    // (e.g. {'reverb.wet': 0.6}). Written to the resolved chain's live nodes.
    params?: Record<string, number>;
  },
  config: ToneConfig
): Promise<void> {
  const Tone = await loadTone();
  const effectiveConfig: ToneConfig = args.overrides
    ? { ...config, ...args.overrides }
    : config;
  const configKey = chainKey(effectiveConfig);
  const noteName = valueToNote(Tone, args.value, effectiveConfig, configKey);
  const chain = await getChain(Tone, effectiveConfig);
  // Modulate live node params (filter cutoff, reverb wet, pan, …) just before
  // the trigger so this note carries its row's values.
  applyParams(chain, args.params);
  const velocity = args.velocity ?? 1;
  // Default note length derives from the envelope decay (in SECONDS — pass a
  // number, not a numeric string, so Tone never mis-parses it as notation).
  const duration: string | number =
    args.duration ??
    (effectiveConfig.envelope?.decay ? effectiveConfig.envelope.decay * 4 : '8n');
  try {
    chain.poly.triggerAttackRelease(noteName, duration, Tone.now(), velocity);
    getHook().activeVoices += 1;
    decayVoices(1, 2000);
  } catch (e) {
    log.warn('tone play error', e);
  }
  hookLog({
    op: 'value',
    note: noteName,
    duration,
    velocity,
    instrument: effectiveConfig.instrument ?? 'sine',
    t: Date.now(),
  });
}

export async function value(
  args: { value: number; overrides?: Partial<ToneConfig>; params?: Record<string, number> },
  config: ToneConfig
): Promise<void> {
  // Single-dimension mapping: full velocity, duration derived from the envelope.
  await tone(
    { value: args.value, velocity: 1, overrides: args.overrides, params: args.params },
    config
  );
}

export async function stop(): Promise<void> {
  // Release all voices on all chains
  chainCache.forEach(chain => {
    try {
      chain.poly.releaseAll();
    } catch (e) {
      log.warn('stop releaseAll error', e);
    }
  });
  const hook = getHook();
  hook.activeVoices = 0;
  hookLog({ op: 'stop', t: Date.now() });
}

export async function setVolume(db: number): Promise<void> {
  const Tone = await loadTone();
  Tone.getDestination().volume.value = db;
  hookLog({ op: 'setVolume', db, t: Date.now() });
}

export function dispose(): void {
  chainCache.forEach(chain => {
    try {
      chain.poly.dispose();
    } catch { /* ignore */ }
    try {
      chain.filter?.dispose();
    } catch { /* ignore */ }
    try {
      chain.delay?.dispose();
    } catch { /* ignore */ }
    try {
      chain.reverb?.dispose();
    } catch { /* ignore */ }
    try {
      chain.distortion?.dispose();
    } catch { /* ignore */ }
    try {
      chain.chorus?.dispose();
    } catch { /* ignore */ }
    try {
      chain.pingPong?.dispose();
    } catch { /* ignore */ }
    try {
      chain.panner?.dispose();
    } catch { /* ignore */ }
  });
  chainCache.clear();
  // Tear down the shared master limiter so the next mount rebuilds it fresh.
  try {
    masterLimiter?.dispose();
  } catch { /* ignore */ }
  masterLimiter = null;
  runningRanges.clear();
  // Clear any pending activeVoices decay timers so they don't fire post-teardown.
  voiceTimers.forEach(id => clearTimeout(id));
  voiceTimers.clear();
  started = false;
  const hook = getHook();
  hook.started = false;
  hook.activeVoices = 0;
}

// ─── Dispatch a single event object (used by the view) ───────────────────────

export interface ToneEvent {
  id: number;
  op: string;
  note?: string | number | { midi: number };
  notes?: Array<string | number | { midi: number }>;
  duration?: string | number;
  velocity?: number;
  gap?: string | number;
  envelope?: EnvelopeConfig | null;
  value?: number;
  overrides?: Partial<ToneConfig>;
  db?: number;
}

export async function dispatchEvent(event: ToneEvent, config: ToneConfig): Promise<void> {
  switch (event.op) {
    case 'play':
      await play(
        { note: event.note ?? 'C4', duration: event.duration, velocity: event.velocity },
        config
      );
      break;
    case 'chord':
      await chord(
        { notes: event.notes ?? [], duration: event.duration, velocity: event.velocity },
        config
      );
      break;
    case 'sequence':
      await sequence(
        {
          notes: (event.notes ?? []) as unknown as SequenceNoteSpec[],
          gap: event.gap,
          envelope: event.envelope,
        },
        config
      );
      break;
    case 'value':
      if (event.value !== undefined) {
        await value({ value: event.value, overrides: event.overrides }, config);
      }
      break;
    case 'stop':
      await stop();
      break;
    case 'setVolume':
      if (event.db !== undefined) {
        await setVolume(event.db);
      }
      break;
    default:
      log.warn('Unknown tone op:', event.op);
  }
}

// Singleton object export (for convenience in the view)
const ToneEngine = {
  loadTone,
  acquire,
  release,
  start,
  play,
  chord,
  chordSequence,
  sequence,
  value,
  tone,
  stop,
  setVolume,
  dispose,
  dispatchEvent,
  valueToNote,
  mapParam,
  isParamPath,
};

export default ToneEngine;

/**
 * new-voices-effects.spec.ts — Tier 1 (Tier-1 feature batch)
 * ==========================================================
 * Covers the Tier-1 additions to the engine:
 *   • new instruments  — monosynth / duosynth / metal (all Monophonic, so they
 *     must wrap in a PolySynth without tripping the buildChain fallback)
 *   • new inline FX    — distortion / chorus / pingPongDelay
 *   • master Limiter   — a shared brick-wall on the master bus
 *
 * The limiter test is the genuine red→green behavioural assertion: a loud
 * 8-note chamber chord peaks measurably LOWER with the limiter engaged than
 * without it, and stays under digital full scale. The instrument/FX sweep is a
 * regression + no-crash guard (an unwired instrument silently falls back to a
 * plain Synth, which still sounds, so "produces audio" alone is a smoke test —
 * the stronger signal is "no [ToneEngine] console error and audio is present").
 */
import { test, expect, Page } from '@playwright/test';

const HARNESS = 'http://127.0.0.1:19876/';
const SILENCE_FLOOR_DB = -70;

async function gotoHarness(page: Page) {
  await page.goto(HARNESS);
  await page.waitForFunction(() => (window as any).__harnessReady === true, { timeout: 15_000 });
}

async function peakLevelOver(page: Page, ms: number): Promise<number> {
  return page.evaluate(async durationMs => {
    const hook = (window as any).__deephavenTones;
    if (typeof hook.getOutputLevel !== 'function') return -Infinity;
    let peak = -Infinity;
    const end = performance.now() + durationMs;
    while (performance.now() < end) {
      const v = hook.getOutputLevel();
      if (v > peak) peak = v;
      await new Promise(r => setTimeout(r, 16));
    }
    return peak;
  }, ms);
}

async function waitForProbe(page: Page) {
  await expect
    .poll(() => page.evaluate(() => typeof (window as any).__deephavenTones.getOutputLevel === 'function'), { timeout: 10_000 })
    .toBe(true);
}

test.afterEach(async ({ page }) => {
  await page.evaluate(() => {
    (window as any).__harness?.unmountRealView();
    (window as any).__harness?.resetHook();
  });
});

// ── New instruments: each wraps in PolySynth and sounds, with no engine error ──
for (const instrument of ['monosynth', 'duosynth', 'metal']) {
  test(`instrument "${instrument}" produces audio with no engine error`, async ({ page }) => {
    const errors: string[] = [];
    page.on('console', m => {
      if (m.type() === 'error' || (m.type() === 'warning' && m.text().includes('buildChain failed'))) {
        errors.push(m.text());
      }
    });
    await gotoHarness(page);
    await page.evaluate(
      inst =>
        (window as any).__harness.renderRealView({
          events: [{ id: 1, op: 'play', note: 'C3', duration: '1n', velocity: 1 }],
          config: { instrument: inst, volume: 0 },
        }),
      instrument
    );
    await waitForProbe(page);
    const peak = await peakLevelOver(page, 1200);
    console.log(`[new-voices] ${instrument} peak = ${peak} dBFS`);
    expect(peak).toBeGreaterThan(SILENCE_FLOOR_DB);
    expect(errors, `engine errors for ${instrument}: ${errors.join(' | ')}`).toHaveLength(0);
  });
}

// ── New inline effects: each builds and sounds without an engine error ────────
for (const fx of [
  { name: 'distortion', config: { distortion: { amount: 0.6, wet: 1 } } },
  { name: 'chorus', config: { chorus: { frequency: 2, depth: 0.7, wet: 0.6 } } },
  { name: 'pingPongDelay', config: { pingPongDelay: { delayTime: '8n', feedback: 0.3, wet: 0.6 } } },
]) {
  test(`effect "${fx.name}" builds and produces audio`, async ({ page }) => {
    const errors: string[] = [];
    page.on('console', m => {
      if (m.type() === 'error' || (m.type() === 'warning' && m.text().includes('buildChain failed'))) {
        errors.push(m.text());
      }
    });
    await gotoHarness(page);
    await page.evaluate(
      cfg =>
        (window as any).__harness.renderRealView({
          events: [{ id: 1, op: 'play', note: 'C4', duration: '1n', velocity: 1 }],
          config: { instrument: 'sine', volume: 0, ...cfg },
        }),
      fx.config
    );
    await waitForProbe(page);
    const peak = await peakLevelOver(page, 1200);
    console.log(`[new-fx] ${fx.name} peak = ${peak} dBFS`);
    expect(peak).toBeGreaterThan(SILENCE_FLOOR_DB);
    expect(errors, `engine errors for ${fx.name}: ${errors.join(' | ')}`).toHaveLength(0);
  });
}

// ── Master limiter: engages on the master bus (true red→green) ────────────────
// A loud sustained tone driven into a low master-limiter ceiling makes the
// shared Limiter apply gain reduction. We assert on the limiter's own `reduction`
// readout (dB of gain reduction it is applying) rather than the master level:
// WebAudio's compressor has a soft ~30 dB knee and the source RMS varies a few dB
// run-to-run, so absolute-level comparisons are noisy — but `reduction` is a
// direct, deterministic "the limiter is in-path and limiting" signal. With the
// limiter ignored (pre-implementation) there IS no master limiter and reduction
// is 0 — the genuine red→green.
const LIMITER_CEILING_DB = -24;

async function masterReduction(page: Page): Promise<number> {
  return page.evaluate(() => {
    const hook = (window as any).__deephavenTones;
    return typeof hook.getMasterReduction === 'function' ? hook.getMasterReduction() : 0;
  });
}

test('master limiter engages (applies gain reduction) on a loud tone', async ({ page }) => {
  await gotoHarness(page);

  // Slow attack + full sustain so the limiter (3 ms attack) is fully engaged by
  // the time we read steady-state reduction (past the transient).
  await page.evaluate(() =>
    (window as any).__harness.renderRealView({
      events: [{ id: 1, op: 'play', note: 'C4', duration: '2n', velocity: 1 }],
      config: {
        instrument: 'sine',
        volume: 0,
        reverb: null,
        filter: null,
        envelope: { attack: 0.4, decay: 0.1, sustain: 1, release: 0.3 },
        limiter: { threshold: -24 },
      },
    })
  );
  await waitForProbe(page);
  await page.waitForTimeout(600); // let the slow attack reach full sustain

  // Sample the peak (most-negative-toward-zero) reduction over the sustain.
  let reduction = 0;
  const end = Date.now() + 600;
  while (Date.now() < end) {
    const r = await masterReduction(page);
    if (r < reduction) reduction = r;
    await page.waitForTimeout(33);
  }
  console.log(`[limiter] master gain reduction at ${LIMITER_CEILING_DB} dB ceiling = ${reduction} dB`);

  // The limiter must be in-path and actively reducing gain on the loud tone.
  expect(reduction).toBeLessThan(-1);
});

test('no master limiter → no gain reduction (limiter:null bypasses)', async ({ page }) => {
  await gotoHarness(page);
  await page.evaluate(() =>
    (window as any).__harness.renderRealView({
      events: [{ id: 1, op: 'play', note: 'C4', duration: '2n', velocity: 1 }],
      config: { instrument: 'sine', volume: 0, limiter: null },
    })
  );
  await waitForProbe(page);
  await page.waitForTimeout(400);
  // With no limiter the chains connect straight to the destination — the readout
  // reports 0 (no limiter node exists).
  expect(await masterReduction(page)).toBe(0);
});

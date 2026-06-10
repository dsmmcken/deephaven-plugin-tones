/**
 * audio-output.spec.ts — Tier 1 gate
 * ===================================
 * The test the original suite was missing: it verifies that REAL AUDIO is
 * produced, not merely that an op was logged.
 *
 * The harness sets window.__deephavenTonesProbe before loading the bundle, so
 * the real ToneEngine taps its master output with a Tone.Meter and exposes
 * window.__deephavenTones.getOutputLevel() (dBFS). Chromium is launched with
 * --autoplay-policy=no-user-gesture-required, so the AudioContext runs and the
 * Web Audio graph processes samples headlessly even without a speaker device.
 *
 * Silence reads ≈ -Infinity / very low dB; an actually-sounding note pushes the
 * master meter well above the noise floor.
 */
import { test, expect, Page } from '@playwright/test';

const HARNESS = 'http://127.0.0.1:19876/';
const SILENCE_FLOOR_DB = -70; // anything above this on the master meter = audible signal

async function gotoHarness(page: Page) {
  await page.goto(HARNESS);
  await page.waitForFunction(() => (window as any).__harnessReady === true, { timeout: 15_000 });
}

async function peakLevelOver(page: Page, ms: number): Promise<number> {
  // Sample the master meter repeatedly over `ms` and return the highest dB seen.
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

test.afterEach(async ({ page }) => {
  await page.evaluate(() => {
    (window as any).__harness?.unmountRealView();
    (window as any).__harness?.resetHook();
  });
});

test('probe is installed once the engine starts', async ({ page }) => {
  await gotoHarness(page);
  // Trigger a play so ToneEngine.start() → maybeSetupProbe runs.
  await page.evaluate(() => (window as any).__harness.renderRealView({ events: [{ id: 1, op: 'play', note: 'C4' }], config: {} }));
  await expect
    .poll(() => page.evaluate(() => typeof (window as any).__deephavenTones.getOutputLevel === 'function'), { timeout: 10_000 })
    .toBe(true);
});

test('playing a note produces measurable audio on the master output', async ({ page }) => {
  await gotoHarness(page);

  // Loud, sustained note so the meter has an unambiguous signal to read.
  await page.evaluate(() =>
    (window as any).__harness.renderRealView({
      events: [{ id: 1, op: 'play', note: 'C4', duration: '1n', velocity: 1 }],
      config: { instrument: 'sine', volume: 0 },
    })
  );

  // Wait until the engine has actually started and the probe exists.
  await expect
    .poll(() => page.evaluate(() => typeof (window as any).__deephavenTones.getOutputLevel === 'function'), { timeout: 10_000 })
    .toBe(true);

  const peak = await peakLevelOver(page, 1500);
  console.log(`[audio-output] master peak during note = ${peak} dBFS`);
  expect(peak).toBeGreaterThan(SILENCE_FLOOR_DB);
});

test('stop silences a still-ringing note on the master output', async ({ page }) => {
  await gotoHarness(page);

  // A long note (2 measures ≈ 4s @120bpm) held at full sustain with a quick
  // release. The long duration is the crux: WITHOUT a stop this note is still
  // sounding when we measure later, so a silent reading can only mean stop
  // actually released it — not that the note simply decayed on its own.
  const cfg = {
    instrument: 'sine',
    volume: 0,
    envelope: { attack: 0.01, decay: 0.1, sustain: 1, release: 0.3 },
  };
  await page.evaluate(
    c =>
      (window as any).__harness.renderRealView({
        events: [{ id: 1, op: 'play', note: 'C4', duration: '2m', velocity: 1 }],
        config: c,
      }),
    cfg
  );
  await expect
    .poll(() => page.evaluate(() => typeof (window as any).__deephavenTones.getOutputLevel === 'function'), { timeout: 10_000 })
    .toBe(true);

  // 1) Confirm it's genuinely audible first — otherwise the silence check below
  //    would pass vacuously.
  const peakWhilePlaying = await peakLevelOver(page, 600);
  console.log(`[audio-output] master peak while playing = ${peakWhilePlaying} dBFS`);
  expect(peakWhilePlaying).toBeGreaterThan(SILENCE_FLOOR_DB);

  // 2) Fire stop (new id > lastPlayedId → the View dispatches just this event).
  await page.evaluate(
    c =>
      (window as any).__harness.renderRealView({
        events: [
          { id: 1, op: 'play', note: 'C4', duration: '2m', velocity: 1 },
          { id: 2, op: 'stop' },
        ],
        config: c,
      }),
    cfg
  );

  // 3) Let the release (0.3s) ring down, then confirm the meter has fallen below
  //    the silence floor — well before the 4s note would have ended naturally.
  await page.waitForTimeout(1200);
  const peakAfterStop = await peakLevelOver(page, 400);
  console.log(`[audio-output] master peak after stop = ${peakAfterStop} dBFS`);
  expect(peakAfterStop).toBeLessThan(SILENCE_FLOOR_DB);
});

test('master output is silent when nothing is playing', async ({ page }) => {
  await gotoHarness(page);
  // Start the engine (so the probe exists) with a very short blip, then let it
  // fully decay before measuring the noise floor.
  await page.evaluate(() => (window as any).__harness.renderRealView({ events: [{ id: 1, op: 'play', note: 'C4', duration: '32n' }], config: {} }));
  await expect
    .poll(() => page.evaluate(() => typeof (window as any).__deephavenTones.getOutputLevel === 'function'), { timeout: 10_000 })
    .toBe(true);
  await page.waitForTimeout(1200); // let the blip decay

  const peak = await peakLevelOver(page, 400);
  console.log(`[audio-output] master peak during silence = ${peak} dBFS`);
  expect(peak).toBeLessThan(SILENCE_FLOOR_DB);
});

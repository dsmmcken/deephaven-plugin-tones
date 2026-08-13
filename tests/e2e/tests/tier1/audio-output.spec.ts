/**
 * audio-output.spec.ts — Tier 1 gate
 * ===================================
 * Verifies that REAL AUDIO is produced, not merely that an op was logged.
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
import { test, expect, Page } from "@playwright/test";

const HARNESS = "http://127.0.0.1:19876/";
const SILENCE_FLOOR_DB = -70; // anything above this on the master meter = audible signal

async function gotoHarness(page: Page) {
  await page.goto(HARNESS);
  await page.waitForFunction(() => (window as any).__harnessReady === true, {
    timeout: 15_000,
  });
}

async function waitForProbe(page: Page) {
  await expect
    .poll(
      () =>
        page.evaluate(
          () =>
            typeof (window as any).__deephavenTones.getOutputLevel ===
            "function",
        ),
      { timeout: 10_000 },
    )
    .toBe(true);
}

async function peakLevelOver(page: Page, ms: number): Promise<number> {
  // Sample the master meter repeatedly over `ms` and return the highest dB seen.
  return page.evaluate(async (durationMs) => {
    const hook = (window as any).__deephavenTones;
    if (typeof hook.getOutputLevel !== "function") return -Infinity;
    let peak = -Infinity;
    const end = performance.now() + durationMs;
    while (performance.now() < end) {
      const v = hook.getOutputLevel();
      if (v > peak) peak = v;
      await new Promise((r) => setTimeout(r, 16));
    }
    return peak;
  }, ms);
}

test.beforeEach(async ({ page }) => {
  await gotoHarness(page);
  await page.evaluate(() => (window as any).__harness.resetHook());
});

test("probe is installed once the engine starts", async ({ page }) => {
  await page.evaluate(() =>
    (window as any).__harness.send({ op: "play", note: "C4", config: {} }),
  );
  await waitForProbe(page);
});

test("playing a note produces measurable audio on the master output", async ({
  page,
}) => {
  // Loud, sustained note so the meter has an unambiguous signal to read.
  await page.evaluate(() =>
    (window as any).__harness.send({
      op: "play",
      note: "C4",
      duration: "1n",
      velocity: 1,
      config: { instrument: "sine", volume: 0 },
    }),
  );
  await waitForProbe(page);

  const peak = await peakLevelOver(page, 1500);
  console.log(`[audio-output] master peak during note = ${peak} dBFS`);
  expect(peak).toBeGreaterThan(SILENCE_FLOOR_DB);
});

test("a rest in a sequence is silent, and the note after it sounds", async ({
  page,
}) => {
  // Rhythm check with the meter: a short blip, a long rest, then a long note.
  // Measuring during the rest window must read silence; measuring after it must
  // read signal — i.e. the rest really consumed its duration.
  await page.evaluate(() =>
    (window as any).__harness.send({
      op: "sequence",
      notes: [
        { note: "C4", duration: 0.15, velocity: 1 },
        { note: null, duration: 1.2 },
        { note: "C5", duration: 1.5, velocity: 1 },
      ],
      gap: 0,
      config: {
        instrument: "sine",
        volume: 0,
        envelope: { attack: 0.005, decay: 0.05, sustain: 1, release: 0.05 },
        reverb: null,
      },
    }),
  );
  await waitForProbe(page);

  // Let the first blip finish, then measure inside the rest.
  await page.waitForTimeout(500);
  const duringRest = await peakLevelOver(page, 500);
  console.log(`[audio-output] master peak during rest = ${duringRest} dBFS`);
  expect(duringRest).toBeLessThan(SILENCE_FLOOR_DB);

  const afterRest = await peakLevelOver(page, 800);
  console.log(`[audio-output] master peak after rest = ${afterRest} dBFS`);
  expect(afterRest).toBeGreaterThan(SILENCE_FLOOR_DB);
});

test("master output is silent when nothing is playing", async ({ page }) => {
  // Start the engine (so the probe exists) with a very short blip, then let it
  // fully decay before measuring the noise floor.
  await page.evaluate(() =>
    (window as any).__harness.send({
      op: "play",
      note: "C4",
      duration: "32n",
      config: {},
    }),
  );
  await waitForProbe(page);
  await page.waitForTimeout(1200); // let the blip decay

  const peak = await peakLevelOver(page, 400);
  console.log(`[audio-output] master peak during silence = ${peak} dBFS`);
  expect(peak).toBeLessThan(SILENCE_FLOOR_DB);
});

/**
 * events.spec.ts — Tier 1 gate
 * =============================
 * Drives every op the Python side can send through the plugin's real event
 * handler, and checks the engine logged what the payload asked for: the
 * instruments and effects, the multi-dimensional `tone` op (value → pitch with
 * a per-event range and voice override), the data-driven effect params, chord
 * progressions, and sequences with chords and rests.
 */
import { test, expect, Page } from "@playwright/test";

const HARNESS = "http://127.0.0.1:19876/";

type ToneRecord = {
  op: string;
  note?: string;
  notes?: string[];
  instrument?: string;
  duration?: string | number;
  velocity?: number;
};

async function gotoHarness(page: Page) {
  await page.goto(HARNESS);
  await page.waitForFunction(() => (window as any).__harnessReady === true, {
    timeout: 15_000,
  });
}

async function log(page: Page): Promise<ToneRecord[]> {
  return page.evaluate(() => (window as any).__deephavenTones.log);
}

async function send(page: Page, payload: Record<string, unknown>) {
  await page.evaluate((p) => (window as any).__harness.send(p), payload);
}

/** Wait until the log holds at least `n` records, then return it. */
async function logOfAtLeast(page: Page, n: number): Promise<ToneRecord[]> {
  await expect
    .poll(async () => (await log(page)).length, { timeout: 10_000 })
    .toBeGreaterThanOrEqual(n);
  return log(page);
}

test.beforeEach(async ({ page }) => {
  await gotoHarness(page);
  await page.evaluate(() => (window as any).__harness.resetHook());
});

test.describe("instruments and effects", () => {
  for (const instrument of [
    "sine",
    "triangle",
    "square",
    "sawtooth",
    "fm",
    "am",
    "membrane",
    "pluck",
    "monosynth",
    "duosynth",
    "metal",
  ]) {
    test(`instrument ${instrument} builds a chain and plays`, async ({
      page,
    }) => {
      const errors: string[] = [];
      page.on("pageerror", (e) => errors.push(String(e)));
      await send(page, { op: "play", note: "C4", config: { instrument } });
      await expect
        .poll(() => log(page), { timeout: 10_000 })
        .toContainEqual(expect.objectContaining({ op: "play", note: "C4" }));
      expect(errors).toEqual([]);
    });
  }

  test("every effect node can be enabled at once", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    await send(page, {
      op: "play",
      note: "C4",
      config: {
        instrument: "monosynth",
        filter: { type: "lowpass", frequency: 1200, q: 2, rolloff: -24 },
        reverb: { decay: 2, wet: 0.3, preDelay: 0.01 },
        delay: { delayTime: "8n", feedback: 0.2, wet: 0.2 },
        distortion: { amount: 0.5, wet: 0.8 },
        chorus: { frequency: 1.5, depth: 0.6, wet: 0.4 },
        pingPongDelay: { delayTime: "8n", feedback: 0.25, wet: 0.35 },
        limiter: { threshold: -2 },
      },
    });
    await expect
      .poll(() => log(page), { timeout: 10_000 })
      .toContainEqual(expect.objectContaining({ op: "play" }));
    expect(errors).toEqual([]);
  });
});

test.describe("ops", () => {
  test("chord plays every note together", async ({ page }) => {
    await send(page, {
      op: "chord",
      notes: ["C4", "E4", "G4"],
      duration: "2n",
      velocity: 1,
      config: {},
    });
    const records = await logOfAtLeast(page, 1);
    expect(records[0].op).toBe("chord");
    expect(records[0].notes).toEqual(["C4", "E4", "G4"]);
  });

  test("chordSequence logs one chord per progression step", async ({
    page,
  }) => {
    await send(page, {
      op: "chordSequence",
      chords: [
        ["C4", "E4", "G4"],
        ["G3", "B3", "D4"],
        ["A3", "C4", "E4"],
      ],
      gap: "8n",
      duration: "4n",
      config: {},
    });
    const records = await logOfAtLeast(page, 3);
    expect(records.filter((r) => r.op === "chord")).toHaveLength(3);
    expect(records[1].notes).toEqual(["G3", "B3", "D4"]);
  });

  test("sequence plays notes, chords and rests", async ({ page }) => {
    await send(page, {
      op: "sequence",
      notes: [
        { note: "C5", duration: "16n", velocity: 0.9 },
        { note: null, duration: "8n" },
        { note: ["C4", "E4", "G4"], duration: "4n", velocity: 0.8 },
      ],
      gap: 0,
      config: {},
    });
    const records = await logOfAtLeast(page, 1);
    const sequences = records.filter((r) => r.op === "sequence");
    expect(sequences).toHaveLength(1);
    // The rest contributes no note; the chord contributes all three.
    expect(sequences[0].notes).toEqual(["C5", "C4", "E4", "G4"]);
  });

  test("sequence envelope override does not break playback", async ({
    page,
  }) => {
    await send(page, {
      op: "sequence",
      notes: [{ note: "C5", duration: "16n" }],
      gap: 0,
      envelope: { attack: 0.005, sustain: 0 },
      config: {
        envelope: { attack: 0.02, decay: 0.1, sustain: 0.6, release: 1.2 },
      },
    });
    await expect
      .poll(() => log(page), { timeout: 10_000 })
      .toContainEqual(expect.objectContaining({ op: "sequence" }));
  });

  test("value maps a number onto the configured scale", async ({ page }) => {
    // Bottom and top of an explicit range → lowest and highest scale note.
    await send(page, {
      op: "value",
      value: 0,
      config: {
        scale: "pentatonic",
        root: "C3",
        octaves: 1,
        valueRange: [0, 100],
      },
    });
    await send(page, {
      op: "value",
      value: 100,
      config: {
        scale: "pentatonic",
        root: "C3",
        octaves: 1,
        valueRange: [0, 100],
      },
    });
    const records = await logOfAtLeast(page, 2);
    expect(records[0].note).toBe("C3");
    expect(records[1].note).not.toBe(records[0].note);
  });
});

test.describe("table-driven tone events", () => {
  test("per-event valueRange decides the pitch", async ({ page }) => {
    // The listener sends the live range with each row; the same value must map
    // differently under a different range.
    const config = { scale: "pentatonic", root: "C3", octaves: 1 };
    await send(page, {
      op: "tone",
      value: 5,
      overrides: { valueRange: [0, 10] },
      config,
    });
    await send(page, {
      op: "tone",
      value: 5,
      overrides: { valueRange: [0, 1000] },
      config,
    });
    const records = await logOfAtLeast(page, 2);
    expect(records[0].note).not.toBe(records[1].note);
  });

  test("a voice override selects a different instrument", async ({ page }) => {
    const config = {
      instrument: "sine",
      scale: "pentatonic",
      root: "C3",
      octaves: 2,
    };
    await send(page, {
      op: "tone",
      value: 1,
      overrides: { valueRange: [0, 10], instrument: "pluck" },
      config,
    });
    await send(page, {
      op: "tone",
      value: 9,
      overrides: { valueRange: [0, 10], instrument: "sawtooth" },
      config,
    });
    const records = await logOfAtLeast(page, 2);
    expect(records[0].instrument).toBe("pluck");
    expect(records[1].instrument).toBe("sawtooth");
  });

  test("velocity and duration ride along with the event", async ({ page }) => {
    await send(page, {
      op: "tone",
      value: 1,
      velocity: 0.42,
      duration: 0.5,
      overrides: { valueRange: [0, 10] },
      config: {},
    });
    const records = await logOfAtLeast(page, 1);
    expect(records[0].velocity).toBeCloseTo(0.42);
    expect(records[0].duration).toBeCloseTo(0.5);
  });

  test("data-driven params are applied without a chain rebuild", async ({
    page,
  }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    await send(page, {
      op: "tone",
      value: 1,
      overrides: { valueRange: [0, 10] },
      params: {
        "filter.frequency": { t: 0.8 },
        "reverb.wet": { t: 0.5, min: 0.1, max: 0.9 },
        pan: { t: 1 },
      },
      config: {
        filter: { type: "lowpass", frequency: 2200, q: 1, rolloff: -24 },
        reverb: { decay: 2, wet: 0.3, preDelay: 0.01 },
      },
    });
    await expect
      .poll(() => log(page), { timeout: 10_000 })
      .toContainEqual(expect.objectContaining({ op: "value" }));
    expect(errors).toEqual([]);
  });

  test("an unknown param path is ignored", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    await send(page, {
      op: "tone",
      value: 1,
      overrides: { valueRange: [0, 10] },
      params: { "not.a.param": { t: 0.5 } },
      config: {},
    });
    await expect
      .poll(() => log(page), { timeout: 10_000 })
      .toContainEqual(expect.objectContaining({ op: "value" }));
    expect(errors).toEqual([]);
  });
});

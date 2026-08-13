/**
 * real-bundle.spec.ts — Tier 1 gate
 * ==================================
 * Loads the REAL built bundle the way Deephaven does (CJS via
 * `new Function(module, exports, require, text)`) and asserts the shape the
 * host relies on: a plugin descriptor with an `eventMapping` entry for
 * `deephaven_plugin_tones.event`, and no element mapping (this plugin renders
 * nothing — tones are triggered by events).
 */
import { test, expect, Page } from "@playwright/test";

const HARNESS = "http://127.0.0.1:19876/";
const TONES_EVENT = "deephaven_plugin_tones.event";

async function gotoHarness(page: Page) {
  await page.goto(HARNESS);
  await page.waitForFunction(() => (window as any).__harnessReady === true, {
    timeout: 15_000,
  });
}

async function log(page: Page) {
  return page.evaluate(() => (window as any).__deephavenTones.log);
}

test.beforeEach(async ({ page }) => {
  await gotoHarness(page);
  await page.evaluate(() => (window as any).__harness.resetHook());
});

test("the bundle loads as CommonJS and exposes the event handler", async ({
  page,
}) => {
  const info = await page.evaluate(() =>
    (window as any).__harness.pluginInfo(),
  );
  expect(info.name).toBe("deephaven-plugin-tones");
  expect(info.eventName).toBe(TONES_EVENT);
  expect(info.hasHandler).toBe(true);
  // An event-only plugin contributes no elements.
  expect(info.elementKeys).toEqual([]);
});

test("a play event reaches the real engine", async ({ page }) => {
  await page.evaluate(() =>
    (window as any).__harness.send({
      op: "play",
      note: "C4",
      duration: "8n",
      velocity: 1,
      config: { instrument: "sine" },
    }),
  );
  await expect
    .poll(() => log(page), { timeout: 10_000 })
    .toContainEqual(expect.objectContaining({ op: "play", note: "C4" }));
});

test("an unknown op is ignored rather than throwing", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.evaluate(() =>
    (window as any).__harness.send({ op: "nope", config: {} }),
  );
  await page.waitForTimeout(300);
  expect(errors).toEqual([]);
});

test("Tone.js is bundled inline (no sibling chunk fetch)", async ({ page }) => {
  // Deephaven loads plugins with `new Function`, whose require shim cannot fetch
  // a sibling chunk — a code-split Tone.js would 404 here.
  const failed: string[] = [];
  page.on("requestfailed", (r) => failed.push(r.url()));
  page.on("response", (r) => {
    if (r.status() >= 400) failed.push(`${r.status()} ${r.url()}`);
  });
  await page.evaluate(() =>
    (window as any).__harness.send({ op: "play", note: "C4", config: {} }),
  );
  await expect
    .poll(() => page.evaluate(() => (window as any).__deephavenTones.loaded), {
      timeout: 10_000,
    })
    .toBe(true);
  expect(failed).toEqual([]);
});

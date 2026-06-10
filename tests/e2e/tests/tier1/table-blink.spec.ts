/**
 * table-blink.spec.ts — Tier 1 gate (blink-table sonification, auto-detected)
 * ============================================================================
 * A blink table REPLACES its rows each cycle instead of growing — its size
 * stays flat. The append-only size-delta path would compute
 * newRows = size - lastSize = 0 after the first tick and go silent. The View
 * auto-detects blink via the JSAPI `Table.isBlinkTable()` (NO manual flag) and
 * then treats each EVENT_UPDATED's payload as fresh rows.
 *
 * A blink tick may also add SEVERAL rows at once — in mode="all" every row in
 * the payload sounds; in mode="last" just the newest.
 *
 * The mock models a blink table: each __blinkTick(values[]) CLEARS the rows and
 * pushes the given new rows, then fires EVENT_UPDATED. `isBlinkTable()` returns
 * the value passed to setup, so the same fixture also provides a non-blink
 * control.
 */
import { test, expect, Page } from '@playwright/test';

const HARNESS = 'http://127.0.0.1:19876/';

async function gotoHarness(page: Page) {
  await page.goto(HARNESS);
  await page.waitForFunction(() => (window as any).__harnessReady === true, { timeout: 15_000 });
}

async function setupBlinkTable(page: Page, opts: { isBlink: boolean; mode: 'last' | 'all' }) {
  await page.evaluate(({ isBlink, mode }) => {
    const REVERSE = { __reverse: true };
    (window as any).__mockDhApi = {
      Table: { EVENT_UPDATED: 'updated', EVENT_SIZECHANGED: 'sizechanged', reverse: () => REVERSE },
    };

    let rows: Array<{ V: number }> = [];
    const listeners: Array<(e: unknown) => void> = [];
    let viewport = { first: 0, last: -1 };
    let reversed = false;

    const buildDetail = () => {
      const size = rows.length;
      const lo = Math.max(0, viewport.first);
      const hi = Math.min(viewport.last, size - 1);
      const out = [];
      for (let p = lo; p <= hi; p += 1) {
        const idx = reversed ? size - 1 - p : p;
        const rec = rows[idx];
        out.push({ get: (col: { name: string }) => (rec as any)[col.name] });
      }
      return { offset: lo, rows: out };
    };
    const fire = () => {
      const ev = { detail: buildDetail() };
      listeners.slice().forEach(cb => cb(ev));
    };
    const table: any = {
      get size() { return rows.length; }, // blink: never grows past a cycle
      isBlinkTable() { return isBlink; },
      findColumn(name: string) { return { name }; },
      copy() { return Promise.resolve(table); },
      applySort(s: any) { reversed = s.some((x: any) => x && x.__reverse); queueMicrotask(fire); return s; },
      setViewport(f: number, l: number) { viewport = { first: f, last: l }; queueMicrotask(fire); return { close() {} }; },
      addEventListener(_n: string, cb: any) { listeners.push(cb); return () => {}; },
    };
    // Blink: REPLACE the contents with the given new rows, then fire.
    (window as any).__blinkTick = (values: number[]) => {
      rows = values.map(V => ({ V }));
      queueMicrotask(fire);
    };
    const exported = {
      reexport: () => Promise.resolve({ fetch: () => Promise.resolve(table), close() {} }),
      fetch: () => Promise.resolve(table),
      close() {},
    };
    (window as any).__harness.renderRealView({
      table: exported,
      mappings: { pitch: { column: 'V' } },
      mode,
      rateLimitMs: 0,
      config: { scale: 'chromatic', root: 'C4', octaves: 2, valueRange: [0, 14] },
    });
  }, opts);
}

// Each tick replaces the table with `perTick` rows of cycling values.
async function blinkTicks(page: Page, n: number, perTick = 1) {
  await page.evaluate(async ({ count, perTick }) => {
    let v = 0;
    for (let i = 0; i < count; i += 1) {
      const vals = [];
      for (let k = 0; k < perTick; k += 1) { vals.push(v % 15); v += 1; }
      (window as any).__blinkTick(vals);
      // eslint-disable-next-line no-await-in-loop
      await new Promise(r => setTimeout(r, 6));
    }
  }, { count: n, perTick });
}

test.afterEach(async ({ page }) => {
  await page.evaluate(() => {
    (window as any).__harness?.unmountRealView();
    (window as any).__harness?.resetHook();
    delete (window as any).__mockDhApi;
    delete (window as any).__blinkTick;
  });
});

test('auto-detected blink: every tick plays, even though the table never grows', async ({
  page,
}) => {
  await gotoHarness(page);
  await setupBlinkTable(page, { isBlink: true, mode: 'last' });

  await blinkTicks(page, 30, 1);
  await page.waitForTimeout(200);

  const notes = await page.evaluate(() =>
    (window as any).__deephavenTones.log.filter((r: any) => r.op === 'value').map((r: any) => r.note)
  );
  expect(notes.length).toBeGreaterThan(20); // one sound per tick
  expect(new Set(notes).size).toBeGreaterThan(5); // reads each fresh row → modulates
});

test('blink mode=all: every row of a multi-row tick is sonified', async ({ page }) => {
  await gotoHarness(page);
  await setupBlinkTable(page, { isBlink: true, mode: 'all' });

  // 20 ticks × 3 rows each = 60 rows; all must sound.
  await blinkTicks(page, 20, 3);
  await page.waitForTimeout(250);

  const notes = await page.evaluate(() =>
    (window as any).__deephavenTones.log.filter((r: any) => r.op === 'value').map((r: any) => r.note)
  );
  expect(notes.length).toBeGreaterThan(45); // ~60 rows, allow scheduling slack
});

test('control: a non-blink table that does not grow goes silent after the first tick', async ({
  page,
}) => {
  await gotoHarness(page);
  await setupBlinkTable(page, { isBlink: false, mode: 'last' });

  await blinkTicks(page, 30, 1);
  await page.waitForTimeout(200);

  const notes = await page.evaluate(() =>
    (window as any).__deephavenTones.log.filter((r: any) => r.op === 'value').map((r: any) => r.note)
  );
  // isBlinkTable()===false → append-only semantics → flat size → silent.
  expect(notes.length).toBeLessThanOrEqual(2);
});

/**
 * table-tail.spec.ts — Tier 1 gate (regression for the "blaring one tone" bug)
 * ============================================================================
 * Reproduces the user-reported bugs: "blaring the same tone over and over" and
 * (after a partial fix) "plays the first note then stops".
 *
 * Root cause of both: an append-only Deephaven time_table puts new rows at the
 * END, and the original code read a viewport pinned to a fixed window — which
 * froze on stale rows once the table grew.
 *
 * The fix REVERSES a private copy of the table (Table.reverse() — a row-order
 * flip, NOT a value sort), so the newest row is always at index 0. The View
 * then keeps a FIXED viewport [0, WINDOW-1] that never has to move; rows[0] is
 * always the newest value.
 *
 * This test injects a mock JSAPI (window.__mockDhApi, picked up by the harness
 * adapter's useApi shim), renders the REAL View in table mode, and ticks the
 * table well past the window size. If reverse fails to take effect (so rows[0]
 * is the OLDEST/frozen row), the pitch stops modulating and these tests fail.
 */
import { test, expect, Page } from '@playwright/test';

const HARNESS = 'http://127.0.0.1:19876/';

async function gotoHarness(page: Page) {
  await page.goto(HARNESS);
  await page.waitForFunction(() => (window as any).__harnessReady === true, { timeout: 15_000 });
}

// Install a mock Deephaven JSAPI + a fake ticking table on the page, then render
// the real View in table mode. `valueFn` maps a 0-based row index → numeric cell
// value. Returns nothing; drive growth with __mockTick().
async function setupMockTable(
  page: Page,
  opts: { mode: 'last' | 'all'; valueExpr: string }
) {
  await page.evaluate(({ mode, valueExpr }) => {
    // eslint-disable-next-line no-new-func
    const valueFn = new Function('i', `return (${valueExpr});`) as (i: number) => number;

    // Minimal JSAPI surface the View touches. Table.reverse() returns a Sort
    // sentinel that applySort recognises (a row-order flip, not a value sort).
    const REVERSE = { __reverse: true };
    (window as any).__mockDhApi = {
      Table: {
        EVENT_UPDATED: 'updated',
        EVENT_SIZECHANGED: 'sizechanged',
        reverse: () => REVERSE,
      },
    };

    const values: number[] = [];
    const listeners: Array<(e: unknown) => void> = [];
    let viewport = { first: 0, last: -1 };
    let reversed = false;

    // The viewport data DH delivers as the EVENT_UPDATED `detail`. When the table
    // has been reversed, row position p maps to absolute index (size-1-p), so
    // rows[0] is the NEWEST appended value — exactly what the View relies on.
    const buildDetail = () => {
      const size = values.length;
      const lo = Math.max(0, viewport.first);
      const hi = Math.min(viewport.last, size - 1);
      const rows = [];
      for (let p = lo; p <= hi; p += 1) {
        const idx = reversed ? size - 1 - p : p;
        const v = values[idx];
        rows.push({ get: () => v });
      }
      return { offset: lo, rows };
    };

    const fire = () => {
      const ev = { detail: buildDetail() };
      listeners.slice().forEach(cb => cb(ev));
    };

    const table = {
      get size() {
        return values.length;
      },
      findColumn() {
        return { name: 'V' };
      },
      copy() {
        // A real copy is independent; for the test, reusing the same backing
        // store is fine — we only assert on what gets sonified.
        return Promise.resolve(table);
      },
      applySort(sorts: Array<{ __reverse?: boolean }>) {
        reversed = sorts.some(s => s && s.__reverse === true);
        queueMicrotask(fire);
        return sorts;
      },
      // Fixed viewport, set ONCE by the View and never moved.
      setViewport(first: number, last: number) {
        viewport = { first, last };
        queueMicrotask(fire);
        return { close() {} };
      },
      addEventListener(_name: string, cb: (e: unknown) => void) {
        listeners.push(cb);
        return () => {
          const idx = listeners.indexOf(cb);
          if (idx >= 0) listeners.splice(idx, 1);
        };
      },
    };

    // Appends one row and fires EVENT_UPDATED (a ticking table emits it every
    // cycle). On a reversed table the new row lands at position 0 of the fixed
    // viewport — no re-anchoring needed.
    (window as any).__mockTick = () => {
      values.push(valueFn(values.length));
      queueMicrotask(fire);
    };

    // The `table` prop arrives as an ExportedObject: reexport() → fetch() → Table.
    const exported = {
      reexport: () => Promise.resolve({ fetch: () => Promise.resolve(table), close() {} }),
      fetch: () => Promise.resolve(table),
      close() {},
    };

    (window as any).__harness.renderRealView({
      table: exported,
      mappings: { pitch: { column: 'V' } },
      mode,
      rateLimitMs: 0, // disable throttle so every tick sonifies
      config: { scale: 'pentatonic', root: 'C3', octaves: 3, valueRange: [0, 14] },
    });
  }, opts);
}

async function tick(page: Page, n: number) {
  await page.evaluate(async count => {
    for (let i = 0; i < count; i += 1) {
      (window as any).__mockTick();
      // Yield so the View's async update handler runs to completion.
      // eslint-disable-next-line no-await-in-loop
      await new Promise(r => setTimeout(r, 4));
    }
  }, n);
}

test.afterEach(async ({ page }) => {
  await page.evaluate(() => {
    (window as any).__harness?.unmountRealView();
    (window as any).__harness?.resetHook();
    delete (window as any).__mockDhApi;
    delete (window as any).__mockTick;
  });
});

test('mode=last: pitch keeps modulating after the table grows past the old 100-row viewport', async ({
  page,
}) => {
  await gotoHarness(page);
  // value cycles 0..14 every 15 rows → consecutive rows are distinct pentatonic notes.
  await setupMockTable(page, { mode: 'last', valueExpr: 'i % 15' });

  // Grow well past the fixed viewport window (200) — where the old code froze.
  await tick(page, 240);

  const log = await page.evaluate(() => (window as any).__deephavenTones.log);
  const valueNotes = log.filter((r: any) => r.op === 'value').map((r: any) => r.note);

  // The latest ~30 sonifications are the latest ~30 rows. With the old frozen
  // viewport these would all be the SAME note; reversed, they keep cycling.
  const tail = valueNotes.slice(-30);
  const distinct = new Set(tail);
  expect(tail.length).toBeGreaterThan(10);
  expect(distinct.size).toBeGreaterThan(5); // cycling → many distinct; bug → exactly 1
});

test('mode=all: every appended row is sonified, including rows beyond the window', async ({
  page,
}) => {
  await gotoHarness(page);
  await setupMockTable(page, { mode: 'all', valueExpr: 'i % 15' });

  await tick(page, 230);

  const valueNotes = await page.evaluate(() =>
    (window as any).__deephavenTones.log
      .filter((r: any) => r.op === 'value')
      .map((r: any) => r.note)
  );
  // Every tick appends a row and must be sonified — so we expect a play per tick.
  expect(valueNotes.length).toBeGreaterThan(200);
  // …and they must be the NEW values, not a frozen one. If reverse failed and
  // rows[0] were the oldest row, the tail would collapse to a single note.
  const distinct = new Set(valueNotes.slice(-30));
  expect(distinct.size).toBeGreaterThan(5);
});

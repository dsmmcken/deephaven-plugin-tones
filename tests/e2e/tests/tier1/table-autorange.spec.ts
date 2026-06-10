/**
 * table-autorange.spec.ts — Tier 1 gate (server-side auto-range)
 * ============================================================================
 * Verifies the View consumes the live min/max columns that the Python layer
 * attaches via a min/max natural_join (the "auto range" default). The discriminating
 * test: feed the SAME value twice but with DIFFERENT min/max columns. If the
 * View reads the range columns, the two notes differ (same value, different
 * normalised position). If it ignored them and fell back to client running
 * min/max, a constant value would map to the same note both times.
 */
import { test, expect, Page } from '@playwright/test';

const HARNESS = 'http://127.0.0.1:19876/';

async function gotoHarness(page: Page) {
  await page.goto(HARNESS);
  await page.waitForFunction(() => (window as any).__harnessReady === true, { timeout: 15_000 });
}

test('pitch mapping uses the live min/max range columns, not client running min/max', async ({
  page,
}) => {
  await gotoHarness(page);

  await page.evaluate(() => {
    const REVERSE = { __reverse: true };
    (window as any).__mockDhApi = {
      Table: { EVENT_UPDATED: 'updated', EVENT_SIZECHANGED: 'sizechanged', reverse: () => REVERSE },
    };

    interface Rec { V: number; V_min: number; V_max: number }
    const rows: Rec[] = [];
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
      get size() { return rows.length; },
      findColumn(name: string) { return { name }; },
      copy() { return Promise.resolve(table); },
      applySort(s: any) { reversed = s.some((x: any) => x && x.__reverse); queueMicrotask(fire); return s; },
      setViewport(f: number, l: number) { viewport = { first: f, last: l }; queueMicrotask(fire); return { close() {} }; },
      addEventListener(_n: string, cb: any) { listeners.push(cb); return () => {}; },
    };
    // Push a row with explicit value + range columns.
    (window as any).__pushRow = (V: number, V_min: number, V_max: number) => {
      rows.push({ V, V_min, V_max });
      queueMicrotask(fire);
    };
    const exported = {
      reexport: () => Promise.resolve({ fetch: () => Promise.resolve(table), close() {} }),
      fetch: () => Promise.resolve(table),
      close() {},
    };
    (window as any).__harness.renderRealView({
      table: exported,
      mappings: { pitch: { column: 'V', minColumn: 'V_min', maxColumn: 'V_max' } },
      mode: 'all',
      rateLimitMs: 0,
      config: { scale: 'chromatic', root: 'C3', octaves: 3 },
    });
  });

  // Same value (50) both times, but a much wider range the second time → the
  // normalised position drops, so the note must be lower/different.
  await page.evaluate(() => {
    (window as any).__pushRow(50, 0, 100); // t = 0.50
  });
  await page.waitForFunction(
    () => (window as any).__deephavenTones.log.filter((r: any) => r.op === 'value').length >= 1,
    { timeout: 5000 }
  );
  await page.evaluate(() => {
    (window as any).__pushRow(50, 0, 1000); // t = 0.05 — same value, wider range
  });
  await page.waitForFunction(
    () => (window as any).__deephavenTones.log.filter((r: any) => r.op === 'value').length >= 2,
    { timeout: 5000 }
  );

  const notes = await page.evaluate(() =>
    (window as any).__deephavenTones.log.filter((r: any) => r.op === 'value').map((r: any) => r.note)
  );
  expect(notes.length).toBeGreaterThanOrEqual(2);
  // Same value, different live range columns → different pitch. If the range
  // columns were ignored, a constant value would produce the same note twice.
  expect(notes[0]).not.toBe(notes[1]);

  await page.evaluate(() => {
    (window as any).__harness?.unmountRealView();
    (window as any).__harness?.resetHook();
    delete (window as any).__mockDhApi;
    delete (window as any).__pushRow;
  });
});

/**
 * table-sequence-trigger.spec.ts — Tier 1 gate (melodic sequence on a trigger row)
 * ============================================================================
 * The play_sequence analogue: only flagged rows fire a timed melody; unflagged
 * rows are silent. Verifies:
 *   • a 'sequence' op fires ONCE per trigger row (5 triggers → 5 sequences),
 *   • each sequence carries the configured notes,
 *   • non-trigger rows make no sound.
 */
import { test, expect, Page } from '@playwright/test';

const HARNESS = 'http://127.0.0.1:19876/';
const MOTIF = [
  { note: 'C5', duration: '16n' },
  { note: 'E5', duration: '16n' },
  { note: 'G5', duration: '16n' },
  { note: 'C6', duration: '16n' },
];

async function gotoHarness(page: Page) {
  await page.goto(HARNESS);
  await page.waitForFunction(() => (window as any).__harnessReady === true, { timeout: 15_000 });
}

async function setupTriggerTable(page: Page, notes: Array<{ note: string }>) {
  await page.evaluate(({ notes }) => {
    const REVERSE = { __reverse: true };
    (window as any).__mockDhApi = {
      Table: { EVENT_UPDATED: 'updated', EVENT_SIZECHANGED: 'sizechanged', reverse: () => REVERSE },
    };
    interface Rec { Beat: number; Sparkle: boolean }
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
    const fire = () => listeners.slice().forEach(cb => cb({ detail: buildDetail() }));
    const table: any = {
      get size() { return rows.length; },
      findColumn(name: string) { return { name }; },
      copy() { return Promise.resolve(table); },
      applySort(s: any) { reversed = s.some((x: any) => x && x.__reverse); queueMicrotask(fire); return s; },
      setViewport(f: number, l: number) { viewport = { first: f, last: l }; queueMicrotask(fire); return { close() {} }; },
      addEventListener(_n: string, cb: any) { listeners.push(cb); return () => {}; },
    };
    (window as any).__tick = () => {
      const i = rows.length;
      rows.push({ Beat: i, Sparkle: i % 10 === 0 }); // every 10th row triggers
      queueMicrotask(fire);
    };
    const exported = {
      reexport: () => Promise.resolve({ fetch: () => Promise.resolve(table), close() {} }),
      fetch: () => Promise.resolve(table),
      close() {},
    };
    (window as any).__harness.renderRealView({
      table: exported,
      mode: 'all',
      rateLimitMs: 0,
      config: { instrument: 'triangle' },
      sequenceTrigger: { column: 'Sparkle', notes, gap: '16n' },
    });
  }, { notes });
}

async function tick(page: Page, n: number) {
  await page.evaluate(async count => {
    for (let i = 0; i < count; i += 1) {
      (window as any).__tick();
      // eslint-disable-next-line no-await-in-loop
      await new Promise(r => setTimeout(r, 5));
    }
  }, n);
}

test.afterEach(async ({ page }) => {
  await page.evaluate(() => {
    (window as any).__harness?.unmountRealView();
    (window as any).__harness?.resetHook();
    delete (window as any).__mockDhApi;
    delete (window as any).__tick;
  });
});

test('a melodic sequence fires once per trigger row, with the configured notes', async ({
  page,
}) => {
  await gotoHarness(page);
  await setupTriggerTable(page, MOTIF);

  await tick(page, 50); // triggers at Beat 0,10,20,30,40 = 5 sequences
  await page.waitForFunction(
    () => {
      const w = window as any;
      const n = w.__deephavenTones.log.filter((r: any) => r.op === 'sequence').length;
      if (w.__lastSeqN === n) return n > 0;
      w.__lastSeqN = n;
      return false;
    },
    { timeout: 8000, polling: 250 }
  );

  const log = await page.evaluate(() => (window as any).__deephavenTones.log);
  const seqRecords = log.filter((r: any) => r.op === 'sequence');
  expect(seqRecords.length).toBe(5);
  // Each sequence emits the configured melody (4 notes).
  expect(seqRecords.every((r: any) => Array.isArray(r.notes) && r.notes.length === 4)).toBe(true);
  expect(seqRecords[0].notes).toEqual(['C5', 'E5', 'G5', 'C6']);
  // No single-note 'value' ops and no chords from the sequence path.
  expect(log.filter((r: any) => r.op === 'value').length).toBe(0);
  expect(log.filter((r: any) => r.op === 'chord').length).toBe(0);
});

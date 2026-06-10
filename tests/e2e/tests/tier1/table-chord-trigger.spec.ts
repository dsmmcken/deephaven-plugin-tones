/**
 * table-chord-trigger.spec.ts — Tier 1 gate (chord progression on a trigger row)
 * ============================================================================
 * A ticking table where only some rows are flagged (a boolean trigger column).
 * On each flagged row the View plays a chord PROGRESSION; unflagged rows are
 * silent. Verifies:
 *   • chords fire ONLY on trigger rows (5 triggers × 4 chords = 20 chord ops),
 *   • each chord carries multiple notes (a real chord, not a single note),
 *   • non-trigger rows make no sound (no stray 'value' ops).
 */
import { test, expect, Page } from '@playwright/test';

const HARNESS = 'http://127.0.0.1:19876/';

const PROGRESSION = [
  ['C4', 'E4', 'G4'],
  ['G3', 'B3', 'D4'],
  ['A3', 'C4', 'E4'],
  ['F3', 'A3', 'C4'],
];

async function gotoHarness(page: Page) {
  await page.goto(HARNESS);
  await page.waitForFunction(() => (window as any).__harnessReady === true, { timeout: 15_000 });
}

async function setupTriggerTable(page: Page, chords: string[][]) {
  await page.evaluate(({ chords }) => {
    const REVERSE = { __reverse: true };
    (window as any).__mockDhApi = {
      Table: { EVENT_UPDATED: 'updated', EVENT_SIZECHANGED: 'sizechanged', reverse: () => REVERSE },
    };

    interface Rec { Beat: number; IsChord: boolean }
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
    // Deterministic: every 10th row is a chord row.
    (window as any).__tick = () => {
      const i = rows.length;
      rows.push({ Beat: i, IsChord: i % 10 === 0 });
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
      chordTrigger: { column: 'IsChord', chords, gap: '8n', duration: '8n' },
    });
  }, { chords });
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

test('chords fire only on trigger rows, as full multi-note chords', async ({ page }) => {
  await gotoHarness(page);
  await setupTriggerTable(page, PROGRESSION);

  // 50 ticks → trigger rows at Beat 0,10,20,30,40 = 5 progressions.
  await tick(page, 50);
  // Let the async chordSequence calls flush.
  await page.waitForFunction(
    () => {
      const w = window as any;
      const n = w.__deephavenTones.log.filter((r: any) => r.op === 'chord').length;
      if (w.__lastChordN === n) return n > 0;
      w.__lastChordN = n;
      return false;
    },
    { timeout: 8000, polling: 250 }
  );

  const log = await page.evaluate(() => (window as any).__deephavenTones.log);
  const chordRecords = log.filter((r: any) => r.op === 'chord');
  const valueRecords = log.filter((r: any) => r.op === 'value');

  // 5 trigger rows × 4 chords in the progression.
  expect(chordRecords.length).toBe(20);
  // Each is a real chord (3 notes here), not a single note.
  expect(chordRecords.every((r: any) => Array.isArray(r.notes) && r.notes.length === 3)).toBe(true);
  // The chord path never emits single-note 'value' ops.
  expect(valueRecords.length).toBe(0);
  // First chord of the progression is the tonic C-E-G.
  expect(chordRecords[0].notes).toEqual(['C4', 'E4', 'G4']);
});

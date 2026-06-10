/**
 * table-trigger-columns.spec.ts — Tier 1 gate (per-row notes/chords from a column)
 * ============================================================================
 * The trigger's chord(s)/notes can come from a COLUMN instead of a static list.
 * The notes column also doubles as the gate (fires when its cell is non-empty).
 * Verifies the View parses both String cells ("C4,E4,G4") and String[] cells,
 * plays exactly what the cell carries, and stays silent on empty cells.
 */
import { test, expect, Page } from '@playwright/test';

const HARNESS = 'http://127.0.0.1:19876/';

async function gotoHarness(page: Page) {
  await page.goto(HARNESS);
  await page.waitForFunction(() => (window as any).__harnessReady === true, { timeout: 15_000 });
}

// Generic mock: each row is a record; cells may be strings or arrays.
async function setupTable(page: Page, props: Record<string, unknown>, colName: string) {
  await page.evaluate(({ props, colName }) => {
    const REVERSE = { __reverse: true };
    (window as any).__mockDhApi = {
      Table: { EVENT_UPDATED: 'updated', EVENT_SIZECHANGED: 'sizechanged', reverse: () => REVERSE },
    };
    const rows: Array<Record<string, unknown>> = [];
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
        out.push({ get: (col: { name: string }) => rec[col.name] });
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
    // Each tick pushes a row whose `colName` cell is provided by __cells[i].
    (window as any).__push = (cell: unknown) => {
      const rec: Record<string, unknown> = { Beat: rows.length };
      rec[colName] = cell;
      rows.push(rec);
      queueMicrotask(fire);
    };
    const exported = {
      reexport: () => Promise.resolve({ fetch: () => Promise.resolve(table), close() {} }),
      fetch: () => Promise.resolve(table),
      close() {},
    };
    (window as any).__harness.renderRealView({ ...props, table: exported, mode: 'all', rateLimitMs: 0 });
  }, { props, colName });
}

async function pushCells(page: Page, cells: unknown[]) {
  await page.evaluate(async list => {
    for (const c of list) {
      (window as any).__push(c);
      // eslint-disable-next-line no-await-in-loop
      await new Promise(r => setTimeout(r, 5));
    }
  }, cells);
}

async function settle(page: Page, op: string) {
  await page.evaluate(op => { (window as any).__n = -1; (window as any).__op = op; }, op);
  await page.waitForFunction(
    () => {
      const w = window as any;
      const n = w.__deephavenTones.log.filter((r: any) => r.op === w.__op).length;
      if (w.__n === n) return n > 0;
      w.__n = n;
      return false;
    },
    { timeout: 8000, polling: 250 }
  );
}

test.afterEach(async ({ page }) => {
  await page.evaluate(() => {
    (window as any).__harness?.unmountRealView();
    (window as any).__harness?.resetHook();
    delete (window as any).__mockDhApi;
    delete (window as any).__push;
  });
});

test('chord notes come from a String column (column doubles as the gate)', async ({ page }) => {
  await gotoHarness(page);
  await setupTable(
    page,
    { chordTrigger: { column: 'Chord', chords: [['X1', 'X2']], notesColumn: 'Chord', gap: '8n', duration: '8n' } },
    'Chord'
  );

  // 'C4,E4,G4' and 'G3,B3,D4' rows fire; '' rows are silent.
  const cells = [];
  for (let i = 0; i < 30; i += 1) {
    cells.push(i % 3 === 0 ? 'C4,E4,G4' : i % 3 === 1 ? 'G3,B3,D4' : '');
  }
  await pushCells(page, cells);
  await settle(page, 'chord');

  const chords = await page.evaluate(() =>
    (window as any).__deephavenTones.log.filter((r: any) => r.op === 'chord').map((r: any) => r.notes)
  );
  // 20 non-empty rows → 20 single chords, matching the cell (NOT the static default).
  expect(chords.length).toBe(20);
  expect(chords).toContainEqual(['C4', 'E4', 'G4']);
  expect(chords).toContainEqual(['G3', 'B3', 'D4']);
  // The static default ['X1','X2'] was never used.
  expect(chords.some((c: string[]) => c.includes('X1'))).toBe(false);
});

test('a single cell can hold a whole progression (chords split on "|")', async ({ page }) => {
  await gotoHarness(page);
  await setupTable(
    page,
    { chordTrigger: { column: 'Chords', chords: [['X']], notesColumn: 'Chords', gap: '8n', duration: '8n' } },
    'Chords'
  );

  // One flagged row whose cell carries a 4-chord progression; the rest rest.
  const prog = 'C4,E4,G4 | G3,B3,D4 | A3,C4,E4 | F3,A3,C4';
  const cells = [];
  for (let i = 0; i < 20; i += 1) cells.push(i === 5 ? prog : '');
  await pushCells(page, cells);
  await settle(page, 'chord');

  const chords = await page.evaluate(() =>
    (window as any).__deephavenTones.log.filter((r: any) => r.op === 'chord').map((r: any) => r.notes)
  );
  // One row → the whole 4-chord progression from that single cell.
  expect(chords.length).toBe(4);
  expect(chords[0]).toEqual(['C4', 'E4', 'G4']);
  expect(chords[3]).toEqual(['F3', 'A3', 'C4']);
});

test('sequence notes come from a String[] column', async ({ page }) => {
  await gotoHarness(page);
  await setupTable(
    page,
    { sequenceTrigger: { column: 'Motif', notes: [{ note: 'Z9' }], notesColumn: 'Motif', gap: '16n' } },
    'Motif'
  );

  const cells = [];
  for (let i = 0; i < 20; i += 1) cells.push(i % 2 === 0 ? ['C5', 'E5', 'G5'] : []);
  await pushCells(page, cells);
  await settle(page, 'sequence');

  const seqs = await page.evaluate(() =>
    (window as any).__deephavenTones.log.filter((r: any) => r.op === 'sequence').map((r: any) => r.notes)
  );
  // 10 non-empty array cells fire; empty arrays are silent.
  expect(seqs.length).toBe(10);
  expect(seqs.every((s: string[]) => JSON.stringify(s) === JSON.stringify(['C5', 'E5', 'G5']))).toBe(true);
});

/**
 * table-multi.spec.ts — Tier 1 gate (multi-dimensional "duet" sonification)
 * ============================================================================
 * Exercises the multi-column mapping path: a single ticking table whose rows
 * carry THREE dimensions —
 *
 *     Price  -> pitch        (scale-quantised)
 *     Volume -> velocity + duration (loudness + note length)
 *     Side   -> instrument   (BUY = pluck, SELL = sawtooth)  ← the "duet"
 *
 * It injects a mock JSAPI (window.__mockDhApi), renders the REAL View with a
 * `mappings` prop, and ticks the table. Assertions prove all three channels are
 * actually driven independently:
 *   • TWO distinct instruments appear in the log  → the Side column selected the
 *     voice (regression: if voice mapping breaks, only one instrument plays).
 *   • Pitch keeps modulating (many distinct notes) → Price drives pitch and the
 *     reversed-table tail doesn't freeze.
 *   • Velocity varies                              → Volume drives loudness.
 *
 * Like table-tail.spec.ts the table is REVERSED (a row-order flip) so the newest
 * row is index 0 under a fixed viewport.
 */
import { test, expect, Page } from '@playwright/test';

const HARNESS = 'http://127.0.0.1:19876/';

async function gotoHarness(page: Page) {
  await page.goto(HARNESS);
  await page.waitForFunction(() => (window as any).__harnessReady === true, { timeout: 15_000 });
}

// Install a mock JSAPI + a fake ticking 3-column trade table, then render the
// real View in multi-mapping mode.
async function setupMockTradeTable(page: Page) {
  await page.evaluate(() => {
    const REVERSE = { __reverse: true };
    (window as any).__mockDhApi = {
      Table: {
        EVENT_UPDATED: 'updated',
        EVENT_SIZECHANGED: 'sizechanged',
        reverse: () => REVERSE,
      },
    };

    // Each row is a {Price, Volume, Side} record.
    interface Trade { Price: number; Volume: number; Side: string }
    const rows: Trade[] = [];
    const listeners: Array<(e: unknown) => void> = [];
    let viewport = { first: 0, last: -1 };
    let reversed = false;

    // EVENT_UPDATED detail. Reversed table => row position p maps to absolute
    // index (size-1-p), so rows[0] is the NEWEST trade. row.get(col) reads the
    // field named by the column (findColumn returns {name}).
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

    const table = {
      get size() {
        return rows.length;
      },
      findColumn(name: string) {
        return { name };
      },
      copy() {
        return Promise.resolve(table);
      },
      applySort(sorts: Array<{ __reverse?: boolean }>) {
        reversed = sorts.some(s => s && s.__reverse === true);
        queueMicrotask(fire);
        return sorts;
      },
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

    // Append one trade and fire EVENT_UPDATED. Price cycles (so pitch keeps
    // moving), Volume cycles (so velocity varies), Side alternates BUY/SELL.
    (window as any).__mockTick = () => {
      const i = rows.length;
      rows.push({
        Price: 90 + (i % 21), // 90..110, distinct pentatonic notes as it climbs
        Volume: 40 + ((i * 7) % 60), // 40..99, varied loudness
        Side: i % 2 === 0 ? 'BUY' : 'SELL',
      });
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
      config: { scale: 'pentatonic', root: 'C3', octaves: 3 }, // auto pitch range
      mappings: {
        pitch: { column: 'Price' },
        velocity: { column: 'Volume' }, // auto range
        duration: { column: 'Volume' },
        voice: {
          column: 'Side',
          voices: {
            BUY: { instrument: 'pluck' },
            SELL: {
              instrument: 'sawtooth',
              envelope: { attack: 0.12, decay: 0.2, sustain: 0.7, release: 0.4 },
            },
          },
        },
      },
    });
  });
}

async function tick(page: Page, n: number) {
  await page.evaluate(async count => {
    for (let i = 0; i < count; i += 1) {
      (window as any).__mockTick();
      // eslint-disable-next-line no-await-in-loop
      await new Promise(r => setTimeout(r, 4));
    }
  }, n);
}

// The View's per-row playback is async (build voice chains + schedule). At a
// 4ms tick cadence the async tails lag the synchronous ticks, so wait until the
// 'value' log stops growing before asserting. (Real DH ticks ~500ms apart, so
// no backlog exists in practice — this only models the fast test cadence.)
async function waitForSettled(page: Page) {
  await page.evaluate(() => { (window as any).__lastLen = -1; });
  await page.waitForFunction(
    () => {
      const w = window as any;
      const len = w.__deephavenTones.log.filter((r: any) => r.op === 'value').length;
      if (w.__lastLen === len) return true; // two consecutive polls equal → settled
      w.__lastLen = len;
      return false;
    },
    { timeout: 10_000, polling: 250 }
  );
}

test.afterEach(async ({ page }) => {
  await page.evaluate(() => {
    (window as any).__harness?.unmountRealView();
    (window as any).__harness?.resetHook();
    delete (window as any).__mockDhApi;
    delete (window as any).__mockTick;
  });
});

test('duet: Side selects instrument, Price drives pitch, Volume drives loudness', async ({
  page,
}) => {
  await gotoHarness(page);
  await setupMockTradeTable(page);

  await tick(page, 120);
  await waitForSettled(page);

  const log = await page.evaluate(() =>
    (window as any).__deephavenTones.log.filter((r: any) => r.op === 'value')
  );

  // Every tick is a trade; mode=all sonifies them all.
  expect(log.length).toBeGreaterThan(100);

  // (1) Side -> instrument: BOTH voices must appear. If the voice mapping
  // failed, every note would use the single base instrument.
  const instruments = new Set(log.map((r: any) => r.instrument));
  expect(instruments.has('pluck')).toBe(true);
  expect(instruments.has('sawtooth')).toBe(true);

  // (2) Price -> pitch: the tail keeps modulating (reversed table never froze).
  const tailNotes = log.slice(-30).map((r: any) => r.note);
  expect(new Set(tailNotes).size).toBeGreaterThan(5);

  // (3) Volume -> velocity: loudness genuinely varies across rows.
  const velocities = new Set(log.map((r: any) => Math.round(r.velocity * 100)));
  expect(velocities.size).toBeGreaterThan(3);
});

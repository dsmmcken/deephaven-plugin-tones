/**
 * table-lifecycle.spec.ts — Tier 1 gate (subscription lifecycle & leaks)
 * ======================================================================
 * Regression tests for the table-listener rewrite:
 *
 *   1. CLOSE-ON-UNMOUNT  — every server handle the View opens (reexported,
 *      fetched, the private copy) plus the viewport and the EVENT_UPDATED
 *      listener must be closed/removed when the View unmounts. The old code
 *      closed only the copy, leaking the reexport + fetched table per mount.
 *
 *   2. NO-RESUBSCRIBE-ON-RERENDER — re-rendering with new `events` (or any
 *      unrelated prop) must NOT tear down and rebuild the table subscription.
 *      The old deps array depended on freshly-built object props by identity,
 *      so every render resubscribed (re-fetch, re-copy, re-listen, leak).
 *
 *   3. LIVE-CONFIG — a runtime `config` change must affect table playback
 *      WITHOUT resubscribing (config is read from a ref, excluded from deps).
 *
 *   4. REFCOUNT-DISPOSE — the shared ToneEngine singleton is disposed only when
 *      the LAST mounted View unmounts, not the first.
 *
 *   5. WINDOW-DROP WARNING — a single tick that adds more rows than the viewport
 *      window must log a warning rather than silently drop the overflow.
 *
 * All of these drive the REAL bundle via the harness adapter and a mock JSAPI.
 */
import { test, expect, Page } from '@playwright/test';

const HARNESS = 'http://127.0.0.1:19876/';

async function gotoHarness(page: Page) {
  await page.goto(HARNESS);
  await page.waitForFunction(() => (window as any).__harnessReady === true, { timeout: 15_000 });
}

// Build an instrumented mock JSAPI + ExportedObject chain on the page, stored at
// window.__lc[label]. Counters track every close()/reexport()/copy() so a test
// can assert the View opened and later released each handle exactly once.
async function installMock(page: Page, label: string) {
  await page.evaluate((lbl) => {
    const REVERSE = { __reverse: true };
    (window as any).__mockDhApi = {
      Table: { EVENT_UPDATED: 'updated', reverse: () => REVERSE },
    };

    const c = {
      reexport: 0, fetch: 0, copy: 0,
      reexportedClose: 0, fetchedClose: 0, copyClose: 0,
      vpClose: 0, listenerRemoved: 0,
    };

    const values: number[] = [];
    const listeners: Array<(e: unknown) => void> = [];
    let viewport = { first: 0, last: -1 };
    let reversed = false;

    const buildDetail = () => {
      const size = values.length;
      const lo = Math.max(0, viewport.first);
      const hi = Math.min(viewport.last, size - 1);
      const rows = [];
      for (let p = lo; p <= hi; p += 1) {
        const idx = reversed ? size - 1 - p : p;
        rows.push({ get: () => values[idx] });
      }
      return { offset: lo, rows };
    };
    const fire = () => {
      const ev = { detail: buildDetail() };
      listeners.slice().forEach(cb => cb(ev));
    };

    // The private copy the View reverses + subscribes to.
    const copyTable = {
      get size() { return values.length; },
      isBlinkTable() { return false; },
      findColumn() { return { name: 'V' }; },
      applySort(sorts: Array<{ __reverse?: boolean }>) {
        reversed = sorts.some(s => s && s.__reverse === true);
        queueMicrotask(fire);
        return sorts;
      },
      setViewport(first: number, last: number) {
        viewport = { first, last };
        queueMicrotask(fire);
        return { close() { c.vpClose += 1; } };
      },
      addEventListener(_n: string, cb: (e: unknown) => void) {
        listeners.push(cb);
        return () => {
          c.listenerRemoved += 1;
          const i = listeners.indexOf(cb);
          if (i >= 0) listeners.splice(i, 1);
        };
      },
      close() { c.copyClose += 1; },
    };

    // The live table fetched from the reexport.
    const fetchedTable = {
      isBlinkTable() { return false; },
      copy() { c.copy += 1; return Promise.resolve(copyTable); },
      close() { c.fetchedClose += 1; },
    };

    // The reexported ExportedObject (distinct from the prop).
    const reexported = {
      fetch() { c.fetch += 1; return Promise.resolve(fetchedTable); },
      close() { c.reexportedClose += 1; },
    };

    // The prop the View receives. We never expect the View to close THIS one.
    const exported = {
      reexport() { c.reexport += 1; return Promise.resolve(reexported); },
      fetch() { return Promise.resolve(fetchedTable); },
      close() { /* prop — must not be closed by the plugin */ },
    };

    (window as any).__lc = (window as any).__lc || {};
    (window as any).__lc[lbl] = {
      exported,
      counters: c,
      listenerCount: () => listeners.length,
      tick(v?: number) { values.push(v ?? values.length); queueMicrotask(fire); },
      burst(n: number) { for (let i = 0; i < n; i += 1) values.push(i); queueMicrotask(fire); },
    };
  }, label);
}

test.afterEach(async ({ page }) => {
  await page.evaluate(() => {
    (window as any).__harness?.unmountRealView();
    (window as any).__harness?.unmountViewFrom('rc-a');
    (window as any).__harness?.unmountViewFrom('rc-b');
    (window as any).__harness?.resetHook();
    delete (window as any).__mockDhApi;
    delete (window as any).__lc;
  });
});

test('unmount closes every server handle and removes the listener', async ({ page }) => {
  await gotoHarness(page);
  await installMock(page, 'm');

  await page.evaluate(() => {
    const lc = (window as any).__lc.m;
    (window as any).__harness.renderRealView({
      table: lc.exported, mappings: { pitch: { column: 'V' } }, mode: 'last', rateLimitMs: 0,
      config: { scale: 'pentatonic', root: 'C3', octaves: 3, valueRange: [0, 14] },
    });
  });

  // Let reexport→fetch→copy→applySort→setViewport settle, then tick once.
  await page.waitForFunction(() => (window as any).__lc.m.counters.copy === 1, { timeout: 5000 });
  await page.evaluate(() => (window as any).__lc.m.tick(3));
  await page.waitForTimeout(30);

  const before = await page.evaluate(() => (window as any).__lc.m.counters);
  expect(before.reexport).toBe(1);
  expect(before.fetch).toBe(1);
  expect(before.copy).toBe(1);

  await page.evaluate(() => (window as any).__harness.unmountRealView());
  await page.waitForTimeout(30);

  const after = await page.evaluate(() => ({
    counters: (window as any).__lc.m.counters,
    listeners: (window as any).__lc.m.listenerCount(),
  }));
  // All three handles + viewport closed exactly once; listener removed.
  expect(after.counters.reexportedClose).toBe(1);
  expect(after.counters.fetchedClose).toBe(1);
  expect(after.counters.copyClose).toBe(1);
  expect(after.counters.vpClose).toBe(1);
  expect(after.counters.listenerRemoved).toBe(1);
  expect(after.listeners).toBe(0);
});

test('re-rendering with new events does NOT resubscribe the table', async ({ page }) => {
  await gotoHarness(page);
  await installMock(page, 'm');

  await page.evaluate(() => {
    const lc = (window as any).__lc.m;
    (window as any).__harness.renderRealView({
      table: lc.exported, mappings: { pitch: { column: 'V' } }, mode: 'last', rateLimitMs: 0,
      events: [{ id: 1, op: 'play', note: 'C4' }],
      config: {},
    });
  });
  await page.waitForFunction(() => (window as any).__lc.m.counters.copy === 1, { timeout: 5000 });

  // Re-render with an additional event but the SAME table reference + props.
  await page.evaluate(() => {
    const lc = (window as any).__lc.m;
    (window as any).__harness.renderRealView({
      table: lc.exported, mappings: { pitch: { column: 'V' } }, mode: 'last', rateLimitMs: 0,
      events: [{ id: 1, op: 'play', note: 'C4' }, { id: 2, op: 'play', note: 'E4' }],
      config: {},
    });
  });
  await page.waitForTimeout(40);

  const counters = await page.evaluate(() => (window as any).__lc.m.counters);
  // The table subscription must NOT have been rebuilt by the re-render.
  expect(counters.reexport).toBe(1);
  expect(counters.fetch).toBe(1);
  expect(counters.copy).toBe(1);
  expect(counters.reexportedClose).toBe(0);
  expect(counters.copyClose).toBe(0);

  // …and the new event still played.
  const notes = await page.evaluate(() =>
    (window as any).__deephavenTones.log.filter((r: any) => r.op === 'play').map((r: any) => r.note)
  );
  expect(notes).toContain('E4');
});

test('a runtime config change affects playback without resubscribing', async ({ page }) => {
  await gotoHarness(page);
  await installMock(page, 'm');

  const render = (instrument: string) =>
    page.evaluate((inst) => {
      const lc = (window as any).__lc.m;
      (window as any).__harness.renderRealView({
        table: lc.exported, mappings: { pitch: { column: 'V' } }, mode: 'last', rateLimitMs: 0,
        config: { instrument: inst, scale: 'pentatonic', root: 'C3', octaves: 3, valueRange: [0, 14] },
      });
    }, instrument);

  await render('sine');
  await page.waitForFunction(() => (window as any).__lc.m.counters.copy === 1, { timeout: 5000 });
  await page.evaluate(() => (window as any).__lc.m.tick(5));
  await page.waitForTimeout(30);

  // Change only the instrument, keep the same table/column/mode.
  await render('fm');
  await page.evaluate(() => (window as any).__lc.m.tick(9));
  await page.waitForTimeout(30);

  const result = await page.evaluate(() => {
    const valueLog = (window as any).__deephavenTones.log.filter((r: any) => r.op === 'value');
    return {
      instruments: valueLog.map((r: any) => r.instrument),
      copy: (window as any).__lc.m.counters.copy,
    };
  });
  // No resubscribe across the config change…
  expect(result.copy).toBe(1);
  // …and the latest note used the NEW instrument (stale-config bug would keep 'sine').
  expect(result.instruments).toContain('sine');
  expect(result.instruments[result.instruments.length - 1]).toBe('fm');
});

test('engine is disposed only when the LAST view unmounts (refcount)', async ({ page }) => {
  await gotoHarness(page);
  await installMock(page, 'a');
  await installMock(page, 'b');

  // Mount two Views. One carries an event so the engine actually starts.
  await page.evaluate(() => {
    (window as any).__harness.renderViewInto('rc-a', {
      table: (window as any).__lc.a.exported, mappings: { pitch: { column: 'V' } }, mode: 'last', rateLimitMs: 0,
      events: [{ id: 0, op: 'play', note: 'C4' }], config: {},
    });
    (window as any).__harness.renderViewInto('rc-b', {
      table: (window as any).__lc.b.exported, mappings: { pitch: { column: 'V' } }, mode: 'last', rateLimitMs: 0, config: {},
    });
  });
  await page.waitForFunction(() => (window as any).__deephavenTones.started === true, { timeout: 5000 });

  // Unmount the FIRST view — engine must stay alive (second view still mounted).
  await page.evaluate(() => (window as any).__harness.unmountViewFrom('rc-a'));
  await page.waitForTimeout(30);
  expect(await page.evaluate(() => (window as any).__deephavenTones.started)).toBe(true);

  // Unmount the SECOND (last) view — now the engine is disposed.
  await page.evaluate(() => (window as any).__harness.unmountViewFrom('rc-b'));
  await page.waitForTimeout(30);
  expect(await page.evaluate(() => (window as any).__deephavenTones.started)).toBe(false);
});

test('a tick exceeding the viewport window warns instead of dropping silently', async ({ page }) => {
  await gotoHarness(page);
  await installMock(page, 'm');

  const warnings: string[] = [];
  page.on('console', msg => {
    if (msg.type() === 'warning' || msg.type() === 'error') warnings.push(msg.text());
  });

  await page.evaluate(() => {
    const lc = (window as any).__lc.m;
    (window as any).__harness.renderRealView({
      table: lc.exported, mappings: { pitch: { column: 'V' } }, mode: 'all', rateLimitMs: 0,
      config: { scale: 'pentatonic', root: 'C3', octaves: 3, valueRange: [0, 300] },
    });
  });
  await page.waitForFunction(() => (window as any).__lc.m.counters.copy === 1, { timeout: 5000 });

  // One update adds 240 rows at once — far beyond the WINDOW (200).
  await page.evaluate(() => (window as any).__lc.m.burst(240));
  await page.waitForTimeout(80);

  // 40 rows are beyond the window and unreachable; the View must warn.
  const dropWarn = warnings.find(w => /dropped \d+ row/.test(w));
  expect(dropWarn).toBeTruthy();
  expect(dropWarn).toContain('40');
});

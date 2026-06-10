/**
 * real-bundle.spec.ts — Tier 1 gate
 * ==================================
 * Drives the REAL plugin bundle, loaded the SAME way Deephaven loads it
 * (adapter does `new Function(module, exports, require, text)`), then renders
 * the REAL DeephavenPluginTonesView and feeds it `events` props. All assertions
 * read window.__deephavenTones — the hook the REAL ToneEngine writes to.
 *
 * This replaces the old tone-engine.spec.ts (which tested a re-implementation
 * of the engine living in the harness, not the bundle) and the old
 * real-engine.spec.ts (which loaded the bundle via ESM `import`, the path that
 * masked the CJS regression).
 */
import { test, expect, Page } from '@playwright/test';

const HARNESS = 'http://127.0.0.1:19876/';

async function gotoHarness(page: Page) {
  const errors: string[] = [];
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', e => errors.push(String(e)));
  await page.goto(HARNESS);
  await page.waitForFunction(() => (window as any).__harnessReady === true, { timeout: 15_000 });
  return errors;
}

// Render the real View with props, then wait for the engine to log `count`
// entries (the View dispatches asynchronously inside a useEffect).
async function renderAndWaitLog(page: Page, props: unknown, count: number) {
  await page.evaluate(p => (window as any).__harness.renderRealView(p), props);
  await expect
    .poll(async () => page.evaluate(() => (window as any).__deephavenTones.log.length), {
      timeout: 10_000,
    })
    .toBeGreaterThanOrEqual(count);
  return page.evaluate(() => (window as any).__deephavenTones.log);
}

test.afterEach(async ({ page }) => {
  await page.evaluate(() => {
    (window as any).__harness?.unmountRealView();
    (window as any).__harness?.resetHook();
  });
});

test('plugin bundle loads via Deephaven\'s new Function path and exposes the View', async ({ page }) => {
  const errors = await gotoHarness(page);
  const info = await page.evaluate(() => (window as any).__harness.pluginInfo());
  expect(info.name).toBe('deephaven-plugin-tones');
  expect(info.hasView).toBe(true);
  // The user's exact failure was a SyntaxError at new Function — assert none.
  expect(errors.join('\n')).not.toContain('import statement outside a module');
});

test('no-DOM invariant: rendering the View adds no layout', async ({ page }) => {
  await gotoHarness(page);
  await page.evaluate(() => (window as any).__harness.renderRealView({}));
  const { childCount, siblingWidth } = await page.evaluate(() => ({
    childCount: document.getElementById('real-engine-container')!.childElementCount,
    siblingWidth: (document.getElementById('sibling') as HTMLElement).offsetWidth,
  }));
  expect(childCount).toBe(0);
  expect(siblingWidth).toBe(100);
});

test('play event triggers the real engine and logs the note', async ({ page }) => {
  await gotoHarness(page);
  const log = await renderAndWaitLog(page, { events: [{ id: 1, op: 'play', note: 'C4' }], config: {} }, 1);
  expect(log[0].op).toBe('play');
  expect(log[0].note).toBe('C4');
  // Real engine flips the hook's started flag after Tone.start() resolves.
  const started = await page.evaluate(() => (window as any).__deephavenTones.started);
  expect(started).toBe(true);
});

test('{midi:60} resolves to C4 via the REAL Tone.Frequency', async ({ page }) => {
  await gotoHarness(page);
  const log = await renderAndWaitLog(page, { events: [{ id: 1, op: 'play', note: { midi: 60 } }], config: {} }, 1);
  expect(log[0].note).toBe('C4'); // only the real ToneEngine.resolveNote does this
});

test('event id-diffing: only ids greater than the last played fire', async ({ page }) => {
  await gotoHarness(page);
  // First render: id 1 plays.
  await renderAndWaitLog(page, { events: [{ id: 1, op: 'play', note: 'C4' }], config: {} }, 1);
  // Re-render with id 1 (already played) + id 2 (new): only id 2 should fire.
  const log = await renderAndWaitLog(
    page,
    { events: [{ id: 1, op: 'play', note: 'C4' }, { id: 2, op: 'play', note: 'E4' }], config: {} },
    2
  );
  expect(log.map((r: any) => r.note)).toEqual(['C4', 'E4']);
});

test('chord event plays all notes together', async ({ page }) => {
  await gotoHarness(page);
  const log = await renderAndWaitLog(
    page,
    { events: [{ id: 1, op: 'chord', notes: ['C4', 'E4', 'G4'] }], config: {} },
    1
  );
  expect(log[0].op).toBe('chord');
  expect(log[0].notes).toEqual(['C4', 'E4', 'G4']);
});

test('sequence event logs the ordered notes', async ({ page }) => {
  await gotoHarness(page);
  const log = await renderAndWaitLog(
    page,
    {
      events: [{ id: 1, op: 'sequence', notes: [{ note: 'C5' }, { note: 'E5' }, { note: 'G5' }], gap: '16n' }],
      config: {},
    },
    1
  );
  expect(log[0].op).toBe('sequence');
  expect(log[0].notes).toEqual(['C5', 'E5', 'G5']);
});

// Parse a Tone note name (e.g. 'C#4', 'Eb3') to a MIDI number for ordering asserts.
function noteToMidi(name: string): number {
  const m = name.match(/^([A-G])(#|b)?(-?\d+)$/);
  if (!m) throw new Error(`bad note ${name}`);
  const base: Record<string, number> = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };
  let semi = base[m[1]];
  if (m[2] === '#') semi += 1;
  if (m[2] === 'b') semi -= 1;
  return semi + (Number(m[3]) + 1) * 12;
}

test('value mapping is monotonic in pitch (low value → lower note than high value)', async ({ page }) => {
  await gotoHarness(page);
  const config = { scale: 'pentatonic', root: 'C3', octaves: 3, valueRange: [0, 100] };
  const log = await renderAndWaitLog(
    page,
    {
      events: [
        { id: 1, op: 'value', value: 5 },
        { id: 2, op: 'value', value: 95 },
      ],
      config,
    },
    2
  );
  const [low, high] = log;
  expect(low.op).toBe('value');
  expect(high.op).toBe('value');
  expect(noteToMidi(high.note)).toBeGreaterThan(noteToMidi(low.note));
});

test('stop event clears active voices', async ({ page }) => {
  await gotoHarness(page);
  const log = await renderAndWaitLog(
    page,
    { events: [{ id: 1, op: 'play', note: 'C4' }, { id: 2, op: 'stop' }], config: {} },
    2
  );
  expect(log.some((r: any) => r.op === 'stop')).toBe(true);
  const voices = await page.evaluate(() => (window as any).__deephavenTones.activeVoices);
  expect(voices).toBe(0);
});

test('setVolume event records the dB level', async ({ page }) => {
  await gotoHarness(page);
  const log = await renderAndWaitLog(page, { events: [{ id: 1, op: 'setVolume', db: -12 }], config: {} }, 1);
  expect(log[0].op).toBe('setVolume');
  expect(log[0].db).toBe(-12);
});

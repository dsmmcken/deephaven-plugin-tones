/**
 * engine-adapter.js  (browser, ES module)
 * ========================================
 * Loads the plugin bundle EXACTLY the way the Deephaven web client does — by
 * fetching the bundle text and evaluating it with
 *
 *     new Function('module', 'exports', 'require', text)
 *
 * (this is what @deephaven/app-utils → @paciolan/remote-module-loader does).
 * A `require` shim resolves only the host modules the bundle imports
 * (react, @deephaven/log, @deephaven/plugin, @deephaven/jsapi-bootstrap).
 *
 * This is the load path the OLD harness skipped: it did `import` of the bundle
 * as an ES module, so it never caught the ESM-vs-CJS regression and never
 * exercised the real loader.
 *
 * The adapter does NOT re-implement the engine (the old one did, which gave
 * false confidence). The plugin's only export is the plugin descriptor, so the
 * ONLY way to drive the real ToneEngine is to render the real View component
 * with `events`/`config` props and let its useEffect call ToneEngine.dispatchEvent.
 */

const COMPONENT_KEY = 'deephaven_plugin_tones.deephaven_plugin_tones_component';

// ── Host module require() shim (mirrors Deephaven's resolve map) ─────────────
const noop = () => {};
const logger = {
  debug: noop,
  info: noop,
  warn: (...a) => console.warn('[ToneEngine]', ...a),
  error: (...a) => console.error('[ToneEngine]', ...a),
};

const hostModules = {
  // The host provides the REAL React (same instance the rest of the app uses).
  react: window.React,
  // The bundle only calls Log.module(name).
  '@deephaven/log': { module: () => logger },
  // The bundle only reads PluginType.* off this.
  '@deephaven/plugin': {
    PluginType: { ELEMENT_PLUGIN: 'ELEMENT_PLUGIN', DASHBOARD_PLUGIN: 'DASHBOARD_PLUGIN' },
  },
  // The View calls useApi() inside a try/catch. By default this returns undefined
  // (table mode disabled — events mode is what plays sound). Tier-1 table-mode
  // regression tests inject a mock JSAPI on window.__mockDhApi before rendering.
  '@deephaven/jsapi-bootstrap': { useApi: () => window.__mockDhApi },
};

function hostRequire(name) {
  if (Object.prototype.hasOwnProperty.call(hostModules, name)) {
    return hostModules[name];
  }
  throw new Error(
    `[adapter] require('${name}') not provided by the host shim — the bundle ` +
      `must bundle this inline, not leave it external or split into a chunk.`
  );
}

// ── Load the bundle via new Function (the real Deephaven path) ───────────────
const bundleText = await fetch('/plugin/index.js').then(r => {
  if (!r.ok) throw new Error(`failed to fetch /plugin/index.js: ${r.status}`);
  return r.text();
});

const module = { exports: {} };
// If the bundle were ESM, the next line throws the user's exact error:
// "SyntaxError: Cannot use import statement outside a module".
const factory = new Function('module', 'exports', 'require', bundleText);
factory(module, module.exports, hostRequire);

const pluginDescriptor =
  module.exports && module.exports.name != null
    ? module.exports
    : module.exports && module.exports.default;

export const PLUGIN = pluginDescriptor;
export const PLUGIN_NAME = pluginDescriptor?.name;
export const VIEW_COMPONENT = pluginDescriptor?.mapping?.[COMPONENT_KEY];

if (typeof VIEW_COMPONENT !== 'function') {
  console.error(
    '[adapter] VIEW_COMPONENT not found. plugin keys:',
    pluginDescriptor && Object.keys(pluginDescriptor)
  );
}

// ── Render the REAL View (React 17 legacy render) ────────────────────────────
const ReactDOM = window.ReactDOM;
let _container = null;

export function renderRealView(props) {
  const container = document.getElementById('real-engine-container');
  if (!container) { console.error('[adapter] #real-engine-container missing'); return; }
  if (!VIEW_COMPONENT || !ReactDOM) { console.error('[adapter] view or ReactDOM missing'); return; }
  ReactDOM.render(window.React.createElement(VIEW_COMPONENT, props || {}), container);
  _container = container;
}

export function unmountRealView() {
  if (_container && ReactDOM) {
    try { ReactDOM.unmountComponentAtNode(_container); } catch { /* ignore */ }
    _container = null;
  }
}

// ── Multi-root rendering (for engine-refcount tests) ─────────────────────────
// Render the real View into an arbitrary container id (created on demand) so a
// test can mount more than one View at once and assert the engine is only
// disposed when the LAST one unmounts.
export function renderViewInto(id, props) {
  if (!VIEW_COMPONENT || !ReactDOM) { console.error('[adapter] view or ReactDOM missing'); return; }
  let el = document.getElementById(id);
  if (!el) {
    el = document.createElement('div');
    el.id = id;
    document.body.appendChild(el);
  }
  ReactDOM.render(window.React.createElement(VIEW_COMPONENT, props || {}), el);
}

export function unmountViewFrom(id) {
  const el = document.getElementById(id);
  if (el && ReactDOM) {
    try { ReactDOM.unmountComponentAtNode(el); } catch { /* ignore */ }
  }
}

export function getHook() {
  return window.__deephavenTones;
}

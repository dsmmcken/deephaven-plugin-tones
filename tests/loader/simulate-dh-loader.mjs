#!/usr/bin/env node
/**
 * simulate-dh-loader.mjs
 * ======================
 * Reproduces EXACTLY how the Deephaven web client loads a JS plugin bundle:
 * `@deephaven/app-utils` → loadRemoteModule → `@paciolan/remote-module-loader`,
 * which fetches the bundle text and evaluates it with
 *
 *     new Function('module', 'exports', 'require', text)
 *
 * i.e. a CommonJS sandbox whose `require` resolves ONLY a fixed map of host
 * modules (react, @deephaven/*, ...). A top-level ESM `import` statement is a
 * SyntaxError in that sandbox — which is the failure the user hit
 * ("Cannot use import statement outside a module ... at new Function").
 *
 * This is a pure-Node regression test (no browser needed). It is the test that
 * was MISSING: the Playwright harness used a real ESM `import` of the bundle,
 * so it never went through this path and never caught the ESM regression.
 *
 * Exit 0 = bundle loads as Deephaven loads it and exposes a valid element
 * plugin with the expected component mapping. Exit 1 = it would fail in DH.
 */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { createRequire } from 'module';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BUNDLE = path.resolve(__dirname, '../../src/js/dist/index.js');

// Resolve real react/react-dom from the plugin's own node_modules — this
// mirrors the host (Deephaven web client) providing the real React 17 to the
// plugin's require('react'). The inlined react/jsx-runtime touches React
// internals at module-eval time, so a real React is required.
const jsRequire = createRequire(path.resolve(__dirname, '../../src/js/package.json'));

const COMPONENT_KEY = 'deephaven_plugin_tones.deephaven_plugin_tones_component';

function fail(msg) {
  console.error(`✗ ${msg}`);
  process.exit(1);
}
function ok(msg) {
  console.log(`✓ ${msg}`);
}

if (!fs.existsSync(BUNDLE)) {
  fail(`bundle not found at ${BUNDLE} — run \`npm run build\` in src/js first`);
}
const text = fs.readFileSync(BUNDLE, 'utf8');

// ── Host module map, mirroring what Deephaven's require shim provides ────────
// Only bare specifiers the host knows. Notably NOT `tone` and NOT any relative
// path: if the bundle tried to `require('tone')` or `require('./chunk.js')`
// this shim would throw — exactly as the real host would.
const PluginType = { ELEMENT_PLUGIN: 'element', WIDGET_PLUGIN: 'widget' };
const hostModules = {
  react: jsRequire('react'),
  'react-dom': jsRequire('react-dom'),
  redux: {},
  'react-redux': {},
  '@deephaven/components': {},
  '@deephaven/dashboard': {},
  '@deephaven/icons': {},
  '@deephaven/jsapi-bootstrap': { useApi: () => ({}) },
  '@deephaven/jsapi-types': {},
  '@deephaven/log': { module: () => ({ debug() {}, info() {}, warn() {}, error() {} }) },
  '@deephaven/plugin': { PluginType },
};

function hostRequire(name) {
  if (Object.prototype.hasOwnProperty.call(hostModules, name)) {
    return hostModules[name];
  }
  // This is the real-world failure mode for a code-split chunk or an
  // un-provided dep: the host cannot resolve it.
  throw new Error(
    `Cannot find module '${name}'. The Deephaven require shim only resolves ` +
      `host-provided modules; '${name}' must be bundled INTO index.js, not ` +
      `left external or split into a sibling chunk.`
  );
}

// ── Evaluate exactly like @paciolan/remote-module-loader ─────────────────────
const module = { exports: {} };
let factory;
try {
  factory = new Function('module', 'exports', 'require', text);
} catch (e) {
  fail(
    `bundle is not loadable by Deephaven: ${e.message}\n` +
      `   This is the ESM-in-CJS failure. The bundle must be CommonJS ` +
      `(no top-level import/export statements).`
  );
}
try {
  factory(module, module.exports, hostRequire);
} catch (e) {
  fail(`bundle threw while evaluating in the CJS sandbox: ${e.stack}`);
}
ok('bundle evaluated via new Function(module, exports, require) — no ESM syntax error');

// ── Validate the exported plugin, like getPluginModuleValue() does ───────────
const exported = module.exports;
const plugin =
  exported && exported.name != null ? exported : exported && exported.default;
if (!plugin || plugin.name == null) {
  fail(`no plugin value exported. module.exports keys: ${Object.keys(exported)}`);
}
ok(`exported plugin name = '${plugin.name}'`);

if (plugin.name !== 'deephaven-plugin-tones') {
  fail(`unexpected plugin name '${plugin.name}'`);
}
if (plugin.type !== PluginType.ELEMENT_PLUGIN) {
  fail(`plugin.type is '${plugin.type}', expected ELEMENT_PLUGIN ('${PluginType.ELEMENT_PLUGIN}')`);
}
ok(`plugin.type = ELEMENT_PLUGIN`);

if (!plugin.mapping || typeof plugin.mapping !== 'object') {
  fail('plugin.mapping is missing');
}
const view = plugin.mapping[COMPONENT_KEY];
if (typeof view !== 'function') {
  fail(`mapping['${COMPONENT_KEY}'] is not a React component (got ${typeof view})`);
}
ok(`mapping['${COMPONENT_KEY}'] is a React component`);

// ── Confirm tone was bundled inline (not external / not a sibling chunk) ─────
if (!/15\.1\.22|triggerAttackRelease|PolySynth/.test(text)) {
  fail('tone does not appear to be bundled into index.js (no Tone.js symbols found)');
}
ok('tone is bundled inline into index.js (no sibling chunk required at runtime)');

console.log('\n✅ Bundle loads exactly as Deephaven loads it. This would NOT throw the user\'s error.');

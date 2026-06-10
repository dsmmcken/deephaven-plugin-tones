import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react-swc';

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => ({
  build: {
    minify: false,
    lib: {
      entry: './src/index.ts',
      // MUST be CommonJS. Deephaven's PluginUtils loads plugin bundles by
      // evaluating the code with `new Function(module, exports, require, ...)`,
      // which is a CJS context — a top-level ESM `import` statement throws
      // "SyntaxError: Cannot use import statement outside a module" there.
      // CJS forces Rollup's inlineDynamicImports=true, so `tone` is inlined
      // into this single bundle rather than split into a lazy chunk. That is
      // the correct trade-off: a loadable plugin > a smaller initial download.
      // tone still creates its AudioContext lazily at Tone.start(), so there
      // is no autoplay/gesture penalty from inlining.
      formats: ['cjs'],
      // Keep the main entry as index.js so package.json "main" still resolves.
      fileName: () => 'index.js',
    },
    rollupOptions: {
      // Externalize only what the plugin actually imports AND the Deephaven host
      // provides to plugins at runtime. React is host-provided (the host renders
      // our component). The three @deephaven packages below are the only ones the
      // source imports as values; jsapi-types is type-only (erased at compile,
      // never in the bundle) but kept here defensively.
      external: [
        'react',
        'react-dom',
        '@deephaven/jsapi-bootstrap',
        '@deephaven/jsapi-types',
        '@deephaven/log',
        '@deephaven/plugin',
      ],
      // tone is NOT external — the Deephaven server does not provide it, so it
      // is bundled (inlined) into index.js.
      output: {
        // Force the dynamic import('tone') to be inlined into the single
        // index.js bundle rather than emitted as a sibling chunk. Deephaven's
        // require() shim (used by `new Function`) only resolves known bare
        // module specifiers, NOT a relative path to a sibling chunk — so a
        // separate chunk would fail at first-play time with a require error.
        inlineDynamicImports: true,
      },
    },
  },
  define:
    mode === 'production' ? { 'process.env.NODE_ENV': '"production"' } : {},
  plugins: [react()],
}));

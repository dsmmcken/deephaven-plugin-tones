#!/usr/bin/env node
/**
 * Static file server for the Tier-1 harness.
 *
 * Routes:
 *   GET /                           → harness/index.html
 *   GET /stubs/<name>.js            → harness/stubs/<name>.js
 *   GET /adapter/engine-adapter.js  → harness/engine-adapter.js
 *   GET /plugin/index.js            → /workspace/src/js/dist/index.js
 *   GET /plugin/tone-*.js           → /workspace/src/js/dist/tone-*.js
 *
 * The server adds appropriate CORS / COEP / COOP headers so the browser
 * can execute the module scripts (required for SharedArrayBuffer, but also
 * good practice for a test harness).
 *
 * Usage:  node server.js [port]
 * Default port: 19876
 */

import http from 'http';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DIST_DIR = path.resolve(__dirname, '../../../src/js/dist');
const PORT = Number(process.argv[2] ?? process.env.HARNESS_PORT ?? 19876);

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js':   'application/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.css':  'text/css; charset=utf-8',
};

function mime(filePath) {
  return MIME[path.extname(filePath)] ?? 'application/octet-stream';
}

function serve(res, filePath) {
  if (!fs.existsSync(filePath)) {
    res.writeHead(404, { 'Content-Type': 'text/plain' });
    res.end(`404 Not Found: ${filePath}`);
    console.error('[server] 404', filePath);
    return;
  }
  const body = fs.readFileSync(filePath);
  res.writeHead(200, {
    'Content-Type': mime(filePath),
    'Cache-Control': 'no-store',
    // Required for SharedArrayBuffer (Tone.js Worklet):
    'Cross-Origin-Opener-Policy': 'same-origin',
    'Cross-Origin-Embedder-Policy': 'require-corp',
    // Allow cross-origin reads from localhost during testing:
    'Access-Control-Allow-Origin': '*',
  });
  res.end(body);
}

const server = http.createServer((req, res) => {
  const url = req.url.split('?')[0]; // strip query string
  console.log('[server]', req.method, url);

  if (url === '/' || url === '/index.html') {
    serve(res, path.join(__dirname, 'index.html'));
  } else if (url.startsWith('/stubs/')) {
    const name = path.basename(url);
    serve(res, path.join(__dirname, 'stubs', name));
  } else if (url === '/adapter/engine-adapter.js') {
    serve(res, path.join(__dirname, 'engine-adapter.js'));
  } else if (url === '/plugin/index.js') {
    serve(res, path.join(DIST_DIR, 'index.js'));
  } else if (url.startsWith('/plugin/tone-') && url.endsWith('.js')) {
    const name = path.basename(url);
    serve(res, path.join(DIST_DIR, name));
  } else if (url === '/vendor/react.js') {
    serve(res, path.resolve(__dirname, '../../../src/js/node_modules/react/umd/react.development.js'));
  } else if (url === '/vendor/react-dom.js') {
    serve(res, path.resolve(__dirname, '../../../src/js/node_modules/react-dom/umd/react-dom.development.js'));
  } else {
    res.writeHead(404, { 'Content-Type': 'text/plain' });
    res.end('404');
  }
});

server.listen(PORT, '127.0.0.1', () => {
  console.log(`[harness-server] listening on http://127.0.0.1:${PORT}`);
});

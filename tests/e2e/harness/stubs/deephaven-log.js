/**
 * Stub for @deephaven/log
 * The bundle calls Log.module('ToneEngine') which returns a logger with .warn/.info etc.
 */
const noop = () => {};
const logger = {
  debug: noop,
  info: noop,
  warn: (...args) => console.warn('[ToneEngine]', ...args),
  error: (...args) => console.error('[ToneEngine]', ...args),
};

const Log = {
  module: (_name) => logger,
};

export default Log;

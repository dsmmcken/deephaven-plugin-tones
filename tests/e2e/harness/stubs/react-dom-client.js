/**
 * Stub for 'react-dom/client' — re-exports createRoot from window.ReactDOM.
 */
const RD = window.ReactDOM;
export const createRoot = RD?.createRoot ?? ((container) => ({
  render: () => {},
  unmount: () => {},
}));
export const hydrateRoot = RD?.hydrateRoot ?? (() => ({ render: () => {}, unmount: () => {} }));

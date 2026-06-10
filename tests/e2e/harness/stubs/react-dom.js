/**
 * Stub for 'react-dom' — re-exports the real ReactDOM from window.ReactDOM.
 */
const RD = window.ReactDOM;
export default RD;
export const { createPortal, findDOMNode, flushSync, hydrate, render, unmountComponentAtNode } = RD || {};

/**
 * Stub for 'react' — re-exports the real React from the CDN or a bundled copy.
 *
 * We use the real React 18 UMD build so that React hooks work correctly.
 * The script is loaded synchronously before this module is evaluated, setting
 * window.React.  We re-export its members here so ES import syntax works.
 *
 * IMPORTANT: This stub is loaded by the import map.  The <script> that loads
 * the real React must come BEFORE the <script type="module"> that kicks off
 * the import chain.  The harness HTML file handles this ordering.
 */

// The harness loads React 18 via a <script> tag into window.React before
// any module code runs.  We forward everything from there.
const R = window.React;

export default R;
export const {
  useState,
  useEffect,
  useRef,
  useCallback,
  useMemo,
  useContext,
  createContext,
  createElement,
  Fragment,
  Children,
  cloneElement,
  createRef,
  forwardRef,
  memo,
  Component,
  PureComponent,
  StrictMode,
  Suspense,
  lazy,
  startTransition,
} = R;

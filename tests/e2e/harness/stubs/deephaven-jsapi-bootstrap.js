/**
 * Stub for @deephaven/jsapi-bootstrap
 * The bundle calls useApi() which throws when outside a Provider.
 * The view component catches that error and degrades gracefully (dh = undefined).
 * We replicate that throw here so the component's catch branch runs.
 */
export function useApi() {
  throw new Error('[stub] No ApiContext.Provider in test harness — table mode disabled');
}

export function useClient() {
  throw new Error('[stub] No ClientContext in test harness');
}

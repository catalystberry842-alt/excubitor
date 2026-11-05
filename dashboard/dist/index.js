/* Excubitor dashboard half: API-only (tab.hidden). No UI tab. */
(function () {
  try {
    if (window.__HERMES_PLUGINS__ && typeof window.__HERMES_PLUGINS__.register === 'function') {
      window.__HERMES_PLUGINS__.register('excubitor', function () {
        return null;
      });
    }
  } catch (_) {
    /* fail-open */
  }
})();

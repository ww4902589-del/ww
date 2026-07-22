(function attachLivV8RuntimeConfig(global) {
  'use strict';

  const MODES = Object.freeze({
    PRODUCTION: 'production',
    DIAGNOSTIC: 'diagnostic'
  });

  function readRequestedMode() {
    let requested = '';
    try {
      requested = new URLSearchParams(global.location.search).get('v8mode') || '';
    } catch (_) {
      requested = '';
    }

    if (requested.toLowerCase() === MODES.DIAGNOSTIC) return MODES.DIAGNOSTIC;
    if (global.LIV_V8_FORCE_DIAGNOSTIC === true) return MODES.DIAGNOSTIC;
    return MODES.PRODUCTION;
  }

  function resolve() {
    const mode = readRequestedMode();
    const diagnostic = mode === MODES.DIAGNOSTIC;
    return Object.freeze({
      mode,
      diagnostic,
      productionUsable: !diagnostic,
      loaderOptions: Object.freeze({
        baseUrl: diagnostic ? 'assets/frame-v8/diagnostic' : 'assets/frame-v8',
        manifestBaseUrl: 'assets/frame-v8/manifests'
      }),
      fallbackUrl: 'assets/frame-v8/references/reference_A_standing.svg',
      statusLabel: diagnostic ? 'DIAGNOSTIC_LAYER_TEST' : 'PRODUCTION_ASSET_MODE'
    });
  }

  global.LivV8RuntimeConfig = Object.freeze({
    MODES,
    resolve
  });
})(window);

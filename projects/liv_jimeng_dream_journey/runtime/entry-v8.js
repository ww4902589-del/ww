(function attachLivV8Entry(global) {
  'use strict';

  function setupDiagnosticPanel(config, app) {
    const panel = document.getElementById('v8-diagnostic');
    if (!panel || !config.diagnostic) return;

    panel.hidden = false;
    const mode = panel.querySelector('[data-role="mode"]');
    const status = panel.querySelector('[data-role="status"]');
    const assets = panel.querySelector('[data-role="assets"]');
    const missing = panel.querySelector('[data-role="missing"]');
    const frame = panel.querySelector('[data-role="frame"]');
    const fps = panel.querySelector('[data-role="fps"]');

    if (mode) mode.textContent = config.mode;
    if (status) status.textContent = app.status;
    if (assets) assets.textContent = `${app.report.loaded?.length || 0} / ${app.report.expectedCount || 79}`;
    if (missing) missing.textContent = String(app.report.missingRequired?.length || 0);

    let previous = performance.now();
    let count = 0;
    let displayFps = 0;

    function update() {
      if (!global.livV8App || !global.livV8App.runtime) return;
      const now = performance.now();
      count += 1;
      if (now - previous >= 1000) {
        displayFps = count;
        count = 0;
        previous = now;
      }
      if (fps) fps.textContent = String(displayFps);
      if (frame && global.livV8App.runtime.lastFrame != null) {
        frame.textContent = `${global.livV8App.runtime.lastFrame} / 287`;
      }
      requestAnimationFrame(update);
    }
    requestAnimationFrame(update);
  }

  async function start() {
    try {
      const runtimeConfig = global.LivV8RuntimeConfig
        ? global.LivV8RuntimeConfig.resolve()
        : {
          mode: 'production',
          diagnostic: false,
          loaderOptions: {
            baseUrl: 'assets/frame-v8',
            manifestBaseUrl: 'assets/frame-v8/manifests'
          }
        };

      const app = await global.bootLivV8({
        canvas: document.getElementById('wallpaper'),
        titleNode: document.getElementById('title'),
        statusNode: document.getElementById('runtime-status'),
        loaderOptions: runtimeConfig.loaderOptions,
        fallbackUrl: runtimeConfig.fallbackUrl
      });

      if (app.status !== 'READY') {
        console.warn('[LivV8] started in blocked mode', app.report);
      }

      setupDiagnosticPanel(runtimeConfig, app);
      global.livV8Started = true;
      return app;
    } catch (error) {
      const node = document.getElementById('runtime-status');
      if (node) {
        node.hidden = false;
        node.dataset.state = 'BOOT_ERROR';
        node.querySelector('[data-role="state"]').textContent = 'BOOT_ERROR';
        node.querySelector('[data-role="detail"]').textContent = error.message;
      }
      console.error('[LivV8] boot error', error);
      return null;
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }

  global.startLivV8 = start;
})(window);

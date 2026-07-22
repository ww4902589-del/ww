(function attachLivV8Entry(global) {
  'use strict';

  function setupDiagnosticPanel(config, app) {
    const panel = document.getElementById('v8-diagnostic');
    if (!panel || !config.diagnostic) return;

    panel.hidden = false;
    const modeNode = panel.querySelector('[data-role="mode"]');
    const statusNode = panel.querySelector('[data-role="status"]');
    const assetsNode = panel.querySelector('[data-role="assets"]');
    const missingNode = panel.querySelector('[data-role="missing"]');
    const frameNode = panel.querySelector('[data-role="frame"]');
    const fpsNode = panel.querySelector('[data-role="fps"]');

    if (modeNode) modeNode.textContent = config.mode;
    if (statusNode) statusNode.textContent = app.status;
    if (assetsNode) assetsNode.textContent = `${app.report.loaded?.length || 0} / ${app.report.expectedCount || 79}`;
    if (missingNode) missingNode.textContent = String(app.report.missingRequired?.length || 0);

    let previousSampleAt = performance.now();
    let renderedFrames = 0;
    let displayFps = 0;

    function update() {
      const currentApp = global.livV8App;
      if (!currentApp || !currentApp.runtime) return;

      const now = performance.now();
      renderedFrames += 1;
      if (now - previousSampleAt >= 1000) {
        displayFps = Math.round(renderedFrames * 1000 / Math.max(1, now - previousSampleAt));
        renderedFrames = 0;
        previousSampleAt = now;
      }

      const runtimeState = typeof currentApp.runtime.getState === 'function'
        ? currentApp.runtime.getState()
        : currentApp.runtime.lastState;
      const runtimeStatus = typeof currentApp.runtime.getStatus === 'function'
        ? currentApp.runtime.getStatus()
        : currentApp.status;

      if (fpsNode) fpsNode.textContent = String(displayFps);
      if (statusNode) statusNode.textContent = runtimeStatus || currentApp.status || '-';
      if (frameNode && runtimeState && Number.isInteger(runtimeState.runtimeFrame)) {
        frameNode.textContent = `${runtimeState.runtimeFrame} / 287`;
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
          },
          fallbackUrl: 'assets/frame-v8/references/reference_A_standing.svg'
        };

      const app = await global.bootLivV8({
        canvas: document.getElementById('wallpaper'),
        titleNode: document.getElementById('title'),
        statusNode: document.getElementById('runtime-status'),
        loaderOptions: runtimeConfig.loaderOptions,
        fallbackUrl: runtimeConfig.fallbackUrl
      });

      app.assetMode = runtimeConfig.mode;
      app.productionUsable = runtimeConfig.productionUsable !== false;

      if (app.status !== 'READY') {
        console.warn('[LivV8] started in blocked mode', app.report);
      } else if (runtimeConfig.diagnostic) {
        console.warn('[LivV8] diagnostic assets are active; this is not production artwork.');
      }

      setupDiagnosticPanel(runtimeConfig, app);
      global.livV8Started = true;
      return app;
    } catch (error) {
      const node = document.getElementById('runtime-status');
      if (node) {
        node.hidden = false;
        node.dataset.state = 'BOOT_ERROR';
        const stateNode = node.querySelector('[data-role="state"]');
        const detailNode = node.querySelector('[data-role="detail"]');
        if (stateNode) stateNode.textContent = 'BOOT_ERROR';
        if (detailNode) detailNode.textContent = error.message;
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

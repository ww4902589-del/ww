(function attachLivV8Entry(global) {
  'use strict';

  async function start() {
    try {
      const app = await global.bootLivV8({
        canvas: document.getElementById('wallpaper'),
        titleNode: document.getElementById('title'),
        statusNode: document.getElementById('runtime-status'),
        loaderOptions: {
          baseUrl: 'assets/frame-v8',
          manifestBaseUrl: 'assets/frame-v8/manifests'
        }
      });

      if (app.status !== 'READY') {
        console.warn('[LivV8] started in blocked mode', app.report);
      }

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

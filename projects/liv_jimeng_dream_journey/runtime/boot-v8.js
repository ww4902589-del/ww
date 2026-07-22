(function attachLivV8Boot(global) {
  'use strict';

  async function bootLivV8(options) {
    const settings = options || {};
    const canvas = settings.canvas || document.getElementById('wallpaper');
    if (!canvas) throw new Error('Missing #wallpaper canvas');

    const loader = new global.LivV8FrameLoader(settings.loaderOptions);
    const timeline = global.LivV8Timeline;
    const background = new global.LivV8BackgroundEngine();
    const layers = new global.LivV8LayerManager(settings.layout || {});

    const runtime = new global.LivV8Runtime({
      loader,
      timeline,
      background,
      frameMap: null
    });

    const report = await runtime.initialize();
    if (!report.ok) {
      console.warn('Liv v8 blocked:', report);
      return { runtime, report };
    }

    const renderer = new global.LivV8Renderer({
      canvas,
      loader,
      layout: loader.manifests.layout,
      zOrder: loader.manifests.zOrder
    });

    renderer.resize();
    window.addEventListener('resize', () => renderer.resize(), { passive: true });

    function loop(now) {
      const state = runtime.tick(now);
      if (state) renderer.draw(state, { quietFactor: 1 });
      requestAnimationFrame(loop);
    }

    requestAnimationFrame(loop);

    return { runtime, renderer, report };
  }

  global.bootLivV8 = bootLivV8;
})(window);

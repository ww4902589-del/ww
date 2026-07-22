(function attachLivV8Boot(global) {
  'use strict';

  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

  function pointerPosition(canvas, event) {
    const rect = canvas.getBoundingClientRect();
    return {
      x: clamp((event.clientX - rect.left) / Math.max(1, rect.width), 0, 1),
      y: clamp((event.clientY - rect.top) / Math.max(1, rect.height), 0, 1)
    };
  }

  function setStatus(node, state, detail) {
    if (!node) return;
    node.dataset.state = state;
    node.hidden = false;
    node.querySelector('[data-role="state"]').textContent = state;
    node.querySelector('[data-role="detail"]').textContent = detail || '';
  }

  function hideStatus(node) {
    if (node) node.hidden = true;
  }

  async function bootLivV8(options) {
    const settings = options || {};
    const canvas = settings.canvas || document.getElementById('wallpaper');
    const titleNode = settings.titleNode || document.getElementById('title');
    const statusNode = settings.statusNode || document.getElementById('runtime-status');
    if (!canvas) throw new Error('Missing #wallpaper canvas');

    const config = {
      quality: 2,
      motion: 0.6,
      characterAction: 0.78,
      audio: 0.5,
      parallax: 0.45,
      interaction: true,
      particles: true,
      showTitle: true,
      quietMode: true,
      idleSeconds: 60,
      scale: 1.08,
      offsetX: 0
    };

    const state = {
      lastInputAt: performance.now(),
      pointer: { x: 0.5, y: 0.5, smoothX: 0.5, smoothY: 0.5 },
      audioRaw: 0,
      audioSmooth: 0,
      ripples: [],
      clickReadyAt: 0,
      stopped: false
    };

    setStatus(statusNode, 'LOADING_V8_ASSETS', '正在校验多图片帧动画资源。');

    const loader = new global.LivV8FrameLoader(settings.loaderOptions);
    const timeline = global.LivV8Timeline;
    const background = new global.LivV8BackgroundEngine();
    const runtime = new global.LivV8Runtime({ loader, timeline, background, frameMap: null });
    const report = await runtime.initialize();

    if (!report.ok) {
      const detail = `缺少 ${report.missing.length}/${report.expectedCount} 个生产素材；未启用整图回退。`;
      setStatus(statusNode, 'BLOCKED_BY_ASSET_PRODUCTION', detail);
      console.warn('Liv v8 blocked:', report);
      const blockedApp = { runtime, loader, report, config, state, status: 'BLOCKED_BY_ASSET_PRODUCTION' };
      global.livV8App = blockedApp;
      return blockedApp;
    }

    const renderer = new global.LivV8Renderer({
      canvas,
      loader,
      layout: loader.manifests.layout,
      zOrder: loader.manifests.zOrder
    });

    function syncRendererConfig() {
      renderer.setConfig({
        quality: config.quality,
        motion: config.motion,
        audio: config.audio,
        parallax: config.parallax,
        particles: config.particles,
        showTitle: config.showTitle,
        scale: config.scale,
        offsetX: config.offsetX
      });
      if (titleNode) {
        titleNode.classList.toggle('hidden', !config.showTitle);
        titleNode.classList.toggle('canvas-rendered', config.showTitle);
      }
    }

    function applyUserProperties(properties) {
      if (properties.quality) config.quality = Number(properties.quality.value);
      if (properties.motion) config.motion = clamp(Number(properties.motion.value) / 100, 0, 1);
      if (properties.characteraction) config.characterAction = clamp(Number(properties.characteraction.value) / 100, 0, 1);
      if (properties.audioresponse) config.audio = clamp(Number(properties.audioresponse.value) / 100, 0, 1);
      if (properties.parallax) config.parallax = clamp(Number(properties.parallax.value) / 100, 0, 1);
      if (properties.interaction) config.interaction = Boolean(properties.interaction.value);
      if (properties.particles) config.particles = Boolean(properties.particles.value);
      if (properties.showtitle) config.showTitle = Boolean(properties.showtitle.value);
      if (properties.quietmode) config.quietMode = Boolean(properties.quietmode.value);
      if (properties.idletime) config.idleSeconds = Math.max(1, Number(properties.idletime.value));
      if (properties.imagescale) config.scale = clamp(Number(properties.imagescale.value) / 100, 1, 1.3);
      if (properties.offsetx) config.offsetX = clamp(Number(properties.offsetx.value) / 100, -0.2, 0.2);
      syncRendererConfig();
      renderer.resize();
    }

    global.wallpaperPropertyListener = { applyUserProperties };

    function wallpaperAudioListener(audioArray) {
      if (!audioArray || !audioArray.length) return;
      const count = Math.min(16, audioArray.length);
      let sum = 0;
      for (let index = 0; index < count; index += 1) sum += Math.max(0, Number(audioArray[index]) || 0);
      state.audioRaw = clamp(sum / count, 0, 1.25);
    }

    if (typeof global.wallpaperRegisterAudioListener === 'function') {
      global.wallpaperRegisterAudioListener(wallpaperAudioListener);
    }

    canvas.addEventListener('pointermove', event => {
      const point = pointerPosition(canvas, event);
      state.pointer.x = point.x;
      state.pointer.y = point.y;
      state.lastInputAt = performance.now();
    }, { passive: true });

    canvas.addEventListener('pointerdown', event => {
      state.lastInputAt = performance.now();
      if (!config.interaction) return;
      const now = performance.now();
      if (now < state.clickReadyAt) return;
      state.clickReadyAt = now + 900;
      const point = pointerPosition(canvas, event);
      state.ripples.push({
        x: point.x * renderer.width,
        y: point.y * renderer.height,
        start: now,
        duration: 1800 + (1 - config.motion) * 1000
      });
    });

    global.addEventListener('resize', () => renderer.resize(), { passive: true });
    renderer.resize();
    syncRendererConfig();
    hideStatus(statusNode);

    function loop(now) {
      if (state.stopped) return;
      state.pointer.smoothX += (state.pointer.x - state.pointer.smoothX) * 0.055;
      state.pointer.smoothY += (state.pointer.y - state.pointer.smoothY) * 0.055;
      state.audioSmooth += (state.audioRaw - state.audioSmooth) * 0.08;

      const idleSeconds = (now - state.lastInputAt) / 1000;
      const quietFactor = config.quietMode && idleSeconds >= config.idleSeconds ? 0.22 : 1;

      runtime.setOptions({
        motion: config.motion,
        characterAction: config.characterAction,
        quietFactor,
        audioBoost: state.audioSmooth * config.audio
      });
      renderer.setPointer(state.pointer.smoothX, state.pointer.smoothY);

      const frameState = runtime.tick(now);
      if (frameState) {
        renderer.draw(frameState, { quietFactor, audioBoost: state.audioSmooth });
        state.ripples = state.ripples.filter(ripple => now - ripple.start < ripple.duration);
        renderer.drawRipples(state.ripples, now);
      }
      requestAnimationFrame(loop);
    }

    requestAnimationFrame(loop);

    const app = {
      runtime,
      renderer,
      loader,
      report,
      config,
      state,
      status: 'READY',
      applyUserProperties,
      stop() { state.stopped = true; }
    };
    global.livV8App = app;
    return app;
  }

  global.bootLivV8 = bootLivV8;
})(window);

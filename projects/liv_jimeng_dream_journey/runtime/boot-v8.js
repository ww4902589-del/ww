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
    const stateNode = node.querySelector('[data-role="state"]');
    const detailNode = node.querySelector('[data-role="detail"]');
    if (stateNode) stateNode.textContent = state;
    if (detailNode) detailNode.textContent = detail || '';
  }

  function hideStatus(node) {
    if (node) node.hidden = true;
  }

  function createDefaultConfig() {
    return {
      quality: 2,
      motion: 0.6,
      characterAction: 0.78,
      audio: 0.5,
      theme: 0,
      ambient: false,
      parallax: 0.45,
      interaction: true,
      interactionStrength: 0.82,
      particles: true,
      showTitle: true,
      quietMode: true,
      idleSeconds: 60,
      scale: 1.08,
      offsetX: 0
    };
  }

  function applyPropertiesToConfig(config, properties) {
    const source = properties || {};
    if (source.quality) config.quality = Number(source.quality.value);
    if (source.motion) config.motion = clamp(Number(source.motion.value) / 100, 0, 1);
    if (source.characteraction) config.characterAction = clamp(Number(source.characteraction.value) / 100, 0, 1);
    if (source.audioresponse) config.audio = clamp(Number(source.audioresponse.value) / 100, 0, 1);
    if (source.theme) config.theme = Number(source.theme.value);
    if (source.ambient) config.ambient = Boolean(source.ambient.value);
    if (source.parallax) config.parallax = clamp(Number(source.parallax.value) / 100, 0, 1);
    if (source.interaction) config.interaction = Boolean(source.interaction.value);
    if (source.interactionstrength) config.interactionStrength = clamp(Number(source.interactionstrength.value) / 100, 0, 1);
    if (source.particles) config.particles = Boolean(source.particles.value);
    if (source.showtitle) config.showTitle = Boolean(source.showtitle.value);
    if (source.quietmode) config.quietMode = Boolean(source.quietmode.value);
    if (source.idletime) config.idleSeconds = Math.max(1, Number(source.idletime.value));
    if (source.imagescale) config.scale = clamp(Number(source.imagescale.value) / 100, 1, 1.3);
    if (source.offsetx) config.offsetX = clamp(Number(source.offsetx.value) / 100, -0.2, 0.2);
    document.documentElement.dataset.livTheme = String(config.theme);
    return config;
  }

  function loadStaticImage(url) {
    return new Promise((resolve, reject) => {
      const image = new Image();
      image.decoding = 'async';
      image.onload = () => resolve(image);
      image.onerror = () => reject(new Error(`Unable to load static fallback: ${url}`));
      image.src = url;
    });
  }

  function drawStaticFallback(canvas, image, config) {
    const width = Math.max(1, global.innerWidth || canvas.clientWidth || 1920);
    const height = Math.max(1, global.innerHeight || canvas.clientHeight || 1080);
    const qualityLimit = config.quality === 3 ? 2 : config.quality === 1 ? 1 : 1.5;
    const dpr = Math.min(global.devicePixelRatio || 1, qualityLimit);
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;

    const context = canvas.getContext('2d', { alpha: false });
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    context.fillStyle = '#020817';
    context.fillRect(0, 0, width, height);

    if (image) {
      const imageWidth = image.naturalWidth || image.width || 160;
      const imageHeight = image.naturalHeight || image.height || 90;
      const scale = Math.max(width / imageWidth, height / imageHeight) * config.scale;
      const drawWidth = imageWidth * scale;
      const drawHeight = imageHeight * scale;
      const drawX = (width - drawWidth) * 0.5 + width * config.offsetX;
      const drawY = (height - drawHeight) * 0.5;
      context.drawImage(image, drawX, drawY, drawWidth, drawHeight);
    }

    const gradient = context.createLinearGradient(0, 0, width, height);
    gradient.addColorStop(0, 'rgba(1, 8, 24, 0.18)');
    gradient.addColorStop(0.58, 'rgba(2, 8, 23, 0.03)');
    gradient.addColorStop(1, 'rgba(1, 5, 18, 0.34)');
    context.fillStyle = gradient;
    context.fillRect(0, 0, width, height);
  }

  async function startStaticFallback(options) {
    const settings = options || {};
    const canvas = settings.canvas;
    const titleNode = settings.titleNode;
    const statusNode = settings.statusNode;
    const config = settings.config;
    const report = settings.report;
    const fallbackUrl = settings.fallbackUrl || 'assets/frame-v8/references/reference_A_standing.svg';
    let image = null;
    let fallbackError = null;

    try {
      image = await loadStaticImage(fallbackUrl);
    } catch (error) {
      fallbackError = error;
      console.warn('[LivV8] static fallback image unavailable; using static color field', error);
    }

    function redraw() {
      drawStaticFallback(canvas, image, config);
      if (titleNode) {
        titleNode.classList.toggle('hidden', !config.showTitle);
        titleNode.classList.remove('canvas-rendered');
      }
    }

    function applyUserProperties(properties) {
      applyPropertiesToConfig(config, properties);
      redraw();
    }

    global.wallpaperPropertyListener = { applyUserProperties };
    global.addEventListener('resize', redraw, { passive: true });
    redraw();

    const missingRequired = report?.missingRequired || report?.missing || [];
    const requiredCount = report?.requiredCount || report?.expectedCount || 0;
    const detail = fallbackError
      ? `缺少 ${missingRequired.length}/${requiredCount} 个必需素材；静态纯色安全回退已启用。`
      : `缺少 ${missingRequired.length}/${requiredCount} 个必需素材；单张静态参考回退已启用，未使用整图动态变形。`;
    setStatus(statusNode, 'BLOCKED_BY_ASSET_PRODUCTION', detail);

    return {
      status: 'BLOCKED_BY_ASSET_PRODUCTION',
      mode: 'STATIC_FALLBACK',
      report,
      config,
      fallbackUrl: image ? fallbackUrl : null,
      fallbackError,
      applyUserProperties,
      redraw,
      stop() {}
    };
  }

  async function bootLivV8(options) {
    const settings = options || {};
    const canvas = settings.canvas || document.getElementById('wallpaper');
    const titleNode = settings.titleNode || document.getElementById('title');
    const statusNode = settings.statusNode || document.getElementById('runtime-status');
    if (!canvas) throw new Error('Missing #wallpaper canvas');

    const config = createDefaultConfig();
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
    let report;

    try {
      report = await runtime.initialize();
    } catch (error) {
      report = {
        ok: false,
        status: 'BLOCKED_BY_ASSET_PRODUCTION',
        expectedCount: 0,
        requiredCount: 0,
        loaded: [],
        missing: [],
        missingRequired: [],
        missingOptional: [],
        errors: [{ id: 'manifest_or_runtime_init', required: true, message: error.message }]
      };
      console.error('[LivV8] manifest/runtime initialization failed', error);
    }

    if (!report.ok) {
      const blockedApp = await startStaticFallback({
        canvas,
        titleNode,
        statusNode,
        config,
        report,
        fallbackUrl: settings.fallbackUrl
      });
      blockedApp.runtime = runtime;
      blockedApp.loader = loader;
      blockedApp.state = state;
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
        theme: config.theme,
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
      applyPropertiesToConfig(config, properties);
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
        duration: 1800 + (1 - config.motion) * 1000,
        strength: config.interactionStrength
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
      mode: 'LAYERED_FRAME_SEQUENCE',
      applyUserProperties,
      stop() { state.stopped = true; }
    };
    global.livV8App = app;
    return app;
  }

  global.bootLivV8 = bootLivV8;
})(window);

(function (global) {
  'use strict';

  const W = 1920;
  const H = 1080;
  const TAU = Math.PI * 2;
  const clamp = (v, min, max) => Math.max(min, Math.min(max, v));

  class LivV8Renderer {
    constructor(options) {
      this.canvas = options.canvas;
      this.ctx = options.context || this.canvas.getContext('2d', { alpha: false });
      this.loader = options.loader;
      this.layout = options.layout;
      this.zOrder = options.zOrder.layers;
      this.pointer = { x: 0.5, y: 0.5 };
      this.config = {
        quality: 2, motion: 0.6, audio: 0.5, parallax: 0.45,
        particles: true, showTitle: true, scale: 1.08, offsetX: 0
      };
      this.width = 1;
      this.height = 1;
      this.dpr = 1;
    }

    setConfig(next) { Object.assign(this.config, next || {}); }
    setPointer(x, y) { this.pointer = { x: clamp(x, 0, 1), y: clamp(y, 0, 1) }; }

    resize() {
      this.width = Math.max(1, window.innerWidth);
      this.height = Math.max(1, window.innerHeight);
      const limit = this.config.quality === 3 ? 2 : this.config.quality === 1 ? 1 : 1.5;
      this.dpr = Math.min(window.devicePixelRatio || 1, limit);
      this.canvas.width = Math.round(this.width * this.dpr);
      this.canvas.height = Math.round(this.height * this.dpr);
      this.canvas.style.width = `${this.width}px`;
      this.canvas.style.height = `${this.height}px`;
    }

    viewTransform() {
      const scale = Math.max(this.width / W, this.height / H) * clamp(this.config.scale, 1, 1.3);
      return {
        scale,
        x: (this.width - W * scale) * 0.5 + this.width * clamp(this.config.offsetX, -0.2, 0.2),
        y: (this.height - H * scale) * 0.5
      };
    }

    drawAsset(id, layer, options) {
      const image = this.loader.get(id);
      if (!image) return;
      const opt = options || {};
      const def = this.layout.layers[layer] || { pivot: [960, 540], parallax_px: [0, 0] };
      const view = this.viewTransform();
      const px = (this.pointer.x - 0.5) * 2;
      const py = (this.pointer.y - 0.5) * 2;
      const parallax = def.parallax_px || [0, 0];
      const dx = (opt.x || 0) + px * parallax[0] * this.config.parallax * this.config.motion;
      const dy = (opt.y || 0) + py * parallax[1] * this.config.parallax * this.config.motion;
      const pivot = opt.pivot || def.pivot || [960, 540];

      this.ctx.save();
      this.ctx.globalAlpha = clamp(opt.opacity == null ? 1 : opt.opacity, 0, 1);
      this.ctx.globalCompositeOperation = opt.composite || 'source-over';
      this.ctx.imageSmoothingEnabled = true;
      this.ctx.imageSmoothingQuality = 'high';
      this.ctx.translate(view.x, view.y);
      this.ctx.scale(view.scale, view.scale);
      this.ctx.translate(dx, dy);
      this.ctx.translate(pivot[0], pivot[1]);
      this.ctx.rotate(opt.rotation || 0);
      this.ctx.translate(-pivot[0], -pivot[1]);
      this.ctx.drawImage(image, 0, 0, image.naturalWidth, image.naturalHeight, 0, 0, W, H);
      this.ctx.restore();
    }

    drawFragments(state, bg, quiet) {
      if (!this.config.particles) return;
      Object.entries(state.fragments || {}).forEach(([id, asset], index) => {
        const region = this.layout.fragment_regions[id];
        if (!region) return;
        const phase = state.runtimeFrame / 288 * TAU + index * 1.37;
        const depth = region.depth === 'near' ? 1 : region.depth === 'mid' ? 0.65 : 0.35;
        const amount = 18 * depth * this.config.motion;
        this.drawAsset(asset, 'fragments_far', {
          pivot: region.pivot,
          x: Math.sin(phase) * amount,
          y: Math.cos(phase * 0.87) * amount * 0.55,
          rotation: Math.sin(phase * 0.71) * 0.035 * depth,
          opacity: (bg.fragmentOpacity || 0.7) * (bg.reveal?.fragments ?? 1) * quiet
        });
      });
    }

    drawMemories(state, bg, quiet) {
      if (!this.config.particles) return;
      for (let i = 0; i < 6; i += 1) {
        const cycle = ((state.runtimeFrame + i * 41) % 288) / 288;
        const fadeIn = clamp((cycle - 0.08) / 0.14, 0, 1);
        const fadeOut = 1 - clamp((cycle - 0.68) / 0.20, 0, 1);
        const opacity = fadeIn * fadeOut * (bg.memoryOpacity || 0.6) * (bg.reveal?.memories ?? 1) * quiet;
        if (opacity < 0.01) continue;
        const phase = cycle * TAU;
        this.drawAsset(`memory_${String(i + 1).padStart(2, '0')}`, 'memory_shards', {
          x: Math.sin(phase) * (5 + i), y: Math.cos(phase * 0.8) * (4 + i),
          rotation: Math.sin(phase * 0.6) * 0.018, opacity
        });
      }
    }

    draw(state, options) {
      const opt = options || {};
      const bg = state.background || {};
      const quiet = clamp(opt.quietFactor == null ? 1 : opt.quietFactor, 0.1, 1);
      const audio = clamp(opt.audioBoost || 0, 0, 1.25);
      this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      this.ctx.fillStyle = '#07101e';
      this.ctx.fillRect(0, 0, this.width, this.height);

      for (const layer of this.zOrder) {
        if (layer === 'background_clean') this.drawAsset('background_clean', layer);
        else if (layer === 'starfield_far') this.drawAsset(state.starfieldFar, layer, { x: bg.farOffset?.x, y: bg.farOffset?.y, opacity: 0.92 * quiet });
        else if (layer === 'starfield_mid') this.drawAsset(state.starfieldMid, layer, { x: bg.midOffset?.x, y: bg.midOffset?.y, opacity: 0.82 * quiet });
        else if (layer === 'hair_back') this.drawAsset(state.hairBack, layer);
        else if (layer === 'fragments_far') this.drawFragments(state, bg, quiet);
        else if (layer === 'body_base') this.drawAsset('body_base', layer);
        else if (layer === 'skirt') this.drawAsset(state.skirt, layer);
        else if (layer === 'ribbon') this.drawAsset(state.ribbon, layer);
        else if (layer === 'arm') this.drawAsset(state.arm, layer);
        else if (layer === 'head') this.drawAsset(state.head, layer);
        else if (layer === 'hair_front') this.drawAsset(state.hairFront, layer);
        else if (layer === 'eyes') this.drawAsset(state.eyes, layer);
        else if (layer === 'starfield_near') this.drawAsset(state.starfieldNear, layer, { x: bg.nearOffset?.x, y: bg.nearOffset?.y, opacity: 0.64 * quiet });
        else if (layer === 'crystal_glow') this.drawAsset(state.crystalGlow, layer, {
          rotation: bg.crystalRotation || 0,
          opacity: clamp((bg.crystalOpacity || 0.8) + audio * this.config.audio * 0.12, 0, 1) * (bg.reveal?.crystal ?? 1),
          composite: 'screen'
        });
        else if (layer === 'memory_shards') this.drawMemories(state, bg, quiet);
        else if (layer === 'title' && this.config.showTitle) this.drawAsset('title_base', layer, { opacity: 0.92 * quiet, composite: 'screen' });
      }
    }

    drawRipples(ripples, now) {
      this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      this.ctx.save();
      for (const ripple of ripples) {
        const t = clamp((now - ripple.start) / ripple.duration, 0, 1);
        if (t >= 1) continue;
        const radius = 8 + Math.min(this.width, this.height) * 0.11 * t;
        this.ctx.strokeStyle = `rgba(185,225,255,${(1 - t) * 0.62})`;
        this.ctx.lineWidth = 1.2 + (1 - t);
        this.ctx.beginPath();
        this.ctx.arc(ripple.x, ripple.y, radius, 0, TAU);
        this.ctx.stroke();
      }
      this.ctx.restore();
    }
  }

  global.LivV8Renderer = LivV8Renderer;
})(window);

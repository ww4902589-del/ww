(function attachLivV8BackgroundEngine(global) {
  'use strict';

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function lerp(a, b, t) {
    return a + (b - a) * t;
  }

  class BackgroundEngine {
    constructor() {
      this.state = {
        reveal: {
          fragments: 1,
          memories: 1,
          crystal: 1
        }
      };
    }

    update(frame, options) {
      const settings = options || {};
      const motion = clamp(settings.motion ?? 1, 0, 1);
      const quiet = settings.quietFactor ?? 1;

      const t = frame / 288;
      const breathing = 0.5 + Math.sin(t * Math.PI * 2) * 0.5;

      this.state = {
        farOffset: {
          x: Math.sin(t * Math.PI * 2) * 2 * motion,
          y: Math.cos(t * Math.PI * 2) * 2 * motion
        },
        midOffset: {
          x: Math.sin(t * Math.PI * 4 + 1) * 5 * motion,
          y: Math.cos(t * Math.PI * 3) * 4 * motion
        },
        nearOffset: {
          x: Math.sin(t * Math.PI * 6) * 10 * motion,
          y: Math.cos(t * Math.PI * 5) * 8 * motion
        },
        crystalRotation: Math.sin(t * Math.PI * 2) * 0.02 * motion,
        crystalOpacity: lerp(0.72, 1, breathing) * quiet,
        fragmentOpacity: lerp(0.55, 0.95, breathing) * quiet,
        memoryOpacity: lerp(0.3, 0.85, (breathing + 1) / 2),
        reveal: {
          fragments: this.state.reveal.fragments,
          memories: this.state.reveal.memories,
          crystal: this.state.reveal.crystal
        }
      };

      return this.state;
    }

    setReveal(channel, value) {
      if (!(channel in this.state.reveal)) return;
      this.state.reveal[channel] = clamp(value, 0, 1);
    }

    getState() {
      return JSON.parse(JSON.stringify(this.state));
    }
  }

  global.LivV8BackgroundEngine = BackgroundEngine;
})(window);

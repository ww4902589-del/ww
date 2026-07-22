(function attachLivV8Runtime(global) {
  'use strict';

  class LivV8Runtime {
    constructor(options) {
      const settings = options || {};
      this.loader = settings.loader;
      this.timeline = settings.timeline;
      this.background = settings.background;
      this.frameMap = settings.frameMap || null;
      this.loopSeconds = settings.loopSeconds || 12;
      this.fps = settings.fps || 24;
      this.startTime = performance.now();
      this.status = 'NOT_INITIALIZED';
      this.lastState = null;
      this.options = {
        motion: 1,
        quietFactor: 1,
        audioBoost: 0
      };
    }

    async initialize() {
      const report = await this.loader.preloadAll();
      if (!report.ok) {
        this.status = 'BLOCKED_BY_ASSET_PRODUCTION';
        return report;
      }

      this.frameMap = this.frameMap || this.loader.manifests.frameMap;
      this.loopSeconds = this.frameMap.loop_seconds || this.loopSeconds;
      this.fps = this.frameMap.fps || this.fps;
      this.startTime = performance.now();
      this.status = 'READY';
      return report;
    }

    setOptions(nextOptions) {
      Object.assign(this.options, nextOptions || {});
    }

    restart(now) {
      this.startTime = Number.isFinite(now) ? now : performance.now();
      this.lastState = null;
    }

    tick(now) {
      if (this.status !== 'READY' || !this.frameMap) return null;
      const currentTime = Number.isFinite(now) ? now : performance.now();
      const elapsedSeconds = (currentTime - this.startTime) / 1000;
      const frame = this.timeline.runtimeFrame(elapsedSeconds, this.loopSeconds, this.fps);
      const state = this.timeline.resolveFrameState(this.frameMap, frame);
      state.elapsedSeconds = elapsedSeconds;
      state.loopPhase = frame / this.frameMap.frame_count;
      state.audioBoost = this.options.audioBoost;
      state.background = this.background.update(frame, {
        motion: this.options.motion,
        quietFactor: this.options.quietFactor
      });
      this.lastState = state;
      return state;
    }

    getState() {
      return this.lastState;
    }

    getStatus() {
      return this.status;
    }
  }

  global.LivV8Runtime = LivV8Runtime;
})(window);

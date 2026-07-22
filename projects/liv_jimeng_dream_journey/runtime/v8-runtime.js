(function attachLivV8Runtime(global) {
  'use strict';

  class LivV8Runtime {
    constructor(options) {
      this.loader = options.loader;
      this.timeline = options.timeline;
      this.layers = options.layers;
      this.background = options.background;
      this.frameMap = options.frameMap;
      this.startTime = performance.now();
      this.running = false;
      this.lastState = null;
    }

    async initialize() {
      const report = await this.loader.preloadAll();
      if (!report.ok) {
        this.status = 'BLOCKED_BY_ASSET_PRODUCTION';
        return report;
      }
      this.status = 'READY';
      return report;
    }

    tick(now) {
      if (this.status !== 'READY') return null;
      const elapsed = (now - this.startTime) / 1000;
      const frame = this.timeline.runtimeFrame(elapsed, 12, 24);
      const state = this.timeline.resolveFrameState(this.frameMap, frame);
      state.background = this.background.update(frame, { motion: 1, quietFactor: 1 });
      this.lastState = state;
      return state;
    }

    getState() {
      return this.lastState;
    }
  }

  global.LivV8Runtime = LivV8Runtime;
})(window);

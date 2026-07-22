(function attachLivV8LayerManager(global) {
  'use strict';

  class LayerManager {
    constructor(loader) {
      this.loader = loader;
      this.layers = new Map();
    }

    register(name, definition) {
      this.layers.set(name, Object.assign({ visible: true, opacity: 1 }, definition));
    }

    draw(ctx, state) {
      for (const [name, layer] of this.layers) {
        if (!layer.visible) continue;
        const assetName = typeof layer.asset === 'function' ? layer.asset(state) : layer.asset;
        const image = this.loader.get(assetName);
        if (!image) continue;

        ctx.save();
        ctx.globalAlpha = layer.opacity;
        if (layer.transform) layer.transform(ctx, state);
        ctx.drawImage(image, layer.x || 0, layer.y || 0, layer.width, layer.height);
        ctx.restore();
      }
    }
  }

  global.LivV8LayerManager = LayerManager;
})(window);

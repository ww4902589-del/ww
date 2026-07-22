(function attachLivV8FrameLoader(global) {
  'use strict';

  const BASE_ASSET_IDS = ['background_clean', 'body_base', 'crystal_base', 'title_base'];

  function unique(values) {
    return Array.from(new Set(values));
  }

  function pad2(value) {
    return String(value).padStart(2, '0');
  }

  function assetDirectory(assetId) {
    if (BASE_ASSET_IDS.includes(assetId)) return 'base';
    if (assetId.startsWith('head_')) return 'head';
    if (assetId.startsWith('eye_')) return 'eyes';
    if (assetId.startsWith('arm_')) return 'arm';
    if (assetId.startsWith('hair_')) return 'hair';
    if (assetId.startsWith('skirt_') || assetId.startsWith('ribbon_')) return 'cloth';
    if (assetId.startsWith('starfield_') || assetId.startsWith('crystal_glow_')) return 'background';
    if (assetId.startsWith('frag_')) return 'fragments';
    if (assetId.startsWith('memory_')) return 'memories';
    throw new Error(`Unknown v8 asset id: ${assetId}`);
  }

  function assetPath(baseUrl, assetId) {
    return `${baseUrl}/${assetDirectory(assetId)}/${assetId}.png`;
  }

  function collectAssetIds(frameMap) {
    const channels = frameMap.channels;
    const ids = [...BASE_ASSET_IDS];

    ids.push(...channels.head.frames);
    ids.push(channels.eyes.default, ...channels.eyes.sequence);
    ids.push(...channels.arm.frames);

    for (let index = 0; index < 5; index += 1) {
      ids.push(`hair_back_${pad2(index)}`, `hair_front_${pad2(index)}`);
    }
    for (let index = 0; index < 4; index += 1) {
      ids.push(`skirt_${pad2(index)}`, `ribbon_${pad2(index)}`);
      ids.push(`starfield_far_${pad2(index)}`, `starfield_mid_${pad2(index)}`);
      ids.push(`starfield_near_${pad2(index)}`, `crystal_glow_${pad2(index)}`);
    }
    for (const fragmentId of Object.keys(channels.background.fragment_cycles)) {
      for (let index = 0; index < 3; index += 1) ids.push(`${fragmentId}_${pad2(index)}`);
    }
    for (let index = 1; index <= 6; index += 1) ids.push(`memory_${pad2(index)}`);

    return unique(ids);
  }

  async function fetchJson(url) {
    const response = await fetch(url, { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}: ${url}`);
    return response.json();
  }

  function loadImage(url) {
    return new Promise((resolve, reject) => {
      const image = new Image();
      image.decoding = 'async';
      image.onload = async () => {
        try {
          if (typeof image.decode === 'function') await image.decode();
        } catch (_) {
          // The load event already confirms the bitmap is usable.
        }
        resolve(image);
      };
      image.onerror = () => reject(new Error(`Unable to load image: ${url}`));
      image.src = url;
    });
  }

  class FrameLoader {
    constructor(options) {
      const settings = options || {};
      this.baseUrl = settings.baseUrl || 'assets/frame-v8';
      this.manifestBaseUrl = settings.manifestBaseUrl || `${this.baseUrl}/manifests`;
      this.images = new Map();
      this.records = new Map();
      this.manifests = null;
      this.report = {
        ok: false,
        status: 'NOT_STARTED',
        loaded: [],
        missing: [],
        missingRequired: [],
        missingOptional: [],
        errors: []
      };
    }

    async loadManifests() {
      const [frameMap, layout, zOrder, assetSpec, checklist] = await Promise.all([
        fetchJson(`${this.manifestBaseUrl}/frame-map-v8.json`),
        fetchJson(`${this.manifestBaseUrl}/sprite-layout-v8.json`),
        fetchJson(`${this.manifestBaseUrl}/z-order-v8.json`),
        fetchJson(`${this.manifestBaseUrl}/asset-spec-v8.json`),
        fetchJson(`${this.manifestBaseUrl}/asset-checklist-v8.json`)
      ]);
      this.manifests = { frameMap, layout, zOrder, assetSpec, checklist };
      return this.manifests;
    }

    buildInventory() {
      if (!this.manifests) throw new Error('loadManifests() must run before buildInventory()');
      const ids = collectAssetIds(this.manifests.frameMap);
      this.records.clear();
      for (const id of ids) {
        this.records.set(id, {
          id,
          path: assetPath(this.baseUrl, id),
          format: 'png',
          alphaRequired: id !== 'background_clean',
          required: !id.startsWith('memory_')
        });
      }
      return Array.from(this.records.values());
    }

    async preloadAll(options) {
      const settings = options || {};
      const concurrency = Math.max(1, Math.min(12, settings.concurrency || 6));
      if (!this.manifests) await this.loadManifests();
      const records = this.buildInventory();
      const queue = records.slice();
      const loaded = [];
      const missing = [];
      const missingRequired = [];
      const missingOptional = [];
      const errors = [];

      const worker = async () => {
        while (queue.length) {
          const record = queue.shift();
          try {
            const image = await loadImage(record.path);
            if (!image.naturalWidth || !image.naturalHeight) throw new Error('zero-sized image');
            this.images.set(record.id, image);
            loaded.push(record.id);
          } catch (error) {
            missing.push(record.id);
            if (record.required) missingRequired.push(record.id);
            else missingOptional.push(record.id);
            errors.push({
              id: record.id,
              path: record.path,
              required: record.required,
              message: error.message
            });
          }
        }
      };

      await Promise.all(Array.from({ length: concurrency }, () => worker()));
      this.report = {
        ok: missingRequired.length === 0,
        status: missingRequired.length === 0 ? 'READY' : 'BLOCKED_BY_ASSET_PRODUCTION',
        expectedCount: records.length,
        requiredCount: records.filter(record => record.required).length,
        optionalCount: records.filter(record => !record.required).length,
        loaded,
        missing,
        missingRequired,
        missingOptional,
        errors
      };
      return this.report;
    }

    get(assetId) {
      return this.images.get(assetId) || null;
    }

    has(assetId) {
      return this.images.has(assetId);
    }

    getRecord(assetId) {
      return this.records.get(assetId) || null;
    }

    getReport() {
      return Object.assign({}, this.report, {
        loaded: this.report.loaded.slice(),
        missing: this.report.missing.slice(),
        missingRequired: this.report.missingRequired.slice(),
        missingOptional: this.report.missingOptional.slice(),
        errors: this.report.errors.slice()
      });
    }
  }

  global.LivV8FrameLoader = FrameLoader;
})(window);

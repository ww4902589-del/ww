(function attachLivV8FrameLoader(global) {
  'use strict';

  const PNG_SIGNATURE = [137, 80, 78, 71, 13, 10, 26, 10];

  function unique(values) {
    return Array.from(new Set(values));
  }

  function pad2(value) {
    return String(value).padStart(2, '0');
  }

  function collectFrameMapAssetIds(frameMap) {
    const channels = frameMap.channels;
    const ids = ['background_clean', 'body_base', 'crystal_base', 'title_base'];

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

  function readChunkType(view, offset) {
    return String.fromCharCode(
      view.getUint8(offset),
      view.getUint8(offset + 1),
      view.getUint8(offset + 2),
      view.getUint8(offset + 3)
    );
  }

  function parsePngMetadata(arrayBuffer) {
    const view = new DataView(arrayBuffer);
    if (view.byteLength < 33) throw new Error('invalid_png_too_small');
    for (let index = 0; index < PNG_SIGNATURE.length; index += 1) {
      if (view.getUint8(index) !== PNG_SIGNATURE[index]) throw new Error('invalid_png_signature');
    }

    const ihdrLength = view.getUint32(8, false);
    const ihdrType = readChunkType(view, 12);
    if (ihdrLength !== 13 || ihdrType !== 'IHDR') throw new Error('invalid_png_ihdr');

    const width = view.getUint32(16, false);
    const height = view.getUint32(20, false);
    const bitDepth = view.getUint8(24);
    const colorType = view.getUint8(25);
    let hasTransparencyChunk = false;
    let offset = 8;

    while (offset + 12 <= view.byteLength) {
      const length = view.getUint32(offset, false);
      if (offset + 12 + length > view.byteLength) throw new Error('invalid_png_chunk_length');
      const type = readChunkType(view, offset + 4);
      if (type === 'tRNS') hasTransparencyChunk = true;
      offset += 12 + length;
      if (type === 'IEND') break;
    }

    return {
      width,
      height,
      bitDepth,
      colorType,
      hasAlpha: colorType === 4 || colorType === 6 || hasTransparencyChunk
    };
  }

  function decodeImage(blob, urlLabel) {
    return new Promise((resolve, reject) => {
      const objectUrl = URL.createObjectURL(blob);
      const image = new Image();
      image.decoding = 'async';
      image.onload = async () => {
        try {
          if (typeof image.decode === 'function') await image.decode();
        } catch (_) {
          // The load event already confirms a usable decoded bitmap.
        }
        URL.revokeObjectURL(objectUrl);
        resolve(image);
      };
      image.onerror = () => {
        URL.revokeObjectURL(objectUrl);
        reject(new Error(`unable_to_decode_png: ${urlLabel}`));
      };
      image.src = objectUrl;
    });
  }

  async function loadAndValidatePng(record) {
    const response = await fetch(record.path, { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const buffer = await response.arrayBuffer();
    const metadata = parsePngMetadata(buffer);

    if (metadata.width !== record.width || metadata.height !== record.height) {
      throw new Error(`dimension_mismatch:${metadata.width}x${metadata.height}`);
    }
    if (record.alphaRequired && !metadata.hasAlpha) {
      throw new Error(`alpha_channel_required:color_type_${metadata.colorType}`);
    }

    const image = await decodeImage(new Blob([buffer], { type: 'image/png' }), record.path);
    return { image, metadata };
  }

  function validateChecklist(frameMap, checklist) {
    const errors = [];
    const assets = Array.isArray(checklist.assets) ? checklist.assets : [];
    const ids = assets.map(asset => asset.id);
    const duplicateIds = ids.filter((id, index) => ids.indexOf(id) !== index);
    const expectedIds = collectFrameMapAssetIds(frameMap);
    const idSet = new Set(ids);

    if (assets.length !== checklist.expected_asset_count) {
      errors.push(`asset_count_mismatch:${assets.length}/${checklist.expected_asset_count}`);
    }
    if (checklist.expected_asset_count !== 79) errors.push('expected_asset_count_must_be_79');
    if (duplicateIds.length) errors.push(`duplicate_asset_ids:${unique(duplicateIds).join(',')}`);

    const missingFromChecklist = expectedIds.filter(id => !idSet.has(id));
    const extraInChecklist = ids.filter(id => !expectedIds.includes(id));
    if (missingFromChecklist.length) errors.push(`missing_checklist_ids:${missingFromChecklist.join(',')}`);
    if (extraInChecklist.length) errors.push(`unexpected_checklist_ids:${extraInChecklist.join(',')}`);

    const requiredCount = assets.filter(asset => asset.required !== false).length;
    const optionalCount = assets.length - requiredCount;
    if (requiredCount !== checklist.required_asset_count) {
      errors.push(`required_count_mismatch:${requiredCount}/${checklist.required_asset_count}`);
    }
    if (optionalCount !== checklist.optional_asset_count) {
      errors.push(`optional_count_mismatch:${optionalCount}/${checklist.optional_asset_count}`);
    }

    for (const asset of assets) {
      if (!asset.id || !asset.path) errors.push(`invalid_asset_record:${asset.id || 'unknown'}`);
      if (asset.width !== 1920 || asset.height !== 1080) errors.push(`invalid_dimensions:${asset.id}`);
      if (!asset.path.endsWith(`${asset.id}.png`)) errors.push(`path_id_mismatch:${asset.id}`);
      if (asset.id !== 'background_clean' && asset.alpha_required !== true) {
        errors.push(`alpha_contract_missing:${asset.id}`);
      }
    }

    return { ok: errors.length === 0, errors, expectedIds };
  }

  class FrameLoader {
    constructor(options) {
      const settings = options || {};
      this.baseUrl = settings.baseUrl || 'assets/frame-v8';
      this.manifestBaseUrl = settings.manifestBaseUrl || `${this.baseUrl}/manifests`;
      this.images = new Map();
      this.records = new Map();
      this.metadata = new Map();
      this.manifests = null;
      this.report = {
        ok: false,
        status: 'NOT_STARTED',
        loaded: [],
        missing: [],
        missingRequired: [],
        missingOptional: [],
        errors: [],
        manifestErrors: []
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
      const checklistValidation = validateChecklist(frameMap, checklist);
      if (!checklistValidation.ok) {
        const error = new Error(`invalid_v8_asset_contract:${checklistValidation.errors.join('|')}`);
        error.validationErrors = checklistValidation.errors;
        throw error;
      }
      this.manifests = { frameMap, layout, zOrder, assetSpec, checklist };
      return this.manifests;
    }

    buildInventory() {
      if (!this.manifests) throw new Error('loadManifests() must run before buildInventory()');
      this.records.clear();
      for (const asset of this.manifests.checklist.assets) {
        const record = {
          id: asset.id,
          group: asset.group,
          path: `${this.baseUrl}/${asset.path}`,
          format: 'png',
          width: asset.width,
          height: asset.height,
          alphaRequired: asset.alpha_required === true,
          required: asset.required !== false
        };
        this.records.set(record.id, record);
      }
      return Array.from(this.records.values());
    }

    async preloadAll(options) {
      const settings = options || {};
      const concurrency = Math.max(1, Math.min(12, settings.concurrency || 6));
      this.images.clear();
      this.metadata.clear();

      try {
        if (!this.manifests) await this.loadManifests();
      } catch (error) {
        const manifestErrors = error.validationErrors || [error.message];
        this.report = {
          ok: false,
          status: 'BLOCKED_BY_INVALID_MANIFEST',
          expectedCount: 79,
          requiredCount: 73,
          optionalCount: 6,
          loaded: [],
          missing: [],
          missingRequired: [],
          missingOptional: [],
          errors: [{ id: 'asset_contract', required: true, message: error.message }],
          manifestErrors
        };
        return this.report;
      }

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
            const result = await loadAndValidatePng(record);
            this.images.set(record.id, result.image);
            this.metadata.set(record.id, result.metadata);
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
      const ready = missingRequired.length === 0;
      this.report = {
        ok: ready,
        status: ready ? 'READY' : 'BLOCKED_BY_ASSET_PRODUCTION',
        expectedCount: records.length,
        requiredCount: records.filter(record => record.required).length,
        optionalCount: records.filter(record => !record.required).length,
        loaded,
        missing,
        missingRequired,
        missingOptional,
        errors,
        manifestErrors: []
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

    getMetadata(assetId) {
      return this.metadata.get(assetId) || null;
    }

    getReport() {
      return Object.assign({}, this.report, {
        loaded: this.report.loaded.slice(),
        missing: this.report.missing.slice(),
        missingRequired: this.report.missingRequired.slice(),
        missingOptional: this.report.missingOptional.slice(),
        errors: this.report.errors.slice(),
        manifestErrors: this.report.manifestErrors.slice()
      });
    }
  }

  global.LivV8FrameLoader = FrameLoader;
})(window);

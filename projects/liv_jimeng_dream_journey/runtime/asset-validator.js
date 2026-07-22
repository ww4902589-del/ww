(function attachLivV8AssetValidator(global) {
  'use strict';

  function validateAssetRecord(asset) {
    const errors = [];
    if (!asset.path) errors.push('missing_path');
    if (asset.format && asset.format !== 'png') errors.push('unsupported_format');
    if (asset.alphaRequired && asset.hasAlpha !== true) errors.push('alpha_required');
    if (asset.forbidden && asset.forbidden.length) errors.push(...asset.forbidden);
    return errors;
  }

  function validateManifest(manifest) {
    const result = { ok: true, errors: [] };
    for (const asset of manifest.assets || []) {
      const errors = validateAssetRecord(asset);
      if (errors.length) {
        result.ok = false;
        result.errors.push({ asset: asset.id || asset.path, errors });
      }
    }
    return result;
  }

  global.LivV8AssetValidator = Object.freeze({
    validateAssetRecord,
    validateManifest
  });
})(window);

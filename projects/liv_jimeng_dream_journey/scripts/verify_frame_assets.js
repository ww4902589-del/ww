#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const zlib = require('node:zlib');

const PROJECT_ROOT = path.resolve(__dirname, '..');
const ASSET_ROOT = path.join(PROJECT_ROOT, 'assets', 'frame-v8');
const PRODUCTION_ROOT = path.join(ASSET_ROOT, 'production');
const MANUAL_REVIEW_PATH = path.join(PRODUCTION_ROOT, 'manual-review-v8.json');
const DEFAULT_REPORT_PATH = path.join(PROJECT_ROOT, 'qa', 'asset-validation-v8.json');
const EXPECTED_WIDTH = 1920;
const EXPECTED_HEIGHT = 1080;
const PNG_SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);

const args = new Set(process.argv.slice(2));
const reportArg = process.argv.find(arg => arg.startsWith('--write-report='));
const requireManualReview = args.has('--require-manual-review');
const strictWarnings = args.has('--strict-warnings');
const reportPath = reportArg
  ? path.resolve(PROJECT_ROOT, reportArg.slice('--write-report='.length))
  : DEFAULT_REPORT_PATH;

function pad2(value) {
  return String(value).padStart(2, '0');
}

function range(prefix, count, start = 0) {
  return Array.from({ length: count }, (_, index) => `${prefix}${pad2(start + index)}`);
}

function buildInventory() {
  return [
    { group: 'base', id: 'background_clean', alphaRequired: false, optional: false },
    { group: 'base', id: 'body_base', alphaRequired: true, optional: false },
    { group: 'base', id: 'crystal_base', alphaRequired: true, optional: false },
    { group: 'base', id: 'title_base', alphaRequired: true, optional: false },
    ...range('head_', 9).map(id => ({ group: 'head', id, alphaRequired: true, optional: false })),
    ...['eye_open', 'eye_half', 'eye_closed', 'eye_half_return', 'eye_open_hold']
      .map(id => ({ group: 'eyes', id, alphaRequired: true, optional: false })),
    ...range('arm_', 9).map(id => ({ group: 'arm', id, alphaRequired: true, optional: false })),
    ...range('hair_back_', 5).map(id => ({ group: 'hair', id, alphaRequired: true, optional: false })),
    ...range('hair_front_', 5).map(id => ({ group: 'hair', id, alphaRequired: true, optional: false })),
    ...range('skirt_', 4).map(id => ({ group: 'cloth', id, alphaRequired: true, optional: false })),
    ...range('ribbon_', 4).map(id => ({ group: 'cloth', id, alphaRequired: true, optional: false })),
    ...range('starfield_far_', 4).map(id => ({ group: 'background', id, alphaRequired: true, optional: false })),
    ...range('starfield_mid_', 4).map(id => ({ group: 'background', id, alphaRequired: true, optional: false })),
    ...range('starfield_near_', 4).map(id => ({ group: 'background', id, alphaRequired: true, optional: false })),
    ...range('crystal_glow_', 4).map(id => ({ group: 'background', id, alphaRequired: true, optional: false })),
    ...['frag_lt', 'frag_top', 'frag_mid', 'frag_rb'].flatMap(prefix =>
      range(`${prefix}_`, 3).map(id => ({ group: 'fragments', id, alphaRequired: true, optional: false }))
    ),
    ...range('memory_', 6, 1).map(id => ({ group: 'memories', id, alphaRequired: true, optional: true }))
  ];
}

function assetPath(record) {
  return path.join(ASSET_ROOT, record.group, `${record.id}.png`);
}

function sha256(buffer) {
  return crypto.createHash('sha256').update(buffer).digest('hex');
}

function readPng(buffer) {
  if (buffer.length < 33 || !buffer.subarray(0, 8).equals(PNG_SIGNATURE)) {
    throw new Error('invalid_png_signature');
  }

  let offset = 8;
  let ihdr = null;
  const idat = [];
  let hasTransparencyChunk = false;

  while (offset + 12 <= buffer.length) {
    const length = buffer.readUInt32BE(offset);
    const type = buffer.toString('ascii', offset + 4, offset + 8);
    const dataStart = offset + 8;
    const dataEnd = dataStart + length;
    const nextOffset = dataEnd + 4;
    if (nextOffset > buffer.length) throw new Error('truncated_png_chunk');

    if (type === 'IHDR') {
      ihdr = {
        width: buffer.readUInt32BE(dataStart),
        height: buffer.readUInt32BE(dataStart + 4),
        bitDepth: buffer[dataStart + 8],
        colorType: buffer[dataStart + 9],
        compression: buffer[dataStart + 10],
        filter: buffer[dataStart + 11],
        interlace: buffer[dataStart + 12]
      };
    } else if (type === 'IDAT') {
      idat.push(buffer.subarray(dataStart, dataEnd));
    } else if (type === 'tRNS') {
      hasTransparencyChunk = true;
    } else if (type === 'IEND') {
      break;
    }
    offset = nextOffset;
  }

  if (!ihdr) throw new Error('missing_ihdr');
  if (!idat.length) throw new Error('missing_idat');
  return { ihdr, idat, hasTransparencyChunk };
}

function paethPredictor(a, b, c) {
  const p = a + b - c;
  const pa = Math.abs(p - a);
  const pb = Math.abs(p - b);
  const pc = Math.abs(p - c);
  if (pa <= pb && pa <= pc) return a;
  if (pb <= pc) return b;
  return c;
}

function inspectRgbaPixels(parsed) {
  const { ihdr, idat } = parsed;
  if (ihdr.bitDepth !== 8 || ihdr.colorType !== 6 || ihdr.interlace !== 0) {
    return {
      inspectable: false,
      reason: 'requires_non_interlaced_8bit_rgba'
    };
  }

  const bytesPerPixel = 4;
  const stride = ihdr.width * bytesPerPixel;
  const inflated = zlib.inflateSync(Buffer.concat(idat));
  const expectedLength = (stride + 1) * ihdr.height;
  if (inflated.length !== expectedLength) throw new Error('unexpected_inflated_length');

  let previous = Buffer.alloc(stride);
  let minAlpha = 255;
  let maxAlpha = 0;
  let visibleSamples = 0;
  let transparentSamples = 0;
  let transparentRgbLeakSamples = 0;
  const sampleStepPixels = 8;

  for (let rowIndex = 0; rowIndex < ihdr.height; rowIndex += 1) {
    const rowOffset = rowIndex * (stride + 1);
    const filterType = inflated[rowOffset];
    const source = inflated.subarray(rowOffset + 1, rowOffset + 1 + stride);
    const row = Buffer.allocUnsafe(stride);

    for (let index = 0; index < stride; index += 1) {
      const raw = source[index];
      const left = index >= bytesPerPixel ? row[index - bytesPerPixel] : 0;
      const up = previous[index] || 0;
      const upLeft = index >= bytesPerPixel ? previous[index - bytesPerPixel] : 0;
      let value;
      if (filterType === 0) value = raw;
      else if (filterType === 1) value = (raw + left) & 255;
      else if (filterType === 2) value = (raw + up) & 255;
      else if (filterType === 3) value = (raw + Math.floor((left + up) / 2)) & 255;
      else if (filterType === 4) value = (raw + paethPredictor(left, up, upLeft)) & 255;
      else throw new Error(`unsupported_filter_${filterType}`);
      row[index] = value;
    }

    for (let pixel = 0; pixel < ihdr.width; pixel += sampleStepPixels) {
      const pixelOffset = pixel * bytesPerPixel;
      const alpha = row[pixelOffset + 3];
      minAlpha = Math.min(minAlpha, alpha);
      maxAlpha = Math.max(maxAlpha, alpha);
      if (alpha === 0) {
        transparentSamples += 1;
        if (Math.max(row[pixelOffset], row[pixelOffset + 1], row[pixelOffset + 2]) > 16) {
          transparentRgbLeakSamples += 1;
        }
      } else {
        visibleSamples += 1;
      }
    }
    previous = row;
  }

  return {
    inspectable: true,
    minAlpha,
    maxAlpha,
    visibleSamples,
    transparentSamples,
    transparentRgbLeakRatio: transparentSamples
      ? transparentRgbLeakSamples / transparentSamples
      : 0
  };
}

function validateManualReview() {
  if (!fs.existsSync(MANUAL_REVIEW_PATH)) {
    return { ok: false, pending: true, errors: ['manual_review_file_missing'] };
  }

  let review;
  try {
    review = JSON.parse(fs.readFileSync(MANUAL_REVIEW_PATH, 'utf8'));
  } catch (error) {
    return { ok: false, pending: false, errors: [`manual_review_invalid_json:${error.message}`] };
  }

  const requiredChecks = [
    'reference_identity_match',
    'hands_and_fingers_natural',
    'eyes_no_ghosting',
    'no_glow_noise',
    'no_chain_or_restraint_pattern',
    'no_alpha_halo',
    'head_motion_structurally_visible',
    'arm_motion_structurally_visible',
    'background_motion_perceptible',
    'loop_seam_approved'
  ];
  const failures = requiredChecks.filter(key => review.global_checks?.[key] !== true);
  const metadataOk = Boolean(review.reviewer && review.reviewed_at && review.review_status === 'APPROVED');
  if (!metadataOk) failures.push('review_metadata_incomplete');
  return {
    ok: failures.length === 0,
    pending: review.review_status !== 'APPROVED',
    errors: failures,
    reviewer: review.reviewer || null,
    reviewedAt: review.reviewed_at || null
  };
}

function validateDynamicGroups(results) {
  const thresholds = {
    head: 6,
    eyes: 3,
    arm: 6,
    hair: 6,
    cloth: 4,
    background: 8,
    fragments: 6
  };
  const errors = [];
  const warnings = [];

  for (const [group, minimumUnique] of Object.entries(thresholds)) {
    const present = results.filter(result => result.group === group && result.ok);
    const unique = new Set(present.map(result => result.sha256));
    if (present.length && unique.size < minimumUnique) {
      errors.push({
        code: 'insufficient_visual_variation',
        group,
        uniqueFiles: unique.size,
        minimumUnique
      });
    }
    if (present.length && unique.size < present.length) {
      warnings.push({
        code: 'duplicate_frame_hashes',
        group,
        duplicates: present.length - unique.size
      });
    }
  }
  return { errors, warnings };
}

function ensureDirectory(filePath) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
}

function main() {
  const inventory = buildInventory();
  const results = [];
  const errors = [];
  const warnings = [];

  for (const record of inventory) {
    const filePath = assetPath(record);
    const relativePath = path.relative(PROJECT_ROOT, filePath).replaceAll(path.sep, '/');
    const result = {
      id: record.id,
      group: record.group,
      optional: record.optional,
      path: relativePath,
      ok: true,
      errors: [],
      warnings: []
    };

    if (!fs.existsSync(filePath)) {
      result.ok = false;
      result.errors.push(record.optional ? 'optional_asset_missing' : 'required_asset_missing');
      results.push(result);
      if (!record.optional) errors.push({ id: record.id, code: 'required_asset_missing', path: relativePath });
      else warnings.push({ id: record.id, code: 'optional_asset_missing', path: relativePath });
      continue;
    }

    try {
      const buffer = fs.readFileSync(filePath);
      const parsed = readPng(buffer);
      const pixels = inspectRgbaPixels(parsed);
      result.bytes = buffer.length;
      result.sha256 = sha256(buffer);
      result.png = Object.assign({}, parsed.ihdr, {
        hasTransparencyChunk: parsed.hasTransparencyChunk
      });
      result.pixelInspection = pixels;

      if (parsed.ihdr.width !== EXPECTED_WIDTH || parsed.ihdr.height !== EXPECTED_HEIGHT) {
        result.errors.push('unexpected_dimensions');
      }
      if (parsed.ihdr.bitDepth !== 8) result.errors.push('bit_depth_must_be_8');
      if (record.alphaRequired && parsed.ihdr.colorType !== 6) result.errors.push('transparent_assets_must_be_rgba');
      if (record.alphaRequired && pixels.inspectable) {
        if (pixels.maxAlpha === 0) result.errors.push('fully_transparent_placeholder');
        if (pixels.minAlpha === 255) result.errors.push('alpha_channel_has_no_transparency');
        if (pixels.transparentRgbLeakRatio > 0.02) {
          result.warnings.push('high_hidden_rgb_ratio_in_transparent_pixels');
        }
      }
      if (!record.alphaRequired && pixels.inspectable && pixels.maxAlpha === 0) {
        result.errors.push('background_is_fully_transparent');
      }
    } catch (error) {
      result.errors.push(error.message);
    }

    result.ok = result.errors.length === 0;
    for (const code of result.errors) errors.push({ id: record.id, code, path: relativePath });
    for (const code of result.warnings) warnings.push({ id: record.id, code, path: relativePath });
    results.push(result);
  }

  const variation = validateDynamicGroups(results);
  errors.push(...variation.errors);
  warnings.push(...variation.warnings);

  const manualReview = validateManualReview();
  if (requireManualReview && !manualReview.ok) {
    errors.push({ code: 'manual_visual_review_not_approved', details: manualReview.errors });
  } else if (!manualReview.ok) {
    warnings.push({ code: 'manual_visual_review_pending', details: manualReview.errors });
  }

  const requiredExpected = inventory.filter(record => !record.optional).length;
  const requiredPresent = results.filter(result => !result.optional && result.ok).length;
  const optionalPresent = results.filter(result => result.optional && result.ok).length;
  const ok = errors.length === 0 && (!strictWarnings || warnings.length === 0);
  const report = {
    schemaVersion: 1,
    generatedAt: new Date().toISOString(),
    project: 'liv_jimeng_dream_journey',
    version: 'v8',
    status: ok ? 'READY_FOR_RUNTIME_QA' : 'BLOCKED_BY_ASSET_PRODUCTION',
    ok,
    expected: {
      total: inventory.length,
      required: requiredExpected,
      optional: inventory.length - requiredExpected
    },
    presentAndValid: {
      required: requiredPresent,
      optional: optionalPresent
    },
    errors,
    warnings,
    manualReview,
    assets: results
  };

  ensureDirectory(reportPath);
  fs.writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`, 'utf8');

  console.log(`V8 asset validation: ${report.status}`);
  console.log(`Required valid: ${requiredPresent}/${requiredExpected}`);
  console.log(`Optional valid: ${optionalPresent}/${inventory.length - requiredExpected}`);
  console.log(`Errors: ${errors.length}; warnings: ${warnings.length}`);
  console.log(`Report: ${path.relative(PROJECT_ROOT, reportPath)}`);

  process.exitCode = ok ? 0 : 2;
}

main();

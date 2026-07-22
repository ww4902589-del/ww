#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');

const PROJECT_ROOT = path.resolve(__dirname, '..');
const ASSET_ROOT = path.join(PROJECT_ROOT, 'assets', 'frame-v8');
const MANIFEST_ROOT = path.join(ASSET_ROOT, 'manifests');
const CHECKLIST_PATH = path.join(MANIFEST_ROOT, 'asset-checklist-v8.json');
const LAYOUT_PATH = path.join(MANIFEST_ROOT, 'sprite-layout-v8.json');
const OUTPUT_ROOT = path.join(ASSET_ROOT, 'diagnostic');
const WIDTH = 1920;
const HEIGHT = 1080;

const args = new Set(process.argv.slice(2));
const cleanFirst = args.has('--clean');

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

function crc32(buffer) {
  let crc = 0xffffffff;
  for (const byte of buffer) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function pngChunk(type, data) {
  const typeBuffer = Buffer.from(type, 'ascii');
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length, 0);
  const checksum = Buffer.alloc(4);
  checksum.writeUInt32BE(crc32(Buffer.concat([typeBuffer, data])), 0);
  return Buffer.concat([length, typeBuffer, data, checksum]);
}

function encodePng(width, height, pixels) {
  const signature = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8;
  ihdr[9] = 6;
  ihdr[10] = 0;
  ihdr[11] = 0;
  ihdr[12] = 0;

  const stride = width * 4;
  const raw = Buffer.alloc((stride + 1) * height);
  for (let y = 0; y < height; y += 1) {
    const rawOffset = y * (stride + 1);
    raw[rawOffset] = 0;
    pixels.copy(raw, rawOffset + 1, y * stride, (y + 1) * stride);
  }

  return Buffer.concat([
    signature,
    pngChunk('IHDR', ihdr),
    pngChunk('IDAT', zlib.deflateSync(raw, { level: 9 })),
    pngChunk('IEND', Buffer.alloc(0))
  ]);
}

function createSurface() {
  return Buffer.alloc(WIDTH * HEIGHT * 4);
}

function blendPixel(surface, x, y, color) {
  const px = Math.round(x);
  const py = Math.round(y);
  if (px < 0 || py < 0 || px >= WIDTH || py >= HEIGHT) return;
  const offset = (py * WIDTH + px) * 4;
  const srcAlpha = clamp(color[3] / 255, 0, 1);
  const dstAlpha = surface[offset + 3] / 255;
  const outAlpha = srcAlpha + dstAlpha * (1 - srcAlpha);
  if (outAlpha <= 0) return;
  for (let channel = 0; channel < 3; channel += 1) {
    surface[offset + channel] = Math.round(
      (color[channel] * srcAlpha + surface[offset + channel] * dstAlpha * (1 - srcAlpha)) / outAlpha
    );
  }
  surface[offset + 3] = Math.round(outAlpha * 255);
}

function fillRect(surface, x, y, width, height, color) {
  const left = clamp(Math.floor(x), 0, WIDTH);
  const top = clamp(Math.floor(y), 0, HEIGHT);
  const right = clamp(Math.ceil(x + width), 0, WIDTH);
  const bottom = clamp(Math.ceil(y + height), 0, HEIGHT);
  for (let py = top; py < bottom; py += 1) {
    for (let px = left; px < right; px += 1) blendPixel(surface, px, py, color);
  }
}

function fillEllipse(surface, cx, cy, rx, ry, color) {
  const left = clamp(Math.floor(cx - rx), 0, WIDTH - 1);
  const right = clamp(Math.ceil(cx + rx), 0, WIDTH - 1);
  const top = clamp(Math.floor(cy - ry), 0, HEIGHT - 1);
  const bottom = clamp(Math.ceil(cy + ry), 0, HEIGHT - 1);
  for (let y = top; y <= bottom; y += 1) {
    const ny = (y - cy) / Math.max(1, ry);
    for (let x = left; x <= right; x += 1) {
      const nx = (x - cx) / Math.max(1, rx);
      if (nx * nx + ny * ny <= 1) blendPixel(surface, x, y, color);
    }
  }
}

function strokeLine(surface, x1, y1, x2, y2, thickness, color) {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const steps = Math.max(1, Math.ceil(Math.hypot(dx, dy)));
  for (let step = 0; step <= steps; step += 1) {
    const t = step / steps;
    fillEllipse(surface, x1 + dx * t, y1 + dy * t, thickness, thickness, color);
  }
}

function fillOpaqueBackground(surface) {
  for (let y = 0; y < HEIGHT; y += 1) {
    const ty = y / (HEIGHT - 1);
    for (let x = 0; x < WIDTH; x += 1) {
      const tx = x / (WIDTH - 1);
      const offset = (y * WIDTH + x) * 4;
      surface[offset] = Math.round(5 + tx * 9 + ty * 3);
      surface[offset + 1] = Math.round(13 + tx * 18 + ty * 8);
      surface[offset + 2] = Math.round(32 + tx * 34 + ty * 20);
      surface[offset + 3] = 255;
    }
  }
  fillRect(surface, 0, 0, WIDTH, 14, [255, 70, 180, 255]);
  fillRect(surface, 0, HEIGHT - 14, WIDTH, 14, [80, 220, 255, 255]);
  fillRect(surface, 0, 0, 14, HEIGHT, [255, 70, 180, 255]);
  fillRect(surface, WIDTH - 14, 0, 14, HEIGHT, [80, 220, 255, 255]);
}

function indexFromId(id) {
  const match = id.match(/_(\d{2})$/);
  return match ? Number(match[1]) : 0;
}

function seededRandom(seedText) {
  let seed = 2166136261;
  for (const character of seedText) {
    seed ^= character.charCodeAt(0);
    seed = Math.imul(seed, 16777619);
  }
  return function random() {
    seed += 0x6d2b79f5;
    let value = seed;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
}

function layerBox(layout, layerName) {
  return layout.layers[layerName]?.content_bbox || [0, 0, WIDTH, HEIGHT];
}

function drawStarfield(surface, id, density, alpha) {
  const random = seededRandom(id);
  for (let index = 0; index < density; index += 1) {
    const x = random() * WIDTH;
    const y = random() * HEIGHT;
    const radius = 1 + random() * 3;
    const warmth = random();
    fillEllipse(surface, x, y, radius, radius, [
      Math.round(150 + warmth * 90),
      Math.round(195 + warmth * 55),
      255,
      alpha
    ]);
  }
}

function drawDiagnosticAsset(asset, layout) {
  const surface = createSurface();
  const id = asset.id;
  const frame = indexFromId(id);
  const phase = frame / 8;

  if (id === 'background_clean') {
    fillOpaqueBackground(surface);
    return surface;
  }

  if (id === 'body_base') {
    const [x, y, width, height] = layerBox(layout, 'body_base');
    fillEllipse(surface, x + width * 0.5, y + height * 0.5, width * 0.34, height * 0.47, [70, 190, 255, 130]);
    strokeLine(surface, x + width * 0.2, y + height * 0.88, x + width * 0.8, y + height * 0.88, 8, [255, 90, 190, 220]);
  } else if (id === 'crystal_base') {
    fillRect(surface, 1510, 95, 240, 650, [95, 180, 255, 55]);
    strokeLine(surface, 1630, 90, 1630, 760, 6, [210, 245, 255, 210]);
  } else if (id === 'title_base') {
    fillRect(surface, 1330, 850, 450, 92, [120, 210, 255, 68]);
    strokeLine(surface, 1330, 960, 1780, 960, 3, [245, 250, 255, 210]);
  } else if (id.startsWith('head_')) {
    const [x, y, width, height] = layerBox(layout, 'head');
    const turn = (frame - 4) * 7;
    fillEllipse(surface, x + width * 0.5 + turn, y + height * 0.48, width * 0.34, height * 0.42, [255, 160, 210, 180]);
    strokeLine(surface, x + width * 0.5, y + height * 0.88, x + width * 0.5 + turn * 0.3, y + height, 8, [255, 235, 250, 220]);
  } else if (id.startsWith('eye_')) {
    const [x, y, width, height] = layerBox(layout, 'eyes');
    const openness = id === 'eye_closed' ? 0.06 : id.includes('half') ? 0.35 : 0.72;
    fillEllipse(surface, x + width * 0.33, y + height * 0.5, width * 0.16, height * openness * 0.25, [255, 255, 255, 235]);
    fillEllipse(surface, x + width * 0.68, y + height * 0.5, width * 0.16, height * openness * 0.25, [255, 255, 255, 235]);
    strokeLine(surface, x + width * 0.16, y + height * 0.5, x + width * 0.84, y + height * 0.5, 2, [80, 220, 255, 240]);
  } else if (id.startsWith('arm_')) {
    const anchors = layout.layers.arm.joint_anchors;
    const shoulder = anchors.shoulder;
    const elbow = [anchors.elbow[0] + frame * 8, anchors.elbow[1] - frame * 6];
    const wrist = [anchors.wrist[0] + frame * 12, anchors.wrist[1] - frame * 8];
    strokeLine(surface, shoulder[0], shoulder[1], elbow[0], elbow[1], 18, [255, 180, 220, 190]);
    strokeLine(surface, elbow[0], elbow[1], wrist[0], wrist[1], 15, [120, 220, 255, 210]);
    fillEllipse(surface, wrist[0], wrist[1], 28, 20, [255, 245, 250, 230]);
  } else if (id.startsWith('hair_back_') || id.startsWith('hair_front_')) {
    const isBack = id.startsWith('hair_back_');
    const [x, y, width, height] = layerBox(layout, isBack ? 'hair_back' : 'hair_front');
    const sway = Math.sin(phase * Math.PI * 2) * 45;
    strokeLine(surface, x + width * 0.55, y + height * 0.15, x + width * 0.48 + sway, y + height * 0.9, isBack ? 24 : 15, [190, 230, 255, isBack ? 120 : 185]);
  } else if (id.startsWith('skirt_') || id.startsWith('ribbon_')) {
    const ribbon = id.startsWith('ribbon_');
    const [x, y, width, height] = layerBox(layout, ribbon ? 'ribbon' : 'skirt');
    const sway = (frame - 1.5) * 28;
    strokeLine(surface, x + width * 0.45, y + height * 0.1, x + width * 0.55 + sway, y + height * 0.9, ribbon ? 12 : 32, [150, 175, 255, ribbon ? 210 : 125]);
  } else if (id.startsWith('starfield_far_')) {
    drawStarfield(surface, id, 85, 145);
  } else if (id.startsWith('starfield_mid_')) {
    drawStarfield(surface, id, 55, 185);
  } else if (id.startsWith('starfield_near_')) {
    drawStarfield(surface, id, 28, 225);
  } else if (id.startsWith('crystal_glow_')) {
    const pulse = 100 + frame * 32;
    fillEllipse(surface, 1630, 425, 130 + frame * 14, 310 + frame * 10, [110, 215, 255, clamp(pulse, 0, 230)]);
  } else if (id.startsWith('frag_')) {
    const regionName = id.replace(/_\d{2}$/, '');
    const region = layout.fragment_regions[regionName];
    const [x, y, width, height] = region.content_bbox;
    const shift = (frame - 1) * 30;
    fillRect(surface, x + width * 0.18 + shift, y + height * 0.22, width * 0.28, height * 0.22, [120, 220, 255, 185]);
    strokeLine(surface, x + width * 0.12, y + height * 0.75, x + width * 0.82 + shift, y + height * 0.3, 5, [255, 120, 220, 210]);
  } else if (id.startsWith('memory_')) {
    const random = seededRandom(id);
    const x = 1080 + random() * 650;
    const y = 160 + random() * 700;
    fillRect(surface, x, y, 110 + random() * 100, 70 + random() * 80, [215, 235, 255, 150]);
    strokeLine(surface, x, y, x + 150, y + 90, 3, [255, 110, 210, 210]);
  } else {
    fillRect(surface, 40, 40, 160, 90, [255, 40, 170, 220]);
  }

  return surface;
}

function removeDirectory(directory) {
  if (fs.existsSync(directory)) fs.rmSync(directory, { recursive: true, force: true });
}

function main() {
  const checklist = readJson(CHECKLIST_PATH);
  const layout = readJson(LAYOUT_PATH);
  const assets = checklist.assets || [];

  if (assets.length !== 79) {
    throw new Error(`Diagnostic generation requires the exact 79-entry checklist; received ${assets.length}.`);
  }
  if (cleanFirst) removeDirectory(OUTPUT_ROOT);
  fs.mkdirSync(OUTPUT_ROOT, { recursive: true });

  const generated = [];
  for (const asset of assets) {
    const outputPath = path.join(OUTPUT_ROOT, asset.path);
    fs.mkdirSync(path.dirname(outputPath), { recursive: true });
    const pixels = drawDiagnosticAsset(asset, layout);
    const png = encodePng(WIDTH, HEIGHT, pixels);
    fs.writeFileSync(outputPath, png);
    generated.push({ id: asset.id, path: path.relative(PROJECT_ROOT, outputPath).replaceAll(path.sep, '/'), bytes: png.length });
    process.stdout.write(`generated ${asset.id}\n`);
  }

  const report = {
    version: 'v8',
    mode: 'DIAGNOSTIC_ONLY',
    production_usable: false,
    generated_at: new Date().toISOString(),
    canvas: `${WIDTH}x${HEIGHT}`,
    expected_asset_count: 79,
    generated_asset_count: generated.length,
    source_checklist: path.relative(PROJECT_ROOT, CHECKLIST_PATH).replaceAll(path.sep, '/'),
    output_root: path.relative(PROJECT_ROOT, OUTPUT_ROOT).replaceAll(path.sep, '/'),
    assets: generated
  };
  fs.writeFileSync(path.join(OUTPUT_ROOT, 'diagnostic-build-v8.json'), `${JSON.stringify(report, null, 2)}\n`);
  process.stdout.write(`DIAGNOSTIC_READY ${generated.length}/79\n`);
}

try {
  main();
} catch (error) {
  console.error(`DIAGNOSTIC_GENERATION_FAILED: ${error.stack || error.message}`);
  process.exitCode = 1;
}

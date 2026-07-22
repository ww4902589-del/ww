#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');
const MANIFEST_DIR = path.join(ROOT, 'assets', 'frame-v8', 'manifests');
const DIAGNOSTIC_ROOT = path.join(ROOT, 'assets', 'frame-v8', 'diagnostic');
const OUTPUT_DIR = path.join(ROOT, 'qa');
const OUTPUT = path.join(OUTPUT_DIR, 'diagnostic-report-v8.json');

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

function exists(file) {
  return fs.existsSync(file);
}

function run() {
  const checklist = readJson(path.join(MANIFEST_DIR, 'asset-checklist-v8.json'));
  const frameMap = readJson(path.join(MANIFEST_DIR, 'frame-map-v8.json'));
  const required = checklist.assets.filter(asset => asset.required !== false);

  const missing = [];
  const present = [];

  for (const asset of required) {
    const file = path.join(DIAGNOSTIC_ROOT, asset.path);
    if (exists(file)) present.push(asset.id);
    else missing.push({ id: asset.id, path: asset.path });
  }

  const frameCount = frameMap.frame_count || 288;
  const fps = frameMap.fps || 24;
  const seconds = frameMap.loop_seconds || 12;

  const report = {
    version: 'v8',
    generated_at: new Date().toISOString(),
    mode: 'DIAGNOSTIC_QA',
    source: 'assets/frame-v8/diagnostic',
    timeline: {
      frames: frameCount,
      fps,
      seconds,
      valid: frameCount === fps * seconds
    },
    assets: {
      expected_required: required.length,
      present_required: present.length,
      missing_required: missing.length,
      ready: missing.length === 0,
      missing
    },
    production_ready: false,
    notes: [
      'This report validates diagnostic pipeline structure, not artistic quality.',
      'Diagnostic assets must never replace final character assets.',
      'Production readiness requires genuine artwork and Wallpaper Engine visual QA.'
    ]
  };

  fs.mkdirSync(OUTPUT_DIR, { recursive: true });
  fs.writeFileSync(OUTPUT, `${JSON.stringify(report, null, 2)}\n`);
  console.log(JSON.stringify(report, null, 2));
}

try {
  run();
} catch (error) {
  console.error(error.stack || error.message);
  process.exitCode = 1;
}

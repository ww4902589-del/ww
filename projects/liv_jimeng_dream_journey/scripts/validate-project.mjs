import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const projectRoot = resolve(fileURLToPath(new URL('..', import.meta.url)));
const requiredScripts = [
  'src/00-core.js',
  'src/10-character.js',
  'src/20-effects.js',
  'src/30-title.js',
  'src/40-runtime.js'
];
const requiredAssets = new Map([
  ['assets/master-keyframe.png', 'd782f341a7b6801b78d902686e063135e7a48a3c10700b5ef97c3ea66221d268'],
  ['assets/motion-blink.png', '92e547d91598f86bf6e7121bae58ae91722c59f0903537fa0ff531cbfa61ce8d'],
  ['assets/motion-reach.png', 'ab2e5300b44720c5ea34d512c53036cf6388b457633e268a5fe8977b1a323010']
]);

const indexHtml = await readFile(resolve(projectRoot, 'index.html'), 'utf8');
for (const scriptPath of requiredScripts) {
  if (!indexHtml.includes(scriptPath)) throw new Error(`Missing script reference: ${scriptPath}`);
}

JSON.parse(await readFile(resolve(projectRoot, 'project.json'), 'utf8'));

for (const [assetPath, expectedHash] of requiredAssets) {
  const content = await readFile(resolve(projectRoot, assetPath));
  const actualHash = createHash('sha256').update(content).digest('hex');
  if (actualHash !== expectedHash) throw new Error(`Checksum mismatch: ${assetPath}`);
}

const characterSource = await readFile(resolve(projectRoot, 'src/10-character.js'), 'utf8');
if (/drawImage\(blinkArt,\s*r\.x/.test(characterSource)) {
  throw new Error('Full-frame blink overlay is prohibited.');
}

console.log('Project structure, JSON, script order, asset checksums and blink guard passed.');


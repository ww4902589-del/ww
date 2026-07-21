'use strict';

const LOOP_SECONDS = 12;
const TAU = Math.PI * 2;
const canvas = document.getElementById('wallpaper');
const ctx = canvas.getContext('2d', { alpha: false, desynchronized: true });
const titleNode = document.getElementById('title');
const art = new Image();
const motionArt = new Image();
const blinkArt = new Image();
art.src = 'assets/master-keyframe.png';
motionArt.src = 'assets/motion-reach.png';
blinkArt.src = 'assets/motion-blink.png';

const config = {
  quality: 2,
  motion: 0.6,
  characterAction: 0.78,
  audio: 0.5,
  parallax: 0.45,
  interactionPower: 0.82,
  particles: true,
  interaction: true,
  showTitle: true,
  ambient: false,
  quietMode: true,
  idleSeconds: 60,
  theme: 0,
  scale: 1.08,
  offsetX: 0
};

const state = {
  width: 0,
  height: 0,
  dpr: 1,
  start: performance.now(),
  lastFrame: performance.now(),
  mouse: { x: 0.5, y: 0.5, sx: 0.5, sy: 0.5 },
  lastInput: performance.now(),
  quiet: false,
  audioRaw: 0,
  audioSmooth: 0,
  clickReadyAt: 0,
  responseUntil: 0,
  ripples: [],
  geo: null,
  themeMix: { r: 0.15, g: 0.35, b: 0.65 },
  audioContext: null,
  nextAmbient: 0,
  particles: []
};

function seededRandom(seed) {
  let x = Math.sin(seed * 12.9898) * 43758.5453;
  return x - Math.floor(x);
}

function buildParticles() {
  const count = config.quality === 1 ? 44 : config.quality === 3 ? 128 : 82;
  state.particles = Array.from({ length: count }, (_, i) => ({
    x: seededRandom(i + 1),
    y: seededRandom(i + 101),
    radius: 0.45 + seededRandom(i + 201) * 1.65,
    phase: seededRandom(i + 301) * TAU,
    depth: 0.25 + seededRandom(i + 401) * 0.75,
    hue: seededRandom(i + 501)
  }));
}

function resize() {
  state.dpr = Math.min(window.devicePixelRatio || 1, config.quality === 3 ? 2 : 1.5);
  state.width = Math.max(1, window.innerWidth);
  state.height = Math.max(1, window.innerHeight);
  canvas.width = Math.round(state.width * state.dpr);
  canvas.height = Math.round(state.height * state.dpr);
  ctx.setTransform(state.dpr, 0, 0, state.dpr, 0, 0);
}

function clamp(v, min, max) { return Math.max(min, Math.min(max, v)); }
function mix(a, b, t) { return a + (b - a) * t; }
function ease(t) { return t * t * (3 - 2 * t); }

function coverRect(extraScale, shiftX, shiftY) {
  const iw = art.naturalWidth || 1672;
  const ih = art.naturalHeight || 941;
  const scale = Math.max(state.width / iw, state.height / ih) * extraScale;
  const w = iw * scale;
  const h = ih * scale;
  return {
    x: (state.width - w) * 0.5 + shiftX + state.width * config.offsetX,
    y: (state.height - h) * 0.5 + shiftY,
    w, h
  };
}

function imagePoint(r, x, y) {
  return { x: r.x + r.w * x, y: r.y + r.h * y };
}

function cyclicDistance(a, b) {
  const d = Math.abs(a - b);
  return Math.min(d, 1 - d);
}

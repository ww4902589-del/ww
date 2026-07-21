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

const motionTuning = {
  loopDurationSeconds: 12,
  character: {
    head: {
      rotationDeg: 3.2,
      shiftXPx: 6,
      shiftYPx: 3,
      turnStartSeconds: 2,
      turnPeakSeconds: 5.2,
      returnStartSeconds: 8.2
    },
    breathing: { cycleSeconds: 6, shoulderLiftPx: 3, torsoScaleAmount: 0.0025 },
    arm: { reachDistancePx: 9, rotationDeg: 2, wristRotationDeg: 3.5 },
    hair: { frontDriftPx: 3, midDriftPx: 8, backDriftPx: 14 },
    cloth: { skirtDriftPx: 12, ribbonDriftPx: 20 }
  },
  blink: { centersSeconds: [2.8, 8.1], durationSeconds: 0.16 },
  background: {
    starfieldRotationDeg: 6,
    crystalGlowAmount: 0.16,
    fragmentNearAmplitudePx: 18,
    fragmentMidAmplitudePx: 10,
    fragmentFarAmplitudePx: 5
  }
};

const layerRegions = {
  head: { x: 0.365, y: 0.145, width: 0.105, height: 0.235, pivotX: 0.43, pivotY: 0.32 },
  eyes: { x: 0.397, y: 0.252, width: 0.057, height: 0.035 },
  torso: { x: 0.315, y: 0.33, width: 0.17, height: 0.31, pivotX: 0.40, pivotY: 0.48 },
  arm: { x: 0.47, y: 0.255, width: 0.19, height: 0.19, pivotX: 0.50, pivotY: 0.37 },
  hairBack: { x: 0.035, y: 0.105, width: 0.34, height: 0.61, pivotX: 0.34, pivotY: 0.38 },
  cloth: { x: 0.18, y: 0.50, width: 0.29, height: 0.46, pivotX: 0.38, pivotY: 0.60 }
};

const fragmentDefinitions = [
  { id: 'fragment-top-left', x: 0.245, y: 0.035, width: 0.075, height: 0.145, depth: 0.55, phaseOffset: 0.08 },
  { id: 'fragment-top-mid', x: 0.475, y: 0.005, width: 0.070, height: 0.145, depth: 0.35, phaseOffset: 0.31 },
  { id: 'memory-mid', x: 0.545, y: 0.385, width: 0.095, height: 0.135, depth: 0.72, phaseOffset: 0.57 },
  { id: 'memory-lower', x: 0.625, y: 0.46, width: 0.105, height: 0.17, depth: 0.9, phaseOffset: 0.79 }
];

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
function degreesToRadians(degrees) { return degrees * Math.PI / 180; }

function calculateHeadTurnAmount(elapsedSeconds) {
  const timeSeconds = ((elapsedSeconds % motionTuning.loopDurationSeconds) + motionTuning.loopDurationSeconds) % motionTuning.loopDurationSeconds;
  const head = motionTuning.character.head;
  if (timeSeconds < head.turnStartSeconds) return 0;
  if (timeSeconds < head.turnPeakSeconds) return ease((timeSeconds - head.turnStartSeconds) / (head.turnPeakSeconds - head.turnStartSeconds));
  if (timeSeconds < head.returnStartSeconds) return 1;
  return 1 - ease((timeSeconds - head.returnStartSeconds) / (motionTuning.loopDurationSeconds - head.returnStartSeconds));
}

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


'use strict';

function frame(now) {
  if (!art.complete || !art.naturalWidth) {
    requestAnimationFrame(frame);
    return;
  }
  const elapsed = (now - state.start) / 1000;
  const loopPhase = (elapsed % LOOP_SECONDS) / LOOP_SECONDS;
  state.quiet = config.quietMode && (now - state.lastInput) / 1000 >= config.idleSeconds;
  const quietFactor = state.quiet ? 0.18 : 1;
  state.mouse.sx += (state.mouse.x - state.mouse.sx) * 0.045;
  state.mouse.sy += (state.mouse.y - state.mouse.sy) * 0.045;
  state.audioSmooth += (state.audioRaw - state.audioSmooth) * 0.08;

  ctx.setTransform(state.dpr, 0, 0, state.dpr, 0, 0);
  ctx.clearRect(0, 0, state.width, state.height);
  const imageRect = drawImageLayer(loopPhase, elapsed, quietFactor);
  drawBackgroundMotion(imageRect, loopPhase, quietFactor);
  drawCharacterInteraction(imageRect, loopPhase, quietFactor);
  drawAtmosphere(loopPhase, quietFactor);
  drawParticles(loopPhase, quietFactor);
  drawCursorLight(quietFactor);
  drawRipples(now);
  drawArtisticTitle(loopPhase, quietFactor);
  maybeAmbient(now, quietFactor);
  requestAnimationFrame(frame);
}

function pointerPosition(event) {
  const rect = canvas.getBoundingClientRect();
  return {
    x: clamp((event.clientX - rect.left) / rect.width, 0, 1),
    y: clamp((event.clientY - rect.top) / rect.height, 0, 1)
  };
}

window.addEventListener('pointermove', event => {
  const p = pointerPosition(event);
  state.mouse.x = p.x;
  state.mouse.y = p.y;
  state.lastInput = performance.now();
});

window.addEventListener('pointerdown', event => {
  state.lastInput = performance.now();
  if (!config.interaction) return;
  const now = performance.now();
  if (now < state.clickReadyAt) return;
  state.clickReadyAt = now + 1000;
  const p = pointerPosition(event);
  const x = (p.x >= 0.53 ? p.x : 0.79) * state.width;
  const y = (p.x >= 0.53 ? p.y : 0.47) * state.height;
  const duration = mix(3200, 1800, clamp(config.motion, 0, 1));
  state.ripples.push({ x, y, start: now, duration });
  state.responseUntil = now + 1650;
  const baseRect = coverRect(config.scale, 0, 0);
  const fingertip = imagePoint(baseRect, 0.645, 0.315);
  state.ripples.push({ x: fingertip.x, y: fingertip.y, start: now, duration: duration * 0.72 });
  playCrystalTone(false);
});

function ensureAudioContext() {
  if (!state.audioContext) state.audioContext = new (window.AudioContext || window.webkitAudioContext)();
  if (state.audioContext.state === 'suspended') state.audioContext.resume();
  return state.audioContext;
}

function playCrystalTone(ambient) {
  if (ambient && !config.ambient) return;
  const ac = ensureAudioContext();
  const now = ac.currentTime;
  const osc = ac.createOscillator();
  const gain = ac.createGain();
  const pan = ac.createStereoPanner ? ac.createStereoPanner() : null;
  osc.type = 'sine';
  osc.frequency.setValueAtTime(ambient ? 720 + Math.random() * 260 : 980, now);
  osc.frequency.exponentialRampToValueAtTime(ambient ? 440 : 560, now + (ambient ? 1.4 : 0.72));
  gain.gain.setValueAtTime(0.0001, now);
  gain.gain.exponentialRampToValueAtTime(ambient ? 0.012 : 0.035, now + 0.025);
  gain.gain.exponentialRampToValueAtTime(0.0001, now + (ambient ? 1.5 : 0.82));
  if (pan) {
    pan.pan.value = (Math.random() - 0.5) * 0.8;
    osc.connect(gain).connect(pan).connect(ac.destination);
  } else osc.connect(gain).connect(ac.destination);
  osc.start(now);
  osc.stop(now + (ambient ? 1.55 : 0.86));
}

function maybeAmbient(now, quietFactor) {
  if (!config.ambient || now < state.nextAmbient) return;
  const audioBoost = state.audioSmooth * config.audio;
  const min = state.quiet ? 12000 : mix(8000, 3500, audioBoost);
  const max = state.quiet ? 20000 : mix(15000, 6500, audioBoost);
  state.nextAmbient = now + min + Math.random() * (max - min);
  playCrystalTone(true);
}

function wallpaperAudioListener(audioArray) {
  if (!audioArray || !audioArray.length) return;
  const n = Math.min(16, audioArray.length);
  let total = 0;
  for (let i = 0; i < n; i++) total += Math.max(0, audioArray[i]);
  state.audioRaw = clamp(total / n, 0, 1.25);
}

if (typeof window.wallpaperRegisterAudioListener === 'function') {
  window.wallpaperRegisterAudioListener(wallpaperAudioListener);
}

window.wallpaperPropertyListener = {
  applyUserProperties(properties) {
    if (properties.quality) config.quality = Number(properties.quality.value);
    if (properties.motion) config.motion = Number(properties.motion.value) / 100;
    if (properties.characteraction) config.characterAction = Number(properties.characteraction.value) / 100;
    if (properties.audioresponse) config.audio = Number(properties.audioresponse.value) / 100;
    if (properties.parallax) config.parallax = Number(properties.parallax.value) / 100;
    if (properties.interactionstrength) config.interactionPower = Number(properties.interactionstrength.value) / 100;
    if (properties.particles) config.particles = Boolean(properties.particles.value);
    if (properties.interaction) config.interaction = Boolean(properties.interaction.value);
    if (properties.showtitle) config.showTitle = Boolean(properties.showtitle.value);
    if (properties.ambient) config.ambient = Boolean(properties.ambient.value);
    if (properties.quietmode) config.quietMode = Boolean(properties.quietmode.value);
    if (properties.idletime) config.idleSeconds = Number(properties.idletime.value);
    if (properties.theme) config.theme = Number(properties.theme.value);
    if (properties.imagescale) config.scale = Number(properties.imagescale.value) / 100;
    if (properties.offsetx) config.offsetX = Number(properties.offsetx.value) / 100;
    titleNode.classList.toggle('hidden', !config.showTitle);
    buildParticles();
    resize();
  }
};

window.addEventListener('resize', resize);
window.addEventListener('pointerdown', ensureAudioContext, { once: true });

let animationStarted = false;
function startWallpaperAnimation() {
  if (animationStarted) return;
  animationStarted = true;
  titleNode.classList.add('canvas-rendered');
  resize();
  buildParticles();
  requestAnimationFrame(frame);
}

art.addEventListener('load', startWallpaperAnimation);
if (art.complete && art.naturalWidth) startWallpaperAnimation();


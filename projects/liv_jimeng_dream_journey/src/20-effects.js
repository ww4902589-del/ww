'use strict';

function drawStarfieldRotation(r, loopPhase, quietFactor) {
  if (config.motion <= 0) return;
  const motionAmount = config.motion * quietFactor;
  const oscillation = Math.sin(loopPhase * TAU);
  const centerX = r.x + r.w * 0.81;
  const centerY = r.y + r.h * 0.45;
  ctx.save();
  ctx.beginPath();
  ctx.ellipse(centerX, centerY, r.w * 0.19, r.h * 0.40, -0.12, 0, TAU);
  ctx.clip();
  ctx.globalCompositeOperation = 'screen';
  ctx.globalAlpha = 0.10 * motionAmount;
  ctx.translate(centerX, centerY);
  ctx.rotate(degreesToRadians(motionTuning.background.starfieldRotationDeg * oscillation));
  ctx.translate(-centerX, -centerY);
  ctx.drawImage(art, r.x, r.y, r.w, r.h);
  ctx.restore();
}

function drawFloatingFragments(r, loopPhase, quietFactor) {
  if (config.quality === 1 || config.motion <= 0) return;
  const motionAmount = config.motion * quietFactor;
  for (const fragment of fragmentDefinitions) {
    const phase = loopPhase * TAU + fragment.phaseOffset * TAU;
    const amplitudePx = mix(
      motionTuning.background.fragmentFarAmplitudePx,
      motionTuning.background.fragmentNearAmplitudePx,
      fragment.depth
    ) * motionAmount;
    const centerX = r.x + r.w * (fragment.x + fragment.width * 0.5);
    const centerY = r.y + r.h * (fragment.y + fragment.height * 0.5);
    ctx.save();
    ctx.beginPath();
    ctx.ellipse(centerX, centerY, r.w * fragment.width * 0.52, r.h * fragment.height * 0.52, 0, 0, TAU);
    ctx.clip();
    ctx.globalAlpha = 0.20 + fragment.depth * 0.14;
    ctx.translate(centerX + Math.sin(phase) * amplitudePx, centerY + Math.cos(phase) * amplitudePx * 0.55);
    ctx.rotate(degreesToRadians(Math.sin(phase) * mix(1.2, 4.2, fragment.depth)));
    ctx.translate(-centerX, -centerY);
    ctx.drawImage(art, r.x, r.y, r.w, r.h);
    ctx.restore();
  }
}

function drawCrystalFlow(r, loopPhase, quietFactor) {
  const motionAmount = config.motion * quietFactor;
  const crystalX = r.x + r.w * 0.805;
  const crystalY = r.y + r.h * 0.44;
  const flowOffset = Math.sin(loopPhase * TAU - 0.35);
  const audioBoost = state.audioSmooth * config.audio;
  ctx.save();
  ctx.beginPath();
  ctx.ellipse(crystalX, crystalY, r.w * 0.105, r.h * 0.32, -0.08, 0, TAU);
  ctx.clip();
  ctx.globalCompositeOperation = 'screen';
  ctx.globalAlpha = (0.055 + audioBoost * 0.07) * motionAmount;
  ctx.drawImage(art, r.x + flowOffset * 4, r.y - flowOffset * 7, r.w, r.h);
  const flowGradient = ctx.createLinearGradient(crystalX, crystalY + r.h * 0.22, crystalX, crystalY - r.h * 0.22);
  flowGradient.addColorStop(0, 'rgba(120,190,255,0)');
  flowGradient.addColorStop(0.48, `rgba(225,242,255,${0.08 + motionTuning.background.crystalGlowAmount * 0.35})`);
  flowGradient.addColorStop(1, 'rgba(180,150,255,0)');
  ctx.fillStyle = flowGradient;
  ctx.fillRect(crystalX - r.w * 0.10, crystalY - r.h * 0.30 + flowOffset * 9, r.w * 0.20, r.h * 0.60);
  ctx.restore();
}

function drawBackgroundMotion(r, loopPhase, quietFactor) {
  drawStarfieldRotation(r, loopPhase, quietFactor);
  drawFloatingFragments(r, loopPhase, quietFactor);
  drawCrystalFlow(r, loopPhase, quietFactor);
}

function drawAtmosphere(loopPhase, quietFactor) {
  const theme = resolveTheme();
  ctx.save();
  ctx.globalCompositeOperation = 'screen';
  ctx.globalAlpha = 0.09;
  ctx.fillStyle = `rgb(${theme.r},${theme.g},${theme.b})`;
  ctx.fillRect(0, 0, state.width, state.height);

  const audio = state.audioSmooth * config.audio * quietFactor;
  const crystalPulse = 0.58 + 0.18 * Math.sin(loopPhase * TAU - 0.2) + audio * 0.55;
  radialGlow(state.width * 0.81, state.height * 0.47, state.height * 0.25,
    'rgba(115,195,255,ALPHA)', clamp(0.10 * crystalPulse, 0, 0.18));
  radialGlow(state.width * 0.785, state.height * 0.48, state.height * 0.10,
    'rgba(255,205,238,ALPHA)', clamp(0.09 * crystalPulse, 0, 0.16));
  radialGlow(state.width * 0.37, state.height * 0.30, state.height * 0.20,
    'rgba(210,235,255,ALPHA)', 0.025 + audio * 0.025);
  ctx.restore();
}

function drawParticles(loopPhase, quietFactor) {
  if (!config.particles) return;
  const audio = state.audioSmooth * config.audio;
  const crystalX = state.width * 0.81;
  const crystalY = state.height * 0.47;
  const faceX = state.width * 0.425;
  const faceY = state.height * 0.267;
  ctx.save();
  ctx.globalCompositeOperation = 'screen';
  for (const p of state.particles) {
    const angle = loopPhase * TAU + p.phase;
    const baseX = p.x * state.width;
    const baseY = p.y * state.height;
    const attractionAmount = (0.5 - 0.5 * Math.cos(loopPhase * TAU)) * 0.035 * config.motion * quietFactor;
    const x = (baseX + Math.sin(angle) * 13 * p.depth * config.motion * quietFactor + (crystalX - baseX) * attractionAmount + state.width) % state.width;
    const y = (baseY + Math.cos(angle) * 7 * p.depth * config.motion * quietFactor + (crystalY - baseY) * attractionAmount + state.height) % state.height;
    const faceDistance = Math.hypot(x - faceX, y - faceY);
    const faceSuppression = clamp((faceDistance - state.height * 0.07) / (state.height * 0.10), 0.18, 1);
    const twinkle = 0.35 + 0.65 * (0.5 + 0.5 * Math.sin(angle * 2.0));
    const a = clamp((0.12 + twinkle * 0.42 + audio * 0.36) * p.depth * faceSuppression, 0, 0.92);
    ctx.fillStyle = p.hue > 0.72 ? `rgba(255,210,245,${a})` : `rgba(170,220,255,${a})`;
    ctx.beginPath();
    ctx.arc(x, y, p.radius * (1 + audio * 0.7), 0, TAU);
    ctx.fill();
  }
  ctx.restore();
}

function drawCursorLight(quietFactor) {
  if (!config.interaction || state.quiet) return;
  ctx.save();
  ctx.globalCompositeOperation = 'screen';
  radialGlow(state.mouse.sx * state.width, state.mouse.sy * state.height, state.height * 0.115,
    'rgba(175,220,255,ALPHA)', 0.055 * config.motion * quietFactor);
  ctx.restore();
}

function drawRipples(now) {
  state.ripples = state.ripples.filter(r => now - r.start < r.duration);
  ctx.save();
  ctx.globalCompositeOperation = 'screen';
  for (const r of state.ripples) {
    const t = clamp((now - r.start) / r.duration, 0, 1);
    const e = ease(t);
    const radius = mix(7, Math.min(state.width, state.height) * 0.105, e);
    const alpha = (1 - e) * 0.72;
    ctx.strokeStyle = `rgba(170,225,255,${alpha})`;
    ctx.lineWidth = 1.4 + (1 - e) * 1.4;
    ctx.beginPath();
    ctx.arc(r.x, r.y, radius, 0, TAU);
    ctx.stroke();
    ctx.strokeStyle = `rgba(255,210,238,${alpha * 0.48})`;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(r.x, r.y, radius * 0.58, 0, TAU);
    ctx.stroke();
  }
  ctx.restore();
}


'use strict';

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
  ctx.save();
  ctx.globalCompositeOperation = 'screen';
  for (const p of state.particles) {
    const angle = loopPhase * TAU + p.phase;
    const x = (p.x * state.width + Math.sin(angle) * 13 * p.depth * config.motion * quietFactor + state.width) % state.width;
    const y = (p.y * state.height + Math.cos(angle) * 7 * p.depth * config.motion * quietFactor + state.height) % state.height;
    const twinkle = 0.35 + 0.65 * (0.5 + 0.5 * Math.sin(angle * 2.0));
    const a = clamp((0.12 + twinkle * 0.42 + audio * 0.36) * p.depth, 0, 0.92);
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

'use strict';

function applyCharacterClip(r) {
  ctx.beginPath();
  // Keep the face locked to the base frame. Animate only hair tips, shoulders/torso,
  // reaching arm and skirt so cross-frame interpolation cannot create facial ghosting.
  ctx.ellipse(r.x + r.w * 0.195, r.y + r.h * 0.47, r.w * 0.19, r.h * 0.40, -0.08, 0, TAU);
  ctx.ellipse(r.x + r.w * 0.415, r.y + r.h * 0.46, r.w * 0.145, r.h * 0.235, -0.08, 0, TAU);
  ctx.moveTo(r.x + r.w * 0.47, r.y + r.h * 0.31);
  ctx.lineTo(r.x + r.w * 0.65, r.y + r.h * 0.25);
  ctx.lineTo(r.x + r.w * 0.69, r.y + r.h * 0.35);
  ctx.lineTo(r.x + r.w * 0.50, r.y + r.h * 0.42);
  ctx.closePath();
  ctx.ellipse(r.x + r.w * 0.34, r.y + r.h * 0.71, r.w * 0.225, r.h * 0.24, 0.05, 0, TAU);
  ctx.clip();
}

function drawCharacterAction(r, loopPhase, quietFactor) {
  if (!motionArt.complete || !motionArt.naturalWidth) return;
  const cycleReach = 0.5 - 0.5 * Math.cos(loopPhase * TAU);
  const cursorNearHand = config.interaction
    ? clamp((state.mouse.sx - 0.38) * 1.8, 0, 1) * clamp(1 - Math.abs(state.mouse.sy - 0.34) * 2.1, 0, 1)
    : 0;
  const clickResponse = performance.now() < state.responseUntil ? 1 : 0;
  const actionMix = clamp(Math.max(cycleReach * 0.82, cursorNearHand, clickResponse) * config.characterAction * quietFactor, 0, 0.95);

  ctx.save();
  applyCharacterClip(r);
  const actionAlpha = actionMix <= 0.08 ? 0 : ease(clamp((actionMix - 0.08) / 0.22, 0, 1));
  ctx.globalAlpha = actionAlpha;
  ctx.drawImage(motionArt, r.x, r.y, r.w, r.h);
  ctx.restore();

  if (blinkArt.complete && blinkArt.naturalWidth) {
    const blinkA = Math.max(
      clamp(1 - cyclicDistance(loopPhase, 0.265) / 0.018, 0, 1),
      clamp(1 - cyclicDistance(loopPhase, 0.735) / 0.015, 0, 1)
    );
    if (blinkA > 0) {
      ctx.save();
      const face = imagePoint(r, 0.425, 0.267);
      ctx.beginPath();
      ctx.ellipse(face.x, face.y, r.w * 0.034, r.h * 0.025, -0.08, 0, TAU);
      ctx.clip();
      ctx.globalAlpha = ease(blinkA) * quietFactor;
      ctx.drawImage(blinkArt, r.x, r.y, r.w, r.h);
      ctx.restore();
    }
  }
}

function drawImageLayer(loopPhase, quietFactor) {
  const motion = config.motion * quietFactor;
  const px = (state.mouse.sx - 0.5) * config.parallax * 38 * config.interactionPower * quietFactor;
  const py = (state.mouse.sy - 0.5) * config.parallax * 22 * config.interactionPower * quietFactor;
  const orbitX = Math.sin(loopPhase * TAU) * 3.8 * motion;
  const orbitY = Math.cos(loopPhase * TAU) * 2.4 * motion;
  const breath = 1 + Math.sin(loopPhase * TAU) * 0.0028 * motion;
  const r = coverRect(config.scale * breath, px + orbitX, py + orbitY);
  ctx.drawImage(art, r.x, r.y, r.w, r.h);
  drawCharacterAction(r, loopPhase, quietFactor);

  // Localized crystalline highlights: they pulse but never cover the face or fingers.
  const memoryPulse = 0.035 + (0.04 + state.audioSmooth * 0.12) * (0.5 + 0.5 * Math.sin(loopPhase * TAU - 0.8));
  ctx.save();
  ctx.globalCompositeOperation = 'screen';
  ctx.globalAlpha = memoryPulse * motion;
  const fragmentZones = [
    [0.50, 0.14, 0.14], [0.64, 0.16, 0.14], [0.61, 0.43, 0.13]
  ];
  for (const [x, y, radius] of fragmentZones) {
    ctx.save();
    ctx.beginPath();
    ctx.ellipse(state.width * x, state.height * y, state.width * radius, state.height * radius * 0.7, 0, 0, TAU);
    ctx.clip();
    ctx.drawImage(art, r.x + 1.5 * motion, r.y - 1.0 * motion, r.w, r.h);
    ctx.restore();
  }
  ctx.restore();
  return r;
}

function drawCharacterInteraction(r, loopPhase, quietFactor) {
  if (!config.interaction || state.quiet) return;
  const fingertip = imagePoint(r, 0.645, 0.315);
  const core = imagePoint(r, 0.405, 0.405);
  const crystal = imagePoint(r, 0.825, 0.405);
  const cursor = { x: state.mouse.sx * state.width, y: state.mouse.sy * state.height };
  const influence = clamp((state.mouse.sx - 0.30) * 1.45, 0, 1) * config.interactionPower * quietFactor;
  const guide = {
    x: mix((fingertip.x + crystal.x) * 0.5, cursor.x, influence * 0.42),
    y: mix((fingertip.y + crystal.y) * 0.5, cursor.y, influence * 0.32)
  };
  const pulse = 0.55 + 0.45 * Math.sin(loopPhase * TAU - 0.4);

  ctx.save();
  ctx.globalCompositeOperation = 'screen';
  const beam = ctx.createLinearGradient(fingertip.x, fingertip.y, crystal.x, crystal.y);
  beam.addColorStop(0, `rgba(220,241,255,${0.22 + influence * 0.30})`);
  beam.addColorStop(0.52, `rgba(255,192,241,${0.16 + influence * 0.34})`);
  beam.addColorStop(1, 'rgba(185,220,255,0.08)');
  ctx.strokeStyle = beam;
  ctx.lineWidth = 0.8 + influence * 1.7 + state.audioSmooth * 1.2;
  ctx.shadowColor = 'rgba(175,215,255,0.82)';
  ctx.shadowBlur = 5 + influence * 14;
  ctx.beginPath();
  ctx.moveTo(fingertip.x, fingertip.y);
  ctx.quadraticCurveTo(guide.x, guide.y, crystal.x, crystal.y);
  ctx.stroke();

  radialGlow(fingertip.x, fingertip.y, state.height * (0.035 + influence * 0.018),
    'rgba(255,213,244,ALPHA)', (0.10 + influence * 0.20) * pulse);
  radialGlow(core.x, core.y, state.height * 0.055,
    'rgba(124,205,255,ALPHA)', 0.035 + influence * 0.075);
  ctx.restore();

  titleNode.style.setProperty('--title-shift', `${(state.mouse.sx - 0.5) * 8 * influence}px`);
  titleNode.style.setProperty('--title-glow', String(0.42 + influence * 0.58));
}

function radialGlow(x, y, radius, color, alpha) {
  const g = ctx.createRadialGradient(x, y, 0, x, y, radius);
  g.addColorStop(0, color.replace('ALPHA', String(alpha)));
  g.addColorStop(0.42, color.replace('ALPHA', String(alpha * 0.38)));
  g.addColorStop(1, color.replace('ALPHA', '0'));
  ctx.fillStyle = g;
  ctx.fillRect(x - radius, y - radius, radius * 2, radius * 2);
}

'use strict';

function applyNormalizedEllipseClip(r, region, insetAmount = 0) {
  const centerX = r.x + r.w * (region.x + region.width * 0.5);
  const centerY = r.y + r.h * (region.y + region.height * 0.5);
  ctx.beginPath();
  ctx.ellipse(
    centerX,
    centerY,
    r.w * region.width * (0.5 - insetAmount),
    r.h * region.height * (0.5 - insetAmount),
    -0.06,
    0,
    TAU
  );
  ctx.clip();
}

function drawTransformedImageRegion(image, r, region, transform) {
  const pivotX = r.x + r.w * (region.pivotX ?? (region.x + region.width * 0.5));
  const pivotY = r.y + r.h * (region.pivotY ?? (region.y + region.height * 0.5));
  ctx.save();
  applyNormalizedEllipseClip(r, region, transform.clipInsetAmount || 0);
  ctx.globalAlpha = transform.alpha;
  ctx.translate(pivotX + transform.translateXPx, pivotY + transform.translateYPx);
  ctx.rotate(transform.rotationRadians);
  ctx.scale(transform.scaleX, transform.scaleY);
  ctx.translate(-pivotX, -pivotY);
  ctx.drawImage(image, r.x, r.y, r.w, r.h);
  ctx.restore();
}

function calculateBlinkFrame(elapsedSeconds) {
  const blink = motionTuning.blink;
  let nearestDistanceSeconds = motionTuning.loopDurationSeconds;
  for (const centerSeconds of blink.centersSeconds) {
    const directDistanceSeconds = Math.abs(elapsedSeconds - centerSeconds);
    nearestDistanceSeconds = Math.min(
      nearestDistanceSeconds,
      directDistanceSeconds,
      motionTuning.loopDurationSeconds - directDistanceSeconds
    );
  }
  const halfDurationSeconds = blink.durationSeconds * 0.5;
  if (nearestDistanceSeconds >= halfDurationSeconds) return null;
  const progress = 1 - nearestDistanceSeconds / halfDurationSeconds;
  return progress > 0.58 ? 'closed' : 'half';
}

function drawBlinkLocal(r, elapsedSeconds, quietFactor) {
  const blinkFrame = calculateBlinkFrame(elapsedSeconds);
  if (!blinkFrame || !blinkArt.complete || !blinkArt.naturalWidth) return;
  const eyes = layerRegions.eyes;
  const sourceX = blinkArt.naturalWidth * eyes.x;
  const sourceY = blinkArt.naturalHeight * eyes.y;
  const sourceWidth = blinkArt.naturalWidth * eyes.width;
  const sourceHeight = blinkArt.naturalHeight * eyes.height;
  const destinationX = r.x + r.w * eyes.x;
  const destinationY = r.y + r.h * eyes.y;
  const destinationWidth = r.w * eyes.width;
  const destinationHeight = r.h * eyes.height;
  const verticalScale = blinkFrame === 'closed' ? 1 : 0.72;

  ctx.save();
  applyNormalizedEllipseClip(r, eyes, 0.04);
  ctx.globalAlpha = quietFactor;
  ctx.drawImage(
    blinkArt,
    sourceX,
    sourceY,
    sourceWidth,
    sourceHeight,
    destinationX,
    destinationY + destinationHeight * (1 - verticalScale) * 0.35,
    destinationWidth,
    destinationHeight * verticalScale
  );
  ctx.restore();
}

function drawCharacterAction(r, loopPhase, elapsedSeconds, quietFactor) {
  if (!motionArt.complete || !motionArt.naturalWidth) return;
  const motionAmount = config.characterAction * config.motion * quietFactor;
  const headTurnAmount = calculateHeadTurnAmount(elapsedSeconds) * motionAmount;
  const breathingPhase = elapsedSeconds / motionTuning.character.breathing.cycleSeconds * TAU;
  const breathingAmount = (0.5 - 0.5 * Math.cos(breathingPhase)) * motionAmount;
  const cycleReach = 0.5 - 0.5 * Math.cos(loopPhase * TAU);
  const cursorNearHand = config.interaction
    ? clamp((state.mouse.sx - 0.38) * 1.8, 0, 1) * clamp(1 - Math.abs(state.mouse.sy - 0.34) * 2.1, 0, 1)
    : 0;
  const clickResponse = performance.now() < state.responseUntil ? 1 : 0;
  const actionAmount = clamp(Math.max(cycleReach * 0.82, cursorNearHand, clickResponse) * motionAmount, 0, 1);
  const head = motionTuning.character.head;
  drawTransformedImageRegion(art, r, layerRegions.head, {
    translateXPx: head.shiftXPx * headTurnAmount,
    translateYPx: head.shiftYPx * headTurnAmount - breathingAmount,
    rotationRadians: degreesToRadians(head.rotationDeg * headTurnAmount),
    scaleX: 1,
    scaleY: 1,
    alpha: 0.96
  });
  drawTransformedImageRegion(motionArt, r, layerRegions.torso, {
    translateXPx: 0,
    translateYPx: -motionTuning.character.breathing.shoulderLiftPx * breathingAmount,
    rotationRadians: degreesToRadians(-0.8 * headTurnAmount),
    scaleX: 1 + motionTuning.character.breathing.torsoScaleAmount * breathingAmount,
    scaleY: 1 + motionTuning.character.breathing.torsoScaleAmount * breathingAmount,
    alpha: clamp(0.22 + breathingAmount * 0.35, 0, 0.62)
  });
  drawTransformedImageRegion(motionArt, r, layerRegions.arm, {
    translateXPx: motionTuning.character.arm.reachDistancePx * actionAmount,
    translateYPx: -1.5 * actionAmount,
    rotationRadians: degreesToRadians(motionTuning.character.arm.rotationDeg * actionAmount),
    scaleX: 1,
    scaleY: 1,
    alpha: ease(actionAmount)
  });
  const delayedSway = Math.sin(loopPhase * TAU - 0.52) * motionAmount;
  drawTransformedImageRegion(motionArt, r, layerRegions.hairBack, {
    translateXPx: motionTuning.character.hair.backDriftPx * delayedSway,
    translateYPx: 2 * delayedSway,
    rotationRadians: degreesToRadians(1.6 * delayedSway),
    scaleX: 1,
    scaleY: 1,
    alpha: 0.42
  });
  drawTransformedImageRegion(motionArt, r, layerRegions.cloth, {
    translateXPx: motionTuning.character.cloth.skirtDriftPx * Math.sin(loopPhase * TAU - 0.82) * motionAmount,
    translateYPx: 2.5 * breathingAmount,
    rotationRadians: degreesToRadians(1.5 * Math.sin(loopPhase * TAU - 0.82) * motionAmount),
    scaleX: 1,
    scaleY: 1,
    alpha: 0.38
  });
  drawBlinkLocal(r, elapsedSeconds, quietFactor);
}

function drawImageLayer(loopPhase, elapsedSeconds, quietFactor) {
  const motion = config.motion * quietFactor;
  const px = (state.mouse.sx - 0.5) * config.parallax * 38 * config.interactionPower * quietFactor;
  const py = (state.mouse.sy - 0.5) * config.parallax * 22 * config.interactionPower * quietFactor;
  const orbitX = Math.sin(loopPhase * TAU) * 3.8 * motion;
  const orbitY = Math.cos(loopPhase * TAU) * 2.4 * motion;
  const breath = 1 + Math.sin(loopPhase * TAU) * 0.0028 * motion;
  const r = coverRect(config.scale * breath, px + orbitX, py + orbitY);
  ctx.drawImage(art, r.x, r.y, r.w, r.h);
  drawCharacterAction(r, loopPhase, elapsedSeconds, quietFactor);

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


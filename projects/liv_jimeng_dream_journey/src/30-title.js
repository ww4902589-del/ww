'use strict';

function spacedTextWidth(text, spacing) {
  let width = 0;
  for (let i = 0; i < text.length; i++) width += ctx.measureText(text[i]).width;
  return width + Math.max(0, text.length - 1) * spacing;
}

function fillSpacedText(text, x, y, spacing) {
  let cursor = x;
  for (const char of text) {
    ctx.fillText(char, cursor, y);
    cursor += ctx.measureText(char).width + spacing;
  }
}

function strokeSpacedText(text, x, y, spacing) {
  let cursor = x;
  for (const char of text) {
    ctx.strokeText(char, cursor, y);
    cursor += ctx.measureText(char).width + spacing;
  }
}

function drawArtisticTitle(loopPhase, quietFactor) {
  if (!config.showTitle) return;
  const size = clamp(state.width * 0.020, 20, 38);
  const spacing = size * 0.34;
  const main = '丽芙·霁梦';
  const hover = config.interaction ? clamp((state.mouse.sx - 0.55) * 2.2, 0, 1) : 0;
  const shimmer = 0.5 + 0.5 * Math.sin(loopPhase * TAU - 0.3);

  ctx.save();
  ctx.globalCompositeOperation = 'screen';
  ctx.globalAlpha = (0.82 + hover * 0.16) * quietFactor;
  ctx.font = `400 ${size}px "STKaiti", "KaiTi", "FangSong", serif`;
  ctx.textBaseline = 'middle';
  const mainWidth = spacedTextWidth(main, spacing);
  const centerX = state.width * 0.80 + (state.mouse.sx - 0.5) * 4 * hover;
  const startX = centerX - mainWidth * 0.5;
  const y = state.height * 0.82;
  const grad = ctx.createLinearGradient(startX, y, startX + mainWidth, y);
  grad.addColorStop(0, 'rgba(190,225,255,0.90)');
  grad.addColorStop(0.46, 'rgba(255,255,255,0.98)');
  grad.addColorStop(0.72, 'rgba(221,196,255,0.96)');
  grad.addColorStop(1, 'rgba(155,216,255,0.90)');
  ctx.fillStyle = grad;
  ctx.strokeStyle = 'rgba(235,247,255,0.48)';
  ctx.lineWidth = 0.55;
  ctx.shadowColor = `rgba(130,195,255,${0.38 + hover * 0.34 + shimmer * 0.10})`;
  ctx.shadowBlur = 9 + hover * 12;
  strokeSpacedText(main, startX, y, spacing);
  fillSpacedText(main, startX, y, spacing);

  const diamond = size * 0.22;
  for (const dx of [-mainWidth * 0.5 - size * 0.65, mainWidth * 0.5 + size * 0.65]) {
    ctx.save();
    ctx.translate(centerX + dx, y);
    ctx.rotate(Math.PI * 0.25);
    ctx.fillStyle = 'rgba(190,225,255,0.34)';
    ctx.strokeStyle = 'rgba(235,248,255,0.84)';
    ctx.lineWidth = 0.8;
    ctx.fillRect(-diamond * 0.5, -diamond * 0.5, diamond, diamond);
    ctx.strokeRect(-diamond * 0.5, -diamond * 0.5, diamond, diamond);
    ctx.restore();
  }

  const ruleY = y + size * 0.82;
  const rule = ctx.createLinearGradient(startX - size, ruleY, startX + mainWidth + size, ruleY);
  rule.addColorStop(0, 'rgba(120,190,255,0)');
  rule.addColorStop(0.5, 'rgba(225,242,255,0.88)');
  rule.addColorStop(1, 'rgba(185,150,255,0)');
  ctx.strokeStyle = rule;
  ctx.lineWidth = 0.75;
  ctx.beginPath();
  ctx.moveTo(startX - size, ruleY);
  ctx.lineTo(startX + mainWidth + size, ruleY);
  ctx.stroke();

  const subtitle = 'LIV · DREAMWEAVER';
  const subSize = clamp(size * 0.31, 7, 11);
  const subSpacing = subSize * 0.34;
  ctx.font = `300 ${subSize}px "Segoe UI", sans-serif`;
  ctx.fillStyle = 'rgba(194,220,246,0.72)';
  ctx.shadowBlur = 4;
  const subWidth = spacedTextWidth(subtitle, subSpacing);
  fillSpacedText(subtitle, centerX - subWidth * 0.5, ruleY + size * 0.50, subSpacing);
  ctx.restore();
}

function resolveTheme() {
  let mode = config.theme;
  if (mode === 0) {
    const hour = new Date().getHours() + new Date().getMinutes() / 60;
    if (hour >= 17.5 && hour < 21) mode = 2;
    else if (hour >= 21 || hour < 6.5) mode = 3;
    else mode = 1;
  }
  if (mode === 2) return { r: 86, g: 32, b: 92 };
  if (mode === 3) return { r: 60, g: 36, b: 112 };
  return { r: 26, g: 74, b: 128 };
}

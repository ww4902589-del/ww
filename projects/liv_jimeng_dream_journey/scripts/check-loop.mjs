const tau = Math.PI * 2;
const loopDurationSeconds = 12;

function ease(value) {
  return value * value * (3 - 2 * value);
}

function headTurnAmount(elapsedSeconds) {
  const timeSeconds = ((elapsedSeconds % loopDurationSeconds) + loopDurationSeconds) % loopDurationSeconds;
  if (timeSeconds < 2) return 0;
  if (timeSeconds < 5.2) return ease((timeSeconds - 2) / 3.2);
  if (timeSeconds < 8.2) return 1;
  return 1 - ease((timeSeconds - 8.2) / 3.8);
}

const curves = {
  headTurn: headTurnAmount,
  breathing: seconds => 0.5 - 0.5 * Math.cos(seconds / 6 * tau),
  reach: seconds => 0.5 - 0.5 * Math.cos(seconds / loopDurationSeconds * tau),
  hair: seconds => Math.sin(seconds / loopDurationSeconds * tau - 0.52),
  cloth: seconds => Math.sin(seconds / loopDurationSeconds * tau - 0.82),
  starfield: seconds => Math.sin(seconds / loopDurationSeconds * tau),
  crystal: seconds => Math.sin(seconds / loopDurationSeconds * tau - 0.35)
};

for (const [name, curve] of Object.entries(curves)) {
  const startValue = curve(0);
  const endValue = curve(loopDurationSeconds);
  if (Math.abs(startValue - endValue) > 1e-9) {
    throw new Error(`${name} position does not close: ${startValue} vs ${endValue}`);
  }
  const stepSeconds = 1e-4;
  const startVelocity = (curve(stepSeconds) - curve(0)) / stepSeconds;
  const endVelocity = (curve(loopDurationSeconds) - curve(loopDurationSeconds - stepSeconds)) / stepSeconds;
  if (Math.abs(startVelocity - endVelocity) > 1e-3) {
    throw new Error(`${name} velocity does not close: ${startVelocity} vs ${endVelocity}`);
  }
}

console.log('All primary 12-second motion curves close in position and velocity.');


(function attachLivV8Timeline(global) {
  'use strict';

  function positiveModulo(value, modulus) {
    return ((value % modulus) + modulus) % modulus;
  }

  function runtimeFrame(elapsedSeconds, loopSeconds, fps) {
    const loop = Number(loopSeconds) || 12;
    const rate = Number(fps) || 24;
    return Math.floor(positiveModulo(elapsedSeconds, loop) * rate);
  }

  function resolveSegment(segments, frame) {
    for (const segment of segments || []) {
      const [startFrame, endFrame, frameIndex] = segment;
      if (frame >= startFrame && frame <= endFrame) return frameIndex;
    }
    return 0;
  }

  function pingPongIndex(frame, itemCount, cycleFrames, offsetFrames) {
    if (itemCount <= 1) return 0;
    const offset = Number(offsetFrames) || 0;
    const cycle = Math.max(itemCount * 2 - 2, 2);
    const normalized = positiveModulo(frame + offset, Math.max(1, cycleFrames));
    const phase = Math.floor((normalized / Math.max(1, cycleFrames)) * cycle);
    return phase < itemCount ? phase : cycle - phase;
  }

  function phaseIndex(frame, itemCount, cycleFrames, offsetFrames) {
    const normalized = positiveModulo(frame + (offsetFrames || 0), Math.max(1, cycleFrames));
    return Math.min(itemCount - 1, Math.floor((normalized / Math.max(1, cycleFrames)) * itemCount));
  }

  function resolveBlink(eyesChannel, frame) {
    for (const window of eyesChannel.blink_windows || []) {
      if (frame < window.start_frame || frame > window.end_frame) continue;
      const sequence = eyesChannel.sequence || [eyesChannel.default];
      const span = Math.max(1, window.end_frame - window.start_frame + 1);
      const local = frame - window.start_frame;
      const index = Math.min(sequence.length - 1, Math.floor((local / span) * sequence.length));
      return sequence[index];
    }
    return eyesChannel.default;
  }

  function resolveFrameState(frameMap, frame) {
    const channels = frameMap.channels;
    const hairSequence = channels.hair.sequence;
    const headIndex = resolveSegment(channels.head.segments, frame);
    const armIndex = resolveSegment(channels.arm.segments, frame);
    const hairBackIndex = pingPongIndex(frame, 5, channels.hair.back_cycle_frames, 0);
    const hairFrontIndex = pingPongIndex(frame, 5, channels.hair.back_cycle_frames, channels.hair.front_offset_frames);

    return {
      runtimeFrame: frame,
      head: channels.head.frames[headIndex],
      eyes: resolveBlink(channels.eyes, frame),
      arm: channels.arm.frames[armIndex],
      hairBack: `hair_back_${String(hairSequence[hairBackIndex]).padStart(2, '0')}`,
      hairFront: `hair_front_${String(hairSequence[hairFrontIndex]).padStart(2, '0')}`,
      skirt: `skirt_${String(pingPongIndex(frame, 4, 144, 0)).padStart(2, '0')}`,
      ribbon: `ribbon_${String(pingPongIndex(frame, 4, 96, 10)).padStart(2, '0')}`,
      starfieldFar: `starfield_far_${String(phaseIndex(frame, 4, channels.background.far_cycle_frames, 0)).padStart(2, '0')}`,
      starfieldMid: `starfield_mid_${String(phaseIndex(frame, 4, channels.background.mid_cycle_frames, 24)).padStart(2, '0')}`,
      starfieldNear: `starfield_near_${String(phaseIndex(frame, 4, channels.background.near_cycle_frames, 48)).padStart(2, '0')}`,
      crystalGlow: `crystal_glow_${String(phaseIndex(frame, 4, channels.background.crystal_cycle_frames, 0)).padStart(2, '0')}`,
      fragments: Object.fromEntries(Object.entries(channels.background.fragment_cycles).map(([id, cycleFrames], index) => [
        id,
        `${id}_${String(pingPongIndex(frame, 3, cycleFrames, index * 17)).padStart(2, '0')}`
      ]))
    };
  }

  global.LivV8Timeline = Object.freeze({
    positiveModulo,
    runtimeFrame,
    resolveSegment,
    pingPongIndex,
    phaseIndex,
    resolveFrameState
  });
})(window);

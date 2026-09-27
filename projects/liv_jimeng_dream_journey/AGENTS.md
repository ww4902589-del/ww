# Codex Agent Guardrails

## Scope

Work only inside `projects/liv_jimeng_dream_journey/` unless the handoff explicitly authorizes another path.

## Objective

Upgrade the Wallpaper Engine Web wallpaper from full-frame image blending to controlled local motion. The primary character action is a subtle head turn. Preserve two blinks per 12-second loop while replacing the current ghost-prone blink implementation. Strengthen starfield rotation, layered fragment drift, crystal flow, breathing, hair and cloth motion.

## Required execution order

1. Inspect and validate the current baseline.
2. Restore the binary assets listed in `assets/ASSET_MANIFEST.md`.
3. Create a backup before implementation changes.
4. Implement character motion.
5. Implement and validate local blinking as a separate milestone.
6. Implement background motion.
7. Integrate, profile and validate the seamless loop.
8. Package only after all checks pass.

Do not work on several milestones at the same time.

## Allowed changes

- JavaScript rendering and animation code.
- Local masks, transparent PNG layers and motion metadata under `assets/`.
- `project.json` only when a user-facing tuning property is required.
- Validation scripts and QA records.

## Prohibited changes

- Do not redesign the character, costume, face, headpiece or palette.
- Do not replace the Canvas 2D Web wallpaper with video, WebGL, Three.js, React, Vue or another framework.
- Do not delete mouse interaction, click ripples, audio response, title rendering, quality presets or quiet mode.
- Do not solve motion by repeatedly cross-fading complete character frames.
- Do not use the complete `motion-blink.png` as a transparent face overlay in the final blink implementation.
- Do not add unrelated UI, narrative text, effects or dependencies.
- Do not edit `main` directly.

## Motion limits

- Head rotation: 2–4 degrees.
- Head translation: 4–8 px horizontally and 2–4 px vertically at 1920×1080.
- Shoulder movement: 1–4 px.
- Arm reach: 6–12 px.
- Wrist rotation: 2–5 degrees.
- Front hair drift: 1–4 px.
- Long hair drift: 8–18 px.
- Skirt drift: 8–16 px.
- Ribbon drift: 12–24 px.
- Starfield rotation: 4–8 degrees over 12 seconds.

## Blink acceptance gate

Blink timing remains twice per 12-second loop, around 2.8 s and 8.1 s. Use local open, half-closed and closed eye assets with a soft mask, or an equivalent local eyelid solution. Reject the milestone when any of the following appears:

- double eyelashes;
- iris or eye-socket ghosting;
- grey facial haze;
- face, hair or headpiece flicker;
- nose or mouth movement;
- visible rectangular crop edges.

Do not begin background integration until this gate passes.

## Coding rules

- Keep all tuning values in one configuration object instead of scattering constants.
- Use full descriptive names and suffix units such as `Px`, `Deg` and `Seconds`.
- All looped position, rotation, opacity and velocity curves must match at 0 and 12 seconds.
- Reuse arrays and cached masks; do not allocate large objects every frame.
- Disabled features must skip their rendering work.
- Record every meaningful decision in the pull request or `HANDOFF.md`.

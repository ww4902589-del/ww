# Handoff — 丽芙·霁梦动态壁纸 v7

## Current status

`PLANNING_REPAIRED / IMPLEMENTATION_NOT_COMPLETE / BLOCKED_BY_ASSET_PRODUCTION`

The former v6 Canvas overlay implementation failed visual review: the character action was not perceptible, blinking disappeared or ghosted, and repeated full-frame/local crops increased double images. Do not continue tuning that implementation.

## User-approved route

- Character: **Live2D Cubism**
- Blink: **E1 implemented as Live2D eyelid/eye-open parameters**
- Background: **independent starfield layers + independent fragment particles**
- Runtime: Wallpaper Engine Web wallpaper
- Rendering: one Live2D WebGL canvas for the character, Canvas 2D for background/effects

## Authority order

The next agent must read these files in this exact order:

1. `HANDOFF.md`
2. `projects/liv_jimeng_dream_journey/AGENTS.md`
3. `projects/liv_jimeng_dream_journey/EXECUTION_CONTRACT_V7.md`
4. `projects/liv_jimeng_dream_journey/PROJECT_STATUS_V7.yaml`
5. `projects/liv_jimeng_dream_journey/INPUT_ASSET_CONTRACT_V7.yaml`
6. `projects/liv_jimeng_dream_journey/TASKS.md`
7. `projects/liv_jimeng_dream_journey/QA_EVIDENCE_REQUIREMENTS_V7.md`

When any other project document conflicts with the files above, the authority order above wins.

## Superseded material

The following files describe the failed v6 route or earlier option selection. They are retained only for history and must not drive implementation:

- `projects/liv_jimeng_dream_journey/IMPLEMENTATION_PLAN.md`
- `projects/liv_jimeng_dream_journey/MOTION_DESIGN_OPTIONS.md`
- earlier v6 text in Git history

`UNFINISHED_IMPLEMENTATION_METHODS_AND_TOOLS.md` is supplemental background only. `EXECUTION_CONTRACT_V7.md` is the single executable specification.

## Non-negotiable rules

- Do not render the baked character from `master-keyframe.png` behind a visible Live2D character.
- Do not draw `motion-reach.png` or `motion-blink.png` as runtime character overlays.
- Do not implement E1 as three semi-transparent eye images over an already rendered Live2D eye.
- Do not fabricate `.psd`, `.cmo3`, `.moc3`, `.model3.json`, `.physics3.json`, `.motion3.json`, screenshots, videos, or QA results.
- Do not claim completion when only folders, placeholders, loaders, documentation, or automated syntax checks exist.
- Do not fetch runtime libraries or model assets from an online CDN.
- Do not commit Cubism Core to the public repository until its redistribution terms have been reviewed and explicitly recorded.

## Capability boundary

Codex may implement repository text/code, asset validators, build scripts, Canvas background code, Live2D Web integration, fallback behavior, configuration, and QA automation.

Actual art separation, occlusion repainting, Cubism mesh/deformer/physics work, and Runtime model export require the corresponding GUI tools and a human or tool-enabled operator. If those outputs are absent, record `BLOCKED_BY_ASSET_PRODUCTION`; do not replace them with fake files or the failed Canvas crop method.

## Current allowed work

The next agent may:

1. Audit inputs and record exact checksums/dimensions.
2. Create non-binary scaffolding that clearly fails closed when Live2D assets/Core are missing.
3. Implement and test the independent starfield/fragment engine behind a static fallback, provided the baked character is not duplicated.
4. Prepare validators and QA evidence templates.

The next agent may not mark Stage 1 or Stage 2 complete without genuine PSD/Cubism deliverables and human approval recorded in `PROJECT_STATUS_V7.yaml`.

## Completion definition

The project is complete only when all acceptance items in `EXECUTION_CONTRACT_V7.md` and `QA_EVIDENCE_REQUIREMENTS_V7.md` are supported by committed or explicitly referenced evidence, and Wallpaper Engine visibly runs one Live2D character with two ghost-free blinks, independent starfield layers, independent fragment assets, preserved interactions, and a seamless 12-second loop.

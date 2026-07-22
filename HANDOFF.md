# Handoff — 丽芙·霁梦动态壁纸 v8

## Current status

`V8_SCAFFOLDING_IN_PROGRESS / IMPLEMENTATION_NOT_COMPLETE / BLOCKED_BY_ASSET_PRODUCTION`

The v6 Canvas crop/overlay route failed visual review because it produced picture stretching, weak arm motion, missing blinking and barely visible background motion. The v7 Live2D route is no longer the selected implementation because the required layered PSD and Cubism export package are unavailable.

## User-approved v8 route

- Runtime: Wallpaper Engine Web wallpaper
- Animation method: layered frame-sequence animation using multiple transparent images
- Loop: 12 seconds
- Playback: 24 FPS, 288 runtime frames
- Art target: approximately 80–90 reusable layered frame assets, not 288 full-screen images
- Character motion: head frames, arm frames, eye frames, hair frames, cloth frames
- Background motion: independent starfield phases, crystal glow phases, fragments and memory shards
- Background elements may appear and disappear during the loop

## Visual hard constraints

- No glow noise, dirty light speckles or glitter-like random particles
- No chain textures, chain-link motifs or restraint-like mechanical patterns
- No whole-image stretching as character motion
- No full-frame crop overlays as head or arm animation
- No semi-transparent eye overlay as blinking
- No duplicated baked character behind the frame character
- Natural hands and fingers are mandatory

## Reference images

Repository-embedded previews:

- `projects/liv_jimeng_dream_journey/assets/frame-v8/references/reference_A_standing.svg`
- `projects/liv_jimeng_dream_journey/assets/frame-v8/references/reference_B_sitting.svg`

Reference A is the primary standing-composition reference. Reference B is the supplementary pose, atmosphere and background-reveal reference. The SVG files contain embedded reduced previews for repository review; use the original user-provided high-resolution images for final asset production.

## Authority order

The next agent must read these files in this order:

1. `HANDOFF.md`
2. `projects/liv_jimeng_dream_journey/AGENTS.md`
3. `projects/liv_jimeng_dream_journey/MULTI_IMAGE_SPRITE_ANIMATION_V8.md`
4. `projects/liv_jimeng_dream_journey/V8_ASSET_PROMPTS_AND_CODEX_TASKS.md`
5. `projects/liv_jimeng_dream_journey/TASKS_V8.md`
6. `projects/liv_jimeng_dream_journey/PROJECT_STATUS_V8.yaml`
7. `projects/liv_jimeng_dream_journey/assets/frame-v8/manifests/asset-checklist-v8.json`
8. `projects/liv_jimeng_dream_journey/assets/frame-v8/manifests/frame-timeline-v8.json`
9. `projects/liv_jimeng_dream_journey/assets/frame-v8/manifests/z-order-v8.json`
10. `projects/liv_jimeng_dream_journey/assets/frame-v8/manifests/sprite-layout-v8.json`

When any older v6/v7 document conflicts with the authority order above, the v8 files win.

## Superseded routes

The following are historical only and must not drive implementation:

- v6 full-frame/local crop animation
- v7 Live2D/Cubism implementation contract
- `EXECUTION_CONTRACT_V7.md`
- `PROJECT_STATUS_V7.yaml`
- `INPUT_ASSET_CONTRACT_V7.yaml`
- `QA_EVIDENCE_REQUIREMENTS_V7.md`

Do not delete the historical files; mark them as superseded through this handoff and avoid executing them.

## Allowed Codex work now

Codex may:

1. Create and validate the v8 directory structure.
2. Maintain asset manifests, layout coordinates, z-order and timeline data.
3. Implement the frame-sequence loader and fail-closed asset validation.
4. Implement the background reveal/hide, starfield, fragment and memory-shard scheduler after real assets exist.
5. Preserve mouse interaction, click ripple, audio response, title, quality levels, quiet mode and ultrawide support.

## Asset gate

Actual production PNG frames are not yet present. Missing assets must produce:

`BLOCKED_BY_ASSET_PRODUCTION`

Codex must not resolve the blocker by reusing the failed complete-image crop method, by fabricating transparent assets, or by claiming that placeholder files are finished artwork.

## Completion definition

The project is complete only when:

- real layered frame assets exist;
- the head visibly changes direction;
- the arm sequence visibly changes shoulder, elbow, wrist and fingers;
- two clean blinks occur near 2.8s and 8.1s;
- background movement is perceptible within two seconds;
- background reveal/hide is controlled and clean;
- no glow noise or chain-like pattern is present;
- the 0s and 12s states close seamlessly;
- the preserved Wallpaper Engine interactions pass regression testing.

# Codex Agent Guardrails — v8

## Scope

Work only inside `projects/liv_jimeng_dream_journey/` unless the root `HANDOFF.md` explicitly authorizes another path.

## Primary objective

Implement a real layered frame-sequence wallpaper using multiple transparent image assets. The approved route is no longer Live2D/Cubism.

Required visual systems:

- head frame sequence
- arm frame sequence with visible shoulder/elbow/wrist/finger changes
- local eye frame sequence for two blinks
- hair, skirt and ribbon frame sequences
- independent background starfield phases
- independent crystal glow phases
- independent fragment and memory-shard assets
- preserved Wallpaper Engine interactions

## Canonical route

- Loop duration: 12 seconds
- Playback clock: 24 FPS
- Runtime frames: 288
- Artwork strategy: approximately 80–90 reusable transparent layered images
- Runtime: Wallpaper Engine Web wallpaper
- Renderer: Canvas 2D unless a later approved contract states otherwise

## Visual hard constraints

Forbidden:

- glow noise, dirty light speckles or glitter-like random particles
- chain textures, chain-link motifs or restraint-like mechanical patterns
- whole-image scaling as character breathing or body motion
- full-frame/local crop redraws as head or arm animation
- semi-transparent eye overlays as blinking
- duplicated baked character behind active frame layers
- using `motion-blink.png` or `motion-reach.png` as runtime character overlays
- fabricated transparent assets, fabricated QA evidence or placeholder art reported as finished

Allowed:

- character pose adjustments
- head-direction changes
- arm-pose changes
- controlled background appearance/disappearance
- memory-shard count and content changes
- crystal composition changes

## Required reading order

1. root `HANDOFF.md`
2. this file
3. `MULTI_IMAGE_SPRITE_ANIMATION_V8.md`
4. `V8_ASSET_PROMPTS_AND_CODEX_TASKS.md`
5. `TASKS_V8.md`
6. `PROJECT_STATUS_V8.yaml`
7. files under `assets/frame-v8/manifests/`

Older v6/v7 documents are historical only when they conflict with the v8 authority chain.

## Capability boundary

Code agents may create:

- directories and manifests
- asset validators
- frame loaders and schedulers
- background reveal/hide logic
- interaction integration
- build and QA automation

Code agents may not claim to have created final artwork unless genuine image assets exist and pass validation.

Missing real frame assets must produce:

`BLOCKED_BY_ASSET_PRODUCTION`

## Asset rules

Every production frame asset must:

- be a real PNG file
- use transparent background where specified
- have stable alignment with adjacent frames
- avoid white/black matte edges
- contain only the intended layer/group
- use approved naming from `asset-checklist-v8.json`

Do not create empty PNG placeholders to satisfy the validator.

## Blink gate

Blink centers:

- first: approximately 2.8 seconds
- second: approximately 8.1 seconds

Reject if any appears:

- double eyelashes
- iris ghosting
- grey eye haze
- face patch rectangle
- nose or mouth jump
- front-hair flicker

## Arm-motion gate

The arm sequence must visibly show at least three structural changes:

- shoulder/upper-arm change
- elbow/forearm change
- wrist or finger change

A single translated arm block is not acceptable.

## Background gate

- background motion must be perceptible within two seconds without mouse input
- far, mid and near layers must not all move in lockstep
- reveal/hide transitions must be smooth and intentional
- memory shards and crystal fragments must be independent assets

## Code rules

- Keep timing data in manifests, not scattered constants.
- Use explicit units in names.
- Derive runtime state from `(elapsedSeconds % 12)`; avoid unbounded incremental drift.
- Cache decoded images.
- Fail closed when required assets are missing.
- Preserve mouse parallax, click ripple, audio response, title, quality levels, quiet mode and ultrawide support.
- Record implementation status honestly in `PROJECT_STATUS_V8.yaml`.

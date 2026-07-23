# Codex Image Generation Workflow — No API / ChatGPT Plan

## Authority and purpose

This document defines the approved image-production workflow for the V8 layered
frame-sequence wallpaper when the user has a ChatGPT subscription but does not
have an OpenAI API key or API billing account.

This file is authoritative for image-production execution. When an older file
mentions direct OpenAI API image calls, API keys, `images.generate`, HTTP image
endpoints or paid API automation, this document replaces that part of the older
workflow.

## Non-negotiable environment rule

Do not request, create, store or use an OpenAI API key for this project.

Do not add any of the following as a required production step:

- `OPENAI_API_KEY`
- OpenAI API billing setup
- direct image-generation HTTP requests
- SDK calls such as `openai.images.generate(...)`
- `curl` requests to an image-generation endpoint
- scripts that silently consume API credits

The approved route is:

1. Sign in to Codex with the user's existing ChatGPT account.
2. Use the image-generation or image-editing capability exposed by the current
   ChatGPT/Codex surface, when that capability is available.
3. Save the resulting image files into the repository checkout.
4. Use local, non-API scripts for naming, canvas placement, alpha checks,
   duplication checks and QA.

ChatGPT plan access, Codex usage and image-generation usage can have separate
limits. Work in reviewable batches and stop cleanly when the current surface or
usage allowance cannot generate more images.

## Capability fallback

Codex must first determine whether the current surface exposes an integrated
image-generation or image-editing tool.

### When integrated image generation is available

Use it directly for the requested batch. Do not invoke an external API.

### When integrated image generation is not available

Do not invent files and do not switch to an API workflow. Instead:

1. prepare the exact prompt and reference package for the batch;
2. write it to `qa/pending-image-generation-v8.md`;
3. report `BLOCKED_BY_IMAGE_GENERATION_SURFACE`;
4. ask the user to run that prepared prompt in ChatGPT Images or another
   available ChatGPT image-generation surface;
5. resume only after the genuine generated files are added to the checkout.

This fallback is a workflow handoff, not a failure and not permission to create
placeholder artwork.

## Required reading before generating images

Read in this order:

1. `HANDOFF_FINAL_V8.md`
2. `PROJECT_STATUS_V8.yaml`
3. `AGENTS.md`
4. `V8_ASSET_PROMPTS_AND_CODEX_TASKS.md`
5. `assets/frame-v8/manifests/asset-checklist-v8.json`
6. `assets/frame-v8/manifests/asset-spec-v8.json`
7. `assets/frame-v8/manifests/sprite-layout-v8.json`
8. `assets/frame-v8/manifests/z-order-v8.json`
9. `assets/frame-v8/manifests/frame-map-v8.json`
10. this file

Use the original high-resolution user references when available. Repository SVG
previews are review aids and are not sufficient substitutes for final facial,
costume, hand or hair detail.

## Production strategy

Do not request all 79 assets in one generation operation.

Use this batch order:

1. Base group — 4 assets
2. Head group — 9 assets
3. Eyes group — 5 assets
4. Arm group — 9 assets
5. Hair group — 10 assets
6. Cloth group — 8 assets
7. Background group — 16 assets
8. Fragment group — 12 assets
9. Optional memory group — 6 assets

Complete validation and human review after each batch. Do not continue merely
because the expected file count exists.

## First production batch

The first real-art batch is limited to:

- `background_clean`
- `body_base`
- `crystal_base`
- `title_base`

For this batch:

- preserve the approved 1920×1080 composition;
- preserve the primary character identity and costume;
- keep `background_clean` opaque;
- keep other layers transparent;
- place visible content at the coordinates defined by
  `sprite-layout-v8.json`;
- do not bake head, active arm, front hair, eyes, animated cloth, fragments or
  memory shards into `body_base`;
- generate `title_base` only when the manifest and renderer still require an
  image title layer; do not duplicate the DOM title visually.

Stop after the four assets and present them for review before generating the
head sequence.

## Image-generation instruction template

Use the following structure for every asset or tightly related sequence:

```text
Project: 丽芙·霁梦 V8 layered Wallpaper Engine wallpaper
Asset IDs: <exact IDs from asset-checklist-v8.json>
Canvas: 1920×1080
Format target: PNG
Background: transparent, except background_clean
Reference priority: user-provided high-resolution official/reference images
Identity rule: same face, hairstyle, hair ornaments, costume structure and body proportions
Layer rule: render only the requested layer and its required connection overlap
Alignment rule: keep pivots and anchors fixed to sprite-layout-v8.json
Sequence rule: adjacent frames must change progressively without camera movement
Forbidden: whole-image stretching, crop animation, duplicated full character,
semi-transparent fake blink, anatomy drift, malformed fingers, glow noise,
chain or restraint motifs, white/black matte edges, text not requested by the manifest
Output: save each approved result at the exact checklist path
```

For image editing, use the same approved source image for every frame in a
sequence whenever the tool permits. Prefer editing a shared source over
regenerating the entire character independently for each frame.

## Sequence-specific rules

### Head sequence

- Produce `head_00` through `head_08` as one coherent sequence.
- Keep the neck anchor within the manifest tolerance.
- Change head direction structurally; do not translate a static head patch.
- Preserve face shape, eye spacing, hairline, ornaments and lighting direction.
- Review all nine frames together before approval.

### Eye sequence

- Produce only the eye-area layer required by the manifest.
- Required states: open, half, closed, half-return and open-hold.
- Keep nose, mouth, face contour and front hair unchanged.
- Reject double eyelashes, iris residue, grey haze and rectangular patches.

### Arm sequence

- Produce `arm_00` through `arm_08` as one coherent sequence.
- Show real changes at shoulder, elbow, wrist and fingers.
- Keep the shoulder anchor within tolerance.
- Reject a single arm image shifted or rotated as the complete sequence.
- Review hand and finger anatomy at full resolution.

### Hair and cloth

- Motion must be progressive and loop-compatible.
- Do not change costume design, trim, ornaments or material between frames.
- Preserve connection overlaps so no gaps appear against `body_base`.

### Background, crystal, fragments and memories

- Keep far, mid and near systems independent.
- Do not generate random glitter noise or dirty speckles.
- Do not introduce chain-link, restraint or mechanical-binding motifs.
- Memory shards may appear and disappear, but must remain separate assets.
- Optional memory assets may remain absent without falsely blocking the 73
  required production assets.

## Local processing without API

After generation, Codex may use local tools such as Node.js, Python,
ImageMagick or ffmpeg for deterministic file processing.

Allowed local operations:

- convert to PNG;
- place an image on a transparent 1920×1080 canvas without non-uniform scaling;
- preserve or clean the alpha channel;
- rename files to exact manifest paths;
- compare hashes;
- build contact sheets and loop previews;
- run repository validators.

Forbidden local operations:

- non-uniformly stretch the character to fit anchors;
- synthesize missing character motion by warping a single full image;
- fill missing artwork with empty transparent PNGs;
- copy diagnostic geometric assets into production paths;
- overwrite original generated files without keeping a reviewable source copy.

Suggested local canvas command, when ImageMagick is installed:

```bash
magick input.png -background none -gravity northwest -extent 1920x1080 output.png
```

This command is only valid when the input is already at the intended scale and
position. Use explicit geometry or a reviewed placement script when alignment
is required.

## Validation after every batch

Run:

```bash
node scripts/verify_frame_assets.js
```

During partial production, missing future required groups are expected to keep
the overall project blocked. Review the generated batch's individual validation
records rather than marking the whole project complete.

For a strict release candidate, run the validator's strict/manual-review options
specified by the script and repository QA documentation.

Every batch report must state:

- generated asset IDs;
- exact output paths;
- assets rejected and regenerated;
- unresolved anchor or alpha issues;
- whether human visual review is pending;
- current blocker status.

## Human review gate

Automated checks cannot approve character identity, anatomy or artistic quality.
A human must inspect:

- face and costume identity across all frames;
- head/neck continuity;
- shoulder/elbow/wrist/finger structure;
- blink cleanliness;
- hair and cloth continuity;
- alpha halos at 100% and 200% zoom;
- absence of glow noise and chain-like motifs;
- 0-second to 12-second visual closure.

Do not write `APPROVED` to `manual-review-v8.json` before this inspection.

## Completion language

Allowed before genuine artwork and QA:

- `CODEX_HANDOFF_READY`
- `ENGINE_INTEGRATED_ASSET_BLOCKED`
- `BLOCKED_BY_ASSET_PRODUCTION`
- `BLOCKED_BY_IMAGE_GENERATION_SURFACE`
- `BATCH_GENERATED_PENDING_REVIEW`

Forbidden before genuine artwork and QA:

- `PRODUCTION_READY`
- `FINAL_ASSETS_COMPLETE`
- `WALLPAPER_COMPLETE`
- any statement that diagnostic or placeholder assets are finished artwork

## Codex kickoff instruction

Use this instruction when starting the production session:

```text
Work only in projects/liv_jimeng_dream_journey/.
Do not use or request an OpenAI API key.
Read CODEX_IMAGE_GENERATION_NO_API_V8.md and the listed authority files.
Use the integrated ChatGPT/Codex image-generation capability available in the
current signed-in ChatGPT surface. If no image-generation tool is exposed, write
qa/pending-image-generation-v8.md and report BLOCKED_BY_IMAGE_GENERATION_SURFACE;
do not use an API and do not fabricate assets.

Start with only the four base assets: background_clean, body_base,
crystal_base and title_base. Save approved outputs to the exact paths in
asset-checklist-v8.json, run the local validator, report actual files and
problems, and stop for human review before starting the head sequence.
```

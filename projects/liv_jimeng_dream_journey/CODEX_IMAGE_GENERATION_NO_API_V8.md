# Codex Direct Image Generation — No API Key V8

## 0. Highest-priority correction

For this project, Codex must generate images directly with the **built-in
`imagegen` skill and built-in `image_gen` tool** available in current Codex.

Do **not** select the fallback CLI/API path merely because the requested assets
need transparent backgrounds.

The following message is not an acceptable final blocker:

```text
OPENAI_API_KEY=MISSING
OPENAI_PACKAGE=MISSING
```

Those variables are required only by the fallback script/API workflow. They are
not required by the preferred built-in `image_gen` workflow.

## 1. Required execution mode

Use this order:

1. Update to the latest Codex release.
2. Sign in to Codex with the user's ChatGPT account.
3. Load the automatically installed system `imagegen` skill.
4. Use its built-in `image_gen` tool for every normal image generation or edit.
5. Copy the selected output from Codex's generated-images location into the
   current repository checkout.
6. Perform local alpha conversion, positioning, naming and QA.

Do not ask the user to configure an API key unless the user later explicitly
requests the fallback API/CLI mode.

## 2. Forbidden path

Unless the user explicitly approves API fallback, Codex must not use:

- `scripts/image_gen.py`
- `gpt-image-1.5 --background transparent`
- `OPENAI_API_KEY`
- the OpenAI Node or Python SDK for image generation
- `curl` requests to image endpoints
- custom API runners
- paid API billing as a prerequisite

Do not report the project as blocked merely because these fallback components
are missing.

## 3. Transparent PNG workflow without API

The V8 assets require transparency. The default built-in tool does not expose a
native transparent-background switch, so Codex must use the built-in-first
chroma-key workflow.

For each transparent layer:

1. Generate the requested subject with built-in `image_gen` on a perfectly flat
   chroma-key background.
2. Choose a key color absent from the requested subject:
   - default: `#00ff00`;
   - use `#ff00ff` when green appears in the subject;
   - avoid blue keys for the blue/white/ice palette in this project.
3. Require no shadows, gradients, floor plane, reflections or texture in the
   key background.
4. Copy the generated image into `tmp/imagegen/` or another reviewable workspace
   location.
5. Run the installed Codex helper:

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/.system/imagegen/scripts/remove_chroma_key.py" \
  --input <source-image> \
  --out <final-asset.png> \
  --auto-key border \
  --soft-matte \
  --transparent-threshold 12 \
  --opaque-threshold 220 \
  --despill
```

6. Validate:
   - the PNG has an alpha channel;
   - all four canvas corners are transparent;
   - no key-color fringe remains;
   - hair, fingers, cloth edges and crystal edges remain intact;
   - the visible layer occupies a plausible area;
   - the file is not empty or duplicated.
7. When a thin fringe remains, retry once with `--edge-contract 1`.
8. Do not switch to `gpt-image-1.5` automatically when removal is imperfect.
   First retry the built-in generation with a cleaner key background or a more
   suitable key color.

The opaque `background_clean` asset does not need chroma-key removal.

## 4. Direct-generation instruction to Codex

Codex must interpret the following as a tool-use instruction, not as a request
to write an API script:

```text
Use the system imagegen skill. Invoke the built-in image_gen tool directly.
Do not run scripts/image_gen.py and do not check OPENAI_API_KEY.
Generate the requested image, inspect it, copy it into the repository, then use
local processing and validation scripts.
```

For multiple assets, use one built-in generation call per asset or tightly
related variant. The word "batch" does not authorize the API fallback.

## 5. Required reading order

Before generation, read:

1. `HANDOFF_FINAL_V8.md`
2. `PROJECT_STATUS_V8.yaml`
3. `AGENTS.md`
4. this file
5. `V8_ASSET_PROMPTS_AND_CODEX_TASKS.md`
6. `assets/frame-v8/manifests/asset-checklist-v8.json`
7. `assets/frame-v8/manifests/asset-spec-v8.json`
8. `assets/frame-v8/manifests/sprite-layout-v8.json`
9. `assets/frame-v8/manifests/z-order-v8.json`
10. `assets/frame-v8/manifests/frame-map-v8.json`

Use the user's original high-resolution references when they are available.
Repository SVG previews are review aids only.

## 6. Asset-production order

Do not attempt all 79 assets in one uninterrupted pass.

Use this order:

1. Base group — 4 assets
2. Head group — 9 assets
3. Eyes group — 5 assets
4. Arm group — 9 assets
5. Hair group — 10 assets
6. Cloth group — 8 assets
7. Background group — 16 assets
8. Fragment group — 12 assets
9. Optional memory group — 6 assets

Validate and review each group before proceeding.

## 7. First direct-generation batch

Generate only the four base assets defined by the current manifest:

- `background_clean`
- `body_base`
- `crystal_base`
- `title_base`

Rules:

- output composition: 1920×1080;
- preserve the approved character identity, costume and proportions;
- `background_clean` is opaque;
- the other requested layers use the chroma-key-to-alpha workflow;
- include only the requested layer and the minimum overlap needed for clean
  connections;
- do not bake the head, active arm, front hair, eyes, animated cloth, fragments
  or memory shards into `body_base`;
- place content according to `sprite-layout-v8.json`;
- save the final files to the exact paths in `asset-checklist-v8.json`;
- stop for review after the four assets.

## 8. Standard built-in prompt template

```text
Use case: V8 Wallpaper Engine production asset
Asset ID: <exact checklist ID>
Reference role: use the supplied high-resolution image as the character identity,
costume, color, lighting and composition reference
Canvas target: 1920×1080
Requested layer: render only <layer description> and required connection overlap
Alignment: preserve the pivot, anchor and content region from sprite-layout-v8.json
Identity invariants: same face, eye spacing, hairstyle, ornaments, costume structure,
body proportions and lighting direction
Motion invariants: no camera movement; adjacent frames change progressively
Background for removal: perfectly flat solid <key color>, uniform edge-to-edge,
no shadows, no gradient, no floor, no texture, no reflections
Forbidden: whole-image stretching, crop animation, duplicated full character,
fake transparent blink, anatomy drift, malformed fingers, glow noise, dirty
speckles, chain/restraint motifs, watermark, unrequested text, matte halo
```

For `background_clean`, replace the chroma-key paragraph with the approved opaque
background specification.

## 9. Sequence requirements

### Head

- Generate `head_00` through `head_08` as a coherent edit sequence.
- Keep the neck anchor stable.
- Change direction structurally rather than translating a static patch.
- Preserve face and hair identity across all nine outputs.

### Eyes

- Produce only the eye-area layer.
- Include open, half, closed, half-return and hold states.
- Reject double lashes, iris residue, grey haze and face-patch rectangles.

### Arm

- Generate `arm_00` through `arm_08` as a coherent edit sequence.
- Show visible changes at shoulder, elbow, wrist and fingers.
- Reject a single arm merely shifted or rotated between frames.

### Hair and cloth

- Preserve costume and ornament design.
- Maintain progressive, loop-compatible movement.
- Keep sufficient overlap to avoid seams against the base body.

### Background and effects

- Keep far, mid and near layers independent.
- Do not create glitter noise or dirty light speckles.
- Do not introduce chain-like or restraint motifs.
- Keep fragments and memories as independent assets.

## 10. Local verification

After each generated group, run:

```bash
node scripts/verify_frame_assets.js
```

The project can remain globally blocked while later groups are missing. Report
the current group's actual validation results rather than claiming overall
completion.

Every group report must include:

- generated IDs;
- final repository paths;
- source images retained for review;
- chroma-removal results;
- rejected/regenerated assets;
- unresolved alpha, identity, hand or anchor defects;
- human-review status;
- current blocker status.

## 11. Honest fallback

Only report `BLOCKED_BY_IMAGE_GENERATION_SURFACE` when the current Codex build
actually lacks the built-in `image_gen` tool after all of these checks:

1. Codex is updated to the latest release;
2. Codex has been restarted after the update;
3. the user is signed in with ChatGPT rather than running an obsolete API-key
   session;
4. the system `imagegen` skill is present;
5. Codex was explicitly told to use built-in `image_gen`, not the fallback CLI.

When built-in generation is present, missing `OPENAI_API_KEY` and missing OpenAI
SDK packages are irrelevant and must not block production.

## 12. Exact kickoff prompt

```text
Work only in projects/liv_jimeng_dream_journey/.
Read CODEX_IMAGE_GENERATION_NO_API_V8.md before doing anything else.

Use the system imagegen skill and invoke the built-in image_gen tool directly.
Do not run scripts/image_gen.py. Do not inspect or request OPENAI_API_KEY. Do not
install the OpenAI SDK for image generation.

Generate the four manifest-defined base assets directly with Codex:
background_clean, body_base, crystal_base and title_base.
For transparent layers, generate on a perfectly flat removable chroma-key
background and run the installed remove_chroma_key.py helper locally. Inspect
and validate every result, move approved final PNG files into the exact manifest
paths, run node scripts/verify_frame_assets.js, report actual defects, and stop
for human review before generating the head sequence.

Missing API configuration is not a blocker for the built-in image_gen path.
```

## 13. Completion language

Allowed before final artwork and QA:

- `BATCH_GENERATED_PENDING_REVIEW`
- `ENGINE_INTEGRATED_ASSET_BLOCKED`
- `BLOCKED_BY_ASSET_PRODUCTION`
- `BLOCKED_BY_IMAGE_GENERATION_SURFACE` only under Section 11

Forbidden before genuine artwork and complete QA:

- `PRODUCTION_READY`
- `FINAL_ASSETS_COMPLETE`
- `WALLPAPER_COMPLETE`
- any claim that diagnostic or placeholder images are finished artwork

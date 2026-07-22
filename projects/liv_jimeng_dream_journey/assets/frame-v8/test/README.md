# v8 Test Asset Protocol

Purpose: validate the animation pipeline before final character PNG production.

## Test stage

Use only clearly marked temporary assets.

The test set must never be considered finished artwork.

## Required test files

```text
assets/frame-v8/test/
├── background_clean.png
├── body_base.png
├── head_00.png
├── head_01.png
├── head_02.png
├── eye_open.png
├── eye_closed.png
├── arm_00.png
├── arm_01.png
├── arm_02.png
├── starfield_far_00.png
└── crystal_glow_00.png
```

## Validation goals

1. Canvas starts correctly.
2. Frame timeline advances.
3. Character layer replacement works.
4. Eye layer switches independently.
5. Background layer moves independently.
6. Missing files trigger `BLOCKED_BY_ASSET_PRODUCTION`.
7. No full-image motion fallback is enabled.

## Replacement rule

After real assets are produced, remove this test directory from production manifests and replace entries with final v8 asset paths.

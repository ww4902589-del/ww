# Codex First Run V8

## Mission

Continue the V8 layered frame animation implementation. Do not redesign the
architecture unless a verified technical blocker exists.

## Read first

1. `PROJECT_STATUS_V8.yaml`
2. `assets/frame-v8/manifests/asset-checklist-v8.json`
3. `assets/frame-v8/manifests/frame-map-v8.json`
4. `runtime/v8-runtime.js`
5. `runtime/renderer.js`

## First execution order

```bash
node scripts/generate_diagnostic_assets_v8.js --clean
node scripts/run_v8_diagnostic_qa.js
```

Do not create final artwork from diagnostic assets.

## Required checks

- confirm 79 diagnostic assets are generated
- confirm loader can select diagnostic mode
- open `index.html?v8mode=diagnostic`
- verify 288-frame loop
- verify renderer has no console errors
- verify frame counter changes
- verify layer order
- verify background layers move independently

## If errors appear

Fix in this order:

1. manifest/schema mismatch
2. loader path mismatch
3. runtime initialization mismatch
4. renderer layer mismatch
5. visual tuning

Do not solve runtime errors by:

- stretching the entire image
- replacing layers with a single composite image
- adding transparent duplicate characters
- removing blink layers

## Production asset phase

Only after the diagnostic pipeline passes:

1. import real transparent PNG layers
2. validate using `asset-checklist-v8.json`
3. run `scripts/verify_frame_assets.js`
4. complete manual visual review
5. package Wallpaper Engine release

## Final rejection conditions

Reject the build if:

- head movement is only whole-image movement
- arm movement has no joint structure
- blink creates ghost images
- background is only a moving wallpaper texture
- diagnostic assets are used as final artwork

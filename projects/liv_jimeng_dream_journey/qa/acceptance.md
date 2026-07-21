# v6 acceptance record

## Baseline

- [x] Original v5 source preserved in a separate backup directory.
- [x] Required PNG checksums match `assets/ASSET_MANIFEST.md`.
- [x] Modular JavaScript parses successfully.
- [x] `project.json` parses successfully.
- [x] Baseline visual reference inspected from the v5 source and first local v6 preview.

## Character motion

- [x] Code parameters keep the head turn within 3.2 degrees and 6×3 px.
- [x] Head and shoulder regions use bounded elliptical clipping.
- [x] Arm, hair, cloth and breathing amplitudes remain below the approved maxima.

## Blink gate

- [x] Two blinks occur near 2.8 s and 8.1 s.
- [x] The implementation uses discrete local eye states and no full-frame blink blend.
- [x] The source/destination crop excludes the nose, mouth and headpiece.
- [x] The first available preview showed no rectangular crop edge.

## Background and regression

- [x] Automated curve checks confirm starfield, fragments, crystal flow and particles close at 12 seconds.
- [x] Mouse, click, audio, title, quality presets and quiet-mode code paths remain present.
- [ ] High, medium and low quality modes require final Wallpaper Engine runtime testing.


# EXECUTION CONTRACT V7

This is the single executable specification for Codex.

## Goal

Create a Wallpaper Engine Web wallpaper with:

- real Live2D character motion
- primary action: subtle head turn toward crystal
- ghost-free E1 blink
- independent starfield rotation
- independent fragment drift
- preserved interactions

## Final architecture

Character:

Live2D Cubism -> Cubism SDK for Web -> WebGL canvas

Background:

Canvas 2D -> starfield layers + fragment particles

Effects remain separate.

## Exact motion

12 second loop:

0-2s: idle breathing
2-5.2s: head turns toward crystal
5.2-8.2s: holds gaze
8.2-12s: returns

Blink:

2.8s and 8.1s.

## Asset gates

Before coding character motion, require:

- layered PSD
- restored hidden areas
- eye parts
- Cubism model export

If absent:

STATUS=BLOCKED_BY_ASSET_PRODUCTION

Never fake runtime files.

## Deprecated methods

Do not use:

- frame blending
- transparent complete blink image
- full character overlay
- mother image rotation

## Completion

Only claim completion after:

- real model loads
- blink passes visual gate
- Wallpaper Engine test passes
- 0/12 second loop matches
- evidence exists

# Codex Agent Guardrails — v7

## Scope

Work only inside `projects/liv_jimeng_dream_journey/` unless explicitly authorized.

## Primary objective

Convert the wallpaper from ghost-prone full-frame character blending into a real layered animation pipeline:

- Live2D Cubism character runtime
- E1 ghost-free local eye motion through Live2D parameters
- independent starfield layers
- independent floating fragment particles
- preserved Wallpaper Engine interactions

## Rendering architecture

Allowed:

- Live2D Cubism WebGL canvas for the character
- Canvas 2D for background and effects
- existing Wallpaper Engine Web wallpaper packaging

The previous prohibition on WebGL/Live2D applies only to replacing the project with an unrelated framework. It does NOT prohibit the approved Cubism runtime route.

Forbidden:

- full-frame character crossfades
- baked character behind Live2D
- complete `motion-blink.png` face overlay
- complete `motion-reach.png` character overlay
- video replacement of the interactive wallpaper
- unrelated UI/framework migration

## Required execution order

1. Input asset audit
2. Character asset production gate
3. Cubism model gate
4. Web runtime integration
5. Background starfield and fragments
6. QA and Wallpaper Engine validation

Do not bypass failed gates.

## Capability boundary

Code agents may create code, validators, configuration and runtime integration.

They cannot honestly claim completion of:

- PSD separation
- occlusion repainting
- Cubism mesh/deformer creation
- physics tuning
- runtime model export

without actual artifacts.

Missing art assets must result in `BLOCKED_BY_ASSET_PRODUCTION`.

## Motion limits

Final visible motion:

- Head turn: 2–4 degrees
- Head shift: 4–8 px horizontal
- Hair: delayed secondary motion
- Skirt/ribbon: subtle physics motion
- Starfield: 4–8 degrees over 12 seconds

Model parameter ranges are not final animation keyframes.

## Blink gate

Reject if any appears:

- double eyelashes
- iris ghosting
- grey eye haze
- face crop edge
- nose/mouth movement
- hair/headpiece flicker

Blink centers:

- 2.8 seconds
- 8.1 seconds

## Code rules

- Keep tuning values centralized.
- Use explicit units in names.
- All loops must match at 0 and 12 seconds.
- Cache assets.
- Record decisions in HANDOFF/PR notes.

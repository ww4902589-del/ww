# Performance report

## Static review

- Canvas 2D remains the only renderer; no framework or new runtime dependency was added.
- Images load once and are reused across frames.
- Particle arrays rebuild only after initialization, resize-related setup or quality changes.
- Blink timing avoids per-frame array allocation.
- Low quality skips floating fragment overlays and uses 44 particles.
- Medium quality uses 82 particles; high quality uses 128 particles and a higher DPR cap.
- Disabled particles and interaction paths return before rendering work.
- Quiet mode scales character and background motion to 18%.

## Automated checks

- JavaScript syntax: passed for all five modules.
- Asset SHA-256 validation: passed.
- Full-frame blink overlay guard: passed.
- 12-second position and velocity closure: passed.

## Environment limitations

- Wallpaper Engine was not found in the common local Steam paths, so editor FPS, memory and desktop runtime measurements remain pending.
- `ffmpeg` and ImageMagick are not installed, so an encoded 12-second comparison video was not produced in this environment.
- The first in-app-browser preview rendered without console errors; further localhost reloads were blocked by browser URL policy.



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

## Wallpaper Engine smoke test

- Wallpaper Engine was located under `E:\steam` and loaded the project in a 1920×1080 pop-out window from an ASCII-only QA path.
- The main Wallpaper Engine process and new `webwallpaper64` renderer processes remained responsive after launch; the renderer working set was approximately 122–144 MB during the smoke check.
- No new load or parse error was appended for the ASCII-path run. The QA window was closed after verification.
- Wallpaper Engine's CLI could not parse the original Chinese project path, so the isolated `C:\Users\wyj\Desktop\codex_wallpaper_v6_qa` copy was used for this test.

## Remaining environment limitations

- `ffmpeg` and ImageMagick are not installed, so an encoded 12-second comparison video was not produced.
- The first in-app-browser preview rendered without console errors; further localhost reloads were blocked by browser URL policy.
- Frame-by-frame artistic inspection and multi-minute FPS sampling still require an interactive editor session.


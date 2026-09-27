# Handoff

## Objective

Transfer the supplied `liv_jimeng_dream_journey` Wallpaper Engine Web wallpaper source into the repository and hand the complete v6 modification plan to the local Codex workspace. The implementation must make the character's primary action a subtle head turn, retain two blinks per 12-second loop without the current ghosting, and strengthen starfield rotation, layered fragment drift, crystal flow, breathing, arm, hair and cloth motion while preserving all existing interactions.

## Changes made

- Added the supplied Wallpaper Engine text source under `projects/liv_jimeng_dream_journey/`.
- Preserved the original Canvas 2D baseline behavior while splitting the original monolithic `app.js` into ordered classic-script modules:
  - `src/00-core.js`
  - `src/10-character.js`
  - `src/20-effects.js`
  - `src/30-title.js`
  - `src/40-runtime.js`
- Added `index.html`, `styles.css`, `project.json` and project documentation.
- Added `AGENTS.md` with hard scope, dependency, sequencing, motion-limit and blink acceptance constraints to prevent an implementation agent from expanding or rewriting the task.
- Added `TASKS.md` with the implementation order, motion parameters, 12-second timing, validation gates and fallback strategy.
- Added `IMPLEMENTATION_PLAN.md` containing the full modification scheme and execution steps, including:
  - character head-turn, breathing, arm, finger, hair and cloth motion parameters;
  - ghost-free local blink implementation and acceptance gate;
  - starfield rotation, layered fragment drift, crystal flow and particle behavior;
  - recommended file tree, variable/function naming, software/tool division and sub-agent permissions;
  - staged commits, performance limits, rollback rules and final delivery requirements.
- Added `assets/ASSET_MANIFEST.md` with required binary filenames, sizes and SHA-256 values for the three supplied PNG assets.
- Kept the current baseline blink code visible in `src/10-character.js` solely so the local agent can reproduce and compare the defect before replacing it. The final v6 solution must not use the complete blink image as a face overlay.

## Validation

- Ran `node --check` successfully against the original supplied `app.js` before transfer.
- Parsed the supplied `project.json` successfully as JSON.
- Confirmed the supplied `index.html` contains the required wallpaper canvas and stylesheet/JavaScript references.
- Calculated and recorded SHA-256 values for `master-keyframe.png`, `motion-blink.png` and `motion-reach.png`.
- Fetched `projects/liv_jimeng_dream_journey/index.html` from this branch through the GitHub connector and confirmed that it references all five JavaScript modules in the required order.
- Committed the complete implementation plan to `projects/liv_jimeng_dream_journey/IMPLEMENTATION_PLAN.md`.
- Attempted a public `git clone` for an independent branch check, but the execution container could not resolve `github.com`; no credentials or repository data were exposed.

## Remaining work

- The local Codex workspace should first read `AGENTS.md`, `TASKS.md`, `IMPLEMENTATION_PLAN.md` and this handoff before editing code.
- Restore the binary files listed in `projects/liv_jimeng_dream_journey/assets/ASSET_MANIFEST.md` from the supplied archive. The connector used for this handoff supports repository text writes but did not transfer the approximately 17 MB of PNG assets.
- Execute the plan in the fixed sequence: resource audit → character motion → blink gate → background motion → integration → performance and 12-second loop validation.
- Run syntax and visual checks after restoring the assets.
- Validate the result inside Wallpaper Engine at high, medium and low quality, including interaction regression, blink gate and seamless loop tests.

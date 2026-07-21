# Agent changelog

## 2026-07-21 — Baseline and safeguards

- Created an immutable v5 backup and an independent v6 working copy.
- Restored the three required PNG assets and preview from the verified local source.
- Synced the modular five-script baseline and the approved guardrail documents from PR #1 at commit `25117c813efdf4efba87f531e9477188b678840a`.
- Kept the original monolithic `app.js` only as a baseline reference; `index.html` loads the modular scripts.

## 2026-07-21 — v6 motion implementation

- Added centralized motion tuning and normalized layer/pivot metadata.
- Added deterministic local head, breathing, arm, hair and cloth motion within the approved limits.
- Replaced the former full-frame blink draw with a small source crop limited to the eye region and discrete half/closed states at 2.8 s and 8.1 s.
- Added localized starfield rotation, four phase-offset fragment layers, crystal flow and crystal-directed particles with face-density suppression.
- Fixed cached-image reload startup so the canvas does not remain black when the master image loads before the listener is attached.
- Added structure, checksum, blink-guard and 12-second loop validation scripts.
- Added region/pivot guides and QA records.


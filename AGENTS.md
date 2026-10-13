# Codex handoff workflow

- Treat `HANDOFF.md` as the task contract for handoff branches.
- For any S / ComfyBatch feature, release, status update, or code conflict, read `software/S/docs/00-持续研发任务清单与制作指引.md` before changing code or status.
- Before editing, inspect the complete branch diff against `main`.
- Preserve changes already made by the web agent unless they conflict with the objective.
- Run relevant tests or checks and record exact results in `HANDOFF.md`.
- Keep follow-up commits on the existing handoff branch. Legacy web handoffs use
  `handoff/web-*`; S / ComfyBatch milestones use the `handoff/local-s-*` branch
  required by their delivery guide.
- Never commit credentials, tokens, `.env` files, or generated dependency directories.

- For the desktop S target or style-workbench V3.2 planning, first read `software/S/MAINTENANCE_INDEX.md`. It records the verified desktop version, local source catalogue and the current remote-source gap; use the matching local source before applying old main-branch code.

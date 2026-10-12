# Codex handoff workflow

- Treat `HANDOFF.md` as the task contract for handoff branches.
- For any S / ComfyBatch feature, release, status update, or code conflict, read `software/S/changes/history/repository-docs/00-持续研发任务清单与制作指引.md` before changing code or status.
- Before editing, inspect the complete branch diff against `main`.
- Preserve changes already made by the web agent unless they conflict with the objective.
- Run relevant tests or checks and record exact results in `HANDOFF.md`.
- Keep follow-up commits on the existing handoff branch. Legacy web handoffs use
  `handoff/web-*`; S / ComfyBatch milestones use the `handoff/local-s-*` branch
  required by their delivery guide.
- Never commit credentials, tokens, `.env` files, or generated dependency directories.

- For S maintenance, edit `software/S/code` and record behavior, validation, and release changes in `software/S/changes`. Read `software/S/changes/README.md` before builds or desktop deployment.
- Reuse the canonical local source. Build in a temporary directory; keep one desktop executable and package historical versions as recoverable archives.

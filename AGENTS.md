# Codex handoff workflow

- Treat `HANDOFF.md` as the task contract for handoff branches.
- For any S / ComfyBatch feature, release, status update, or code conflict, read `software/S/docs/00-持续研发任务清单与制作指引.md` before changing code or status.
- Before editing, inspect the complete branch diff against `main`.
- Preserve changes already made by the web agent unless they conflict with the objective.
- Run relevant tests or checks and record exact results in `HANDOFF.md`.
- Keep follow-up commits on the existing `handoff/web-*` branch.
- Never commit credentials, tokens, `.env` files, or generated dependency directories.

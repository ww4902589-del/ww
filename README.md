# Codex GitHub Handoff Bridge

This repository is the shared handoff point between Codex web and the local Codex workspace.

## Web-to-local handoff

1. Codex web creates a branch named `handoff/web-<short-task>` from `main`.
2. It writes or updates the requested code and adds `HANDOFF.md` using the template below.
3. It commits all changes and opens a pull request targeting `main`.
4. The local Codex workspace reads the pull request, continues the work, validates it, and pushes follow-up commits to the same branch.

## Required `HANDOFF.md`

```md
# Handoff

## Objective
Describe the required outcome.

## Changes made
List changed files and important decisions.

## Validation
List commands run and their results.

## Remaining work
List unresolved items. Write `None` when complete.
```

## Prompt for Codex web

```text
Use the connected GitHub repository ww4902589-del/ww as the handoff channel.
Create a branch named handoff/web-<short-task> from main.
Write the supplied code into the repository, add or update HANDOFF.md using the repository template, commit every change, and open a pull request targeting main.
Do not only print code in chat. Return the pull request URL, branch name, and latest commit SHA.
```

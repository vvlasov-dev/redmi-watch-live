---
description: Adversarial review of the current change against this project's failure modes
---

Review the current change before merge. Do NOT self-review — dispatch the
`reviewer` subagent (a hostile optic) so the author isn't checking the author.

Steps:
1. Determine the diff: `git diff` (and `git diff --staged`) if this is a git
   repo; otherwise review the files the user names.
2. Launch the `reviewer` agent on that diff. It reads `docs/CONVENTIONS.md` and
   attacks the change against the project's known failure modes.
3. Relay its findings verbatim (file:line, failure scenario, severity) and its
   SHIP / DO NOT SHIP verdict.
4. If it found blockers, offer to fix them; do not mark the work done until the
   Definition of Done checklist (`docs/CONVENTIONS.md` §7) passes.

$ARGUMENTS

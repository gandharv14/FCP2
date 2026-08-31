# Recovery overlay

This file supersedes fail-closed stops in `SKILL.md` for the remaining
recovery queue. Produce a published Harbor task for the assigned workbook.

## Do not stop

- Do not stop on `unsupported` or `insufficient_evidence`.
- Do not stop on a missing Excel runner or empty formula caches.
- Do not stop after two naturalization attempts.
- Do not treat `FULL_RERUN_BLOCKER: HARD` as a finished result.
- Do not wait for a human confirmation of curation when heuristic curation and
  every existing curation/segmentation gate already pass.

## Required outcome

Publish a complete immutable release and `current-release.json` for this
workbook. Source workbooks, graders, rubrics, answer keys, and acceptance
thresholds stay immutable.

## Recovery ladder

1. Normal path.
2. Smallest task-local pipeline, formula, or validator fix in this worktree.
3. Disclosed deterministic assumption for volatile or external cells.
4. Recurate to a smaller self-contained output cone from the same workbook.
5. Reconstruct protected naturalization sections byte-for-byte; retry only
   editable spans until validation passes.
6. If the same failure repeats, change strategy.

## Record

If any step other than the normal path is used, write `a.md` at the root of the
final outputs bundle. Record the exact blocker, evidence and failing stage,
exact fix, changed files, before/after, commands, and verifier outcome.

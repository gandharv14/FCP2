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
4. Prune only the outputs the verifier implicates: rerun
   `xl_segment.py --prune-failing-outputs` on the existing `curation.toml`
   (never `--recurate`). Keep every output that verifies.
5. Reconstruct protected naturalization sections byte-for-byte; retry only
   editable spans until validation passes.
6. If the same failure repeats, change strategy.

## Output cone rules

The worker's output retention gate checks these before delivery.

- Always run `xl_segment.py` with `--prune-failing-outputs`. On a strict
  verification failure it flips `include = false  # pruned: <codes>` only for
  the outputs whose cone holds the failure, then re-verifies the rest. Exit
  code 3 means the pruned curation is on disk and the re-proof did not fit the
  time budget: rerun the same command. `seg_out/<wb>/last-verification.json`
  holds the blame per output; `seg_out/<wb>/pruning-log.json` the rounds.
- Never flip `include = false` by hand for an output the verifier did not
  implicate. Never keep one output when several verify.
- A hand exclusion of an auto-included output needs a marker on its include
  line: `include = false  # excluded: <code> <evidence>`. Allowed codes:
  `non_financial` (cite `bands.csv` kind/vtype), `check_cell`,
  `duplicate_value <retained band>`, `answer_leak`, `fairness_directed <report
  path>`, `memory` (only after a recorded OOM kill), `budget` (only after a
  recorded timeout or a proof over half the inner budget), `ast_scope` (cite
  the unresolved cells on the dropped sheets), `cone_wide:<code>` (cite
  `last-verification.json`). More than max(3, 25% of auto-included) hand
  exclusions need `memory`, `budget`, `ast_scope`, or `cone_wide`.
- An excluded auto-included output with no marker fails the gate. The worker
  gives one targeted retry on the same ladder step; after that the task still
  ships, flagged `override_after_retry` for the fairness verifier and the
  fleet report.

## Record

If any step other than the normal path is used, write `a.md` at the root of the
final outputs bundle. Record the exact blocker, evidence and failing stage,
exact fix, changed files, before/after, commands, and verifier outcome. When
outputs were pruned or excluded, name every dropped band with its reason code.

The controller keeps its own record of what this recovery did
(`recovery-evidence/v1`, under the lane's `state/recovery-evidence/<wb>/`):
every attempt's outcome, the shell commands and file writes parsed from the
agent streams, and the changed files from the code diff. The fairness
verifier checks `a.md` against it. So: name every changed file the evidence
lists, copy commands from that log rather than from memory (a command that is
not in the log, or a changed file that is not named, fails the
`recovery_record` check), and for the full command history you may point at
the evidence file instead of repeating every line. That file ships beside the
bundle, never inside it; do not copy it into the outputs.

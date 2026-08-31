---
name: harbor-spec-fixer
description: Repairs Harbor variable-source normalization from leak patches, maskability findings, and MCP build diagnostics while preserving fail-closed gates. Use when a fleet workbook is routed off-lane for a spec-only repair.
disable-model-invocation: true
---

# Harbor spec fixer

Repair one workbook off-lane. Do not package, promote, or continue the main lane.
Never weaken a validator, convert an error to a warning, or bypass a failed gate.
Missing or ambiguous evidence is terminal. All routing returns through
`harbor-orchestrator`.

## Shared tracker contract

Use:

```text
runs/harbor-fleet/<batch>/workbooks/<id>.json
```

Every mutation uses
`runs/harbor-fleet/<batch>/workbooks/<id>.json.lock`. Mutate the canonical
`lane_state["harbor-normalization-engineer"]`; never create `lanes`, a display-name
key, or a fixer lane. Allowed states are `pending`, `ready`, `running`,
`repairing`, `passed`, and `terminal`. Use that lock and compare-and-swap:

1. Read a snapshot and its nonnegative integer `revision`.
2. Acquire the shared tracker lock used by every lane.
3. Require the live revision to equal the snapshot revision.
4. Preserve unknown fields and history; apply the mutation.
5. Set `revision` to exactly the prior value plus one, write a sibling temporary
   file, flush and `fsync`, atomically replace the tracker, and `fsync` its parent.
6. On a CAS mismatch, discard the candidate, reload, and recompute the mutation.

Set the handoff's owning lane to `repairing`. Append one `repairs.history` item
with fixer, diagnostic signature, attempt, hashes, result, timestamps, checks, and
reentry gate; increment `repairs.count`. Each lane owns a `current_confidence`
object and append-only `confidence_history`. This fixer reports repair evidence and
does not raise or rewrite either field. On every commit, recompute top-level
`current_confidence` as the worst of all populated lane `current_confidence` values
under `low < medium < high`.

Only the owning lane may improve its `current_confidence`, and only after the
orchestrator authorizes the closed repair, the exact diagnostic is resolved, and
all invalidated owning and downstream gates pass on new hashes. That lane appends
the transition to `confidence_history`. Preserve historical low entries; they
remain evidence but do not permanently block a fully proven repair.

Require an orchestrator assignment, one stable diagnostic signature, and immutable
hashes for the golden, baseline inputs, draft, current generated JSON, and supplied
diagnostics. The repair budget is exactly one source edit attempt per signature.
If `repairs.history` already contains an attempted matching signature, return
terminal without editing.

## Edit boundary

The only source file this skill may edit is:

```text
runs/<id>-variable-sources/normalize_<id>.py
```

Do not edit `normalized.json`, `exclusions.json`, or
`normalization_report.json` directly. They may change only by rerunning the
normalizer. Do not edit the golden, baseline or delivered workbooks, profiles,
maskability reports, leak reports, allowlists, build code, validators, or staged
task. Never hide an unknown leak in an allowlist.

## Diagnose

Consume the assigned `leak_patch.json`, maskability report, validator reports, or
MCP build errors. Preserve the diagnostic verbatim and map it to the responsible
`draft_id`, variable id, workbook reference, and normalizer branch. Do not repair
unrelated rows.

For every proposed `mask` or `extra_cell`:

1. Resolve the reference in the golden workbook.
2. Confirm that it represents the same included variable.
3. Prove the cell is safely blankable: it is an input or display-only duplicate,
   not a formula, graded output, required context, unrelated value, or uncertain
   collateral location.
4. Add a typed mask cell or reasoned extra cell in the normalizer only after all
   checks pass.

If a reference exists only in a generated or delivered workbook and is absent from
the golden, fully exclude the responsible variable. Partial masking is forbidden.
An uncertain, formula-bearing, graded, shared, or unsafe duplicate also forces full
variable exclusion.

Update the normalizer so every affected draft row receives exactly one complete
disposition. Included rows name all resulting variable ids. Excluded rows retain a
specific evidence-based reason. Never silently drop a row or leave
`needs_review`.

## One edit and bounded validation

Make at most one edit transaction to `normalize_<id>.py` for the assigned
signature. Rerun it twice from identical immutable inputs and require byte-identical
`normalized.json`, `exclusions.json`, and `normalization_report.json`.

Validate as far as the invalidated dependency graph permits:

1. one-to-one draft disposition validation
2. `python3 xl_variable_mcp.py validate-spec <normalized.json>`
3. complete maskability review against golden and baseline workbooks
4. offline `leakscan` against the baseline inputs workbook

Do not invoke source-profile validation with `--rehash`, regenerate profiles, build
MCP output, stage, or promote. A specification mutation invalidates source-profile
evidence; profile regeneration and validation belong to the source-profiler lane.

After the one edit, any repeated assigned leak, new leak, nondeterminism, incomplete
disposition, invalid safely-checkable spec, or failed maskability check is terminal.
A spec that was initially non-empty and becomes empty is terminal, not a plain-task
conversion. Do not make a second edit for a changed symptom.

## Orchestrator return

After any spec mutation, return a handoff to `harbor-orchestrator` with
`result: reentry_gate`, `rerun.owning_gate: normalization-phase-1`, and
`rerun.downstream: true`. Explicitly invalidate source-profiler and every later
lane's prior gate evidence and hashes. Preserve every lane's confidence history and
leave live confidence changes to the owning lanes after revalidation. This fixer
never dispatches the normalization engineer or profiler.

Set `terminal` and return to the orchestrator for unsafe or absent golden
references, a repeated/new leak after the edit, prior use of the signature budget,
nondeterminism, incomplete dispositions, failed available validation, or an emptied
previously non-empty spec. Include exact paths, input/output hashes, invalidated
lanes, and confidence reason codes.

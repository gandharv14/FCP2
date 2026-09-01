---
name: harbor-volatile-formula-fixer
description: Deterministically removes provably dead volatile defined names, freezes unreferenced TODAY displays, resolves static INDIRECT references, and replaces a closed OFFSET shape with INDEX. Use during Harbor intake when source health rejects volatile formulas or false external references.
disable-model-invocation: true
---

# Harbor volatile-formula fixer

Run only during orchestrator intake, before a tracker freezes `raw_source`.
Produce a remediated candidate; never overwrite the discovered source.

## Closed contract

- Use `xl_volatile_formula_remediation.py`; do not hand-edit a workbook.
- One plan and one apply per source hash. No model calls or retries.
- Maximum 100,000 actions and five minutes per deterministic command.
- One unresolved action makes the candidate ineligible.
- Preserve every cached cell value and every untouched OOXML member byte.
- Never remove a live defined name.
- Never rewrite arrays, data tables, random functions, dynamic references, or
  formulas outside the tool's closed taxonomy.
- Never weaken source health. Fresh source health on the candidate must not be
  `unsupported` or `insufficient_evidence`.

## Workflow

```bash
RUN="runs/harbor-fleet/<batch>/source-remediation/<id>"
ORIGINAL="<discovered-source>"
CANDIDATE="$RUN/<id>.xlsx"

mkdir -p "$RUN"
python3 xl_volatile_formula_remediation.py plan \
  "$ORIGINAL" -o "$RUN/plan.json"
python3 xl_volatile_formula_remediation.py apply \
  "$ORIGINAL" --plan "$RUN/plan.json" \
  --output "$CANDIDATE" --manifest "$RUN/manifest.json"
python3 xl_volatile_formula_remediation.py verify \
  "$ORIGINAL" "$CANDIDATE" --plan "$RUN/plan.json" \
  --manifest "$RUN/manifest.json"
python3 xl_source_health.py observe \
  "$CANDIDATE" -o "$RUN/source-health-after.json"
```

Require hashes for the original, candidate, plan, manifest, and before/after
health reports. Bind the candidate as tracker `raw_source`; bind the discovered
file as `original_raw_source`. Also bind `source_health_before`,
`source_remediation_plan`, and `source_remediation_manifest`.

## Rewrite taxonomy

The tool alone decides eligibility:

- Expand actual shared members and replace
  `INDIRECT(static_cell & "!" & static_cell)` with a quoted internal A1
  reference when both operands are immutable string constants and every target
  sheet/address exists.
- Replace scalar `OFFSET(reference, rows, blank-column)` with the equivalent
  nonvolatile `INDEX` expression.
- Freeze exact `TODAY()` display cells to their existing cached as-of date only
  when no formula/name/chart/validation consumer references the cell.
- Remove broken or volatile defined names only when a conservative liveness
  graph proves they have no workbook consumer; retain live print names.

## Handoff

On success, return candidate/evidence paths and hashes plus action counts and
the fresh source-health route. On ineligibility or proof failure, return a
sanitized diagnostic; never include formula text or workbook values in notes.

Use:

- `source_policy/volatile_formula_remediation_unresolved` for an ineligible
  closed plan.
- `source_integrity/volatile_formula_remediation_proof_failed` for cache,
  member, hash, or post-health mismatch.
- `retry_budget/volatile_formula_remediation_exhausted` for repeated attempts.

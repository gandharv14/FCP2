---
name: harbor-dialogue-author
description: Converts verified Harbor disclosure records into a template-locked colleagues dialogue with bounded Sol High writing, review, transactional apply, and image smoke. Use as the final staged-bundle mutation before autonomous promotion.
disable-model-invocation: true
---

# Harbor Dialogue Author

Wrap and follow [Additional Assumptions Dialogue](../additional-assumptions-dialogue/SKILL.md).
This skill adds autonomous tracking, off-lane repair routing, mandatory runtime
proof, and a closed terminal policy. The wrapped templates and validators
remain authoritative.

## Lane contract

Work from the `synthetic-data-pipeline` repository root. Require `batch`, `id`,
the staged path, mode, and hashes for `instruction.md`, `task.toml`,
`tests/disclosure.json`, the workbook, and `environment/Dockerfile`.

Read and atomically update:

`runs/harbor-fleet/<batch>/workbooks/<id>.json`

The canonical lane ID is exactly `harbor-dialogue-author`. Every tracker
mutation must:

1. acquire an exclusive OS file lock on the sibling `<id>.json.lock`;
2. read and validate the tracker, its integer `revision`, and its byte hash;
3. preserve unknown fields and history, then update only this lane plus shared
   aggregate fields;
4. set `revision` to exactly the prior value plus one;
5. while still holding the lock, re-read and require the same revision and byte
   hash (compare-and-swap);
6. write a sibling temporary file, flush and `fsync`, atomically replace the
   tracker, `fsync` its parent directory, then release the lock.

Discard a conflicting candidate and rebuild it from the latest tracker; never
overwrite a newer revision. Record round, retry use, gates, evidence paths,
diagnostic IDs, artifact SHA-256 values, review verdicts, image tag, mode, and
`lane_state["harbor-dialogue-author"].current_confidence` with `level` and
`reason_codes`. Append every confidence transition to that lane's
`confidence_history`; entries are immutable and include tracker revision,
timestamp, diagnostic IDs, artifact hashes, level, reasons, and repair
authorization when applicable. This lane may not write another lane's current
value or history.

Current confidence may improve only after an orchestrator-authorized closed
repair resolves the exact recorded diagnostic and the owning gate plus every
invalidated downstream gate re-passes on new hashes. Preserve the prior low
entry in history; it does not permanently cap the repaired lane. Recompute
top-level `current_confidence` as the worst current value across required lanes
(`low` < `medium` < `high`) and union only their current namespaced reasons.
If any required lane lacks a current value, the aggregate is null and cannot
promote. A current low value cannot advance.

On any blocking result, append this handoff under `handoffs[]` and stop:

```json
{
  "schema_version": "harbor-fleet-handoff/v1",
  "batch": "<batch>",
  "workbook_id": "<id>",
  "lane": "harbor-dialogue-author",
  "gate": "<owning-gate>",
  "status": "failed",
  "failure_code": "<stable-code>",
  "summary": "<sanitized-summary>",
  "artifact_hashes": {"<path>": "<sha256>"},
  "current_confidence": "high|medium|low",
  "reasons": ["<reason>"],
  "diagnostics": ["<path>"],
  "next_owner": "harbor-orchestrator",
  "suggested_fixer": "harbor-prose-fixer|harbor-infra-fixer|none",
  "attempts": {"used": 0, "remaining": 0},
  "rerun": {"owning_gate": "<gate>", "downstream": true}
}
```

Do not solicit decisions. The author lane never repairs prose or infrastructure,
edits a validator result, or dispatches a fixer. The orchestrator routes
diagnostics. After a repair, rerun the repaired gate and every later dialogue,
smoke, hygiene, metadata, and promotion gate against new hashes.

## Extract and branch

Set:

```bash
WB="<id>"
TASK="<tracker staged_path>"
S=.cursor/skills/additional-assumptions-dialogue/scripts
RUN="runs/$WB-additional-assumptions"
CLAIMS="$RUN/claims.json"
PACK="$RUN/writer_pack.json"
TEMPLATE="$RUN/draft_template.md"
SLOTS="$RUN/slots.json"
FILLED="$RUN/draft.filled.md"
PRE_APPLY="$RUN/pre-apply-snapshot.json"
POST_APPLY="$RUN/post-apply-verification.json"
```

Extract once from the hash-bound staged bundle:

```bash
uv run --python 3.12 --with openpyxl python "$S/extract_claims.py" \
  --task-dir "$TASK" --out "$RUN"
```

If `claims.json` reports `"empty": true`, perform a strict no-op: launch no
writer, paraphraser, or reviewer; do not touch `instruction.md`, `task.toml`,
the Dockerfile, or environment. Set the allowed lane state to `passed` with
disposition `not_applicable`, record the claims/disclosure hashes, reason
`no_agent_records`, and current confidence `high`, append the transition to
confidence history, then hand back to the orchestrator.

For non-empty records:

```bash
uv run --python 3.12 --with openpyxl python "$S/compose_draft.py" \
  --claims "$CLAIMS" --pack "$PACK" --out "$RUN"
```

The template is created once before round one. It locks claim comments,
speaker lines, first-mention sheet sentences, slot comments, order, and blank
lines. Only `{{SLOT:<id>}}` lines are writable.

## Two-round authoring loop

Hard cap: two rounds.

For each round:

1. A `generalPurpose` writer with model `gpt-5.6-sol-high` receives only
   `writer_pack.json`, `draft_template.md`, and `slots.json`; round two also
   receives round one's clean draft and validated review. It fills a fresh
   copy of the template and writes only `draft.filled.md`.
2. Enforce the template lock:

```bash
uv run --python 3.12 --with openpyxl python "$S/validate_dialogue.py" fill-check \
  --task-dir "$TASK" --template "$TEMPLATE" --draft "$FILLED" \
  --claims "$CLAIMS" --out "$RUN/draft.md" \
  --report "$RUN/fill-check.json"
uv run --python 3.12 --with openpyxl python "$S/validate_dialogue.py" check-draft \
  --task-dir "$TASK" --draft "$RUN/draft.md" --claims "$CLAIMS" \
  --report "$RUN/draft-check.json"
```

3. Preserve the incoming filled and clean drafts. A fresh
   `gpt-5.6-sol-high` paraphraser that did not write the draft rewrites senior
   slot prose only; junior prose and every structural byte remain unchanged.
   Run `fill-check` and `check-draft` again.
4. A fresh `gpt-5.6-sol-high` reviewer that wrote neither version reads only
   `claims.json`, its disclosure body, and the clean draft. It writes
   `review.rN.json`, covering every claim in order. It never edits the draft.
5. Validate the review:

```bash
uv run --python 3.12 --with openpyxl python "$S/validate_dialogue.py" check-review \
  --claims "$CLAIMS" --review "$RUN/review.r1.json" --round 1 \
  --report "$RUN/review-check.r1.json"
```

Use `r2` and `--round 2` in round two. Copy a review to `review.json` only
after `check-review` passes.

The writer and paraphraser must preserve every `must_say` fact, row label,
operator, period locator, and required sheet context; add no modelling fact,
formula, graded value, catalogue ID, cell/range token, or extra alternative.
The dialogue is a new-model conversation between the wrapped allowed cast, not
documentation or a reconstruction narrative.

## Retry and verdict policy

- At most one structural correction is available per round. A `fill-check`
  fault is recorded verbatim and handed to the orchestrator with
  `suggested_fixer: harbor-prose-fixer`. F2 prepares corrective context only;
  on redispatch, this lane invokes the same producer to refill the unchanged
  template once and reruns the validators.
- At most one reviewer-schema correction is available per round, through the
  same handoff path and literal wrapped JSON template. F2 does not execute or
  write the review; this lane invokes the same reviewer and owns validation.
- No lane agent fixes its own prose, template, or review.
- A validated round-one accuracy or naturalness failure advances to round two
  with a fresh template copy and the round-one diagnostics.
- After round two, accuracy failure, missing `must_say`, cast failure, cell or
  range leakage, unclosed alternatives, unknown speakers, or stale coverage is
  terminal. A naturalness-only failure may pass round-two apply exactly as the
  wrapped policy permits.
- There is no third round and no relaxed validator.

## Apply with five-file proof

Before apply, hash all five mutation targets. The first three must exist; the
notes file and mutation marker must be absent. Any pre-existing partial state
is terminal:

```bash
uv run --python 3.12 --with openpyxl python - "$TASK" "$PRE_APPLY" <<'PY'
import hashlib, json, os, sys
from pathlib import Path
task, out = Path(sys.argv[1]), Path(sys.argv[2])
names = [
    "instruction.md",
    "environment/Dockerfile",
    "task.toml",
    "environment/additional-assumptions.md",
    "tests/dialogue-applied.json",
]
files = {}
for name in names:
    path = task / name
    files[name] = {
        "exists": path.is_file(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()
        if path.is_file() else None,
    }
if not all(files[n]["exists"] for n in names[:3]):
    raise SystemExit("required pre-apply file missing")
if any(files[n]["exists"] for n in names[3:]):
    raise SystemExit("stale or mixed dialogue state")
tmp = out.with_name(out.name + ".tmp")
with tmp.open("w", encoding="utf-8") as handle:
    json.dump({"files": files}, handle, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
os.replace(tmp, out)
PY
```

Apply the latest validated review:

```bash
uv run --python 3.12 --with openpyxl python "$S/validate_dialogue.py" apply \
  --task-dir "$TASK" --draft "$RUN/draft.md" --claims "$CLAIMS" \
  --review "$RUN/review.json" --round 1 \
  --report "$RUN/apply.json"
```

Use `--round 2` for round two. Apply must atomically create
`environment/additional-assumptions.md`, add its exact Dockerfile `COPY`,
replace the disclosure section with required pointers, refresh the naturalizer
instruction hash, and write `tests/dialogue-applied.json`. If apply exits
nonzero, hash all five paths again. A sole Docker/runtime infrastructure fault
may be handed to the orchestrator with
`suggested_fixer: harbor-infra-fixer` only when every path exactly matches the
pre-apply snapshot. Any changed, interrupted, partial, or mixed state is
terminal and quarantined.

Immediately verify all five paths against the pre-apply snapshot and write
their post-apply hashes:

```bash
uv run --python 3.12 --with openpyxl python - \
  "$TASK" "$PRE_APPLY" "$POST_APPLY" <<'PY'
import hashlib, json, os, sys
from pathlib import Path
task, before_path, out = map(Path, sys.argv[1:])
before = json.loads(before_path.read_text(encoding="utf-8"))["files"]
files = {}
for name in before:
    path = task / name
    files[name] = {
        "exists": path.is_file(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()
        if path.is_file() else None,
    }
if not all(value["exists"] for value in files.values()):
    raise SystemExit("post-apply file set incomplete")
for name in ("instruction.md", "environment/Dockerfile", "task.toml"):
    if files[name]["sha256"] == before[name]["sha256"]:
        raise SystemExit("required file was not mutated: " + name)
for name in ("environment/additional-assumptions.md",
             "tests/dialogue-applied.json"):
    if before[name]["exists"] or not files[name]["exists"]:
        raise SystemExit("new apply artifact state invalid: " + name)
tmp = out.with_name(out.name + ".tmp")
with tmp.open("w", encoding="utf-8") as handle:
    json.dump({"before": before, "after": files}, handle, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
os.replace(tmp, out)
PY
```

An interruption, missing post-apply hash, or mixed five-file state is
terminal: set current confidence to `low`, quarantine the staged lane, retain
both snapshot reports and apply diagnostics, append the transition to
confidence history, and do not claim transactional recovery or retry apply.

## Mandatory runtime and hygiene gates

Build and smoke the main task image from `"$TASK/environment"`; the MCP
sidecar image is not sufficient. Runtime proof that
`/app/additional-assumptions.md` exists is mandatory:

```bash
uv run --python 3.12 --with openpyxl python "$S/validate_dialogue.py" \
  smoke --task-dir "$TASK"
```

Docker, disk, or daemon faults are infrastructure faults, not semantic
failures. Preserve logs and hand off with
`suggested_fixer: harbor-infra-fixer`. No smoke bypass is accepted. After an
infrastructure repair, rerun apply and five-file verification if the prior
apply returned to the exact pre-apply snapshot, then rebuild and smoke the main
image from scratch.

Then run the mode-matching closed-world check.

Plain mode:

```bash
uv run --python 3.12 --with openpyxl python - "$TASK" "$WB" <<'PY'
import sys
from pathlib import Path
from plain_eligibility import check_plain_environment
report = check_plain_environment(Path(sys.argv[1]), sys.argv[2])
if report.get("valid") is not True:
    raise SystemExit(report)
print("plain environment hygiene PASS")
PY
```

MCP mode:

```bash
uv run --python 3.12 --with openpyxl python - "$TASK" <<'PY'
import sys
from pathlib import Path
from xl_mcp_oracle import check_environment
bundle = Path(sys.argv[1])
books = sorted(p for p in (bundle / "environment").iterdir()
               if p.is_file() and p.suffix.casefold() in {".xlsx", ".xlsm"})
if len(books) != 1:
    raise SystemExit("expected exactly one workbook in environment/")
report = check_environment(bundle, books[0])
if report.get("valid") is not True:
    raise SystemExit(report)
print("MCP environment hygiene PASS")
PY
```

Finally verify refreshed metadata:

```bash
uv run --python 3.12 python \
  .cursor/skills/naturalize-finance-task-instruction/scripts/naturalize_recovery.py \
  verify-metadata --instruction "$TASK/instruction.md" \
  --task-toml "$TASK/task.toml"
```

After apply, do not rerun disclosure writing/verification, instruction
naturalization, task packaging, or the live HTTP oracle; those precede this
final mutation. A fresh upstream stage invalidates this lane and requires the
entire dialogue workflow again.

## Completion

Pass only when apply, main-image smoke, mode hygiene, and metadata verification
all pass and tracker hashes match disk. Require the notes file, exact Dockerfile
copy, mutation marker, no disclosure heading, and latest validated review.
Set current confidence to `high`; any unresolved semantic, runtime, rollback,
or metadata uncertainty sets current `low` and blocks. Append either transition
to confidence history. Return round/verdict, artifact and image hashes, smoke
result, hygiene result, metadata result, diagnostics, and a structured
completion handoff to `harbor-orchestrator`.

Any completion handoff uses the same envelope with `status: passed`,
`next_owner: harbor-orchestrator`, and `suggested_fixer: none`.

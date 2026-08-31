---
name: harbor-independent-verifier
description: Independently verifies a staged Harbor workbook task with a fresh Sol High disclosure review, generalized HTTP oracle, and exact-answer grader. Use when a fleet workbook reaches its independent verification lane or must receive a structured final verdict.
disable-model-invocation: true
---

# Harbor independent verifier

Verify one staged workbook without changing task artifacts, prose, specifications,
workbooks, images, or grader inputs. Missing or ambiguous evidence is terminal.

## Shared tracker contract

Use canonical lane ID `harbor-independent-verifier` and tracker:

```text
runs/harbor-fleet/<batch>/workbooks/<id>.json
```

Its shared lock is exactly:

```text
runs/harbor-fleet/<batch>/workbooks/<id>.json.lock
```

Never use a batch-wide lock or alternate lane key. Allowed states are `pending`,
`ready`, `running`, `repairing`, `passed`, and `terminal`. For every mutation,
read revision N, acquire the shared lock, CAS-require live revision N, preserve
unknown fields/history, write revision N+1 through a flushed and `fsync`ed sibling
temporary file, atomically replace, then `fsync` the parent. On mismatch, discard,
reload, and recompute.

Update only `lane_state["harbor-independent-verifier"]`, append-only diagnostics,
handoffs, hashes, gates, and timestamps. Store live `current_confidence`; append
each transition to `confidence_history`. Recompute top-level `current_confidence`
as the worst populated lane value under `low < medium < high`. Improvement after
a repair requires orchestrator authorization, resolution of the exact diagnostic,
and all invalidated owning/downstream gates passing on new hashes. Retain historical
lows without permanently blocking a fully proven repair.

## Required inputs

Work from the repository root. Set:

```bash
WB="<id>"
STAGED="<tracker staged_path>"
RUN="runs/$WB-variable-sources"
MCP="$RUN/mcp"
GOLDEN="<tracker immutable golden path>"
DELIVERED="$STAGED/environment/$WB-inputs.xlsx"
DISCLOSE=".cursor/skills/task-disclosure/scripts/disclose.py"
REVIEW_MANIFEST="$RUN/disclosure-sentence-manifest.json"
REVIEW_REPORT="$RUN/disclosure-faithfulness.json"
REVIEW_VALIDATION="$RUN/disclosure-faithfulness-validation.json"
```

Require tracker hashes for the golden, delivered workbook, final instruction,
`tests/disclosure.json`, staged answer key and grader, and MCP build in MCP mode.
Hash drift is terminal.

## Disclosure faithfulness review

Build the manifest with the existing renderer:

```bash
python3 - "$DISCLOSE" "$STAGED" "$GOLDEN" "$DELIVERED" \
  "$REVIEW_MANIFEST" <<'PY'
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

renderer_path, staged_raw, golden_raw, delivered_raw, output_raw = sys.argv[1:]
renderer_path = Path(renderer_path).resolve()
staged = Path(staged_raw).resolve()
golden = Path(golden_raw).resolve()
delivered = Path(delivered_raw).resolve()
output = Path(output_raw).resolve()
instruction_path = staged / "instruction.md"
disclosure_path = staged / "tests" / "disclosure.json"

def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(path.parent, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if tmp.exists():
            tmp.unlink()

for required in (
    renderer_path, instruction_path, disclosure_path, golden, delivered
):
    if not required.is_file():
        raise SystemExit(f"missing manifest input: {required}")

spec = importlib.util.spec_from_file_location(
    "harbor_task_disclosure_renderer", renderer_path
)
if spec is None or spec.loader is None:
    raise SystemExit("could not import task-disclosure renderer")
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)

payload = json.loads(disclosure_path.read_text(encoding="utf-8"))
selected = renderer.agent_records(payload.get("records") or [])
if payload.get("agent_records") != selected:
    raise SystemExit("packaged agent_records differ from renderer selection")

def stable_record_id(record):
    existing = record.get("record_id") or record.get("id")
    if isinstance(existing, str) and existing.strip():
        return existing
    canonical = json.dumps(
        record, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return "record-" + hashlib.sha256(canonical).hexdigest()[:24]

ids = [stable_record_id(record) for record in selected]
if len(ids) != len(set(ids)):
    raise SystemExit("agent-facing records have duplicate stable IDs")

by_band = {}
record_id_by_object = {}
for record, record_id in zip(selected, ids):
    by_band.setdefault(record.get("band") or "", []).append(record)
    record_id_by_object[id(record)] = record_id

section = renderer.render_section(selected)
instruction = instruction_path.read_text(encoding="utf-8")
ordered_bands = sorted(by_band)
rendered_lines = [line for line in section.splitlines() if line.startswith("- ")]
if len(rendered_lines) != len(ordered_bands):
    raise SystemExit("renderer bullet count does not match selected bands")
if section:
    if instruction.count(section) != 1:
        raise SystemExit("rendered disclosure section is not byte-exact in instruction")
elif selected or renderer.SECTION_START in instruction:
    raise SystemExit("empty rendered section disagrees with selected records or instruction")

sentences = []
for order, (band, line) in enumerate(zip(ordered_bands, rendered_lines)):
    if instruction.count(line) != 1:
        raise SystemExit(f"rendered sentence occurrence mismatch at order {order}")
    record_ids = [record_id_by_object[id(record)] for record in by_band[band]]
    sentence_id_source = json.dumps(
        {"order": order, "text": line, "record_ids": record_ids},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    sentences.append({
        "sentence_id": "sentence-" + hashlib.sha256(
            sentence_id_source
        ).hexdigest()[:24],
        "order": order,
        "text": line,
        "record_ids": record_ids,
    })

input_hashes = {
    "renderer": sha256(renderer_path),
    "instruction": sha256(instruction_path),
    "disclosure": sha256(disclosure_path),
    "golden": sha256(golden),
    "delivered": sha256(delivered),
}
manifest = {
    "schema_version": "harbor-disclosure-sentence-manifest/v1",
    "input_hashes": input_hashes,
    "agent_record_ids": ids,
    "sentences": sentences,
}
atomic_json(output, manifest)
print(f"disclosure manifest: {len(sentences)} sentence(s), {len(ids)} record(s)")
PY
```

Review each full rendered bullet line. Empty is valid only with no selected records
and no disclosure section.

Launch a fresh independent `generalPurpose` reviewer with model
`gpt-5.6-sol-high`. It must not have written or previously reviewed this workbook.
Give it only the manifest, golden, delivered workbook, final instruction,
disclosure JSON, immutable input hashes, and `"$REVIEW_REPORT"`.

Require this exact top-level schema:

```json
{
  "schema_version": "harbor-disclosure-faithfulness/v1",
  "model": "gpt-5.6-sol-high",
  "input_hashes": {},
  "manifest_sha256": "<sha256>",
  "sentences": [{
    "sentence_id": "<manifest id>",
    "text": "<exact text>",
    "record_ids": [],
    "golden_evidence": [],
    "delivered_evidence": [],
    "verdict": "pass|blocking|non_blocking",
    "findings": []
  }],
  "blocking_findings": [],
  "non_blocking_flags": [],
  "passed": true
}
```

For each sentence, re-derive every reference, literal, sign, operator, lock,
range boundary, period, copied-column scope, row label, and omission from the
golden; verify delivered blanking and check leakage, false claims, unsupported
facts, and missing mechanics. Unnecessary but true mechanics are non-blocking.

Validate the review mechanically:

```bash
python3 - "$REVIEW_MANIFEST" "$REVIEW_REPORT" "$REVIEW_VALIDATION" <<'PY'
import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path

manifest_path, review_path, output_path = map(Path, sys.argv[1:])
faults = []

def read_json(path, label):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        faults.append(f"{label} could not be read: {exc}")
        return {}

def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None

def atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(path.parent, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if tmp.exists():
            tmp.unlink()

manifest = read_json(manifest_path, "manifest")
review = read_json(review_path, "review")
if not isinstance(manifest, dict):
    faults.append("manifest top level must be an object")
    manifest = {}
if not isinstance(review, dict):
    faults.append("review top level must be an object")
    review = {}
top_keys = {
    "schema_version", "model", "input_hashes", "manifest_sha256",
    "sentences", "blocking_findings", "non_blocking_flags", "passed",
}
sentence_keys = {
    "sentence_id", "text", "record_ids", "golden_evidence",
    "delivered_evidence", "verdict", "findings",
}
if set(review) != top_keys:
    faults.append(f"review top-level keys differ: {sorted(set(review) ^ top_keys)}")
if review.get("schema_version") != "harbor-disclosure-faithfulness/v1":
    faults.append("review schema_version mismatch")
if review.get("model") != "gpt-5.6-sol-high":
    faults.append("review model mismatch")
if review.get("input_hashes") != manifest.get("input_hashes"):
    faults.append("review input_hashes mismatch")
if review.get("manifest_sha256") != sha256(manifest_path):
    faults.append("review manifest_sha256 mismatch")

expected = manifest.get("sentences")
actual = review.get("sentences")
if not isinstance(expected, list):
    faults.append("manifest sentences must be a list")
    expected = []
if not isinstance(actual, list):
    faults.append("review sentences must be a list")
    actual = []
if len(actual) != len(expected):
    faults.append("review sentence count mismatch")

seen_sentence_ids = []
seen_record_ids = []
blocking_sentence = False
for index, item in enumerate(actual):
    if not isinstance(item, dict):
        faults.append(f"review sentence {index} is not an object")
        continue
    if set(item) != sentence_keys:
        faults.append(
            f"review sentence {index} keys differ: "
            f"{sorted(set(item) ^ sentence_keys)}"
        )
    sentence_id = item.get("sentence_id")
    seen_sentence_ids.append(sentence_id)
    record_ids = item.get("record_ids")
    if not isinstance(record_ids, list) or not all(
        isinstance(value, str) for value in record_ids
    ):
        faults.append(f"review sentence {index} record_ids invalid")
        record_ids = []
    seen_record_ids.extend(record_ids)
    if item.get("verdict") not in {"pass", "blocking", "non_blocking"}:
        faults.append(f"review sentence {index} verdict invalid")
    blocking_sentence |= item.get("verdict") == "blocking"
    for key in ("golden_evidence", "delivered_evidence", "findings"):
        if not isinstance(item.get(key), list):
            faults.append(f"review sentence {index} {key} must be a list")
    if index < len(expected):
        source = expected[index]
        for key in ("sentence_id", "text", "record_ids"):
            if item.get(key) != source.get(key):
                faults.append(f"review sentence {index} {key} mismatch")

if len(seen_sentence_ids) != len(set(seen_sentence_ids)):
    faults.append("review has duplicate sentence IDs")
expected_sentence_ids = [item.get("sentence_id") for item in expected]
if seen_sentence_ids != expected_sentence_ids:
    faults.append("review has unknown, missing, duplicated, or reordered sentences")

expected_record_ids = manifest.get("agent_record_ids")
if not isinstance(expected_record_ids, list):
    faults.append("manifest agent_record_ids must be a list")
    expected_record_ids = []
if len(seen_record_ids) != len(set(seen_record_ids)):
    faults.append("review covers an agent record more than once")
if Counter(seen_record_ids) != Counter(expected_record_ids):
    faults.append("review agent-record coverage is incomplete or unknown")

blocking_findings = review.get("blocking_findings")
non_blocking_flags = review.get("non_blocking_flags")
if not isinstance(blocking_findings, list):
    faults.append("blocking_findings must be a list")
    blocking_findings = []
if not isinstance(non_blocking_flags, list):
    faults.append("non_blocking_flags must be a list")
expected_passed = not blocking_findings and not blocking_sentence
if not isinstance(review.get("passed"), bool) or review.get("passed") != expected_passed:
    faults.append("passed must equal absence of blocking findings")

validation = {
    "schema_version": "harbor-disclosure-faithfulness-validation/v1",
    "manifest_sha256": sha256(manifest_path),
    "review_sha256": sha256(review_path),
    "valid": not faults,
    "faults": faults,
}
atomic_json(output_path, validation)
if faults:
    raise SystemExit("disclosure review validation failed: " + "; ".join(faults))
print("disclosure review validation PASS")
PY
```

Semantic failure is terminal and is never retried here.

## Plain-mode closed world

In plain mode, do not run an oracle. Require no MCP directory, compose file,
declaration, or research-service instruction, then run:

```bash
python3 - "$STAGED" "$WB" <<'PY'
import sys
from pathlib import Path
from plain_eligibility import check_plain_environment
report = check_plain_environment(Path(sys.argv[1]), sys.argv[2])
if report.get("valid") is not True:
    raise SystemExit(report)
print("plain environment hygiene PASS")
PY
```

Reject symlinks, unknown files, golden workbooks, `eval/`, normalized specs,
profile captures, snapshots, answer values, and audit working files. Record oracle
as `not_applicable` only after this command passes. The grader remains mandatory.

## Exact concurrent oracle and grader

After disclosure review passes, MCP mode runs the generalized HTTP oracle and
exact grader concurrently with independent workspaces, logs, and reports:

```bash
set -euo pipefail
ORACLE_IMAGE="mcp-$WB-oracle"
ORACLE_CONTAINER="mcp-$WB-oracle-$$"
ORACLE_ALLOWLIST="$RUN/oracle-allowlist.json"
GRADER_SMOKE="$RUN/grader-smoke"
docker build -t "$ORACLE_IMAGE" "$STAGED/environment/mcp-server"
docker run -d --name "$ORACLE_CONTAINER" -p 127.0.0.1::8000 "$ORACLE_IMAGE"
trap 'docker rm -f "$ORACLE_CONTAINER" >/dev/null 2>&1 || true' EXIT
ORACLE_PORT=$(docker port "$ORACLE_CONTAINER" 8000/tcp | awk -F: '{print $NF}')
ORACLE_URL="http://127.0.0.1:$ORACLE_PORT/mcp"
mkdir -p "$GRADER_SMOKE/workspace" "$GRADER_SMOKE/output"

(
  uv run --python 3.12 --with fastmcp --with openpyxl \
    python - "$STAGED" "$MCP" "$ORACLE_URL" \
    "$RUN/oracle-report.json" "$ORACLE_ALLOWLIST" <<'PY'
import asyncio, json, sys
from pathlib import Path
from xl_mcp_oracle import run_oracle
bundle, mcp, url, output, allowlist = sys.argv[1:]
allowlist = Path(allowlist) if Path(allowlist).is_file() else None
report = asyncio.run(run_oracle(
    Path(bundle), Path(mcp), url, allowlist_path=allowlist))
Path(output).write_text(
    json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
if report.get("valid") is not True:
    raise SystemExit("HTTP oracle failed")
print("HTTP oracle PASS")
PY
) >"$RUN/oracle-process.log" 2>&1 &
ORACLE_PID=$!

(
  python3 - "$STAGED/tests/answer_key.json" \
    "$GRADER_SMOKE/workspace/answers.json" <<'PY'
import json, sys
key = json.load(open(sys.argv[1], encoding="utf-8"))
with open(sys.argv[2], "w", encoding="utf-8") as out:
    json.dump(key["targets"], out, indent=2)
PY
  python3 "$STAGED/tests/run_grader.py" \
    --workspace "$GRADER_SMOKE/workspace" \
    --answer-key "$STAGED/tests/answer_key.json" \
    --output-dir "$GRADER_SMOKE/output" \
    --mode discrete
  python3 - "$GRADER_SMOKE/output/reward.json" <<'PY'
import json, sys
reward = json.load(open(sys.argv[1], encoding="utf-8"))
if reward.get("score") != 1.0:
    raise SystemExit("exact-answer grader smoke failed: %r" % reward)
print("exact-answer grader score: 1.0")
PY
) >"$RUN/grader-process.log" 2>&1 &
GRADER_PID=$!

set +e
wait "$ORACLE_PID"; ORACLE_RC=$?
wait "$GRADER_PID"; GRADER_RC=$?
set -e
docker logs "$ORACLE_CONTAINER" > "$RUN/oracle-container.log" 2>&1 || true
docker rm -f "$ORACLE_CONTAINER" >/dev/null 2>&1 || true
trap - EXIT
test -f "$RUN/oracle-report.json"
test -f "$GRADER_SMOKE/output/reward.json"
test "$ORACLE_RC" -eq 0
test "$GRADER_RC" -eq 0
```

Keep branch outputs independent. Always join both PIDs and inspect both reports.
MCP mode requires two zero exits, oracle `valid`, and score `1.0`. Plain mode runs
the grader subshell in the foreground and requires its reward score `1.0`.

Never restart the container. Sole pre-semantic `mcp_not_ready` emits an orchestrator
handoff suggesting `harbor-infra-fixer` and reentry here, leaving this lane
`repairing`. Mixed readiness or post-semantic failure is terminal.

## Verdict and routing

Initial success sets high confidence and `passed`; next is
`harbor-dialogue-author`. After repair, retain confidence until every invalidated
gate passes on new hashes. Non-pass appends a canonical orchestrator handoff with
gate, code, hashes, reasons, diagnostics, fixer, and rerun scope. Blocking failure
sets low confidence and appends history.

---
name: create-harbor-task
description: Builds a Harbor rebuild-the-model task bundle from a raw financial workbook such as `4-10 100/0256.xlsx` by running AST graphing, segmentation, input masking, and `xl_output_task.py`. Use when asked to create a Harbor task, package a workbook into tasks_outputs, or turn a raw `.xlsx` into an outputs reconstruction bundle.
disable-model-invocation: true
---

# Create Harbor Task

Turn a raw golden workbook into a Harbor task bundle. The raw `.xlsx` is the
answer key: put only the masked inputs workbook in the task environment, never
the golden file.

## Inputs

Accept any of:

- `4-10 100/0256.xlsx`
- `0256.xlsx` under the default source folder
- workbook id `0256`

Resolve:

| Variable | Default |
| --- | --- |
| `WB` | stem of the workbook path (`0256`) |
| `SOURCE` | parent directory of the `.xlsx`, else `4-10 100` |
| `AST` | `ast_out/<WB>/` |
| `SEG` | `seg_out/<WB>/` |
| `INPUTS` | `inputs_out/<WB>-inputs.xlsx` |
| `TASK` | `tasks_outputs/<WB>-outputs/` |

Optional variants when the user asks:

- lineage hints → `tasks_outputs_hinted/<WB>-outputs_hinted/` via `--hints`
- semantic hints → requires a `primary` family in `taxonomy_out/workbooks.json`

## Preconditions

From the repository root:

```bash
pip install -r requirements.txt
test -f "$SOURCE/$WB.xlsx"
```

Naturalized instructions need LiteLLM credentials in `.env` (same keys
`xl_output_task.py` / `xl_task_build.py` already use). If naturalization must be
skipped, pass `--no-naturalize` to `xl_output_task.py`.

If `taxonomy_out/workbooks.json` has no entry for `$WB.xlsx`, packaging still
works with an empty family; do not invent a taxonomy entry. Semantic hints
require a known family and will fail without one.

## Pipeline

Copy this checklist and track it:

```text
Harbor task progress for <WB>:
- [ ] 1. AST graph
- [ ] 2. Segment (+ optional LLM adjudication)
- [ ] 3. Human curation of outputs
- [ ] 4. Re-segment after curation edits
- [ ] 5. Inputs-only workbook
- [ ] 6. Package Harbor bundle
- [ ] 7. Smoke-check bundle
```

### 1. AST graph

Skip if `ast_out/$WB/nodes.csv` already exists and the user did not ask to rebuild.

```bash
python3 xl_ast_graph.py "$SOURCE/$WB.xlsx" -o ast_out
```

### 2. Segment

```bash
python3 xl_segment.py "$WB" --source "$SOURCE" -o seg_out
# optional: let the adjudicator flip include flags on the shortlist
python3 xl_segment.py "$WB" --source "$SOURCE" -o seg_out --llm
```

Do not pass `--recurate` unless the user explicitly wants to discard existing
hand edits in `curation.toml`.

If verification fails, stop and report the failures. Do not package a task from
a segmentation that did not verify, unless the user explicitly accepts a
`SKIPPED` / failed-verify workbook.

### 3. Curate outputs (required gate)

Open `seg_out/$WB/curation.toml`. Summarize every `include = true` row (name,
band, score) and the strongest excluded candidates. Ask the user to confirm or
edit `include` / `name` before packaging.

The Harbor deliverable is exactly the curated output set. Never invent extra
targets or drop curated ones without user approval.

### 4. Re-segment after edits

If `curation.toml` changed:

```bash
python3 xl_segment.py "$WB" --source "$SOURCE" -o seg_out
```

This rebuilds the frontier, lineage, and verification from the chosen outputs.

### 5. Inputs-only workbook

```bash
python3 xl_input_mask.py "$WB" --source "$SOURCE" -o inputs_out
```

Confirm the mask reports typed cells preserved and no formulas left. The masked
file is what Harbor agents receive.

### 6. Package the Harbor bundle

Default (no hints):

```bash
python3 xl_output_task.py "$WB" \
  --source "$SOURCE" \
  --seg-root seg_out \
  --inputs-root inputs_out \
  -o tasks_outputs
```

Lineage-hinted variant:

```bash
python3 xl_output_task.py "$WB" \
  --source "$SOURCE" \
  --hints \
  -o tasks_outputs_hinted
```

Semantic-hinted variant (needs taxonomy family):

```bash
python3 xl_output_task.py "$WB" \
  --source "$SOURCE" \
  --semantic-hints \
  -o tasks_outputs_semantic_hints
```

`--hints` and `--semantic-hints` are mutually exclusive.

### 7. Smoke-check

Verify the bundle contains:

```text
tasks_outputs/<WB>-outputs/
  instruction.md
  task.toml
  environment/<WB>-inputs.xlsx
  environment/Dockerfile
  tests/test.sh
  tests/run_grader.py
  tests/finance_grader/
  tests/answer_key.json
  tests/outputs.json
```

Checks:

1. `environment/` holds the masked inputs workbook, not `4-10 100/$WB.xlsx`.
2. `instruction.md` lists only curated figures/cells.
3. `tests/answer_key.json` target cells match `curation.toml` includes.
4. `task.toml` metadata `workbook`, `artifact`, and `n_outputs` look right.

## Hard rules

- Never copy the golden workbook into `environment/`.
- Never put golden formulas, cached answers, or answer-key values into
  `instruction.md`.
- Do not treat rollout failure or model difficulty as a reason to change the
  curated outputs while packaging.
- Prefer reusing existing `ast_out` / `seg_out` when fresh enough; rebuild when
  the user asks or when artifacts are missing.
- After packaging, point the user at the task directory path. Do not run Harbor
  jobs unless asked.

## Done when

Report:

- workbook id and source path
- curated outputs (names + bands)
- paths to `seg_out/$WB/`, `inputs_out/$WB-inputs.xlsx`, and the task bundle
- whether naturalization ran or `--no-naturalize` was used
- any verification warnings (`SKIPPED`, seeded-inside-cone, taxonomy miss)

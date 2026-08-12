# Package manifest

Everything needed to run the synthetic data generation pipeline described in
`README.md`, from a raw `.xlsx` workbook to a packaged Harbor task bundle and
the post-rollout review gate. Workbooks and derived artifacts (`ast_out/`,
`seg_out/`, `inputs_out/`, `tasks_outputs/`, `jobs/`, `runs/`) are not included;
run the pipeline against your own source folder.

## Core pipeline (README sections 3-17)

| File | Role |
| --- | --- |
| `xl_ast_graph.py` | Stage 0: parse `.xlsx` into the AST graph (`nodes.csv`, `edges.csv`). |
| `xl_segment.py` | CLI for stages 1-11; runs segmentation and verifies it. |
| `xl_seg/` | Stages 1-11: projection, banding, condensation, scoring, evaluation, lineage, emit, and the optional LLM adjudication pass. |
| `xl_input_mask.py` | Section 17: writes the inputs-only workbook. |
| `xl_level_split.py` | One workbook per dependency level; `xl_input_mask.py` reuses its XML rewriter. |

## Harbor task packaging (README section 21)

| File | Role |
| --- | --- |
| `xl_output_task.py` | Packages a segmented workbook into a Harbor rebuild task bundle. |
| `xl_task_build.py` | Imported by `xl_output_task.py` (naturalization, TOML helpers); also a standalone template-driven task builder. |
| `xl_harbor_prep.py` | Imported by `xl_output_task.py` for the bundle `Dockerfile`. |
| `grader/` | Copied into every task bundle (`run_grader.py`, `finance_grader/`); includes its design notes and tests. |
| `task_templates.yaml` | Template spec read by `xl_task_build.py`. |
| `taxonomy_out/workbooks.json` | Workbook-family taxonomy; needed for `--semantic-hints`. |

## Rollout scoring and workflow support

| File | Role |
| --- | --- |
| `xl_harbor_score.py` | Scores completed Harbor job attempts (imports `assess` from `xl_eval_run.py`). |
| `xl_passk_score.py` | pass@k summary over scored attempts; exercised by `grader/tests/test_passk_score.py`. |
| `xl_pass1_score.py` | Continuous and pass@1 metrics for one final attempt per task. |
| `xl_eval_run.py` | Chat-level stand-in for `harbor run` through the LiteLLM proxy. |
| `xl_formula_hint_tasks.py` | Clones task bundles and appends audited custom-formula hints (pairs with `/custom-formula-gate`). |
| `xl_wb_classify.py` | Generates `taxonomy_out/workbooks.json`, the family taxonomy used for `--semantic-hints`. |
| `labelbox_llm_proxy.py` | Local proxy adding the `x-labelbox-context` header Harbor's adapter cannot set. |

## Cursor skills (README sections 21-22)

| Path | Role |
| --- | --- |
| `.cursor/skills/create-harbor-task/` | `/create-harbor-task`: raw workbook → Harbor rebuild task, end to end. |
| `.cursor/skills/custom-formula-gate/` | `/custom-formula-gate`: post-rollout classification of golden formulas against the closed catalog (`SKILL.md`, `CATALOG.md`, `scripts/extract_gate_context.py`). |

## Dependencies

- `openpyxl` (see `requirements.txt`) for the core pipeline.
- `pyyaml` is additionally required by `xl_task_build.py`, and therefore by
  `xl_output_task.py` / the `/create-harbor-task` skill.
- Python 3.11+ (`xl_output_task.py` falls back to stdlib `tomllib` when `tomli`
  is absent).
- Naturalized task instructions need LiteLLM credentials in a `.env` at the
  repo root (not included here); pass `--no-naturalize` to skip.

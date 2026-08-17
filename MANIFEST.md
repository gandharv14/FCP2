# Package manifest

Everything needed to run the synthetic data generation pipeline described in
`README.md`, from a raw `.xlsx` workbook to a packaged Harbor task bundle and
the pre-package Terra formula gate. Workbooks and derived artifacts (`ast_out/`,
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

## Variable-source MCP environments (README section 23)

| File | Role |
| --- | --- |
| `xl_variable_source_audit.py` | Default pre-packaging audit stage: deterministic inputs inventory → GPT 5.6 Sol variable/source Markdown through Labelbox LiteLLM, with hash and generation metadata. |
| `xl_variable_mcp.py` | CLI: Markdown table → draft; normalized/profiled spec validation; deterministic MCP build/validation; smoke test; emits `mask_cells.json` and `masked_inputs.json`. |
| `mcp_env/` | Offline generator: reviewed source-profile rendering, dimension distractors, provenance release chains, isolation validator, and the paginated/filter-gated FastMCP sidecar with Dockerfile/compose assets. |
| `xl_mcp_oracle.py` | Reusable live-sidecar oracle for pagination, exact evidence, provenance, broad conflicts, masking, duplicate-value leaks, environment isolation, and attributed profile excerpts. |
| `xl_input_mask.py --mask-cells` | Blanks the served variables from the inputs workbook (deny-set hook). |
| `xl_output_task.py --mcp` | Packages the sidecar into the Harbor bundle: `environment/mcp-server/`, `docker-compose.yaml`, `[[environment.mcp_servers]]`, research instruction section. |

## Rollout scoring and workflow support

| File | Role |
| --- | --- |
| `xl_harbor_score.py` | Scores completed Harbor job attempts (imports `assess` from `xl_eval_run.py`). |
| `xl_passk_score.py` | pass@k summary over scored attempts; exercised by `grader/tests/test_passk_score.py`. |
| `xl_pass1_score.py` | Continuous and pass@1 metrics for one final attempt per task. |
| `xl_eval_run.py` | Chat-level stand-in for `harbor run` through the LiteLLM proxy. |
| `xl_formula_hint_tasks.py` | Validates and renders audited custom-formula hints for packaging; also supports legacy bundle cloning. |
| `xl_wb_classify.py` | Generates `taxonomy_out/workbooks.json`, the family taxonomy used for `--semantic-hints`. |
| `labelbox_llm_proxy.py` | Local proxy adding the `x-labelbox-context` header Harbor's adapter cannot set. |

## Cursor skills (README sections 21-22)

| Path | Role |
| --- | --- |
| `.cursor/skills/create-harbor-task/` | `/create-harbor-task`: fail-closed raw workbook → segmented, normalized, source-profiled, MCP-backed, oracle-validated Harbor task. |
| `.cursor/skills/profile-mcp-sources/` | `/profile-mcp-sources`: bounded GPT 5.6 Sol public reads → reviewed source terminology/structure profiles; auth and blocked pages are skipped. |
| `.cursor/skills/custom-formula-gate/` | `/custom-formula-gate`: pinned GPT-5.6 Terra pre-package classification of key golden variables against the closed textbook catalog, with deterministic extraction and output validation. |
| `.cursor/skills/naturalize-finance-task-instruction/` | `/naturalize-finance-task-instruction`: pinned GPT-5.6 Sol final-instruction rewrite with protected sections, deterministic validation, semantic review, and atomic application. |

## Dependencies

- `openpyxl` (see `requirements.txt`) for the core pipeline.
- `pyyaml` is additionally required by `xl_task_build.py`, and therefore by
  `xl_output_task.py` / the `/create-harbor-task` skill.
- Python 3.11+ (`xl_output_task.py` falls back to stdlib `tomllib` when `tomli`
  is absent).
- Variable/source audits and the standalone early naturalizer need
  `lbx_api_key` in a `.env` at the repo root (not included here).
  `/create-harbor-task` disables that early rewrite and uses the final Cursor
  instruction skill instead; the audit has a separate explicit
  `--no-variable-source-audit` opt-out.
- Public-source profiling and final instruction naturalization use Cursor's
  `gpt-5.6-sol-high` subagent at authoring time. MCP builds and runtime episodes
  remain offline.
- `fastmcp` is optional for generated-server smoke/oracle checks and is normally
  supplied ephemerally with `uv run --with fastmcp`.

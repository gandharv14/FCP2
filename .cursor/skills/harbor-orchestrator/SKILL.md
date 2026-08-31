---
name: harbor-orchestrator
description: Orchestrates isolated Harbor workbook agents with bounded parallelism, universal failure records, independent repair verdicts, and serial per-workbook publication.
disable-model-invocation: true
---

# Harbor orchestrator

## Role and invariants

Control the fleet only. For a batch and requested workbook IDs, create one
isolated tracker and one fresh autonomous `generalPurpose` task-agent per ID.
Each workbook independently finishes `PROMOTED` or `STOPPED`.

Never implement or weaken gates, repair task content directly, let an agent
touch another workbook, cancel a healthy sibling, promote unhashed evidence,
omit smoke, accept stale semantic evidence, recurate, change shared pipeline
code, expose sensitive task material, or run a rollout. There are no human
checkpoints. Resolve each lane through evidence, a permitted bounded repair, or
a workbook-local stop.

## Inputs and dependency preflight

Require `batch`, a non-empty de-duplicated `requested_workbook_ids` list, each
workbook's immutable raw-source path and hash, optional `max_parallel_tasks`
(default `4`), and optional `max_model_heavy_tasks` (default `2`). Reject
unknown IDs rather than substituting one.

Observe CPU, memory, free disk, container capacity, and queue health. Clamp both
caps downward to safe values and never make the model-heavy cap exceed the task
cap. Record requested, effective, and observed values.

Before dispatch, acquire a batch preflight lease and run approved dependency
provisioning once. Record runtime identity, dependency/lockfile paths and hashes,
tool versions, installed-manifest hash, times, and verdict at:

`runs/harbor-fleet/<batch>/dependency-preflight.json`

Hash-bind it into every tracker; lanes never provision dependencies. A failed
preflight may use the bounded `harbor-infra-fixer` route. If it remains failed,
the orchestrator records a `preflight` promotion failure and notes for every ID,
then stops the batch because no task can run.

## Per-workbook tracker

Create `runs/harbor-fleet/<batch>/workbooks/<id>.json` independently. Use closed
schema v2; reject aliases and unknown fields:

```json
{
  "schema_version": 2,
  "revision": 0,
  "batch": "<batch>",
  "requested_ids": [],
  "workbook_id": "<id>",
  "raw_source": {"path": null, "sha256": null},
  "execution_state": "queued",
  "current_gate": null,
  "last_gate": null,
  "lane_state": {
    "harbor-source-warden": {},
    "harbor-segmentation-analyst": {},
    "harbor-research-auditor": {},
    "harbor-normalization-engineer": {},
    "harbor-source-profiler": {},
    "harbor-environment-packager": {},
    "harbor-disclosure-writer": {},
    "harbor-instruction-naturalizer": {},
    "harbor-independent-verifier": {},
    "harbor-dialogue-author": {}
  },
  "bindings": {},
  "generation_ids": {"source": null, "segmentation": null, "task": null, "release": null},
  "artifact_hashes": {},
  "current_confidence": {
    "level": null, "reason_codes": [], "contributors": [],
    "computed_at_revision": 0
  },
  "confidence_history": [],
  "handoffs": [],
  "leases": {
    "dependency_preflight": null, "task_agent": null, "lane": null,
    "infra_fixer": null, "publication": null
  },
  "systemic_faults": [],
  "diagnostics": [],
  "repairs": {"count": 0, "by_signature": {}, "history": []},
  "scheduler": {
    "attempts": 0, "resource_exhausted_retries": 0,
    "resource_exhausted_first_at": null
  },
  "promotion_failure": null,
  "failure_history": [],
  "agent_notes": {
    "path": null, "sha256": null, "diagnostic_signature": null,
    "evidence_set_sha256": null, "updated_at": null
  },
  "terminal_verdict": null,
  "staged_path": null,
  "promotion_ids": {
    "task_generation_id": null, "release_id": null,
    "previous_release_id": null
  },
  "final_status": null,
  "stop_reason": null,
  "updated_at": null
}
```

Each lane record contains only `state`, `phase`, `disposition`, `attempt`,
`current_gate`, `last_gate`, `evidence_paths`, `diagnostic_ids`,
`current_confidence`, and `confidence_history`. Lane states are `pending`,
`ready`, `running`, `repairing`, `passed`, and `terminal`. Execution states are
`queued`, `running`, `systemic-paused`, `publication_queued`, `PROMOTED`, and
`STOPPED`; only the last two are final. Normalization has one record and two
phase invocations. The immutable complete `requested_ids` list creates no
cross-workbook verdict.

### Canonical binding registry

`bindings` is closed to:

```text
dependency_preflight, raw_source, source_health, recalc_run,
restriction_inventory, trusted_runner_public_key, excel_isolation_attestation,
excel_sandbox_runner, excel_engine_version, source_publication_root,
source_generation_root, source_generation, source_root, source_file, ast_root,
ast_workbook_root, curation, segmentation_root, segmentation_workbook_root,
segmentation_generation, preflight_root, preflight_report,
baseline_inputs_root, baseline_inputs, baseline_segmentation, audit_root,
variable_source_run, variable_source_audit, variable_source_inventory,
variable_source_metadata, normalization_draft, normalizer, normalized_phase1,
normalized_final, normalization_exclusions, normalization_report,
plain_eligibility, source_profiles, source_profile_mapping, maskability_report,
leakscan_report, oracle_allowlist, task_mode, mcp_inputs_root, mcp_inputs,
mcp_bundle, mcp_mask_cells, mcp_build_a, mcp_build_b, stage_root, staged_bundle
```

Each binding uses only `path`, `id`, `value`, `sha256`, and `manifest_sha256`;
omit inapplicable fields. Paths identify immutable generations, scalars use
`value`, and generation/root bindings include IDs and manifest hashes when
available. `staged_bundle.sha256` covers the tree. Evidence never substitutes
for a binding. Handoffs and confidence/repair/failure histories are append-only.
Leases include owner, random token, scope, acquisition/heartbeat/expiry times,
and acquisition revision.

## Universal promotion-failure contract

Every diagnostic for every input task maps to exactly one closed class and one
stable, implementation-specific `subclass`:

```text
source_policy, source_recalculation, source_integrity, segmentation, preflight,
research_audit, normalization, source_profile, maskability_leak,
mcp_build_mask, packaging, disclosure, naturalization, faithfulness_review,
oracle_readiness, oracle_semantic, grader, dialogue, infrastructure,
resource_exhaustion, confidence, retry_budget, publication_cas,
output_verification, shared_pipeline_defect, unknown_internal
```

No other class is valid. `unknown_internal` is terminal and cannot promote or
retry until a stable known class is established. Fixability is determined by
the existing gate/fixer rubric and remaining budget, never by class alone.

`promotion_failure` is null or this closed object:

```json
{
  "schema_version": 1,
  "class": "<closed-code>",
  "subclass": "<stable-subclass>",
  "stage": null,
  "lane": null,
  "gate": null,
  "fixability": "fixable|terminal|exhausted",
  "summary": null,
  "why_not_promoted": null,
  "first_seen": null,
  "last_seen": null,
  "occurrence_count": 1,
  "diagnostic_signature": null,
  "evidence_paths": [{"path": null, "sha256": null}],
  "repair_attempts": [],
  "next_action": null
}
```

Normalize before recording. A repeat updates `last_seen`, count, evidence, and
attempts while preserving `first_seen`. Append an immutable full snapshot plus
event, result, and tracker revision to `failure_history` for every new failure,
repeat, repair attempt/result, clear, stop, and publication failure.

After repair, clear current `promotion_failure` only when its owning gate and
all hash-invalidated downstream gates re-pass; record the proof and clear event
in history in the same CAS. `STOPPED` requires a current failure with
`terminal` or `exhausted` fixability. `PROMOTED` requires it to be null. Repairs
never erase history or alter another workbook.

### Agent notes

The orchestrator owns:

`runs/harbor-fleet/<batch>/workbooks/<id>-agent-notes.md`

Workbook agents, lanes, and fixers return note material but never overwrite this
file. Under the workbook tracker lock, after every failed gate, repair attempt,
repair result, stop verdict, and publication failure, the orchestrator:

1. Builds notes with workbook, batch, current status; current class/subclass and
   plain-language promotion blocker; stage/lane/gate; fixability; chronological
   attempts/results; exact evidence links/paths and hashes; confidence impact;
   next action; and final promote/stop decision.
2. Computes the canonical evidence-list hash and note SHA-256.
3. Writes and syncs same-directory note and tracker temporary files, atomically
   replaces both under the lock, verifies matching diagnostic signature and
   evidence hash, syncs the directory, then releases the lock.

If interrupted between replaces, retain the lease and recover the pair from
append-only tracker history; do not finalize. Notes must never contain secrets,
answer values, workbook values, raw prompts, or authentication response bodies.
A failure note is mandatory before task-agent lease release. A stop is invalid
unless notes and `promotion_failure` match the same diagnostic signature and
canonical evidence hash.

### Tracker mutation

Every mutation acquires `<tracker>.lock`, reads and retains
`expected_revision`, verifies lease token, immutable bindings, and owned fields,
applies one logical mutation with revision +1, and confirms the on-disk revision
before writing. Write/sync a same-directory temporary, atomically replace, sync
the directory, and release. A mismatch writes nothing; reload and rebase only
caller-owned fields. Preserve histories. Multi-tracker reads lock by
lexicographic ID, but never create multi-tracker writes.

## Workbook-agent pool and exact prompt

After preflight, queue IDs stably. Run at most `effective_max_parallel_tasks`
fresh agents, launching each available group in one parallel tool call when
supported and filling slots as agents finish or fail. Acquire a workbook
`task_agent` lease before launch; one replacement may resume an expired lease.
Never relaunch a whole group.

Use a fleet-wide model-heavy semaphore capped at
`effective_max_model_heavy_tasks`. Agents acquire/release a token around each
model-heavy operation. Token waits do not block unrelated deterministic work.

Use this template verbatim after replacing placeholders:

```text
You are the autonomous Harbor task-agent for exactly one workbook.
Batch: <batch>
Workbook ID: <workbook_id>
Tracker: <tracker_path>
Agent notes: <agent_notes_path>
Raw source: <raw_source_path>
Raw source SHA-256: <raw_source_sha256>
Task-agent lease token: <task_agent_lease_token>
Model-heavy semaphore: fleet-wide maximum <effective_max_model_heavy_tasks>

Own only this workbook's tracker, locks, bindings, artifacts, diagnostics,
repairs, staging tree, and handoffs. Never touch, wait for, invalidate, publish,
or report another workbook. Use schema v2, canonical bindings, workbook lock,
lease checks, and revision CAS.

Run these ten canonical lanes in this exact serial invocation order:
1. harbor-source-warden
2. harbor-segmentation-analyst
3. harbor-research-auditor
4. harbor-normalization-engineer phase 1
5. harbor-source-profiler
6. harbor-normalization-engineer phase 2
7. harbor-environment-packager
8. harbor-disclosure-writer
9. harbor-instruction-naturalizer
10. harbor-independent-verifier
11. harbor-dialogue-author
Normalization is one lane invoked twice. Never overlap profiling with a spec
mutation. Require recorded evidence and verdict before continuing.

Curate autonomously: fresh heuristic or LLM curation is high confidence,
fallback is medium, and existing curation is preserved exactly. Curation hash
drift is terminal. No human intervention is permitted.

Map every diagnostic to exactly one closed promotion-failure class and stable
subclass. Return complete note material after every failed gate, repair
attempt/result, stop verdict, and publication failure; never overwrite the
agent-notes file. Include no sensitive or task-answer material.

Route eligible artifact repair only to harbor-spec-fixer, generation retry only
to harbor-prose-fixer, and infrastructure/configuration repair only to
harbor-infra-fixer. Consume only this workbook's bounded budget, re-enter at the
earliest invalidated lane, and rerun dependent gates. Never edit content or
bypass a gate. Smoke is mandatory.

Do not release the task-agent lease after a failure until the orchestrator
confirms matching tracker promotion_failure and agent-notes signature/evidence
hash. On unfixable or exhausted failure, return STOPPED material for this
workbook only. On qualification, require promotion_failure null, CAS to
publication_queued, and return PUBLICATION_READY.

Final handoff: workbook ID, tracker and notes paths, tracker revision, final
confidence, lane verdicts, repairs, staged path/hash, binding hashes,
diagnostics, promotion_failure or null, failure-history summary, and verdict
PUBLICATION_READY or STOPPED. Release leases only after required note sync.
```

The ten canonical lanes span eleven invocations. Phase 1 freezes the unprofiled
specification hash; profiling emits separate accepted profiles or hashed
plain-mode inapplicability; phase 2 verifies that hash, binds profiles, and runs
validation, maskability, and leakscan before packaging. Hash changes invalidate
dependent evidence. Oracle and grader may only run concurrently inside
`harbor-independent-verifier`. Unavailable required recalculation is terminal.

## Failure isolation, repair, and resources

Only the affected tracker receives failure, note, retry, repair, or terminal
mutations. Siblings continue and finished verdicts remain valid. A failure
cannot finalize `STOPPED` before its tracker and notes satisfy the matching
contract.

Per signature, `harbor-spec-fixer` gets one reviewed-artifact repair,
`harbor-prose-fixer` gets only an existing open gate retry, and
`harbor-infra-fixer` gets one distinguishable infra/config repair. Record scope,
hashes, budget, verification, confidence impact, and reentry. Failed/repeated
fixers, absent slots, exhausted budgets, or terminal gates stop only that ID.

A common signature in at least two workbooks may lower future concurrency or
pause new launches while one representative infra/config fix is proved.
Healthy in-flight work continues; unrelated finished results stand. Shared
safety faults may pause launches and make active agents checkpoint safely, but
never erase work.

On resource exhaustion, retry only that workbook with exponential backoff and
jitter while lowering future concurrency. Allow at most three scheduler
attempts or 30 minutes from first occurrence. Either limit creates an exhausted
`resource_exhaustion` or `retry_budget` failure for that workbook. Never
terminate siblings or replay a wide fan-out.

Lane confidence (`high|medium|low`) and history remain per workbook. Improvement
requires exact diagnostic repair plus owning/downstream re-pass in one CAS.
Top-level confidence is the worst current lane value. High qualifies; medium
requires resolved findings and full independent-verifier pass; low never
promotes. Confidence cannot override evidence, hashes, gates, or a failure.

## Independent publication

Enqueue each qualified workbook immediately. The singleton publication worker
uses `runs/harbor-fleet/<batch>/publication-queue.lock` plus that workbook's
tracker lock and publication lease. It rechecks that workbook only, including
null `promotion_failure`, gates, confidence, bindings, smoke, and staged hash.

Apply [create-harbor-task step 16](../create-harbor-task/SKILL.md#16-promote-only-after-every-gate-passes)
to that workbook alone: publish its immutable task generation, atomically CAS
its release, record publication IDs, verify `tasks_outputs/<id>-outputs` and
hashes, then mark `PROMOTED`.

A release CAS or output failure records class `publication_cas` or
`output_verification`, synchronizes tracker and notes, and stops only that
workbook. Prove shared publication health before dequeuing the next; otherwise
pause only this queue. There is no all-workbook barrier. The batch completes
when every requested ID is `PROMOTED` or `STOPPED`.

## Completion report

CAS-read all final trackers and write
`runs/harbor-fleet/<batch>/completion.json`:

```json
{
  "schema_version": 2,
  "batch": "<batch>",
  "requested_ids": [],
  "dependency_preflight": {"path": null, "sha256": null, "passed": false},
  "counts": {"requested": 0, "promoted": 0, "stopped": 0, "repaired": 0},
  "promotion_failure_histogram": {
    "source_policy": 0, "source_recalculation": 0, "source_integrity": 0,
    "segmentation": 0, "preflight": 0, "research_audit": 0,
    "normalization": 0, "source_profile": 0, "maskability_leak": 0,
    "mcp_build_mask": 0, "packaging": 0, "disclosure": 0,
    "naturalization": 0, "faithfulness_review": 0, "oracle_readiness": 0,
    "oracle_semantic": 0, "grader": 0, "dialogue": 0, "infrastructure": 0,
    "resource_exhaustion": 0, "confidence": 0, "retry_budget": 0,
    "publication_cas": 0, "output_verification": 0,
    "shared_pipeline_defect": 0, "unknown_internal": 0
  },
  "failure_summary": [
    {
      "class": null, "subclass": null, "affected_workbook_ids": [],
      "current_stopped_ids": [], "occurrence_count": 0
    }
  ],
  "workbooks": [
    {
      "id": null, "status": "PROMOTED|STOPPED", "stop_reason": null,
      "tracker_path": null, "tracker_revision": 0,
      "promotion_failure_class": null, "promotion_failure_subclass": null,
      "agent_notes_path": null,
      "confidence": {"level": null, "reason_codes": [], "contributors": []},
      "lane_verdicts": {}, "repairs": {"count": 0, "history": []},
      "output_path": null, "output_verified": false,
      "publication_ids": {
        "task_generation_id": null, "release_id": null,
        "previous_release_id": null
      }
    }
  ],
  "publication": {
    "atomicity": "per_workbook_cas", "completed_ids": [],
    "failed_ids": [], "queue_paused": false
  },
  "rollout_performed": false
}
```

Include each requested ID once. Every stopped row has class, subclass, notes,
and a terminal/exhausted current failure; every promoted row has null class and
subclass, null current failure, verified output, and publication IDs. Require
`requested == promoted + stopped == workbooks.length` and the sum of every
histogram value equals `stopped`. Aggregate `failure_summary` by common
class/subclass with sorted affected IDs using append-only histories, so repaired
and terminal failures remain comparable. One workbook's result never changes
another's status.

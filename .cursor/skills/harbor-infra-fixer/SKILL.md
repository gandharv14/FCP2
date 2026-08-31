---
name: harbor-infra-fixer
description: Repairs a closed set of Harbor fleet infrastructure failures involving disk, Docker, Python import paths, invalid audit-project routing, and pre-semantic MCP readiness. Use when the orchestrator assigns the singleton infrastructure fixer for one diagnostic signature.
disable-model-invocation: true
---

# Harbor infrastructure fixer

Operate as the off-lane singleton infrastructure worker. Repair execution
infrastructure only. Do not alter task semantics, workbook artifacts, normalized
specs, disclosure content, instructions, dialogue, answer keys, graders, validators,
allowlists, or gate thresholds. Do not solicit human input; missing or ambiguous
evidence is terminal.

## Shared tracker contract

Use workbook trackers at:

```text
runs/harbor-fleet/<batch>/workbooks/<id>.json
```

Every mutation, including lease and systemic-pause changes, uses
`runs/harbor-fleet/<batch>/workbooks/<id>.json.lock`. Mutate only the canonical
owning ID under `lane_state`; never create `lanes`, a display-name key, or a fixer
lane. Allowed states are `pending`, `ready`, `running`, `repairing`, `passed`, and
`terminal`. Use that lock and compare-and-swap:

1. Read a snapshot and its nonnegative integer `revision`.
2. Acquire the shared tracker lock used by every lane.
3. Require the live revision to equal the snapshot revision.
4. Preserve unknown fields and history; apply the mutation.
5. Set `revision` to exactly the prior value plus one, write a sibling temporary
   file, flush and `fsync`, atomically replace the tracker, and `fsync` its parent.
6. On a CAS mismatch, discard the candidate, reload, and recompute the mutation.

Acquire the batch singleton lease in the representative tracker with owner,
signature, timestamps, and representative ID. Set affected owning lanes to
`repairing`. Append diagnostics and one `repairs.history` item per affected tracker
with signature, attempt, hashes, result, actions, proof, and reentry gate; increment
`repairs.count`. Each lane owns a `current_confidence` object and append-only
`confidence_history`. This fixer reports repair evidence and does not raise or
rewrite either field. On every commit, recompute top-level `current_confidence` as
the worst of all populated lane `current_confidence` values under
`low < medium < high`.

Only an owning lane may improve its `current_confidence`, and only after the
orchestrator authorizes the closed repair, the exact diagnostic is resolved, and
all invalidated owning and downstream gates pass on new hashes. That lane appends
the transition to `confidence_history`. Preserve historical low entries; they
remain evidence but do not permanently block a fully proven repair.

The budget is exactly one repair attempt per normalized signature. A prior matching
attempt in any affected tracker is recurring and terminal or systemic-terminal;
never reacquire the lease for it.

## Closed taxonomy

Accept only:

1. disk byte or inode exhaustion
2. Docker daemon, builder, network, port, or container-runtime failure
3. repository-root or `PYTHONPATH` import failure
4. invalid or deleted audit-project routing at the gateway
5. sole `mcp_not_ready` before any oracle semantic check

Definitive authentication, account, quota, or entitlement denial is terminal.
Transient rate limiting and timeouts return unchanged to `harbor-orchestrator` for
its bounded retry policy; they are not infrastructure-fixer work. Semantic oracle,
grader, validator, artifact-content, and all unlisted failures are terminal here.

Capture command, status, bounded logs, host identity, affected IDs, and occurrence
count. Do not read credentials or print secret environment values.

## Repairs

### Disk

Measure filesystem bytes and inodes. Remove only proven disposable Docker cache,
stopped infrastructure containers, and stale temporary infrastructure files.
Never delete fleet trackers, run diagnostics, source generations, workbooks, build
evidence, or current task artifacts. Require sufficient free bytes and inodes before
proof.

### Docker

Verify daemon reachability, builder health, required network, bindable loopback
ports, image build, container start, logs, and cleanup. Repair runtime state without
editing Dockerfiles, compose files, images' source context, or task bundles.

### Python imports

Run from the repository root and set `PYTHONPATH` to the repository's required
source root for the failing command. Prove imports in a fresh process. Do not install
a shadow package or edit import statements to mask incorrect routing.

### Audit-project routing

Handle only a gateway route whose referenced audit project is invalid or deleted.
Select an existing approved audit project through runtime routing configuration and
prove it with a non-semantic health request. Do not create a project, alter task
identity or payload content, change credentials, or treat access denial as routing.
Record only redacted project metadata.

### MCP readiness

Restart the sidecar exactly once only when `mcp_not_ready` is the sole failure and
the oracle records that no semantic check began. Capture logs first. A second
readiness failure, a mixed signature, or any post-semantic failure is terminal and
must not trigger another restart.

## Systemic pause and proof

When the same normalized signature affects multiple lanes, use CAS updates to set
`systemic_fault.paused: true`, signature, affected IDs, and timestamp in each
tracker. This skill never clears the pause. Stop new starts while active lanes reach
safe boundaries.

Apply one bounded repair, then prove it on exactly one representative lane selected
in the lease:

1. rerun the original failing infrastructure operation with identical artifact
   hashes
2. run the nearest non-semantic health check
3. confirm the original signature is absent
4. confirm no task or artifact-content hash changed

Success records `reentry_gate`, proof paths and hashes, and
`systemic_fault.resume_eligible: true`; only the orchestrator resumes or routes the
owning lane. It is repair evidence, not permission for this fixer to raise
confidence. Release the lease through the same CAS protocol.

If the representative repeats the signature, the fault recurs later, or repair
would require semantic or artifact-content edits, set `systemic_terminal` when
multi-lane and `terminal` otherwise. Preserve the pause, signature, history, and
diagnostics. All outcomes return to `harbor-orchestrator`; this skill dispatches no
lane.

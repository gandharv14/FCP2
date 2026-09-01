---
name: run-harbor-recovery-fleet
description: Deploys one starting FCP2 baseline to three Harbor recovery VMs, launches exactly two isolated recovery agents per VM, keeps subsequent fixes local to each task or lane, investigates every failure or skip, and reports task-level progress every 20 minutes. Use when starting, resuming, or monitoring the Harbor workbook recovery fleet.
---

# Run Harbor recovery fleet

## Non-negotiable directive

> launch of the vms again (2 agents each). copy the local code onto the vm as the starting point. but the same instruction -> no failure or skip. it's okay to make code changes for each task or use agentic fixes. Whenever there is a new failure or there is a new skip, iteratively diagnose the issue and apply the fixes. And then report every 20 minutes. When you do the reporting, make sure that you dive into each individual task to see the task level progress.

Treat every queue row as unfinished until a source-bound, complete delivery
passes all required checks. A failure, skip, timeout, `HARD`, fairness failure,
or investigator finding must keep the same row active.

## Fixed fleet

- Project: `saleseng`
- Zone: `us-central1-a`
- VMs: `hhu-server`, `hhu-server-2`, `hhu-server-3`
- Local source of truth: `/Users/henryhu/Documents/GDM_FCP/FCP_recursive_v1`
- Worker: `scripts/batch/recovery_worker.py`
- Concurrency: exactly two generation lanes per VM, six total
- Model: `gpt-5.6-sol-high`

An incident investigator is read-only and does not count as a generation lane.

## Start or resume

1. Verify every file against `BASELINE_MANIFEST.json` and record its
   `source_commit`. The tree hash is SHA-256 over canonical JSON mapping each
   relative file path to its SHA-256; exclude `BASELINE_MANIFEST.json` itself.
2. Run `python3 -m pytest -q tests/test_recovery_worker.py` locally.
3. Confirm there is no existing recovery process or reporting loop.
4. Start all three VMs and wait until SSH works.
5. Read the persisted queues, lane summaries, checkpoints, deliveries, and
   quarantine records on every VM. Do not infer completion from an old status.
6. Reconcile all unfinished rows before repartitioning:
   - include every source-bound row without a complete valid delivery;
   - keep Batch 002 and Batch 003 separate because their approvals differ;
   - remove duplicates without dropping the first durable row record;
   - make three Batch 002 queues and three Batch 003 queues;
   - assign one queue from each batch to each VM.
7. Copy a clean archive of the manifest-verified local snapshot to a fresh
   baseline directory on each
   VM. Never copy `.env`, credentials, caches, generated outputs, or local
   worktrees. Link the VM's existing runtime environment without reading it.
8. Generate a baseline hash manifest from the deployed files. Verify the local
   worker, tests, overlay, snapshot tree hash, and source commit against every
   VM.
9. Run the recovery-worker tests on every VM.
10. Launch exactly two tmux worker sessions per VM with separate queue, state,
    worktree, and log roots. Use the correct approved inventory and approval ID
    for each batch.
11. Smoke-check all six sessions. Confirm one generation agent per session,
    six distinct current rows, queue locks, checkpoint creation, and unchanged
    queue hashes.

Do not reset or delete prior lane state. Adopt a retained worktree only when its
checkpoint, source hash, baseline hash, and marker all match. Otherwise
quarantine it and keep the row active.

## Isolation contract

The initial clean deployment is the only automatic fleet-wide broadcast.
After launch:

- Every task and pipeline fix stays inside that workbook's retained worktree.
- Never copy a worktree edit into another worktree, lane, VM, or the shared
  startup baseline.
- Never pause healthy sibling lanes for a task-local or pipeline failure.
- Test only the active workbook and its failed gate. Do not regression-test a
  task-local fix against other workbooks.
- A repeated fingerprint on another lane is a separate incident. Diagnose and
  fix it in that lane's worktree instead of distributing the earlier patch.
- A controller-only worker defect may use a lane-local worker overlay copied
  from the immutable startup baseline. Keep `RECOVERY_BASELINE_REPO` and
  `RECOVERY_CODE_BASELINE` bound to the unchanged startup baseline, launch the
  worker module from the overlay, and restart only that lane.
- Never automatically promote an isolated fix into local FCP2 or redeploy it
  fleet-wide. Promotion and fleet redeployment require an explicit user
  request after the task has been delivered and its evidence preserved.

## Failure loop

The worker must stop on the first unfinished row. For each new fingerprint:

1. Preserve the release, candidate, logs, checkpoint, code diff, and exact
   error.
2. Launch a fresh read-only incident investigator.
3. Decide whether the defect is in task/pipeline code or in the controller
   worker itself. This classification controls only where the isolated fix is
   applied; it never authorizes broadcasting.
4. For a task or pipeline cause, keep fixes inside that workbook's retained
   worktree, rerun only its failed gate, inspect again, and require fairness
   when code or assumptions changed.
5. For a controller worker defect:
   - mark the row `worker_fix_needed`;
   - pause only that lane and record its generation-agent process group;
   - terminate and rescan only that lane's detached generation processes;
   - create a fresh lane-local worker overlay from the immutable startup
     baseline and apply the smallest controller fix there;
   - add a recurrence test in the overlay and run the focused worker suite;
   - keep the task worktree, checkpoint, source binding, startup baseline, and
     code-baseline manifest unchanged;
   - launch that lane's worker from the overlay while retaining the original
     `RECOVERY_BASELINE_REPO` and `RECOVERY_CODE_BASELINE`;
   - verify exactly one generation agent for the restarted lane and leave the
     other five lanes untouched.
6. Never advance because an agent exited, printed `HARD`, skipped a gate, or
   produced only a partial task.

Continue until the row has a complete immutable candidate, required fairness
passes, delivery is valid, and the incident report is durable.

## Report every 20 minutes

Arm one local recurring loop. Run the report once immediately, then every 20
minutes. Do not create duplicate loops.

For each of the six active rows, inspect and report:

- VM, lane, batch, workbook ID, elapsed time, process health;
- cumulative attempt and current recovery-ladder step;
- latest agent action from the attempt stream, not just file age;
- source, AST, segmentation, task-generation, inspect, fairness, delivery, and
  investigator state;
- retained worktree changes and newly created artifacts;
- exact blocker or progress since the prior report;
- whether intervention was applied and what runs next.

Also report fleet totals: complete deliveries, candidates awaiting fairness,
`recovery_pending`, `task_fix_needed`, `fairness_retry`,
`worker_fix_needed`, quarantined rows, and remaining unique queue rows.

If a row has no meaningful progress for one interval, inspect its process tree
and latest stream. Diagnose before calling it stalled. If it is stalled, retain
its state, restart only that lane, and continue the same row.

## Stop

On a stop request, terminate the reporting loop and record active generation
PIDs. Stop the six tmux sessions and preserve state. Rescan for helper agents
spawned during shutdown, terminate every recovery generation process group,
and require two consecutive zero-agent scans before stopping the three VMs.

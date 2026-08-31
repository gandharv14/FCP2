---
name: run-harbor-recovery-fleet
description: Deploys local FCP2 to the three Harbor recovery VMs, launches exactly two recovery agents per VM, investigates every failure or skip, and reports task-level progress every 20 minutes. Use when starting, resuming, or monitoring the Harbor workbook recovery fleet.
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
- Local source of truth: `/Users/henryhu/Documents/GDM_FCP/FCP2`
- Worker: `scripts/batch/recovery_worker.py`
- Concurrency: exactly two generation lanes per VM, six total
- Model: `gpt-5.6-sol-high`

An incident investigator is read-only and does not count as a generation lane.

## Start or resume

1. Confirm the local checkout is clean and record `HEAD`.
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
7. Copy a clean archive of local `HEAD` to a fresh baseline directory on each
   VM. Never copy `.env`, credentials, caches, generated outputs, or local
   worktrees. Link the VM's existing runtime environment without reading it.
8. Generate a baseline hash manifest from the deployed files. Verify the local
   worker, tests, overlay, and commit hash against every VM.
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

## Failure loop

The worker must stop on the first unfinished row. For each new fingerprint:

1. Preserve the release, candidate, logs, checkpoint, code diff, and exact
   error.
2. Launch a fresh read-only incident investigator.
3. Decide whether the cause is task-local or shared.
4. For a task-local cause, keep fixes inside that workbook's retained
   worktree, rerun the failed gate, inspect again, and require fairness when
   code or assumptions changed.
5. For a shared worker or pipeline defect:
   - mark the row `worker_fix_needed`;
   - pause all six tmux sessions before replacing the shared baseline;
   - record every generation-agent PID and process group before the pause;
     workers launch agents in new sessions, so stopping tmux alone may leave
     detached agents alive;
   - after tmux has stopped, scan the process table again. Include any helper
     generation agents spawned between the first snapshot and the pause;
   - terminate every recorded or newly discovered recovery generation process
     group. Rescan until zero remain before deployment;
   - reproduce and fix it in local FCP2 first;
   - add a recurrence test and run the full focused suite;
   - deploy the exact tested files to all VMs;
   - verify hashes, then resume the same row;
   - after relaunch, count processes directly and require exactly two current
     generation agents on each VM. Remove any stale pre-deployment process
     group before allowing the fleet to continue.
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

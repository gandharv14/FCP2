---
name: run-fcp-workbook-fleet
description: Runs a folder of financial workbooks through the Harbor recovery worker on one or many GCP VMs. Probes hosts (busy, fresh, idle), bootstraps blank VMs, clears disk caches only after the user confirms, builds size-sorted queues (smallest workbook first), deploys memory-governed lanes, and reports per-VM, per-lane progress every 10 minutes. Use when asked to start, deploy, clean, monitor, or stop an FCP workbook run on hhu-server-style VMs, for a single VM or a fleet.
disable-model-invocation: true
---

# Run FCP workbook fleet

Input: a folder of workbooks, a list of VM hostnames, and the approval batch
the workbooks belong to. Output: recovery deliveries on each VM plus a
10-minute report. All scripts live in `scripts/`; run them from any directory.
Nothing here edits `scripts/batch/` or the graders.

Facts that shape every step:

- The worker only runs a `.xlsx` run source. For `xlsm`/`xls`/`xlsb` originals
  the folder must also hold the converted `<id>.xlsx`; the queue row binds both.
- The worker refuses any workbook the pinned inventory does not list. With
  today's approvals that is 19 rows for Batch 002
  (`batch-002-restricted-native-19-v3`) and 33 for Batch 003
  (`batch-003-restricted-native-33-v3`), whatever the folder size.
- Rows are ordered by original file size, smallest first, per lane.
- One host is a single-machine run; more hosts are a fleet. Hosts are Linux VMs
  with systemd and cgroup v2. The Mac is never a host.
- A VM with a live worker or a held `worker.lock` is `busy`, whoever owns it,
  and is never deployed to or cleaned. The VM 1-13 run in progress is `busy`.

## Inputs to collect first

| Input | Example | Notes |
| --- | --- | --- |
| Workbook folder | `.../FCP Workbooks/5-2 300/Batch 002_300 Models_05.01.26` | searched recursively |
| Hosts | `hhu-server-14 hhu-server-15` or `--discover hhu-server` | one host = single machine |
| Approval batch ID | `batch-002-restricted-native-19-v3` | must exist in `verification_manifests/approved_source_inventories.v1.json` |
| Row batch ID | `batch-002` | only an output path segment |
| Lanes per VM | `2` (default) | 3 on 128 GB hosts needs the calibration evidence in [reference.md](reference.md) "Lane count"; 4 is for 512 GB hosts |
| Run tag | `date +%Y%m%d-%H%M` | names every directory on the VM |

Local run directory: `~/fcp-fleet-runs/<tag>/` (`RUN_DIR` below). Everything
the run needs, and every report, lands there.

`FLEET_PROJECT` (default `saleseng`) and `FLEET_ZONE` (default
`us-central1-a`) are environment variables; set them for another project.

## Workflow

```
S=<FCP_recursive_v1>/.cursor/skills/run-fcp-workbook-fleet/scripts
RUN_DIR=~/fcp-fleet-runs/$(date +%Y%m%d-%H%M); mkdir -p $RUN_DIR
```

### 1. Probe hosts (read-only)

```
bash $S/fleet_hosts.sh --out $RUN_DIR/fleet.json HOST [HOST...]
```

Prints one line per host: `busy | fresh | idle | unreachable`, owner of any
live worker, memory, free disk, and every run found under `$HOME/GDM_FCP`
with its unfinished row count. Then:

- `busy`: leave it alone. If the user says it is theirs and finished, they can
  stop it themselves first; this skill never stops another run.
- `unreachable`: report it and continue with the rest.
- `fresh`: the `missing` list says what bootstrap must add.

### 2. Bootstrap fresh hosts

```
bash $S/bootstrap_vm.sh HOST --env-file <repo parent>/.env
```

Installs `python3-venv pip tmux docker.io git`, the Cursor agent CLI, the
`.env`, and applies `harbor_host_protections.sh` (requires `HOSTOK`). It does
not create GCP instances. Exit 3 means the one manual step is left: the user
runs `~/.local/bin/agent login` on that VM. Re-run step 1 until the host
reads `idle`. Never read or print the `.env`.

### 3. Clean disk caches (confirmation required)

```
bash $S/clean_vm.sh HOST                     # phase 1: list only
```

Show the user the full list per host (kind, GiB, path) and the `TOKEN`.
Ask: "Delete these N items (X GiB) on HOST?" Only after an explicit yes:

```
bash $S/clean_vm.sh HOST --apply --token <TOKEN>
```

Phase 2 recomputes the list and refuses if it changed. Candidates are caches
(`__pycache__`, `.pytest_cache`, pip cache, apt cache, journal, dangling
docker), `/tmp/fleet` uploads, and the `work-*`, baseline, and venv of
retired runs (every row `generated`). A run with unfinished rows is protected
together with the baseline and venv it binds; the user can override per run
with `--retire <run-dir-name>`. Never candidates: `output-*/`, `state-*/`,
`verification_manifests/`, `.env`, lane logs.

### 4. Build queues

```
python3 $S/build_queues.py --folder "<folder>" \
  --row-batch-id batch-002 --approval-batch-id batch-002-restricted-native-19-v3 \
  --hosts $RUN_DIR/fleet.json --lanes 2 --out $RUN_DIR
```

Uses only `fresh`/`idle` hosts from `fleet.json`. Fails with exit 2 and the
uncovered IDs when the inventory does not cover every stem; tell the user the
covered count and ask whether to proceed with `--skip-uncovered` or pick a
different approval. Exit 1 on a hash or metadata mismatch means the folder
is not the approved one; stop.

Output: `queues/<label>/queue-<lane>.json` (ascending by `source_size`),
`sources-<label>.tar` (xlsx only), `plan.json`, `coverage.json`. The
`governor` block in `plan.json` carries the memory planner's opinion
(`lanes_recommended`, `heaviest_pair_fits`); a warning there means the
admission gate will serialize the two heaviest rows, not that the run fails.

### 5. Deploy

```
bash $S/deploy_fleet.sh --run-dir $RUN_DIR --fleet $RUN_DIR/fleet.json \
  --approval-batch-id batch-002-restricted-native-19-v3 --lanes 2 [--run-tag TAG]
```

Verifies `BASELINE_MANIFEST.json` against the tree
(`baseline_manifest.py verify`; after an intended local edit run `write
--note "..."` first), runs the three worker test files, tars the baseline
without `.venv .env .git` caches, and runs `drive.sh` per idle host in
parallel. Each `deploy_<label>.log` must end with `DEPLOY_OK`. On the VM:
`$HOME/GDM_FCP/FCP_recursive_v1-<tag>` (baseline), `venv-<tag>`,
`fcp-run-<tag>-<label>` (queues, `state-*`, `work-*`, `output-*`, `logs`),
tmux sessions `fcp-<tag>-<lane>`.

Per-run safeguards go in as `--lane-env KEY=VAL` (repeatable); they are
written into that run's `run-lane-*.sh` only and never into the shared
skill. For a very large workbook (0568-class) use:

```
--lane-env RECOVERY_LARGE_WORKBOOKS=<id> --lane-env RECOVERY_LARGE_TIMEOUT_SECONDS=28800 \
--lane-env RECOVERY_TIMEOUT_SECONDS=43200 --lane-env RECOVERY_FAIRNESS_TIMEOUT_SECONDS=7200 \
--lane-env XL_SEGMENT_TIMING=1
```

(8 h inner budget, 12 h attempt clock, 2 h fairness, stage timers to the
lane log). Then start the monitor with `FLEET_INNER_BUDGET_SECONDS=28800` so
it alerts at 80% of the budget; it also alerts when a scope passes 85% of
its `MemoryMax` or when the agent stream shows `timeout ... xl_segment`.

`deploy_remote.sh` refuses when a worker is live, when the run directory
already has checkpoints, or when the agent is not logged in. A failed host
is reported, not retried automatically; fix the cause and rerun
`deploy_fleet.sh` (the per-host lock makes it idempotent).

### 6. Monitor every 10 minutes

```
bash $S/monitor.sh start --run-dir $RUN_DIR
bash $S/monitor.sh latest --run-dir $RUN_DIR
```

One local background loop per run directory (`monitor.pid`), wrapped in
`caffeinate` so the Mac stays awake; no local tmux needed. Each tick writes
`reports/<UTC>.md`. The report is Markdown tables, in this order:

1. Headline: **Total done: x/y rows (pct)**, deliveries verified, lanes in
   flight, alert count, status counts.
2. `## Lanes` table, one row per lane: VM, lane, done, current row
   (`id (n/total, size)`), status, ladder step, attempts, minutes on this
   attempt, stream age, memory stage and held GiB, deliveries, last agent
   command, remaining count with the next IDs and sizes.
3. `## VMs` table, one row per VM: done, lanes, workers, load, MemAvailable,
   disk free, budget used.
4. `## Alerts`, then `## Notes` (informational: systemic patterns, rows at
   the retry cap, lane fixes), `## Failure classes`, `## Systemic`,
   `## Lane supervisors`, `## Lane fixes`, `## Changes since previous
   report`, then the raw per-VM output inside a collapsed `<details>` block.

When summarizing for the user, paste the headline and the two tables as they
are, then every alert, then the Notes list. Do not re-narrate the tables.

Alerts to act on: stream silent > 30 min, `cgroup_oom`, `oom_score_adj
-1000`, `oomloop_hits > 0`, disk under 140 GiB, workers fewer than lanes
with no supervisor state explaining it (a lane in idle back-off, host-floor
sleep, or the fix step has no worker process on purpose), budget waiters. `worker_fix_needed`, `task_fix_needed`, `fairness_retry` and
rows at the retry cap are handled by the lane itself (immediate retry, lane
supervisor, lane fix step); they appear in Notes for awareness. The one
human decision is in "Lane fixes": whether an installed overlay should be
adopted fleet-wide (a `lane_fix_cap` alert means two automated fixes did not
stick and that lane's parked rows need a look). Diagnose per "Healthy state
and what alerts mean" and "Failure loop" in [reference.md](reference.md)
before touching a lane. Do not start a second monitor for the same tag.

To watch a run deployed by other tooling, set `RUN_PREFIX` to its directory
prefix (the VM 1-13 run uses `RUN_PREFIX=fcp-recursive-b2`).

### 7. Stop

`bash $S/monitor.sh stop --run-dir $RUN_DIR`, then on each VM stop the
`fcp-<tag>-*` tmux sessions, rescan for agents twice, confirm no `harbor-*`
scope is left and the budget file has no reservations. Never delete
`state-*` or `output-*`.

## Hard rules

- Never deploy to, clean, or stop a `busy` host. Never stop another owner's run.
- Deletion needs the user's explicit yes in chat plus the matching token.
- Queue order is original file size ascending, per lane; run source is `.xlsx`.
- Uncovered workbooks stop the run before any upload unless the user chooses
  `--skip-uncovered`.
- Fresh VMs are bootstrapped, never created. Use `$HOME`, never a user path.
- After launch, fixes stay in one workbook's worktree or one lane's overlay;
  never broadcast a fix to other lanes or hosts (Isolation contract in
  [reference.md](reference.md)).
- After editing anything in this skill, run `baseline_manifest.py write`;
  the skill files are part of the hashed baseline tree.

## Scripts

| Script | Runs where | Purpose |
| --- | --- | --- |
| `fleet_hosts.sh` / `probe_remote.sh` | local / VM | probe, write `fleet.json` |
| `bootstrap_vm.sh` | local | packages, agent CLI, `.env`, host protections |
| `clean_vm.sh` / `clean_remote.sh` | local / VM | two-phase cache cleanup |
| `baseline_manifest.py` | local | `verify` or `write` `BASELINE_MANIFEST.json` |
| `build_queues.py` | local | folder to size-sorted queues, coverage, tars |
| `deploy_fleet.sh` / `drive.sh` / `deploy_remote.sh` | local / local / VM | tar, upload, deploy, launch lanes |
| `report.py` / `report_remote.sh` | local / VM | one fleet report |
| `monitor.sh` | local | 10-minute loop, `latest`, `stop` |
| `common.sh` | local | `gssh`, `gscp`, throttling; sourced by the others |

Memory governor, lane count, the OOM inheritance trap, disk rules, host
protections, alert meanings, the isolation contract, the failure loop, and
the stop checklist are in [reference.md](reference.md), with the field names
and on-VM layout. Read them before intervening on a lane.

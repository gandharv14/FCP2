# Reference: run-fcp-workbook-fleet

## Queue row (what `build_queues.py` writes, what the worker validates)

```json
{
  "batch_id": "batch-002",
  "workbook_id": "0631",
  "run_source_path": "sources/0631.xlsx",
  "run_source_sha256": "<sha256 of the xlsx>",
  "source_format": "xlsm",
  "source_sha256": "<sha256 of the original xlsm>",
  "source_size": 2043773
}
```

- `run_source_path` is relative to the queue file's directory on the VM.
- For a native xlsx, `source_sha256 == run_source_sha256` and `source_size` is
  the xlsx size. Any other suffix as run source is refused
  (`recovery_worker._validate_source`).
- Coverage (`resolve_approved_inventory`): the inventory artifact whose sha256
  equals the approval's `inventory_artifact_sha256` must list `workbook_id`
  once with `sha256 == run_source_sha256`; the approval's
  `batch_source_ledger` entry must match `source_sha256` (xlsx),
  `original_source.sha256`, `original_source.size_bytes`, and the original's
  extension. A missing inventory entry is `inventory_missing`, an
  orchestration error; a mismatch is a `PolicyViolation`.

## fleet.json (from `fleet_hosts.sh`)

Per host: `state` (`busy|fresh|idle|unreachable`), `busy_reason`, `missing`,
`user`, `home`, `mem_total_bytes`, `mem_avail_bytes`, `disk_avail_bytes`,
`worker_count`, `worker_owners`, `tmux_sessions`, `tmux_other_uids`,
`harbor_scopes`, `tool_*`, `sudo_n`, `agent`
(`logged_in|installed_not_logged_in|missing`), `env_file`,
`budget_dir_owner`, `runs[]` (`run`, `lanes`, `total`, `unfinished`,
`locks_held`, `base`, `venv`, `bytes`, `retired`), `trees[]`.

`build_queues.py` labels hosts `vm1..vmN` in file order unless `label` is set.
`deploy_fleet.sh` and `report.py` use the same labels.

## On the VM

```
$HOME/GDM_FCP/
  .env                              runtime secrets (600); never read by the agent
  FCP_recursive_v1-<tag>/           immutable code baseline (.venv, .env symlinks)
  venv-<tag>/                       python runtime
  fcp-run-<tag>-<label>/
    queue-<lane>.json  sources/  plan.json  code-baseline.json  host-verify.txt
    run-lane-<lane>.sh              env for one lane (RECOVERY_* below)
    state-<lane>/                   summary.json, checkpoints/<id>.json, memory/, generation/attempts/,
                                    task-evidence/, incidents/, worker.lock, disk-reap.jsonl
    work-<lane>/task-<id>/          retained worktree (source_out, seg_out, tasks_outputs*, release_out, runs)
    output-<lane>/<batch>/<id>-outputs/ + <id>-outputs.verification.json   deliveries
    logs/lane-<lane>.log
/var/tmp/harbor-memory/<host>.json  per-host memory budget shared by lanes
/tmp/fleet/                         upload staging (deleted by the cleaner)
$HOME/GDM_FCP/.deploy-<tag>.lock    one deploy per tag per host
```

tmux sessions: `fcp-<tag>-<lane>`. Systemd scopes: `harbor-<label>-<lane>-<id>-*`.

## Lane environment (`run-lane-<lane>.sh`)

`RECOVERY_QUEUE`, `RECOVERY_BASELINE_REPO`, `RECOVERY_WORK_ROOT`,
`RECOVERY_STATE`, `RECOVERY_OUTPUT`, `RECOVERY_CODE_BASELINE`,
`RECOVERY_MODEL`, `RECOVERY_AGENT`, `RECOVERY_INVENTORY`,
`RECOVERY_APPROVAL_BATCH_ID`, `RECOVERY_LANE_COUNT`, `RECOVERY_LANE_ID`
(`<label>-<lane>`), `RECOVERY_MEMORY_GOVERNOR=1`,
`RECOVERY_MEMORY_BUDGET_DIR`, `RECOVERY_MEMORY_HOST_BYTES`, optional
`RECOVERY_TIMEOUT_SECONDS`. `RECOVERY_MODEL` is `gpt-5.6-sol-high` unless
`deploy_fleet.sh --model` says otherwise. Defaults for everything else are
the worker's (`Config.from_env` in `scripts/batch/recovery_worker.py`).

## Progress fields (checkpoint and summary)

- `summary.json`: `counts` by status, `tasks.<id>.status`, `current`.
- Statuses: `recovery_pending`, `task_fix_needed`, `fairness_retry`,
  `worker_fix_needed`, `disk_exhausted`, `generated` (finished).
- `checkpoints/<id>.json`: `status`, `strategy` (ladder step),
  `cumulative_attempt_count`, `attempt_in_progress.{attempt_number, strategy,
  started_at}`, `last_attempt.{status, execution_limits.limit_reason}`,
  `inspect_result`, `fairness_result` (with `verdict`, the parsed block),
  `incident_reports` (with `verdict.route`), `verifier_errors`,
  `instruction_mode` (`naturalize` | `gdpval_prose`, pinned on the first
  attempt; a relaunch with the other flag follows the recorded mode),
  `disk_reaps`.
- `failures/<id>.json`: the failure record (class, subclass, gate,
  signature, lifetime_attempts); see "Failure loop".
- `lane-supervisor.jsonl`, `lane-fix/`, `overlay/`: supervisor log, lane
  fix attempts and alert, installed worker overlay.
- Ladder (`RECOVERY_LADDER`): normal path, smallest task-local fix, disclosed
  assumption, prune only verifier-implicated outputs (`xl_segment.py
  --prune-failing-outputs`), naturalization reconstruction, different
  strategy.
- Live agent action: newest `generation/attempts/<id>/attempt-NNNN/*.jsonl`
  (the report shows its age, recent event types, last tools and commands).
- Memory: `state/memory/stages.jsonl` (`stage`, `held_bytes`),
  `observations.jsonl` (`observed_peak_bytes`, `limit_reason`).

## Report line grammar (`report_remote.sh`)

```
HOST <name> user= load= memavail_GiB= uptime_h=
PROCS tmux=<fcp-<tag>-* sessions> workers=<live workers> containers=
SCOPE <unit> <state> anon_GiB= peak_GiB= max_GiB= events[...] oom_score_adj=[...]
LANE <lane> done=<x>/<y> counts={...} row=<id> (<n>/<y>, <KB>) status= cum_attempts= attempt= elapsed_min= ladder= last_attempt= limit_reason= inspect= fairness=
  remaining=<n> next=[(id, KB), ...]
  artifacts=[...] newest=<path> (<s> ago)
  stream_bytes= age_s= recent_types={...} tools=[...] last_cmds=[...]
  mem_stage= held_GiB= at=
  observations=<n> last=...
  evidence <id>: <error or generated or inventory_missing>
  incidents_completed=<n>   disk_reaps=<n>
  deliveries=<n>
BUDGET used_GiB=<used>/<total> res={lane: (id, GiB)} waiters=[(lane, id, "<m> min")]
DISK <free> kern=<bytes> syslog=<bytes> oomloop_hits=<n>
REPORT_DONE
```

## Cleaner classes (`clean_remote.sh`)

| kind | path | when |
| --- | --- | --- |
| `upload` | `/tmp/fleet` | always (never the `.deploy-*.lock`) |
| `pipcache` | `~/.cache/pip`, `~/.cache/uv` | always |
| `pycache` | `__pycache__`, `.pytest_cache` under `$HOME/GDM_FCP` | outside protected trees |
| `retired_work`, `staging` | `work-*` and `.source-build-*` etc. of retired runs | run retired or `--retire` |
| `stale_tree` | `FCP_recursive_v1-*`, `venv-*` | bound by no protected run |
| `aptcache`, `journal`, `docker` | system caches | over 1 MiB / 300 MiB / any dangling |

Protected: any run with unfinished rows or a held `worker.lock`, plus the
`RECOVERY_BASELINE_REPO` and `VIRTUAL_ENV` its `run-lane-*.sh` names. Never:
`output-*`, `state-*`, `verification_manifests`, `.env`, `logs`.

## Row completion policy

A queue row is unfinished until a source-bound, complete delivery passes every
required check (inspect, fairness when code or assumptions changed, delivery
validation). A failure, skip, timeout, `HARD` line, fairness failure, or
investigator finding keeps the same row active; the worker retries it on the
next ladder step (`RECOVERY_LADDER`). Nothing here treats an agent exit, a
printed `FULL_RERUN_BLOCKER: HARD`, a skipped gate, or a partial bundle as
done. The read-only incident investigator is not a generation lane and does
not count toward `RECOVERY_LANE_COUNT`, but it still uses memory, and the row
that launched it keeps its budget reservation while it runs.

## Memory governor

Hosts have no swap. When anonymous memory runs out there is no slow phase:
the kernel OOM killer fires, and `sshd` is an ordinary candidate. Never
launch a lane on a host whose protections have not printed `HOSTOK`.

- Budget: one JSON file per host, `RECOVERY_MEMORY_BUDGET_DIR/<host>.json`
  (default `/var/tmp/harbor-memory`), guarded by `flock` and shared by every
  lane on the host whatever its state root. `total_bytes = MemTotal - reserve`
  with `reserve = max(8 GiB, 12% of MemTotal)`; the reserve is never handed
  out. Each host derives its own file from its own `MemTotal`; never copy one
  between hosts. `RECOVERY_MEMORY_HOST_BYTES` overrides `MemTotal` for tests.
- Lane identity: `RECOVERY_LANE_ID` is the key under which a lane holds its
  reservation. Two lanes with the same ID overwrite each other. The deploy
  scripts set it to `<label>-<lane>`; when unset the worker derives it from
  the state root.
- Prediction: `scripts/batch/memory_governor.py` probes the workbook zip
  (sheets, formula cells, expanded bytes) in seconds, applies a seed cost
  model and a learned scalar. A workbook that cannot be probed reserves the
  whole budget and runs alone.
- Admission: a lane reserves before it starts a row and waits in ticket order
  when the budget is full (the report shows these as budget waiters). A row
  that alone exceeds the budget is clamped to the budget instead of
  deadlocking.
- Row scope: one reservation covers the whole row and is resized between
  stages (`state/memory/stages.jsonl`), never released early. Inspect, the
  investigator, the fairness verifier, and the quarantine and delivery copies
  all run under it.
- Enforcement: each generation attempt runs in a transient
  `systemd-run --user --scope` with
  `MemoryMax = min(total, max(1.5 x reservation, (total - 12 GiB) / lanes))`.
  The 12 GiB is `RECOVERY_MEMORY_UNSCOPED_ALLOWANCE_BYTES`, kept back for the
  worker, verifier, investigator, and copies that run outside scopes.
  `MemoryHigh` equals `MemoryMax`: there is no throttle band, because the
  footprint is a Python object graph and nothing in a band is reclaimable
  (the first 0568 smoke spent six minutes and 9.5k `memory.high` events in a
  95% band on the way to the same kill). A child that does not fit is
  OOM-killed in minutes, the attempt records `limit_reason: cgroup_oom`, and
  the retry prompt tells the agent to shrink the cone. Caps sum to under the
  budget, so a wrong estimate kills one agent, not the host. Hosts without
  cgroup delegation fall back to process-group sampling.
- Calibration: `state/memory/observations.jsonl` per lane plus a host-shared
  copy under the budget dir record each attempt's anonymous peak
  (`memory.stat anon`, not `memory.peak`, which counts page cache). Only
  completed attempts set the scale; killed, stalled, and timed-out attempts
  are censored lower bounds that can raise it but never lower it. Three
  samples move the scale off the seed prior. Sharing this file is measurement,
  not code, and does not break the isolation contract.
- Stale reservations: a reservation whose pid is gone is reaped on the next
  acquire. It is not a leak and should not be edited by hand.
- By hand: `python3 -m scripts.batch.memory_governor --budget-dir
  /var/tmp/harbor-memory report` prints the budget file (used vs total,
  reservations, waiters); `report_remote.sh` runs it for the `BUDGET` line.
  Global options (`--budget-dir`, `--host-bytes`, `--observations`) go before
  the subcommand.

### Lane count

`LANE_CAP = 4` (`memory_governor.py`) and `GENERATION_LANE_COUNT = 4`
(`recovery_worker.py`). The cap is a ceiling; the planner (`build_queues.py`
calls `memory_governor plan --lane-cap <lanes>` per host) and the runtime
admission gate pick the real number per host from the heaviest-N fit and the
disk formula. The `governor` block in `plan.json` records `lanes_recommended`
and which limit bound it: `memory_lanes` (heaviest-N fit) or `disk_lanes`
(disk formula). By hand: `python3 -m scripts.batch.memory_governor
--host-bytes <MemTotal> plan --queues <queue.json ...> --vms 1 --lane-cap N
--disk-free-bytes <df -B1 --output=avail />`.

- 128 GB hosts (16 vCPU, ~110 GiB budget): start with 2 lanes. The 2026-09-02
  one-lane smoke showed a lane's real peak is `xl_segment.py` on the full AST
  (101+ GB on 0568), not the 6-8 GB AST build the earlier fleet saw under a
  13.6 GB cap that forced the agent to shrink the AST first. Two lanes at
  ~49 GiB each (110 GiB minus the 12 GiB allowance, halved) is what the data
  supports. Go to 3 only when all of these hold: the host-shared
  observations file has at least three completed attempts from the current
  baseline whose heaviest three anonymous peaks sum below `total_bytes` minus
  12 GiB; a direct one-lane smoke with a 3-lane-sized cap
  (`RECOVERY_LANE_COUNT=3`, `MemoryMax` ~33 GiB) ran `xl_segment` on a
  0568-class AST to completion with `memory.events oom_kill = 0`; and the
  disk allows it.
- 512 GB hosts (`hhu-server-14`, 443 GiB budget): 4 lanes at ~108 GiB each,
  the whole budget of a 128 GB host per lane. One measurement says a
  0568-class row fits such a lane: on 2026-09-04, one lane on
  `hhu-server-14` under the 443 GiB cap ran `xl_segment.py` on the full
  40-output 0568 cone to completion in 8,344 s with a 106.4 GiB anonymous
  peak (110.2 GiB `memory.peak`), recorded as `completed` in
  `~/GDM_FCP/fcp-run-0568-v2-vm1/state-a/memory/observations.jsonl` on that
  host, not in this tree. The same row on the pre-2026-09-04 graph code
  timed out at 158.5 GiB anon on the same host
  (`fcp-run-0568-vm14-vm1`), and the older direct runs in the
  `memory_governor.py` seed-model comment were killed at a 106 GB cap and at
  the 118.9 GB whole budget. This is one sample, right at a 108 GiB lane's
  cap: check the row's `observations.jsonl` peak before assuming a heavier
  workbook fits; otherwise expect `cgroup_oom` and a cone shrink.
- Disk formula, any host: `lanes <= (free - 120 GiB) // 60 GiB`
  (`LANE_DISK_FLOOR_BYTES`, `LANE_DISK_BYTES`). A 290 GB disk carries two.
- Changing the lane count is a redeploy, not a live change: stop the lanes,
  rebuild the queues with the new `--lanes`, redeploy with
  `RECOVERY_LANE_COUNT` matching. A lane added this way gets fresh
  `state-<lane>`/`work-<lane>` roots; never seed them from a sibling.

### The OOM inheritance trap

`oom_score_adj` is inherited across fork and exec. `sshd` runs at -1000, and
every lane is launched from an SSH session through tmux, so without a reset
the worker, the generation agent, and every `xl_*.py` child are OOM-immune.
Inside a `MemoryMax` scope that is the worst state possible: the kernel finds
no killable task, prints a full OOM report, retries the charge, and loops. On
2026-09-02 that flood grew `kern.log` and `syslog` to 124-238 GB per VM,
filled three root disks, and took `sshd` down with them.

- Fix: `hardened_runner._oom_killable_command` wraps every `Popen` in a
  `/bin/sh` prologue that writes `0` to `/proc/self/oom_score_adj` and
  `exec`s the payload (raising the score is unprivileged).
  `_default_investigator_launcher` uses the same wrapper.
- Proof, not trust: the worker runs `oom_score_adj_self_test()` at startup
  (raise a parent to 500; the wrapped child must read `0`) and refuses to
  start when scopes are enabled and it fails. Each scoped attempt records
  `child_oom_score_adj` in `execution_limits`; it must be `0`.
- `OOMPolicy=kill` on the scope (systemd >= 253, probed) is defense in depth.
  It acts after the kernel picks a victim; the incident had none.
- Signature in `kern.log`: `Out of memory and no killable processes`. One
  line is a bug; a stream of them is this incident again. The report's
  `oomloop_hits` counts them.

## Disk

- Floor: the worker parks a row as `disk_exhausted` when free space on the
  work root is below `RECOVERY_MIN_FREE_DISK_BYTES` (default 120 GiB) and
  refuses to start below it. A parked row returns to `recovery_pending` on
  the next start. `0` disables the gate.
- Reaper: below `RECOVERY_DISK_HIGH_WATER_BYTES` (default 140 GiB) the lane
  reclaims its own parked rows before opening a new one: dead staging dirs
  first, then source generations that no pointer, seg manifest, release
  manifest, or `runs/*.json` audit names and that are neither newest nor
  younger than `RECOVERY_DISK_GENERATION_GRACE_SECONDS` (default 7200), then
  whole evidence units oldest first. It stops as soon as free space clears
  the mark. A lane never touches a sibling's work or state root. Reaps are
  noted on the checkpoint under `disk_reaps`, in `state/disk-reap.jsonl`,
  and in `state/memory/stages.jsonl` as `stage=disk_reap`.
- Always-on sweep: at row close and worktree acquisition, dead staging dirs
  (`.source-build-*`, `.source-stage-*`, `.original-source-snapshot-*`,
  `.task-stage-*`, `.release-stage-*`, `.legacy-stage-*`, `.<wb>.seg-stage-*`)
  in that worktree are removed, after checking no live process has the
  worktree as its cwd.
- By hand: `python3 -m scripts.batch.disk_reaper --work-root <work-a>
  --state-root <state-a> report|plan|reap [--apply] [--all]`. Dry-run unless
  `--apply`. `plan` says which signal kept each generation.
- Never delete: `output-*/` (deleting a delivery re-queues a finished row),
  `verification_manifests/`, or `ast/` inside a generation on its own (the
  manifest hashes it; the generation becomes unvalidatable forever). The
  cleaner (`clean_remote.sh`) enforces the same list.
- Log cap: `harbor-log-cap.timer` truncates `/var/log/syslog` and
  `/var/log/kern.log` past `HARBOR_LOG_CAP_BYTES` (default 2 GiB) every
  minute. Installed by `harbor_host_protections.sh apply`. Do not add a
  logrotate stanza for these paths: Ubuntu's `rsyslog` stanza already owns
  them and a duplicate is fatal to the whole logrotate run;
  `/etc/logrotate.d/harbor-kernel-cap` must be absent or `verify` prints
  `HOSTFAIL logcap`.

## Host protections (`HOSTOK`)

`scripts/batch/harbor_host_protections.sh apply` then `verify` must print

```
HOSTOK <ssh|sshd> oom=-1000 linger=yes cgroup=v2 freegib=<n> logcap=on childoom=0
```

meaning: the running sshd's `/proc/<pid>/oom_score_adj` is -1000 (checked on
the live process, not the unit file), the fleet user lingers, `/sys/fs/cgroup`
is cgroup v2 (a memory controller not delegated to `user@<uid>.service` is
only a `HOSTWARN`; the worker then falls back to process-group sampling),
free disk is at or above `HARBOR_MIN_FREE_GIB` (default 120), the
log-cap timer is enabled and active, and a child spawned through the OOM
wrapper reads `0` (`childoom=skip` appears only off-Linux). A recreated VM
loses all of this; `bootstrap_vm.sh` and `deploy_remote.sh` re-verify it.

## Healthy state and what alerts mean

Healthy, per host:

- `HOSTOK` from `harbor_host_protections.sh verify`.
- `/var/tmp/harbor-memory/<host>.json` has the expected `total_bytes` and one
  reservation per active lane, each under a distinct lane key.
- One live `harbor-*` scope per running generation agent
  (`systemctl --user list-units 'harbor-*'`), `memory.events` all zero
  (`high` included: the cap is a budget share, so a rising `high` means a
  row is really near its share and will become an `oom_kill`).
- The generation agent's `oom_score_adj` reads `0`, not `-1000` (find the
  pid with `pgrep -f agent` inside the scope's cgroup, then read
  `/proc/<pid>/oom_score_adj`).
- Free disk above the floor; `systemctl list-timers harbor-log-cap.timer`
  shows a next run.

Alerts the report raises, and what they mean:

- `cgroup_oom`: a killed agent, not a killed VM. The row stays active and
  retries on the next ladder step; expect the agent to shrink the cone.
- `oom_loop` (`memory.events oom` climbing with `oom_kill` at 0) or
  `oomloop_hits > 0` in `kern.log`: the inheritance trap. Stop that lane
  before it floods the disk; check `child_oom_score_adj`.
- `oom_score_adj -1000` on an agent, or `child_oom_score_adj != 0` in an
  attempt record: same trap, caught before the loop.
- `stall` (`limit_reason: stall`): stream and cgroup CPU both flat for
  `RECOVERY_STALL_SECONDS` (default 1800). A censored observation; the row
  stays active. Read the process tree and latest stream before calling it
  stuck.
- Budget waiter: a lane holding a ticket because the budget is full. Normal
  when a heavy row is running; act only if it waits past the row's expected
  time.
- `observed_peak_bytes` (anon) far below `cgroup_peak_bytes` (`memory.peak`):
  page cache from streaming the AST, not a wrong estimate.
- Disk under 140 GiB: the reaper is about to run; under 120 GiB rows park as
  `disk_exhausted`.
- Workers fewer than lanes: a lane process died; read `logs/lane-<lane>.log`.

## Isolation contract

The initial deployment is the only automatic fleet-wide broadcast. After
launch:

- Every task and pipeline fix stays inside that workbook's retained worktree
  (`work-<lane>/task-<id>`). Never copy a worktree edit into another
  worktree, lane, host, or the shared baseline.
- Never pause healthy sibling lanes for a task-local or pipeline failure.
- Test only the active workbook and its failed gate. Do not regression-test a
  task-local fix against other workbooks.
- A repeated fingerprint on another lane is a separate incident. Diagnose and
  fix it in that lane's worktree instead of distributing the earlier patch.
- A controller-only worker defect may use a lane-local worker overlay copied
  from the immutable baseline (`FCP_recursive_v1-<tag>`). Keep
  `RECOVERY_BASELINE_REPO` and `RECOVERY_CODE_BASELINE` bound to the
  unchanged baseline, launch `scripts.batch.recovery_worker` from the
  overlay, and restart only that lane.
- Never promote an isolated fix into the local tree or redeploy it fleet-wide
  on your own. Promotion needs an explicit user request after the task has
  been delivered and its evidence preserved; then `baseline_manifest.py
  write` and a new run tag.
- Retained worktrees: on restart, adopt a worktree only when its checkpoint,
  source hash, baseline hash, and marker all match. Otherwise quarantine it
  and keep the row active. Never reset or delete prior lane state.
- Shared on purpose, not a violation: the per-host budget file (host-local
  scheduling state the lanes must agree on) and `observations.jsonl`
  (measurement).

## Failure loop

Nothing here needs a human in the loop; the human reads the report and
decides whether a lane fix should become fleet-wide. Mechanics:

**Immediate retry.** A row that does not deliver is retried on the spot by
the worker, in the same lane, until it delivers, parks on a controller
defect, or reaches the lifetime cap `RECOVERY_ROW_ATTEMPT_CAP` (default 18
generation attempts; `ATTEMPT_COUNT`, default 6, is how many the worker takes
per pickup). At the cap the row is recorded class `retry_budget` and stays
active (never `generated`, never dropped); the lane moves on. Raising the cap
or fixing the cause makes the next cycle pick it up again. A pickup that
launches no attempt at all (inventory gap, budget refusal, source drift
before resume) is not retried blindly: the row waits for the next cycle.

**Failure record.** Every non-delivered close writes
`state-<lane>/failures/<wb>.json` (`recovery-failure/v1`): `class` (closed
vocabulary: `inspect`, `retention`, `recovery_record`,
`instruction_metadata`, `fairness_semantic|leak|fidelity`,
`verifier_error`, `delivery`, `policy_violation`, `retry_budget`,
`agent_hard_blocker`, `generation_failed`, `disk_exhausted`,
`orchestration`, `shared_pipeline_defect`, `controller`, `unknown`),
`subclass`, `gate` (SKILL.md gate), a `signature` stable across hashes,
paths and counters, `lifetime_attempts`, `occurrence_count`,
`instruction_mode`. The fleet report keys on this file; the retry prompt's
re-entry text (gate to re-enter, artifacts to rebuild) comes from it.

**Structured verdicts.** The fairness verifier ends with a
`recovery-fairness/v1` JSON block (verdict, one value per check, findings
with gate) and a `FAIRNESS_VERDICT:` line that must agree with it. Missing
or disagreeing output is a verifier error: one synchronous rerun, then class
`verifier_error` with no generation retry chasing a phantom defect. The
investigator ends with a `recovery-incident-verdict/v1` block whose `route`
(`worktree_fix`, `paperwork`, `cone_prune`, `shared_pipeline_defect`,
`controller`, `analysis_only`) is what the worker acts on; only `controller`
with an agreeing `INCIDENT_VERDICT: WORKER_FIX_NEEDED` line parks the row.

**Recovery evidence.** Before the verifier runs, the controller writes
`state-<lane>/recovery-evidence/<wb>/<bundle>.json`: attempt outcomes,
shell commands and file writes parsed from the agent streams, changed files
from the code diff, incident verdicts. The verifier judges a.md against it;
delivery copies it to `<wb>-outputs.recovery-evidence.json` and binds its
hash in the sidecar. It never goes inside the bundle.

**Lane supervisor** (`scripts/batch/run_lane.sh`, what the tmux lane runs).
Reads the worker exit code: 0 all generated (lane done); 1 rows remain
(rerun; back off up to 1 h when the ledger did not change); 2 host floor
(disk or evidence space; sleep `RECOVERY_LANE_FLOOR_SLEEP`, default 10 min,
rerun); 3 a new controller fingerprint this run (run the lane fix step, then
rerun). `state-<lane>/lane-supervisor.jsonl` is its log; touching
`state-<lane>/lane.stop` ends the loop.

**Lane fix step** (`scripts/batch/lane_fix.py`, exit 3 only). Gathers the
lane's unresolved `controller`-class failures and investigator reports,
refuses when the same signature set was already worked
`RECOVERY_LANE_FIX_MAX` (2) times, copies the code tree to
`state-<lane>/lane-fix/<fix_id>/work/` (manifests and `.venv` linked; output
trees skipped), hashes the baseline, `code-baseline.json`, every task
worktree and the lane's checkpoints, takes the per-host lock
(`RECOVERY_LANE_FIX_LOCK`, default `/var/tmp/harbor-lane-fix.lock`, so one
fix agent runs per host), stops any lingering `harbor-<lane>-*` scope, and
runs one agent in the copy under a governor `hold`. It accepts the result
only if the guarded hashes are unchanged, nothing outside `scripts/batch/`
and `tests/` changed in the copy, and the worker suites pass there. It then
installs `work/scripts` as `state-<lane>/overlay/`; the supervisor puts the
overlay ahead of the baseline on `PYTHONPATH`, and the worker's build id (a
hash of the loaded batch modules) changes, which releases the rows parked
under the old build. The baseline is never written by the step; deleting the
overlay reverts. `state-<lane>/lane-fix/ALERT.json` kinds: `lane_fix_failed`
(agent, diff or tests), `lane_fix_cap` (two fixes did not stick),
`lane_fix_guard_violation` (the agent wrote outside its copy; the alert
lists the paths to restore from the deploy tarball). The report shows them.
Adopting a lane's overlay fleet-wide is a human decision made from the
report's "Lane fixes" table.

A row whose close is class `controller` by the mechanical rule (an
unexpected exception in worker code, a non-`RecoveryError` from inspect) is
parked as `worker_fix_needed` whatever the investigator's route said;
retrying generation against a worker bug only burns attempts. A controller
verdict from the post-delivery recurrence review is informational only: the
row is delivered and there is no failure record to fix.

**Systemic patterns** are informational. The report's "Systemic" table
groups failure signatures across lanes, split by instruction mode, and
flags one seen on 2+ VMs or 3+ rows. Nothing blocks on it.

**Manual overlay (only if the automated step is refused).** Same rules as
the automated one: fix in a copy, run the focused suite with
`PYTHONDONTWRITEBYTECODE=1 -p no:cacheprovider`, install as
`state-<lane>/overlay/scripts/`, never touch the baseline, checkpoint,
source binding, or `code-baseline.json`. Never advance a row because an
agent exited, printed `HARD`, skipped a gate, or produced a partial task.

## Stop checklist

`monitor.sh stop`, then on each host: stop the `fcp-<tag>-*` tmux sessions,
rescan for helper agents spawned during shutdown, terminate every recovery
generation process group, and require two consecutive zero-agent scans.
Confirm no `harbor-*` scope is left (`systemctl --user list-units
'harbor-*'`) and that the budget file has no reservations (a stale one is
reaped on the next acquire, so it is not a leak, but it should not be there
after a clean stop). Never delete `state-*` or `output-*`.

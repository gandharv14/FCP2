#!/bin/bash
# Runs ON a VM (fed over stdin by report.py). Read-only. One run, all lanes.
# usage: bash -s LABEL RUN_TAG   (env RUN_PREFIX overrides the default fcp-run directory prefix)
set -u
LAB="$1"; TAG="$2"; RUN_PREFIX="${RUN_PREFIX:-fcp-run}"
R="$HOME/GDM_FCP"; RUN="$R/$RUN_PREFIX-$TAG-$LAB"; BASE="$R/FCP_recursive_v1-$TAG"; VENV="$R/venv-$TAG"
[ -d "$RUN" ] || { echo "NO_RUN $RUN"; echo "REPORT_DONE"; exit 0; }
echo "HOST $(hostname -s) user=$(id -un) load=$(cut -d' ' -f1 /proc/loadavg) memavail_GiB=$(awk '/MemAvailable/ {printf "%.1f", $2/1048576}' /proc/meminfo) uptime_h=$(awk '{printf "%.1f", $1/3600}' /proc/uptime)"
echo "PROCS tmux=$(tmux ls -F '#S' 2>/dev/null | grep -c "^fcp-$TAG-" || true) workers=$(pgrep -fc '[s]cripts.batch.recovery_worker' || true) containers=$(docker ps -q 2>/dev/null | wc -l)"
for u in $(systemctl --user list-units 'harbor-*' --no-legend --plain 2>/dev/null | awk '{print $1}'); do
  st=$(systemctl --user show "$u" -p ActiveState --value); cg=$(systemctl --user show "$u" -p ControlGroup --value)
  cgp="/sys/fs/cgroup$cg"
  anon=$(awk '/^anon /{printf "%.1f", $2/1073741824}' "$cgp/memory.stat" 2>/dev/null)
  peak=$(awk '{printf "%.1f", $1/1073741824}' "$cgp/memory.peak" 2>/dev/null)
  max=$(awk '{ if ($1=="max") print "max"; else printf "%.1f", $1/1073741824}' "$cgp/memory.max" 2>/dev/null)
  ev=$(awk '{printf "%s=%s ", $1, $2}' "$cgp/memory.events" 2>/dev/null)
  oomadj=""; for p in $(cat "$cgp/cgroup.procs" 2>/dev/null | head -3); do oomadj="$oomadj $(cat /proc/$p/oom_score_adj 2>/dev/null)"; done
  echo "SCOPE $u $st anon_GiB=$anon peak_GiB=$peak max_GiB=$max events[$ev] oom_score_adj=[$oomadj]"
done
RUN="$RUN" python3 - <<'PY'
import json, os, glob, time, re, collections
run = os.environ["RUN"]
STAGES = [("release_out","release"),("tasks_outputs_final","final"),("tasks_outputs_mcp","mcp_task"),("tasks_outputs","task"),
          ("inputs_out_mcp","mcp_inputs"),("inputs_out","inputs"),("seg_out","segmentation"),("source_out","source"),("runs","runs")]
def load(p, default):
    try: return json.load(open(p))
    except Exception: return default
def age(p):
    try: return int(time.time() - os.path.getmtime(p))
    except Exception: return -1
for qf in sorted(glob.glob(f"{run}/queue-*.json")):
    lane = os.path.basename(qf)[6:-5]
    st = f"{run}/state-{lane}"
    queue = load(qf, [])
    order = [r.get("workbook_id") for r in queue]
    sizes = {r.get("workbook_id"): r.get("source_size", 0) for r in queue}
    summ = load(f"{st}/summary.json", {})
    counts = summ.get("counts", {}) or {}
    tasks = summ.get("tasks", {}) or {}
    done = {wid for wid, t in tasks.items() if isinstance(t, dict) and t.get("status") == "generated"}
    done |= {os.path.basename(p).split("-outputs")[0] for p in glob.glob(f"{run}/output-{lane}/*/*-outputs.verification.json")}
    cps = sorted(glob.glob(f"{st}/checkpoints/*.json"), key=os.path.getmtime)
    # Current row from summary.json.current (what the worker says it is on);
    # newest checkpoint by mtime only as a fallback for older summaries.
    current = summ.get("current")
    if current and os.path.isfile(f"{st}/checkpoints/{current}.json"):
        cps = [f"{st}/checkpoints/{current}.json"]
    line = f"LANE {lane} done={len(done)}/{len(order)} counts={json.dumps(counts, sort_keys=True)}"
    wid = None
    if cps:
        cp = load(cps[-1], {}); wid = os.path.basename(cps[-1])[:-5]
        att = cp.get("attempt_in_progress") or {}
        last = cp.get("last_attempt") or {}
        el = ""
        started = att.get("started_at", "")
        if started:
            try:
                t = time.mktime(time.strptime(started, "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
                el = f" elapsed_min={int((time.time()-t)/60)}"
            except Exception: pass
        idx = order.index(wid) + 1 if wid in order else "?"
        line += (f" row={wid} ({idx}/{len(order)}, {sizes.get(wid,0)/1024:.0f}KB) status={cp.get('status')}"
                 f" cum_attempts={cp.get('cumulative_attempt_count',0)} attempt={att.get('attempt_number','-')}{el}"
                 f" ladder={str(att.get('strategy') or cp.get('strategy'))[:60]!r}")
        if last: line += f" last_attempt={last.get('status')} limit_reason={(last.get('execution_limits') or {}).get('limit_reason', last.get('limit_reason'))}"
        if cp.get("inspect_result"): line += f" inspect={str(cp['inspect_result'].get('status', cp['inspect_result']))[:40]}"
        if cp.get("fairness_result"): line += f" fairness={str(cp['fairness_result'].get('status', cp['fairness_result']))[:40]}"
    print(line)
    remaining = [w for w in order if w not in done and w != wid]
    print(f"  remaining={len(remaining)} next={[(w, f'{sizes.get(w,0)/1024:.0f}KB') for w in remaining[:4]]}")
    if wid:
        wt = f"{run}/work-{lane}/task-{wid}"
        present = [name for d, name in STAGES if os.path.isdir(f"{wt}/{d}") and os.listdir(f"{wt}/{d}")]
        newest = ""
        try:
            files = [p for p in glob.glob(f"{wt}/runs/**/*", recursive=True) if os.path.isfile(p)]
            if files:
                nf = max(files, key=os.path.getmtime); newest = f"{os.path.relpath(nf, wt)} ({age(nf)}s ago)"
        except Exception: pass
        print(f"  artifacts={present} newest={newest}")
        streams = sorted(glob.glob(f"{st}/generation/attempts/{wid}/attempt-*/.stream.*") + glob.glob(f"{st}/generation/attempts/{wid}/attempt-*/*.jsonl"), key=os.path.getmtime)
        if streams:
            s = streams[-1]; size = os.path.getsize(s)
            with open(s, "rb") as f:
                f.seek(max(0, size - 60000)); tail = f.read().decode("utf-8", "replace")
            types = re.findall(r'"type":"([a-z_]+)"', tail)[-400:]
            tools = re.findall(r'"name":"([A-Za-z_]+)"', tail)[-6:]
            cmds = re.findall(r'"command":"((?:[^"\\]|\\.){0,120})', tail)[-2:]
            print(f"  stream_bytes={size} age_s={age(s)} recent_types={dict(collections.Counter(types).most_common(4))} tools={tools} last_cmds={[c[:120] for c in cmds]}")
        else:
            print("  stream=none")
    stg = f"{st}/memory/stages.jsonl"
    if os.path.exists(stg):
        lines = open(stg).read().strip().splitlines()
        if lines:
            o = json.loads(lines[-1]); print(f"  mem_stage={o.get('stage')} held_GiB={round(int(o.get('held_bytes',0))/2**30,1)} at={o.get('at')}")
    obs = f"{st}/memory/observations.jsonl"
    if os.path.exists(obs):
        lines = open(obs).read().strip().splitlines(); print(f"  observations={len(lines)} last={lines[-1][:200] if lines else ''}")
    for e in sorted(glob.glob(f"{st}/task-evidence/*.json"), key=os.path.getmtime)[-2:]:
        d = load(e, {}); err = (d.get("error") or "")
        tag = "inventory_missing" if "absent from the pinned approved inventory" in err else err[:180]
        print(f"  evidence {os.path.basename(e)[:-5]}: {tag}")
    inc = glob.glob(f"{st}/incidents/*/*/completed.json")
    if inc: print(f"  incidents_completed={len(inc)}")
    reaps = f"{st}/disk-reap.jsonl"
    if os.path.exists(reaps): print(f"  disk_reaps={len(open(reaps).read().strip().splitlines())}")
    sidecars = glob.glob(f'{run}/output-{lane}/*/*-outputs.verification.json')
    print(f"  deliveries={len(sidecars)}")
    # Output retention per delivery: candidates vs retained, gate result.
    for sc in sorted(sidecars):
        d = load(sc, {}); r = d.get("output_retention") or {}
        if not r: continue
        wb = d.get("workbook_id", os.path.basename(sc)[:4])
        print(f"  retention {wb}: auto={r.get('candidates_auto_included')} retained={r.get('retained')} pruned={r.get('pruned')} manual={r.get('manual_excluded')} unexplained={r.get('unexplained_excluded')} gate={r.get('gate')} cone_retry={r.get('cone_retry_used')}")
PY
# Structured failure classes, supervisor state and lane fixes (FAILURES /
# SUPERVISOR / LANEFIX lines); report.py aggregates them across VMs.
if [ -f "$BASE/scripts/batch/report_lane_state.py" ]; then
  python3 "$BASE/scripts/batch/report_lane_state.py" "$RUN" 2>/dev/null || echo "LANE_STATE_ERROR"
fi
echo "BUDGET $(cd "$BASE" 2>/dev/null && PYTHONPATH="$BASE" "$VENV/bin/python" -m scripts.batch.memory_governor --budget-dir /var/tmp/harbor-memory report 2>/dev/null | python3 -c '
import json,sys,time,calendar
s=json.load(sys.stdin)
def mins(ts):
    try: return int((time.time()-calendar.timegm(time.strptime(ts,"%Y-%m-%dT%H:%M:%SZ")))/60)
    except Exception: return "?"
print("used_GiB=%.1f/%.1f res=%s waiters=%s"%(s["used_bytes"]/2**30,s["total_bytes"]/2**30,{k:(v.get("workbook_id"),round(v["bytes"]/2**30,1)) for k,v in s["reservations"].items()},[(w.get("lane_id"),w.get("workbook_id"),"%s min"%mins(w.get("queued_at",""))) for w in s.get("waiters",[])]))' 2>/dev/null)"
echo "DISK $(df -h / | awk 'NR==2{print $4" free"}') kern=$(stat -c %s /var/log/kern.log 2>/dev/null) syslog=$(stat -c %s /var/log/syslog 2>/dev/null) oomloop_hits=$(sudo -n grep -c 'Out of memory and no killable' /var/log/kern.log 2>/dev/null; true)"
echo "REPORT_DONE"

#!/bin/bash
# Probe VMs (read-only) and write fleet.json.
#
# usage: fleet_hosts.sh --out fleet.json HOST [HOST...]
#        fleet_hosts.sh --out fleet.json --discover PREFIX     # gcloud list by name prefix
#
# States: unreachable | busy | fresh | idle
#   busy  = a recovery worker or held worker.lock exists (any owner). Never deploy or clean.
#   fresh = reachable but missing agent login, .env, venv support, or docker.
#   idle  = reachable, tools present, nothing running.
# Env: FLEET_PROJECT (saleseng), FLEET_ZONE (us-central1-a), FLEET_PARALLEL (6)
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

OUT=""; DISCOVER=""; HOSTS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --out) OUT="$2"; shift 2;;
    --discover) DISCOVER="$2"; shift 2;;
    -h|--help) sed -n '2,12p' "$0"; exit 0;;
    *) HOSTS+=("$1"); shift;;
  esac
done
[ -n "$OUT" ] || die "--out fleet.json is required"
if [ -n "$DISCOVER" ]; then
  while IFS= read -r line; do [ -n "$line" ] && HOSTS+=("$line"); done < <(
    gcloud compute instances list --project "$FLEET_PROJECT" --filter="name~^${DISCOVER} AND zone:${FLEET_ZONE}" \
      --format='value(name)' 2>/dev/null)
fi
[ ${#HOSTS[@]} -gt 0 ] || die "no hosts given"

WORK=$(mktemp -d)
echo "probing ${#HOSTS[@]} hosts (parallel $FLEET_PARALLEL) ..." >&2
for host in "${HOSTS[@]}"; do
  throttle "$FLEET_PARALLEL"
  (
    status=$(gcloud compute instances describe "$host" --zone "$FLEET_ZONE" --project "$FLEET_PROJECT" \
      --format='value(status,machineType.basename(),disks[0].diskSizeGb,labels.owner)' 2>/dev/null | tr '\t' '|')
    echo "gce=$status" > "$WORK/$host.txt"
    with_timeout 150 "${GSSH[@]}" "$host" --command 'bash -s' < "$SCRIPTS_DIR/probe_remote.sh" 2>"$WORK/$host.err" | strip_noise >> "$WORK/$host.txt"
  ) &
done
wait

python3 - "$WORK" "$OUT" "${HOSTS[@]}" <<'PY'
import json, sys, os, time
work, out, hosts = sys.argv[1], sys.argv[2], sys.argv[3:]
fleet = []
for host in hosts:
    path = os.path.join(work, host + ".txt")
    kv, runs, trees = {}, [], []
    text = open(path).read() if os.path.exists(path) else ""
    for line in text.splitlines():
        if line.startswith("run="):
            runs.append(dict(part.split("=", 1) for part in line.split("|") if "=" in part))
        elif line.startswith("tree="):
            trees.append(dict(part.split("=", 1) for part in line.split("|") if "=" in part))
        elif "=" in line and not line.startswith("PROBE"):
            k, v = line.split("=", 1); kv[k] = v
    gce = kv.get("gce", "").split("|") + ["", "", "", ""]
    entry = {"host": host, "gce_status": gce[0], "machine_type": gce[1], "disk_gb": gce[2], "owner_label": gce[3]}
    if "PROBE_END" not in text:
        entry["state"] = "unreachable"
        err = open(os.path.join(work, host + ".err")).read().strip().splitlines() if os.path.exists(os.path.join(work, host + ".err")) else []
        entry["error"] = (err[-1] if err else "no probe output")[:200]
        fleet.append(entry); continue
    ints = ("mem_total_bytes", "mem_avail_bytes", "disk_avail_bytes", "disk_size_bytes", "worker_count", "agent_count",
            "harbor_scopes", "docker_containers", "budget_files", "kern_log_bytes", "syslog_bytes", "tmp_fleet_bytes", "uptime_s")
    for k, v in kv.items():
        if k == "gce": continue
        try: entry[k] = int(v) if k in ints else v
        except ValueError: entry[k] = v
    for r in runs:
        for k in ("total", "unfinished", "locks_held", "bytes"):
            try: r[k] = int(r.get(k, 0) or 0)
            except ValueError: r[k] = 0
        r["retired"] = r["total"] > 0 and r["unfinished"] == 0 and r["locks_held"] == 0
    for t in trees:
        try: t["bytes"] = int(t.get("bytes", 0) or 0)
        except ValueError: t["bytes"] = 0
    entry["runs"] = runs; entry["trees"] = trees
    locks = sum(r["locks_held"] for r in runs)
    missing = [k for k, want in (("agent", "logged_in"), ("env_file", "yes"), ("has_venv", "yes"), ("tool_docker", "yes"), ("tool_tmux", "yes")) if entry.get(k) != want]
    if entry.get("worker_count", 0) > 0 or locks > 0:
        entry["state"] = "busy"
        entry["busy_reason"] = f"workers={entry.get('worker_count',0)} owners={entry.get('worker_owners','')} locks_held={locks}"
    elif missing:
        entry["state"] = "fresh"; entry["missing"] = missing
    else:
        entry["state"] = "idle"
    fleet.append(entry)
json.dump({"probed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "project": os.environ.get("FLEET_PROJECT"), "zone": os.environ.get("FLEET_ZONE"), "hosts": fleet}, open(out, "w"), indent=1)
w = max(len(h["host"]) for h in fleet)
for h in fleet:
    extra = h.get("busy_reason") or (",".join(h.get("missing", [])) if h.get("missing") else "") or h.get("error", "")
    mem = f"{h['mem_total_bytes']/2**30:.0f}GiB" if isinstance(h.get("mem_total_bytes"), int) else "-"
    disk = f"{h['disk_avail_bytes']/2**30:.0f}GiB free" if isinstance(h.get("disk_avail_bytes"), int) else "-"
    runs = " ".join(f"{r['run']}[{'retired' if r['retired'] else str(r['unfinished'])+' unfinished'}]" for r in h.get("runs", []))
    print(f"{h['host']:<{w}}  {h['state']:<11} {h.get('gce_status','?'):<10} {mem:>7} {disk:>12} user={h.get('user','-')} {extra} {runs}")
print(f"wrote {out}")
PY
rm -rf "$WORK"

#!/usr/bin/env python3
"""Fleet report: run report_remote.sh on every deployed host and print one
Markdown report with per-VM sections, per-lane progress, fleet totals, a diff
against the previous report, and alerts.

usage: report.py --run-dir DIR [--out FILE] [--parallel 6]
DIR is the deploy directory (holds fleet.json, RUN_TAG, deploy_<label>.log).
Hosts are those whose deploy log ends with DEPLOY_OK.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Alert thresholds. FLEET_INNER_BUDGET_SECONDS is the per-row AST/segmentation
# budget the lane was launched with (RECOVERY_LARGE_TIMEOUT_SECONDS or
# RECOVERY_MEASURED_TIMEOUT_SECONDS); unset disables the time alert.
INNER_BUDGET_SECONDS = int(os.environ.get("FLEET_INNER_BUDGET_SECONDS", "0") or 0)
TIME_ALERT_FRACTION = float(os.environ.get("FLEET_TIME_ALERT_FRACTION", "0.8"))
MEM_ALERT_FRACTION = float(os.environ.get("FLEET_MEM_ALERT_FRACTION", "0.85"))
NOISE = ("WARNING", "Warning", "External IP", "Updat", "To incr", "Please", "Existing host keys", "Writing", "Updating project ssh")


def gssh_cmd(host: str) -> list[str]:
    project = os.environ.get("FLEET_PROJECT", "saleseng")
    zone = os.environ.get("FLEET_ZONE", "us-central1-a")
    return [
        "gcloud", "compute", "ssh", host, "--zone", zone, "--project", project, "--tunnel-through-iap", "--quiet",
        "--ssh-flag=-oConnectTimeout=25", "--ssh-flag=-oServerAliveInterval=15", "--ssh-flag=-oServerAliveCountMax=4",
    ]


def targets(run_dir: Path) -> list[tuple[str, str]]:
    fleet = json.loads((run_dir / "fleet.json").read_text())
    hosts = fleet["hosts"] if isinstance(fleet, dict) else fleet
    pairs = []
    for i, h in enumerate(hosts):
        if isinstance(h, str):
            h = {"host": h}
        label = h.get("label") or f"vm{i+1}"
        log = run_dir / f"deploy_{label}.log"
        if log.is_file() and "DEPLOY_OK" in log.read_text():
            pairs.append((h["host"], label))
    return pairs


def previous_lane_lines(run_dir: Path) -> dict[str, str]:
    reports = sorted((run_dir / "reports").glob("*.md")) if (run_dir / "reports").is_dir() else []
    if not reports:
        return {}
    prev: dict[str, str] = {}
    lab = None
    for line in reports[-1].read_text().splitlines():
        if line.startswith("### "):
            lab = line[4:].split()[0]
        m = re.match(r"LANE (\w) (.*)", line)
        if m and lab:
            prev[f"{lab}-{m.group(1)}"] = m.group(2)
    return prev


def collect_structured(outputs: dict[str, str]) -> dict[str, list[dict]]:
    """Parse FAILURES / SUPERVISOR / LANEFIX lines from every VM's report output.

    Returns {"failures": [...], "supervisor": [...], "lanefix": [...]} where
    each entry carries ``vm`` and ``lane`` plus the JSON payload. Bad lines
    are skipped: the structured sections are additive to the report, never
    a reason for it to fail.
    """

    out: dict[str, list[dict]] = {"failures": [], "supervisor": [], "lanefix": []}
    for lab, text in outputs.items():
        for line in text.splitlines():
            m = re.match(r"(FAILURES|SUPERVISOR|LANEFIX) (\w+) (.*)$", line)
            if not m:
                continue
            try:
                payload = json.loads(m.group(3))
            except json.JSONDecodeError:
                continue
            kind, lane = m.group(1), m.group(2)
            if kind == "FAILURES" and isinstance(payload, list):
                for item in payload:
                    if isinstance(item, dict):
                        out["failures"].append({"vm": lab, "lane": lane, **item})
            elif kind == "SUPERVISOR" and isinstance(payload, dict):
                out["supervisor"].append({"vm": lab, "lane": lane, **payload})
            elif kind == "LANEFIX" and isinstance(payload, dict):
                out["lanefix"].append({"vm": lab, "lane": lane, **payload})
    return out


def systemic_groups(failures: list[dict], *, min_vms: int = 2, min_rows: int = 3) -> list[dict]:
    """Same failure signature seen across lanes: the fleet-wide alarm, informational.

    A group is systemic when it appears on at least ``min_vms`` VMs or on at
    least ``min_rows`` rows anywhere. Groups are split by instruction mode,
    because a gate-13 defect in prose mode says nothing about naturalize
    rows. Nothing here blocks generation; lanes keep retrying and the user
    decides whether a lane fix should be adopted fleet-wide.
    """

    groups: dict[tuple, dict] = {}
    for item in failures:
        if not item.get("class") or item.get("class") == "retry_budget" and not item.get("signature"):
            continue
        key = (item.get("instruction_mode") or "unknown", item.get("class"), item.get("subclass"), item.get("signature"))
        group = groups.setdefault(
            key,
            {"mode": key[0], "class": key[1], "subclass": key[2], "signature": key[3], "gate": item.get("gate"),
             "vms": set(), "lanes": set(), "rows": [], "example": item.get("error", ""), "occurrences": 0},
        )
        group["vms"].add(item["vm"])
        group["lanes"].add(f"{item['vm']}-{item['lane']}")
        group["rows"].append(item.get("workbook_id"))
        group["occurrences"] += int(item.get("occurrence_count") or 1)
    result = []
    for group in groups.values():
        systemic = len(group["vms"]) >= min_vms or len(group["rows"]) >= min_rows
        result.append({**group, "vms": sorted(group["vms"]), "lanes": sorted(group["lanes"]), "systemic": systemic})
    result.sort(key=lambda g: (not g["systemic"], -len(g["vms"]), -len(g["rows"]), str(g["class"])))
    return result


def render_structured(structured: dict[str, list[dict]]) -> tuple[list[str], list[str]]:
    """Markdown sections for classes / systemic / lane fixes, plus informational alerts."""

    def table(headers: list[str], rows: list[list[object]]) -> str:
        head = "| " + " | ".join(headers) + " |"
        sep = "|" + "|".join("---" for _ in headers) + "|"
        body = ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
        return "\n".join([head, sep, *body])

    failures = structured.get("failures", [])
    lines: list[str] = []
    notes: list[str] = []
    counts: Counter = Counter((f.get("class") or "unknown") for f in failures)
    by_mode: Counter = Counter(((f.get("instruction_mode") or "unknown"), (f.get("class") or "unknown")) for f in failures)
    lines += ["", "## Failure classes (rows currently not delivered)"]
    if counts:
        lines.append(table(["class", "rows", "naturalize", "gdpval_prose"],
                           [[cls, n, by_mode.get(("naturalize", cls), 0), by_mode.get(("gdpval_prose", cls), 0)]
                            for cls, n in counts.most_common()]))
    else:
        lines.append("none")
    capped = [f for f in failures if f.get("class") == "retry_budget"]
    if capped:
        notes.append("retry cap reached (rows stay active; raise RECOVERY_ROW_ATTEMPT_CAP or fix the cause): "
                     + ", ".join(f"{f['vm']}-{f['lane']}:{f.get('workbook_id')}" for f in capped[:12]))
    controller = [f for f in failures if f.get("class") == "controller"]
    if controller:
        notes.append("controller-class failures (lane fix step owns them): "
                     + ", ".join(f"{f['vm']}-{f['lane']}:{f.get('workbook_id')} {f.get('subclass')}" for f in controller[:12]))
    groups = systemic_groups(failures)
    systemic = [g for g in groups if g["systemic"]]
    lines += ["", "## Systemic (same signature across lanes; informational, nothing is blocked)"]
    if systemic:
        lines.append(table(["mode", "class", "subclass", "gate", "VMs", "rows", "occurrences", "example"],
                           [[g["mode"], g["class"], g["subclass"], g["gate"] if g["gate"] is not None else "-",
                             len(g["vms"]), len(g["rows"]), g["occurrences"], str(g["example"]).replace("|", "/")[:90]]
                            for g in systemic]))
        for g in systemic:
            notes.append(f"systemic {g['mode']}/{g['class']}/{g['subclass']} on {len(g['vms'])} VM(s), {len(g['rows'])} row(s): "
                         + ", ".join(g["lanes"][:8])
                         + " -> candidate for one representative fix in the local tree; promotion needs your explicit request")
    else:
        lines.append("none")
    churning = [f for f in failures if len(f.get("distinct_classes") or []) >= 3]
    for f in churning:
        notes.append(f"{f['vm']}-{f['lane']}:{f.get('workbook_id')} has cycled through {len(f['distinct_classes'])} failure classes "
                     f"({', '.join(f['distinct_classes'])}); the row is retrying, worth a look at its failure record")
    lines += ["", "## Lane supervisors"]
    sup = structured.get("supervisor", [])
    if sup:
        lines.append(table(["lane", "cycles", "last event", "last rc", "overlay", "fixes ok/run", "idle streak", "floor", "crashes"],
                           [[f"{s['vm']}-{s['lane']}", s.get("cycles"), s.get("last_event"), s.get("last_rc"),
                             "yes" if s.get("overlay_active") else "-", f"{s.get('fix_ok', 0)}/{s.get('fixes', 0)}",
                             s.get("idle_streak"), s.get("host_floor_events"), s.get("crashes")] for s in sup]))
        for s in sup:
            if s.get("stopped"):
                notes.append(f"{s['vm']}-{s['lane']}: supervisor exited because lane.stop is present (a human placed it)")
            if s.get("last_rc") == 2:
                notes.append(f"{s['vm']}-{s['lane']}: last worker exit was a host floor (disk/evidence); supervisor is sleeping")
    else:
        lines.append("none (lanes started before the supervisor, or no state yet)")
    lines += ["", "## Lane fixes (approve fleet-wide from here; none of this blocks generation)"]
    fixes = structured.get("lanefix", [])
    rows = []
    for f in fixes:
        overlay = f.get("overlay")
        if overlay:
            rows.append([f"{f['vm']}-{f['lane']}", overlay.get("fix_id"), ", ".join(overlay.get("changed_paths", []))[:80],
                         ", ".join(overlay.get("workbooks", []))[:40], str(overlay.get("summary") or "").replace("|", "/")[:100]])
        alert = f.get("alert")
        if alert:
            notes.append(f"{f['vm']}-{f['lane']}: lane fix {alert.get('kind')}: {alert.get('message') or alert.get('reason')}"
                         f" (workbooks {', '.join(alert.get('workbooks', []))})")
    lines.append(table(["lane", "fix id", "changed files", "rows", "agent summary"], rows) if rows else "no overlays installed")
    return lines, notes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--parallel", type=int, default=int(os.environ.get("FLEET_PARALLEL", "6")))
    ap.add_argument("--timeout", type=int, default=240)
    args = ap.parse_args()
    run_dir = args.run_dir.resolve()
    tag = (run_dir / "RUN_TAG").read_text().strip()
    pairs = targets(run_dir)
    if not pairs:
        print("no deployed hosts (no deploy_<label>.log with DEPLOY_OK)")
        return 1
    prev = previous_lane_lines(run_dir)

    outputs: dict[str, str] = {}
    pending = list(pairs)
    running: list[tuple[str, subprocess.Popen, float]] = []
    script = (HERE / "report_remote.sh").read_bytes()
    while pending or running:
        while pending and len(running) < args.parallel:
            host, lab = pending.pop(0)
            prefix = os.environ.get("RUN_PREFIX", "fcp-run")
            p = subprocess.Popen(gssh_cmd(host) + ["--command", f"RUN_PREFIX={prefix} bash -s {lab} {tag}"], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            p.stdin.write(script.decode()); p.stdin.close()
            running.append((lab, p, time.time()))
        for item in list(running):
            lab, p, t0 = item
            if p.poll() is not None:
                outputs[lab] = p.stdout.read(); running.remove(item)
            elif time.time() - t0 > args.timeout:
                p.kill(); outputs[lab] = f"TIMEOUT contacting {lab}"; running.remove(item)
        time.sleep(0.5)

    totals: Counter = Counter(); deliveries = 0; done_rows = 0; total_rows = 0; alerts: list[str] = []
    changes: list[str] = []
    lane_rows: list[dict] = []   # one per lane, for the lane table
    vm_rows: list[dict] = []     # one per VM, for the VM table
    raw_sections: list[str] = []
    for host, lab in pairs:
        out = outputs.get(lab, "")
        lines = [l for l in out.splitlines() if not l.startswith(NOISE)]
        raw_sections.append(f"\n### {lab} ({host})\n```\n" + "\n".join(lines) + "\n```")
        if "REPORT_DONE" not in out:
            alerts.append(f"{lab}: report incomplete or ssh timeout")
        if "NO_RUN" in out:
            alerts.append(f"{lab}: run directory missing")
        vm = {"vm": lab, "host": host, "load": "-", "mem": "-", "disk": "-", "workers": "-", "lanes": 0, "budget": "-", "done": 0, "total": 0}
        cur: dict | None = None
        for l in lines:
            m = re.match(r"HOST \S+ user=\S+ load=(\S+) memavail_GiB=(\S+)", l)
            if m: vm["load"], vm["mem"] = m.group(1), m.group(2) + " GiB"
            m = re.match(r"PROCS tmux=\d+ workers=(\d+)", l)
            if m: vm["workers"] = m.group(1)
            m = re.match(r"BUDGET used_GiB=(\S+)", l)
            if m: vm["budget"] = m.group(1) + " GiB"
            m = re.match(r"DISK (\S+) free", l)
            if m: vm["disk"] = m.group(1)
            m = re.match(r"LANE (\w) done=(\d+)/(\d+) counts=(\{.*?\})(.*)", l)
            if m:
                d, t = int(m.group(2)), int(m.group(3))
                done_rows += d; total_rows += t; vm["done"] += d; vm["total"] += t; vm["lanes"] += 1
                try: totals.update(json.loads(m.group(4)))
                except Exception: pass
                key = f"{lab}-{m.group(1)}"
                now_norm = re.sub(r" elapsed_min=\d+", "", l[len("LANE x "):])
                prev_norm = re.sub(r" elapsed_min=\d+", "", prev.get(key, ""))
                if prev_norm and prev_norm != now_norm:
                    changes.append(f"{key}: {l[:200]}")
                rest = m.group(5)
                g = lambda pat, default="-": (re.search(pat, rest) or [None, default])[1]
                cur = {"vm": lab, "lane": m.group(1), "done": f"{d}/{t}",
                       "row": g(r"row=(\S+)"), "idx": g(r"row=\S+ \((\d+/\d+, [\d.]+KB)\)"),
                       "status": g(r"status=(\S+)"), "ladder": g(r"ladder='([^']*)'").replace("|", "/")[:38],
                       "attempts": g(r"cum_attempts=(\d+)"), "elapsed": g(r"elapsed_min=(\d+)"),
                       "stream": "-", "last_cmd": "-", "mem": "-", "deliv": "-", "next": "-"}
                lane_rows.append(cur)
                continue
            if cur is not None:
                m = re.match(r"  stream_bytes=\d+ age_s=(\d+).*last_cmds=\[(.*)\]$", l)
                if m:
                    cur["stream"] = f"{int(m.group(1))//60}m"
                    cmds = re.findall(r"'((?:[^'\\]|\\.)*)'", m.group(2))
                    cur["last_cmd"] = (cmds[-1] if cmds else "-").replace("|", "/").replace("\\n", " ")[:60]
                m = re.match(r"  mem_stage=(\S+) held_GiB=(\S+)", l)
                if m: cur["mem"] = f"{m.group(1)} {m.group(2)}G"
                m = re.match(r"  deliveries=(\d+)", l)
                if m: cur["deliv"] = m.group(1)
                m = re.match(r"  remaining=(\d+) next=\[(.*)\]", l)
                if m:
                    ids = re.findall(r"\('(\w+)', '([\d.]+KB)'\)", m.group(2))
                    cur["next"] = f"{m.group(1)} left: " + " ".join(f"{i}({s})" for i, s in ids[:3]) if ids else f"{m.group(1)} left"
            m = re.match(r"  deliveries=(\d+)", l)
            if m: deliveries += int(m.group(1))
            # Output retention: flag deliveries that shipped past the gate on
            # override, or kept under half of the auto-included candidates.
            m = re.match(r"  retention (\w+): auto=(\S+) retained=(\S+) pruned=\S+ manual=\S+ unexplained=(\S+) gate=(\S+) cone_retry=(\S+)", l)
            if m:
                wb, auto, kept, unexplained, gate = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
                if gate in ("override_after_retry", "no_record") or (unexplained not in ("0", "None")):
                    alerts.append(f"{lab}: {wb} shipped with output retention gate={gate}, unexplained={unexplained} -> {l.strip()}")
                try:
                    if int(auto) > 0 and int(kept) / int(auto) < 0.5:
                        alerts.append(f"{lab}: {wb} kept {kept}/{auto} auto-included outputs (<50%)")
                except (TypeError, ValueError):
                    pass
            if l.startswith("PROCS"):
                w = re.search(r"workers=(\d+)", l)
                lanes_here = len([x for x in lines if x.startswith("LANE ")])
                if w and int(w.group(1)) < lanes_here:
                    # Under the lane supervisor a lane legitimately has no
                    # worker process while it sleeps (idle, host floor), runs
                    # the fix step, or backs off after a crash. Only alert when
                    # no supervisor state explains the gap.
                    supervised = 0
                    for sline in lines:
                        sm = re.match(r"SUPERVISOR \w+ (\{.*\})$", sline)
                        if not sm:
                            continue
                        try:
                            sup = json.loads(sm.group(1))
                        except json.JSONDecodeError:
                            continue
                        if sup.get("last_event") in ("idle", "host_floor", "fix_start", "fix_exit", "crash", "stop", "lane_done"):
                            supervised += 1
                    if int(w.group(1)) + supervised < lanes_here:
                        alerts.append(f"{lab}: only {w.group(1)} worker(s) alive for {lanes_here} lanes and no supervisor state explains it -> {l}")
            m = re.search(r"oomloop_hits=(\S+)", l)
            if m and m.group(1) not in ("0", ""): alerts.append(f"{lab}: {l}")
            if l.startswith("SCOPE"):
                if " failed " in l:
                    alerts.append(f"{lab}: stale failed scope {l.split()[1]} (harmless; systemctl --user reset-failed clears it)")
                elif re.search(r"oom_kill=[1-9]", l) or re.search(r"\boom=[1-9]", l) or re.search(r"\bhigh=[1-9]", l) or re.search(r"oom_score_adj=\[[^\]]*-1000", l):
                    alerts.append(f"{lab}: {l[:160]}")
                else:
                    ma = re.search(r"anon_GiB=([\d.]+) peak_GiB=[\d.]* max_GiB=([\d.]+)", l)
                    if ma:
                        anon, cap = float(ma.group(1)), float(ma.group(2))
                        if cap > 0 and anon > MEM_ALERT_FRACTION * cap:
                            alerts.append(f"{lab}: scope at {anon:.0f}/{cap:.0f} GiB ({100*anon/cap:.0f}% of MemoryMax) -> {l.split()[1]}")
            m = re.search(r"elapsed_min=(\d+)", l)
            if m and INNER_BUDGET_SECONDS and int(m.group(1)) * 60 > TIME_ALERT_FRACTION * INNER_BUDGET_SECONDS:
                alerts.append(f"{lab}: attempt at {m.group(1)} min, past {int(100*TIME_ALERT_FRACTION)}% of the {INNER_BUDGET_SECONDS // 60} min inner budget")
            if re.search(r"timeout \d+[smh]? [^\"']*xl_segment", l):
                alerts.append(f"{lab}: agent wrapped xl_segment in a timeout -> {l[:160]}")
            if re.search(r"cgroup_oom|worker_fix_needed|limit_reason=(stall|oom_loop|timeout)|fairness_retry|task_fix_needed|quarantin|disk_exhausted", l):
                alerts.append(f"{lab}: {l[:200]}")
            m = re.search(r"stream_bytes=\d+ age_s=(\d+)", l)
            if m and int(m.group(1)) > 1800: alerts.append(f"{lab}: agent stream silent for {int(m.group(1))//60} min -> {l[:120]}")
            m = re.search(r"DISK (\S+) free", l)
            if m and m.group(1).endswith("G") and float(m.group(1)[:-1]) < 140: alerts.append(f"{lab}: disk {m.group(1)} free is near the 120 GiB floor")
            if "BUDGET " in l and "waiters=[]" not in l and "waiters=" in l: alerts.append(f"{lab}: budget waiter -> {l[:160]}")
        vm_rows.append(vm)

    def table(headers: list[str], rows: list[list[str]]) -> str:
        head = "| " + " | ".join(headers) + " |"
        sep = "|" + "|".join("---" for _ in headers) + "|"
        body = ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
        return "\n".join([head, sep, *body])

    structured = collect_structured(outputs)
    structured_lines, structured_notes = render_structured(structured)
    pct = f"{100*done_rows/total_rows:.0f}%" if total_rows else "-"
    lines_out = [
        f"# Fleet report {time.strftime('%Y-%m-%d %H:%M:%S %Z')} (run {tag}, {len(pairs)} VMs)",
        "",
        f"**Total done: {done_rows}/{total_rows} rows ({pct})**  |  deliveries verified: {deliveries}  |  "
        f"in flight: {sum(1 for r in lane_rows if r['row'] != '-')}  |  alerts: {len(alerts)}",
        "",
        "status counts: " + ", ".join(f"{k}={v}" for k, v in sorted(totals.items())) if totals else "status counts: none",
        "",
        "## Lanes",
        table(["VM", "lane", "done", "row (n/total, size)", "status", "ladder step", "att", "min", "stream", "mem stage", "deliv", "last command", "remaining"],
              [[r["vm"], r["lane"], r["done"], f"{r['row']} ({r['idx']})" if r["row"] != "-" else "-", r["status"], r["ladder"],
                r["attempts"], r["elapsed"], r["stream"], r["mem"], r["deliv"], r["last_cmd"], r["next"]] for r in lane_rows]),
        "",
        "## VMs",
        table(["VM", "host", "done", "lanes", "workers", "load", "mem avail", "disk free", "budget used"],
              [[v["vm"], v["host"], f"{v['done']}/{v['total']}", v["lanes"], v["workers"], v["load"], v["mem"], v["disk"], v["budget"]] for v in vm_rows]),
        "",
        "## Alerts",
        "\n".join(f"- {a}" for a in alerts) if alerts else "none",
        "",
        "## Notes (informational: systemic patterns, caps, lane fixes)",
        "\n".join(f"- {n}" for n in structured_notes) if structured_notes else "none",
        *structured_lines,
        "",
        "## Changes since previous report",
        "\n".join(f"- {c}" for c in changes) if changes else ("none" if prev else "first report"),
        "",
        "<details><summary>Raw per-VM output</summary>",
        *raw_sections,
        "</details>",
    ]
    text = "\n".join(lines_out) + "\n"
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

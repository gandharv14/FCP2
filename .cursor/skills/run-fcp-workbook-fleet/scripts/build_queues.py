#!/usr/bin/env python3
"""Turn a folder of financial workbooks into size-sorted recovery queues.

Steps:
  1. Group files by stem. The run source is always ``<stem>.xlsx`` (the
     worker refuses anything else). The original is the non-xlsx sibling
     (xlsm/xls/xlsb) when one exists, else the xlsx itself.
  2. Find the pinned inventory artifact whose sha256 equals the approval's
     ``inventory_artifact_sha256`` (same rule as the worker).
  3. Coverage: the artifact must list the workbook with ``sha256`` equal to
     the run-source hash, and the approval ledger must bind the original's
     sha256, size, and extension. Uncovered rows stop the run unless
     ``--skip-uncovered``.
  4. Sort covered rows by original file size, smallest first, and deal them
     lane-major across host x lane slots so every lane queue stays ascending.
  5. Write ``queues/<label>/queue-<lane>.json``, ``sources-<label>.tar`` (xlsx
     only), ``plan.json``, ``coverage.json``.

Exit codes: 0 ok, 2 uncovered rows (without --skip-uncovered), 1 other error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import string
import subprocess
import sys
import tarfile
from collections import defaultdict
from pathlib import Path

WORKBOOK_SUFFIXES = {".xlsx", ".xlsm", ".xls", ".xlsb"}
LANE_LETTERS = string.ascii_lowercase
DEFAULT_HOST_BYTES = 135064379392  # e2-highmem-16 MemTotal, used only when fleet.json lacks it


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def walk_dicts(value):
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            yield current
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)


def load_hosts(path: Path) -> list[dict]:
    payload = json.loads(path.read_text())
    hosts = payload.get("hosts") if isinstance(payload, dict) else payload
    if not isinstance(hosts, list) or not hosts:
        raise SystemExit("fleet.json has no hosts")
    out = []
    for entry in hosts:
        if isinstance(entry, str):
            entry = {"host": entry}
        if entry.get("state") not in (None, "fresh", "idle", "target"):
            continue
        out.append(entry)
    if not out:
        raise SystemExit("fleet.json has no fresh/idle hosts to target")
    return out


def label_for(index: int, entry: dict) -> str:
    if entry.get("label"):
        return str(entry["label"])
    return f"vm{index + 1}"


def select_approval(registry: Path, approval_batch_id: str) -> dict:
    payload = json.loads(registry.read_text())
    approvals = [
        value
        for value in payload.get("approvals", [])
        if isinstance(value, dict) and value.get("batch_id") == approval_batch_id
    ]
    if not approvals:
        known = [a.get("batch_id") for a in payload.get("approvals", [])]
        raise SystemExit(f"approval {approval_batch_id!r} not in registry; known: {known}")
    if len(approvals) > 1:
        raise SystemExit(f"approval {approval_batch_id!r} is ambiguous in registry")
    return approvals[0]


def find_inventory(manifest_root: Path, artifact_sha256: str) -> tuple[Path, object]:
    for candidate in sorted(manifest_root.glob("*.json")):
        if candidate.is_symlink() or not candidate.is_file():
            continue
        content = candidate.read_bytes()
        if hashlib.sha256(content).hexdigest() == artifact_sha256:
            return candidate, json.loads(content)
    raise SystemExit(
        f"no file in {manifest_root} hashes to inventory_artifact_sha256={artifact_sha256}"
    )


def group_folder(folder: Path) -> dict[str, dict[str, Path]]:
    groups: dict[str, dict[str, Path]] = defaultdict(dict)
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.name.startswith("._") or path.name.startswith("~$"):
            continue
        suffix = path.suffix.casefold()
        if suffix not in WORKBOOK_SUFFIXES:
            continue
        stem = path.stem
        if suffix in groups[stem]:
            raise SystemExit(f"duplicate {suffix} for stem {stem}: {path} and {groups[stem][suffix]}")
        groups[stem][suffix] = path
    return groups


def build_rows(groups: dict[str, dict[str, Path]], row_batch_id: str) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    no_xlsx: list[str] = []
    for stem, files in sorted(groups.items()):
        run_source = files.get(".xlsx")
        if run_source is None:
            no_xlsx.append(stem)
            continue
        originals = [p for s, p in files.items() if s != ".xlsx"]
        if len(originals) > 1:
            raise SystemExit(f"stem {stem} has several non-xlsx originals: {originals}")
        original = originals[0] if originals else run_source
        run_hash = sha256_file(run_source)
        orig_hash = run_hash if original == run_source else sha256_file(original)
        rows.append(
            {
                "batch_id": row_batch_id,
                "workbook_id": stem,
                "run_source_sha256": run_hash,
                "source_format": original.suffix.casefold().lstrip("."),
                "source_sha256": orig_hash,
                "source_size": original.stat().st_size,
                "run_source_path": f"sources/{run_source.name}",
                "_local_run_source": str(run_source),
                "_local_original": str(original),
                "_xlsx_bytes": run_source.stat().st_size,
            }
        )
    return rows, no_xlsx


def check_coverage(rows: list[dict], inventory_payload, approval: dict) -> dict:
    inventory_records: dict[str, list[dict]] = defaultdict(list)
    for record in walk_dicts(inventory_payload):
        wid = record.get("workbook_id")
        if isinstance(wid, str) and "sha256" in record:
            inventory_records[wid].append(record)
    ledger: dict[str, list[dict]] = defaultdict(list)
    for record in approval.get("batch_source_ledger", []):
        if isinstance(record, dict) and record.get("workbook_id"):
            ledger[record["workbook_id"]].append(record)

    covered, uncovered, mismatch = [], [], []
    for row in rows:
        wid = row["workbook_id"]
        inv = inventory_records.get(wid, [])
        if not inv:
            uncovered.append(wid)
            continue
        if len(inv) != 1:
            mismatch.append((wid, "inventory lists workbook more than once"))
            continue
        if inv[0].get("sha256") != row["run_source_sha256"]:
            mismatch.append((wid, "inventory sha256 != local xlsx sha256"))
            continue
        led = ledger.get(wid, [])
        if len(led) != 1:
            mismatch.append((wid, f"ledger lists workbook {len(led)} times"))
            continue
        record = led[0]
        original = record.get("original_source") or {}
        ext = Path(str(original.get("path", ""))).suffix.casefold().lstrip(".")
        problems = []
        if record.get("source_sha256") != row["run_source_sha256"]:
            problems.append("ledger source_sha256 != local xlsx")
        if original.get("sha256") != row["source_sha256"]:
            problems.append("ledger original sha256 != local original")
        if original.get("size_bytes") != row["source_size"]:
            problems.append("ledger original size != local original")
        if ext != row["source_format"]:
            problems.append(f"ledger extension {ext!r} != local {row['source_format']!r}")
        if problems:
            mismatch.append((wid, "; ".join(problems)))
            continue
        covered.append(row)
    return {"covered": covered, "uncovered": uncovered, "mismatch": mismatch}


def deal(rows: list[dict], labels: list[str], lanes: int) -> dict[str, dict[str, list[dict]]]:
    """Lane-major dealing: h1a, h2a, ..., hNa, h1b, ... Each lane stays ascending."""
    slots = [(label, LANE_LETTERS[lane]) for lane in range(lanes) for label in labels]
    queues: dict[str, dict[str, list[dict]]] = {label: {LANE_LETTERS[l]: [] for l in range(lanes)} for label in labels}
    for index, row in enumerate(rows):
        label, lane = slots[index % len(slots)]
        queues[label][lane].append(row)
    return queues


def public_row(row: dict) -> dict:
    return {key: value for key, value in row.items() if not key.startswith("_")}


def governor_warnings(repo: Path, out: Path, hosts: list[dict], labels: list[str], lanes: int) -> dict:
    """Run memory_governor plan once per host so the estimates are the real ones.

    The planner's partition is ignored; only its lane recommendation and
    heaviest-pair check are surfaced. Rows are listed per host in the order
    the queues were dealt, so the heaviest pair per host is the tail of each lane.
    """
    warnings: dict[str, dict] = {}
    for label, entry in zip(labels, hosts):
        # The planner probes run_source_path relative to the queue file, so it
        # needs the local-path copies, not the VM-relative sources/ paths.
        queue_files = sorted((out / "queues-local" / label).glob("queue-*.json"))
        # The planner reads the local machine's MemTotal otherwise, which on a
        # Mac is neither available nor the VM's. Fall back to e2-highmem-16.
        host_bytes = int(entry.get("mem_total_bytes") or DEFAULT_HOST_BYTES)
        cmd = [sys.executable, "-m", "scripts.batch.memory_governor", "--host-bytes", str(host_bytes)]
        cmd += ["plan", "--queues", *map(str, queue_files), "--vms", "1", "--lane-cap", str(lanes)]
        if entry.get("disk_avail_bytes"):
            cmd += ["--disk-free-bytes", str(entry["disk_avail_bytes"])]
        env = dict(os.environ, PYTHONPATH=str(repo), PYTHONDONTWRITEBYTECODE="1")
        try:
            proc = subprocess.run(cmd, cwd=repo, env=env, capture_output=True, text=True, timeout=1800)
        except (OSError, subprocess.TimeoutExpired) as error:
            warnings[label] = {"error": str(error)}
            continue
        if proc.returncode != 0:
            warnings[label] = {"error": proc.stderr.strip()[-800:]}
            continue
        try:
            plan = json.loads(proc.stdout)
        except json.JSONDecodeError:
            warnings[label] = {"error": "planner printed non-JSON", "stdout": proc.stdout[-800:]}
            continue
        vm = (plan.get("vms") or [{}])[0] if isinstance(plan.get("vms"), list) else plan
        warnings[label] = {
            "lanes_recommended": vm.get("lanes"),
            "memory_lanes": vm.get("memory_lanes"),
            "disk_lanes": vm.get("disk_lanes"),
            "heaviest_pair_fits": vm.get("heaviest_pair_fits"),
            "unprobed": vm.get("unprobed") or plan.get("unprobed"),
            "budget_total_bytes": plan.get("budget_total_bytes") or plan.get("total_bytes"),
        }
    return warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--folder", type=Path, required=True, help="folder of workbooks (searched recursively)")
    parser.add_argument("--row-batch-id", required=True, help="queue row batch_id, e.g. batch-002 (output path segment)")
    parser.add_argument("--approval-batch-id", required=True, help="RECOVERY_APPROVAL_BATCH_ID, e.g. batch-002-restricted-native-19-v3")
    parser.add_argument("--hosts", type=Path, required=True, help="fleet.json from fleet_hosts.sh, or a JSON list of hostnames")
    parser.add_argument("--lanes", type=int, default=2)
    parser.add_argument("--out", type=Path, required=True, help="output directory (created)")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[4])
    parser.add_argument("--only", default="", help="comma-separated workbook IDs to keep (e.g. a single-row smoke: --only 0568)")
    parser.add_argument("--skip-uncovered", action="store_true", help="drop uncovered rows instead of failing")
    parser.add_argument("--no-governor", action="store_true", help="skip memory_governor warnings (slow probe)")
    args = parser.parse_args()

    if args.lanes < 1 or args.lanes > len(LANE_LETTERS):
        raise SystemExit("--lanes must be between 1 and 26")
    repo = args.repo.resolve()
    folder = args.folder.resolve()
    if not folder.is_dir():
        raise SystemExit(f"not a folder: {folder}")
    hosts = load_hosts(args.hosts)
    labels = [label_for(i, h) for i, h in enumerate(hosts)]
    if len(set(labels)) != len(labels):
        raise SystemExit(f"duplicate host labels: {labels}")

    registry = repo / "verification_manifests" / "approved_source_inventories.v1.json"
    approval = select_approval(registry, args.approval_batch_id)
    inventory_path, inventory_payload = find_inventory(
        repo / "verification_manifests", approval["inventory_artifact_sha256"]
    )

    groups = group_folder(folder)
    if args.only:
        keep = {w.strip() for w in args.only.split(",") if w.strip()}
        missing = sorted(keep - set(groups))
        if missing:
            raise SystemExit(f"--only IDs not found in folder: {missing}")
        groups = {k: v for k, v in groups.items() if k in keep}
    rows, no_xlsx = build_rows(groups, args.row_batch_id)
    coverage = check_coverage(rows, inventory_payload, approval)
    covered = sorted(coverage["covered"], key=lambda r: (r["source_size"], r["workbook_id"]))

    print(f"folder={folder}")
    print(f"stems={len(groups)} rows_with_xlsx={len(rows)} no_xlsx={len(no_xlsx)}")
    print(f"approval={args.approval_batch_id} inventory={inventory_path.name}")
    print(f"covered={len(covered)} uncovered={len(coverage['uncovered'])} mismatch={len(coverage['mismatch'])}")
    if no_xlsx:
        print("NO_XLSX (skipped, no run source): " + " ".join(no_xlsx))
    if coverage["mismatch"]:
        for wid, why in coverage["mismatch"]:
            print(f"MISMATCH {wid}: {why}")
    if coverage["uncovered"]:
        print("UNCOVERED: " + " ".join(coverage["uncovered"]))

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "coverage.json").write_text(
        json.dumps(
            {
                "approval_batch_id": args.approval_batch_id,
                "inventory": inventory_path.name,
                "covered": [r["workbook_id"] for r in covered],
                "uncovered": coverage["uncovered"],
                "mismatch": coverage["mismatch"],
                "no_xlsx": no_xlsx,
            },
            indent=1,
        )
        + "\n"
    )
    if coverage["mismatch"]:
        print("STOP: hash or metadata mismatches; the worker would raise PolicyViolation on these rows")
        return 1
    if coverage["uncovered"] and not args.skip_uncovered:
        print("STOP: uncovered rows; pass --skip-uncovered to drop them, or use the right approval")
        return 2
    if not covered:
        print("STOP: nothing to run")
        return 1

    queues = deal(covered, labels, args.lanes)
    per_host: dict[str, dict] = {}
    for label, entry in zip(labels, hosts):
        qdir = args.out / "queues" / label
        ldir = args.out / "queues-local" / label
        qdir.mkdir(parents=True, exist_ok=True)
        ldir.mkdir(parents=True, exist_ok=True)
        host_rows: list[dict] = []
        for lane, lane_rows in queues[label].items():
            (qdir / f"queue-{lane}.json").write_text(
                json.dumps([public_row(r) for r in lane_rows], indent=1) + "\n"
            )
            (ldir / f"queue-{lane}.json").write_text(
                json.dumps(
                    [dict(public_row(r), run_source_path=r["_local_run_source"]) for r in lane_rows],
                    indent=1,
                )
                + "\n"
            )
            host_rows.extend(lane_rows)
        (qdir / "ids.txt").write_text("".join(f"{r['workbook_id']}\n" for r in host_rows))
        tar_path = args.out / f"sources-{label}.tar"
        with tarfile.open(tar_path, "w") as tar:
            for row in host_rows:
                tar.add(row["_local_run_source"], arcname=Path(row["run_source_path"]).name)
        per_host[label] = {
            "host": entry.get("host"),
            "rows": len(host_rows),
            "bytes_original": sum(r["source_size"] for r in host_rows),
            "lanes": {
                lane: {
                    "rows": len(lane_rows),
                    "first": [r["workbook_id"] for r in lane_rows[:3]],
                    "last": [r["workbook_id"] for r in lane_rows[-2:]],
                    "ascending": all(
                        a["source_size"] <= b["source_size"] for a, b in zip(lane_rows, lane_rows[1:])
                    ),
                }
                for lane, lane_rows in queues[label].items()
            },
        }

    warnings = {} if args.no_governor else governor_warnings(repo, args.out, hosts, labels, args.lanes)
    plan = {
        "row_batch_id": args.row_batch_id,
        "approval_batch_id": args.approval_batch_id,
        "inventory": inventory_path.name,
        "inventory_artifact_sha256": approval["inventory_artifact_sha256"],
        "lanes_per_vm": args.lanes,
        "sort_key": "original source_size ascending, then workbook_id",
        "dealing": "lane-major round robin across host x lane slots",
        "folder": str(folder),
        "total_rows": len(covered),
        "vms": per_host,
        "governor": warnings,
    }
    (args.out / "plan.json").write_text(json.dumps(plan, indent=1) + "\n")
    for label, info in per_host.items():
        lanes_desc = " ".join(
            f"{lane}:{d['rows']}rows{'' if d['ascending'] else ' NOT-ASCENDING'}" for lane, d in info["lanes"].items()
        )
        gov = warnings.get(label, {})
        flag = ""
        if gov.get("heaviest_pair_fits") is False:
            flag = " WARN heaviest pair exceeds budget together"
        if gov.get("lanes_recommended") not in (None, args.lanes) and gov.get("lanes_recommended", args.lanes) < args.lanes:
            flag += f" WARN planner recommends {gov['lanes_recommended']} lanes"
        if gov.get("error"):
            flag += f" governor_error={gov['error'][:120]!r}"
        print(f"{label} {info['host']} rows={info['rows']} {lanes_desc}{flag}")
    print(f"WROTE {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

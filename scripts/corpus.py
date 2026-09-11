"""Immutable raw-workbook inventory and resumable, evidence-bound intake.

Run with Python 3.12: python -m scripts.corpus inventory|screen|summary.
Intake readiness is never counted as a promoted Harbor environment.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
import hashlib
import fcntl
import json
from pathlib import Path
import re
import subprocess
import sys
import time

from scripts.batch.hardened_runner import atomic_write_json

ROOT = Path(__file__).resolve().parents[1]
SUFFIXES = {".xlsx", ".xlsm", ".xls", ".xlsb"}


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def object_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False).encode()).hexdigest()


def _validate_code_records(root, records):
    if not isinstance(records, dict) or not records:
        raise ValueError("pipeline code snapshot must declare nonempty files")
    for name, expected in records.items():
        if (not isinstance(name, str) or not name or "\\" in name
                or Path(name).is_absolute() or Path(name).as_posix() != name
                or any(part in {"", ".", ".."} for part in name.split("/"))
                or name == ".pipeline-code.json"
                or not isinstance(expected, str)
                or re.fullmatch(r"[0-9a-f]{64}", expected) is None):
            raise ValueError("invalid pipeline code snapshot record")
        path = root / name
        if any(parent.is_symlink() for parent in (path, *path.parents)
               if parent != root and root in parent.parents):
            raise ValueError("pipeline code snapshot contains a symlink")
        if not path.is_file() or digest(path) != expected:
            raise ValueError(f"pipeline code snapshot changed: {name}")
    return records


def _frozen_code_files(root):
    manifest = root / ".pipeline-code.json"
    if not manifest.exists() and not manifest.is_symlink():
        return None
    if manifest.is_symlink() or not manifest.is_file():
        raise ValueError("invalid pipeline code snapshot manifest")

    def unique_records(pairs):
        records = {}
        for name, value in pairs:
            if name in records:
                raise ValueError("duplicate pipeline code snapshot record")
            records[name] = value
        return records

    records = json.loads(manifest.read_text(), object_pairs_hook=unique_records)
    _validate_code_records(root, records)
    if re.fullmatch(r"[0-9a-f]{64}", root.name) and object_hash(records) != root.name:
        raise ValueError("pipeline code snapshot manifest identity changed")
    return records


def code_files():
    frozen = _frozen_code_files(ROOT)
    if frozen is not None:
        return frozen
    files = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    # Include new source files before their first commit as well.
    files += subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "-z"],
                                     cwd=ROOT).decode().split("\0")
    records = {name: digest(ROOT / name) for name in sorted(set(files))
               if (Path(name).suffix in {".py", ".sh", ".md", ".yaml", ".yml", ".toml", ".txt", ".json"}
                   or name == ".python-version")
               and not name.startswith("release_out/") and (ROOT / name).is_file()}
    return _validate_code_records(ROOT, records)


def code_hash():
    records = _frozen_code_files(ROOT)
    if records is not None:
        return object_hash(records)
    return object_hash(code_files())


def snapshot_code(destination):
    """Freeze worker implementation so edits cannot change an in-flight cohort."""
    import shutil
    records = _validate_code_records(ROOT, code_files())
    fingerprint = object_hash(records)
    target = Path(destination) / fingerprint
    if target.is_symlink():
        raise ValueError("pipeline code snapshot destination contains a symlink")
    target.mkdir(parents=True, exist_ok=True)
    existing = _frozen_code_files(target)
    if existing is not None and existing != records:
        raise ValueError("pipeline code snapshot manifest changed")
    for name, expected in records.items():
        output = target / name
        if any(parent.is_symlink() for parent in (output, *output.parents)
               if parent != target and target in parent.parents):
            raise ValueError("pipeline code snapshot destination contains a symlink")
        if not output.exists():
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, output)
        if digest(output) != expected:
            raise ValueError("code changed while creating the pipeline snapshot")
    _validate_code_records(ROOT, records)
    if existing is None:
        atomic_write_json(target / ".pipeline-code.json", records)
    return target, fingerprint


def inventory(roots, destination, expected_count=None):
    records = []
    seen = set()
    for directory in roots:
        root = Path(directory).resolve(strict=True)
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in SUFFIXES:
                continue
            if path.is_symlink() or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", path.stem):
                raise ValueError(f"unsafe workbook source: {path}")
            identity = path.stem.casefold()
            if identity in seen:
                raise ValueError(f"ambiguous workbook identity: {path.stem}")
            seen.add(identity)
            records.append(dict(workbook_id=path.stem, path=str(path),
                                sha256=digest(path), size_bytes=path.stat().st_size,
                                format=path.suffix.lower(), corpus=root.name))
    if not records or (expected_count is not None and len(records) != expected_count):
        raise ValueError(f"expected {expected_count} sources, found {len(records)}")
    value = dict(schema_version="raw-corpus/v1", sources=sorted(records, key=lambda r:r["workbook_id"]))
    value["inventory_sha256"] = object_hash(value)
    destination = Path(destination)
    if destination.exists():
        if json.loads(destination.read_text()) != value:
            raise ValueError("refusing to overwrite a different frozen inventory")
    else:
        atomic_write_json(destination, value)
    return value


def read_inventory(path):
    value = json.loads(Path(path).read_text())
    expected = value.get("inventory_sha256")
    unsigned = {k:v for k,v in value.items() if k != "inventory_sha256"}
    if expected != object_hash(unsigned) or value.get("schema_version") != "raw-corpus/v1":
        raise ValueError("invalid corpus inventory hash or schema")
    ids = [r["workbook_id"] for r in value["sources"]]
    if len(ids) != len(set(x.casefold() for x in ids)):
        raise ValueError("duplicate corpus identity")
    return value


def screen_one(source, run, fingerprint, timeout):
    directory = run / "workbooks" / source["workbook_id"]
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _screen_one(source, run, fingerprint, timeout)


def _screen_one(source, run, fingerprint, timeout):
    wb = source["workbook_id"]
    directory = run / "workbooks" / wb
    directory.mkdir(parents=True, exist_ok=True)
    ledger_path = directory / "ledger.json"
    health_path = directory / "source-health.json"
    corrupt_checkpoint = None
    try:
        previous = json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
        if not isinstance(previous, dict):
            raise ValueError("checkpoint must be an object")
        if (not isinstance(previous.get("history", []), list)
                or not isinstance(previous.get("build", {}), dict)
                or not isinstance(previous.get("build_history", []), list)
                or type(previous.get("attempts", 0)) is not int
                or previous.get("attempts", 0) < 0):
            raise ValueError("checkpoint stage history is malformed")
    except (ValueError, OSError):
        previous = {}
        if ledger_path.is_file():
            backup = directory / ("ledger-corrupt-" + digest(ledger_path) + ".json")
            if not backup.exists():
                backup.write_bytes(ledger_path.read_bytes())
            corrupt_checkpoint = {"path": str(backup), "sha256": digest(backup)}
    binding = dict(source_sha256=source["sha256"], code_sha256=fingerprint)
    try:
        reusable = (previous.get("binding") == binding and previous.get("state") in {"READY", "STOPPED"}
            and ((health_path.is_file() and previous.get("health_sha256") == digest(health_path))
                 or (source["format"] in {".xls", ".xlsb"} and previous.get("failure") == "conversion_requires_authoritative_evidence"))
            and digest(source["path"]) == source["sha256"])
    except (OSError, ValueError):
        reusable = False
    if reusable:
        return previous
    started = time.monotonic()
    history = list(previous.get("history", []))
    if previous:
        # Retain the actual previous evidence before replacing compatibility
        # paths. A hash of an overwritten report is not a usable checkpoint.
        archive = directory / "intake-attempts" / str(time.time_ns())
        archive.mkdir(parents=True)
        evidence = []
        for path in (ledger_path, health_path, directory / "health.log"):
            if path.is_file():
                saved = archive / path.name
                saved.write_bytes(path.read_bytes())
                evidence.append({"path": str(saved), "sha256": digest(saved)})
        history.append({"binding": previous.get("binding"),
                        "state": previous.get("state"), "failure": previous.get("failure"),
                        "evidence": evidence})
    record = dict(previous)
    record.update(schema_version="corpus-workbook/v1", workbook_id=wb, source=source,
                  binding=binding, attempts=int(previous.get("attempts", 0)) + 1,
                  history=history,
                  state="RUNNING", failure=None, stage="intake", promoted=False)
    for field in ("health_sha256", "route", "reason_codes"):
        record.pop(field, None)
    if corrupt_checkpoint:
        record["corrupt_checkpoint"] = corrupt_checkpoint
    if previous.get("build"):
        record["build_history"] = list(previous.get("build_history", [])) + [previous["build"]]
        record["build"] = {"state": "STALE", "stage": "intake",
                           "failure": "intake_evidence_changed",
                           "attempts": previous["build"].get("attempts", 0)}
    atomic_write_json(ledger_path, record)
    record["state"] = "STOPPED"
    try:
        if digest(source["path"]) != source["sha256"]:
            raise ValueError("source_hash_changed")
        if source["format"] in {".xls", ".xlsb"}:
            record["failure"] = "conversion_requires_authoritative_evidence"
        else:
            candidate_health = directory / "source-health.pending.json"
            candidate_health.unlink(missing_ok=True)
            with (directory / "health.log").open("w") as log:
                result = subprocess.run([sys.executable, "xl_source_health.py", "observe",
                    source["path"], "--output", str(candidate_health)], cwd=ROOT,
                    stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
            if not candidate_health.is_file():
                raise ValueError(f"source_health_failed_exit_{result.returncode}")
            health = json.loads(candidate_health.read_text())
            from xl_source_health import validate_report
            validate_report(health, source["path"])
            candidate_health.replace(health_path)
            record["health_sha256"] = digest(health_path)
            record["route"] = health["route"]
            record["reason_codes"] = health.get("reason_codes", [])
            if health["route"] in {"pass", "restricted_pass"}:
                record["state"] = "READY"
            elif health["route"] in {"recalc_candidate", "restricted_recalc_pass"}:
                record["failure"] = "trusted_recalculation_required"
            else:
                record["failure"] = "source_policy_unsupported"
    except subprocess.TimeoutExpired:
        record["failure"] = "intake_timeout"
    except (OSError, ValueError, KeyError) as exc:
        record["failure"] = f"source_integrity:{type(exc).__name__}:{exc}"
    record["elapsed_seconds"] = round(time.monotonic() - started, 3)
    atomic_write_json(ledger_path, record)
    return record


def summary(manifest, run):
    states = Counter()
    failures = Counter()
    formats = Counter()
    routes = Counter()
    for source in manifest["sources"]:
        formats[source["format"]] += 1
        path = run / "workbooks" / source["workbook_id"] / "ledger.json"
        record = json.loads(path.read_text()) if path.is_file() else {}
        states[record.get("state", "NOT_RUN")] += 1
        if record.get("failure"):
            failures[record["failure"]] += 1
        if record.get("route"):
            routes[record["route"]] += 1
    result = dict(schema_version="corpus-summary/v1", inventory_sha256=manifest["inventory_sha256"],
                  raw_workbooks=len(manifest["sources"]), states=dict(states),
                  failures=dict(failures), formats=dict(formats), routes=dict(routes),
                  verified_promoted=None, yield_rate=None,
                  promotion_note="Intake does not establish a verified Harbor release.")
    atomic_write_json(run / "summary.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("inventory")
    freeze.add_argument("roots", nargs="+")
    freeze.add_argument("--output", required=True)
    freeze.add_argument("--expected-count", type=int)
    for name in ("screen", "summary"):
        cmd = commands.add_parser(name)
        cmd.add_argument("--inventory", required=True)
        cmd.add_argument("--run-root", required=True)
        if name == "screen":
            cmd.add_argument("--workers", type=int, default=4, choices=range(1, 5))
            cmd.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    if args.command == "inventory":
        result = inventory(args.roots, args.output, args.expected_count)
        print(json.dumps({"count":len(result["sources"]), "inventory_sha256":result["inventory_sha256"]}))
        return
    manifest = read_inventory(args.inventory)
    run = Path(args.run_root).resolve()
    run.mkdir(parents=True, exist_ok=True)
    if args.command == "screen":
        fingerprint = code_hash()
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            pending = [pool.submit(screen_one, source, run, fingerprint, args.timeout)
                       for source in manifest["sources"]]
            for i, task in enumerate(as_completed(pending), 1):
                record = task.result()
                print(f"{i}/{len(pending)} {record['workbook_id']} {record['state']} {record.get('route', '')}", flush=True)
    print(json.dumps(summary(manifest, run), indent=2))


if __name__ == "__main__":
    main()

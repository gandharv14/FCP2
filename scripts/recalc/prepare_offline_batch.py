"""Prepare expiring, hash-bound requests and a local offline execution script."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import plistlib
import shlex
import sys

from scripts.corpus import read_inventory, digest
from scripts.batch.hardened_runner import atomic_write_json
from xl_source_health import inspect_workbook
from xl_source_recalc import create_recalc_request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--workbooks", nargs="+", required=True)
    parser.add_argument("--runner-config", default="/etc/harbor/excel-runner.json")
    parser.add_argument("--python", help="persistent Python 3.12 interpreter; defaults to project .venv")
    parser.add_argument("--refresh-runner", action="store_true",
                        help="install the hash-bound maintained runner in the first offline window")
    args = parser.parse_args()
    pipeline = Path(__file__).resolve().parents[2]
    interpreter = Path(args.python) if args.python else pipeline / ".venv/bin/python"
    # Keep the venv entry point: resolving this symlink loses its dependencies.
    interpreter = interpreter.absolute()
    if not interpreter.is_file() or "/.tmp" in str(interpreter):
        parser.error("a persistent project environment is required; create .venv with Python 3.12 first")
    config = json.loads(Path(args.runner_config).read_text())
    version = plistlib.loads((Path(config["excel_app"]) / "Contents/Info.plist").read_bytes())["CFBundleShortVersionString"]
    sources = {s["workbook_id"]: s for s in read_inventory(args.inventory)["sources"]}
    output = Path(args.output).resolve()
    if output.exists():
        parser.error("output already exists; use a fresh request directory")
    unknown = set(args.workbooks) - sources.keys()
    if unknown:
        parser.error("unknown workbook IDs: " + ", ".join(sorted(unknown)))
    output.mkdir(parents=True)
    commands = ["#!/bin/bash", "set -uo pipefail", "failures=0",
                "# Each request runs offline; interfaces are restored between requests."]
    jobs = []
    for wb in args.workbooks:
        source = sources[wb]
        if digest(source["path"]) != source["sha256"]:
            raise ValueError(f"source changed: {wb}")
        health = inspect_workbook(source["path"])
        if health["route"] not in {"recalc_candidate", "restricted_recalc_pass"}:
            raise ValueError(f"{wb}: source is not eligible for cache-only trusted recalculation")
        job = output / wb
        job.mkdir()
        request = create_recalc_request(source["path"], job / f"{wb}.xlsx", allowed_root=job,
            trusted_runner_public_key=config["receipt_public_key"],
            engine_constraints={"permitted_versions": [version]}, ttl_seconds=7 * 86400)
        atomic_write_json(job / "request.json", request)
        command = ["/usr/bin/sudo", "/usr/bin/python3", str(pipeline / "scripts/recalc/offline_window.py"),
            "--request", str(job / "request.json"), "--source", source["path"],
            "--allowed-root", str(job), "--result", str(job / "result.json"),
            "--python", str(interpreter), "--pipeline", str(pipeline),
            "--automation-user", config["automation_user"]]
        if args.refresh_runner and not jobs:
            command += ["--refresh-runner-sha256", digest(pipeline / "scripts/recalc/excel_runner.py")]
        commands.append("if " + shlex.join(command) + "; then :; else failures=$((failures + 1)); fi")
        jobs.append({"workbook_id": wb, "request": str(job / "request.json"),
                     "request_sha256": digest(job / "request.json"), "result": str(job / "result.json")})
    commands.append('exit "$((failures > 0))"')
    (output / "run-local.sh").write_text("\n".join(commands) + "\n")
    atomic_write_json(output / "jobs.json", {"schema_version": "offline-recalc-batch/v1", "jobs": jobs})
    print(shlex.join(["bash", str(output / "run-local.sh")]))


if __name__ == "__main__":
    main()

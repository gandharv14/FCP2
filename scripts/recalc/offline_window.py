#!/usr/bin/python3
"""Run a single trusted recalculation with temporary interface isolation.

Run locally with sudo. The Excel runner independently enforces and signs its
own isolation; this coordinator only enters and restores the offline window.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import signal
import subprocess
import sys
import time


def active_interfaces():
    return subprocess.check_output(["/sbin/ifconfig", "-l", "-u"], text=True).split()


@contextlib.contextmanager
def isolated_interfaces():
    original = active_interfaces()
    downed = []
    try:
        for interface in original:
            if interface == "lo0":
                continue
            subprocess.run(["/sbin/ifconfig", interface, "down"], check=True,
                           capture_output=True)
            downed.append(interface)
        time.sleep(2)
        if set(active_interfaces()) - {"lo0"}:
            raise RuntimeError("could not establish loopback-only interface state")
        yield
    finally:
        failed = []
        for interface in downed:
            result = subprocess.run(["/sbin/ifconfig", interface, "up"], capture_output=True)
            if result.returncode:
                failed.append(interface)
        if failed:
            raise RuntimeError("could not restore network interfaces: " + ", ".join(failed))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--allowed-root", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--pipeline", required=True)
    parser.add_argument("--automation-user", required=True)
    parser.add_argument("--refresh-runner-sha256",
                        help="install the exact reviewed maintained runner inside the offline window")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("offline interface isolation requires sudo on the local host")
    account = pwd.getpwnam(args.automation_user)
    if account.pw_uid < 500 or pwd.getpwuid(Path("/dev/console").stat().st_uid).pw_name != args.automation_user:
        parser.error("automation user must be the non-root console user")
    if subprocess.run(["/usr/bin/pgrep", "-x", "Microsoft Excel"],
                      stdout=subprocess.DEVNULL).returncode == 0:
        parser.error("Excel is already running")
    request = json.loads(Path(args.request).read_text())
    if request.get("expires_at_ns", 0) <= time.time_ns():
        parser.error("request is expired")
    if Path(args.result).exists():
        parser.error("result already exists; refusing to overwrite evidence")
    runtime = subprocess.run(["/usr/bin/sudo", "-n", "-u", args.automation_user,
        str(Path(args.python).absolute()), "-c",
        "import sys, openpyxl; assert sys.version_info >= (3, 12)"], capture_output=True)
    if runtime.returncode:
        parser.error("persistent Python 3.12 runtime is unavailable; network was not changed")
    maintained_runner = Path(args.pipeline).resolve() / "scripts/recalc/excel_runner.py"
    if args.refresh_runner_sha256 and (
            maintained_runner.is_symlink() or not maintained_runner.is_file()
            or hashlib.sha256(maintained_runner.read_bytes()).hexdigest() != args.refresh_runner_sha256):
        parser.error("reviewed runner source changed; network was not changed")
    command = ["/usr/bin/sudo", "-n", "-u", args.automation_user,
               str(Path(args.python).absolute()), str(Path(args.pipeline).resolve() / "xl_source_recalc.py"),
               "execute", str(Path(args.request).resolve()), "--source", str(Path(args.source).resolve()),
               "--allowed-root", str(Path(args.allowed_root).resolve()),
               "--isolation-attestation", "/Library/Application Support/FCP2/recalc/excel-isolation-attestation.json",
               "--sandbox-runner", "/Library/Application Support/FCP2/recalc/harbor-excel-sandbox",
               "-o", str(Path(args.result).resolve())]
    # Serialize all callers on this host, including different workbook batches.
    with open("/var/run/fcp2-recalc-window.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        def interrupted(signum, frame):
            raise KeyboardInterrupt("offline window interrupted")
        signal.signal(signal.SIGTERM, interrupted)
        with isolated_interfaces():
            if args.refresh_runner_sha256:
                refresh_runner(args.pipeline, maintained_runner, args.refresh_runner_sha256)
            return run_isolated(command, args.pipeline)


def refresh_runner(pipeline, source, expected_sha256):
    """Use the existing installer and trusted configuration; retain receipt keys."""
    if hashlib.sha256(Path(source).read_bytes()).hexdigest() != expected_sha256:
        raise RuntimeError("reviewed runner source changed before installation")
    sys.path.insert(0, str(Path(pipeline).resolve()))
    from scripts.recalc import excel_runner, install_runner
    config = excel_runner.load_config()
    return install_runner.install(source_runner=Path(source),
        excel_app=Path(config["excel_app"]), openssl=Path(config["openssl_binary"]),
        automation_user=config["automation_user"], controls=config["isolation_controls"])


def run_isolated(command, directory):
    """Reap the process group before the caller restores network access."""
    process = subprocess.Popen(command, cwd=directory, start_new_session=True)
    try:
        return process.wait()
    finally:
        # Kill descendants even when the launcher exits before Excel does.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        # The launcher may have exited while a descendant is still stopping.
        # Do not restore interfaces while any member can continue executing.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())

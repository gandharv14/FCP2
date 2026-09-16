"""Inspect the eight requested workers before deployment; never launch jobs.

Run with Python 3.12 and an authenticated gcloud CLI. Each invocation requires
a fresh output directory so earlier access and resource evidence is retained.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time


HOSTS = ("hhu-server", *(f"hhu-server-{i}" for i in range(2, 9)))
# Read-only remote inspection. No credentials or process command lines collected.
PROBE = r"""import json,os,platform,shutil,socket
memory={}
with open('/proc/meminfo') as stream:
 for line in stream:
  key,value=line.split(':',1)
  if key in ('MemTotal','MemAvailable'): memory[key]=int(value.split()[0])*1024
print(json.dumps(dict(hostname=socket.gethostname(),python=platform.python_version(),cpus=os.cpu_count(),memory=memory,disk_free_bytes=shutil.disk_usage('.').free,load_average=os.getloadavg(),docker=shutil.which('docker'))))
"""


def inspect(host: str, project: str, zone: str, output: Path) -> dict:
    import shlex

    command = ["gcloud", "compute", "ssh", host, f"--project={project}",
               f"--zone={zone}", "--tunnel-through-iap", "--quiet",
               "--ssh-flag=-oBatchMode=yes", "--ssh-flag=-oConnectTimeout=15",
               "--command=" + "python3 -c " + shlex.quote(PROBE)]
    started = time.monotonic()
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=90)
        stdout, stderr, rc = result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or b""
        stderr = exc.stderr or b""
        stdout = stdout.decode(errors="replace") if isinstance(stdout, bytes) else stdout
        stderr = stderr.decode(errors="replace") if isinstance(stderr, bytes) else stderr
        stderr += "\nLocal connection preflight timed out."
        rc = None
    (output / f"{host}.stdout.txt").write_text(stdout)
    (output / f"{host}.stderr.txt").write_text(stderr)
    resources = None
    if rc == 0:
        for line in reversed(stdout.splitlines()):
            try:
                candidate = json.loads(line)
            except ValueError:
                continue
            if isinstance(candidate, dict) and candidate.get("hostname") in (
                host, f"{host}.c.{project}.internal"
            ):
                resources = candidate
                break
    sufficient = bool(resources and resources.get("memory", {}).get("MemAvailable", 0)
                      >= 12 * 1024**3 and resources.get("disk_free_bytes", 0) >= 12 * 1024**3)
    return dict(host=host, returncode=rc, elapsed_seconds=time.monotonic()-started,
                connected=resources is not None, resources=resources,
                minimum_resources_available=sufficient,
                status="ACCESS_AND_CAPACITY_CHECKED" if sufficient else "BLOCKED",
                deployment_ready=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="saleseng")
    parser.add_argument("--zone", default="us-central1-a")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    started = datetime.now(timezone.utc).isoformat()
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(lambda host: inspect(host, args.project, args.zone, args.output), HOSTS))
    result = dict(schema_version="harbor-vm-access-preflight/v1", started_at=started,
                  completed_at=datetime.now(timezone.utc).isoformat(),
                  project=args.project, zone=args.zone, workers=rows,
                  connected_count=sum(row["connected"] for row in rows),
                  task_generation_started=False,
                  remaining_checks=["Python 3.12 and locked dependencies", "container execution",
                                    "exclusive workbook custody and checkpoint portability",
                                    "shared model limit and serial publication", "artifact transfer"])
    (args.output / "preflight.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "connected": result["connected_count"],
                      "requested": 8, "task_generation_started": False}))
    return 0 if all(row["minimum_resources_available"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Turn task bundles into fully Harbor-runnable tasks for an agentic harness.

The bundles emitted by ``xl_task_build.py`` target a chat-level runner: they
declare a bare ``docker_image`` and rely on Harbor uploading ``environment/``
into the workdir. Running them under a real agent (OpenHands via
``harbor run``) needs a bit more:

  * an ``environment/Dockerfile`` so the image carries the tooling an agent
    installs itself into (curl, git, tmux) plus openpyxl and the artifact
    workbook, instead of a bare slim image
  * agent timeouts scaled to the size of the reconstruction, since an agent
    iterates rather than answering in one shot
  * a verifier stub that copies the agent's ``/app/answers.json`` into
    ``/logs/verifier/`` so the deliverable comes back with the job logs and
    can be scored offline against the golden workbook

Instructions, answer keys and facts are copied through untouched, so results
stay directly comparable with the chat-level runs.

    python3 xl_harbor_prep.py tasks_eval20 -o tasks_harbor20
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

try:
    import tomli
except ImportError:  # pragma: no cover
    try:
        import tomllib as tomli
    except ImportError:
        sys.exit("tomli is required:  python3 -m pip install tomli")

# agents iterate, so they need far more wall clock than a single chat call;
# scale with the number of cells the task asks for
AGENT_MAX_ITERATIONS = 600
AGENT_TIMEOUT_SCALE = 1.5
TIMEOUT_BASE_SEC = 2400.0
TIMEOUT_PER_TARGET_SEC = 2.0
# deeper workbooks push recon_full past 8k cells; the observed rate on 0248 was
# roughly 2.3s per cell, so the ceiling has to clear ~6h or those tasks get cut
# off mid-reconstruction rather than finishing
TIMEOUT_MAX_SEC = 21600.0

DOCKERFILE = """\
FROM python:3.12-slim

# tooling the harness and agent install themselves into the container with
RUN apt-get update && apt-get install -y --no-install-recommends \\
        curl ca-certificates git tmux procps \\
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir openpyxl

# OpenHands reads this environment variable when a job config does not supply
# an agent-specific override. Active evaluation configs use the same ceiling.
ENV MAX_ITERATIONS=600

WORKDIR /app
COPY %s /app/%s
"""

TEST_SH = """\
#!/bin/bash
# No grader in this pipeline iteration: capture the agent's deliverable so it
# comes back with the job logs and can be scored offline against the golden
# workbook, then emit the stub reward.
mkdir -p /logs/verifier
if [ -f /app/answers.json ]; then
  cp /app/answers.json /logs/verifier/answers.json
fi
echo 0 > /logs/verifier/reward.txt
"""


def agent_timeout(n_answer_cells):
    return AGENT_TIMEOUT_SCALE * min(
        TIMEOUT_MAX_SEC,
        TIMEOUT_BASE_SEC + TIMEOUT_PER_TARGET_SEC * n_answer_cells,
    )


def rewrite_task_toml(text, timeout_sec, memory_mb):
    """Swap the bare image for the Dockerfile build and widen the budgets."""
    out = re.sub(r'^docker_image = .*\n', "", text, flags=re.M)
    out = re.sub(r'(\[agent\]\ntimeout_sec = )[\d.]+',
                 lambda m: m.group(1) + "%.1f" % timeout_sec, out)
    out = re.sub(r'(\[verifier\]\ntimeout_sec = )[\d.]+',
                 lambda m: m.group(1) + "300.0", out)
    out = re.sub(r'^memory_mb = \d+', "memory_mb = %d" % memory_mb, out,
                 flags=re.M)
    out = re.sub(r'^cpus = \d+', "cpus = 2", out, flags=re.M)
    return out


def prep(task_dir, out_dir):
    meta = tomli.loads((task_dir / "task.toml").read_text(encoding="utf-8"))
    n_cells = meta["metadata"].get("n_answer_cells", 1)
    artifact = next((task_dir / "environment").glob("L*.xls*"))

    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "environment").mkdir(parents=True)
    (out_dir / "tests").mkdir()

    shutil.copy2(task_dir / "instruction.md", out_dir / "instruction.md")
    for name in ("answer_key.json", "facts.json"):
        shutil.copy2(task_dir / "tests" / name, out_dir / "tests" / name)
    shutil.copy2(artifact, out_dir / "environment" / artifact.name)

    (out_dir / "environment" / "Dockerfile").write_text(
        DOCKERFILE % (artifact.name, artifact.name), encoding="utf-8")

    test_path = out_dir / "tests" / "test.sh"
    test_path.write_text(TEST_SH, encoding="utf-8")
    test_path.chmod(0o755)

    (out_dir / "task.toml").write_text(
        rewrite_task_toml((task_dir / "task.toml").read_text(encoding="utf-8"),
                          agent_timeout(n_cells), 4096),
        encoding="utf-8")
    return n_cells, agent_timeout(n_cells)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Prepare task bundles for an agentic Harbor run")
    parser.add_argument("tasks", help="directory of task bundles")
    parser.add_argument("-o", "--out", default="tasks_harbor")
    parser.add_argument("--only", default="",
                        help="comma-separated task directory names to convert")
    args = parser.parse_args(argv)

    wanted = {t.strip() for t in args.only.split(",") if t.strip()}
    root = Path(args.tasks)
    task_dirs = sorted(p for p in root.iterdir()
                       if p.is_dir() and (p / "task.toml").is_file()
                       and (not wanted or p.name in wanted))
    if not task_dirs:
        sys.exit("no task bundles under %s" % args.tasks)

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    for task_dir in task_dirs:
        cells, timeout = prep(task_dir, out_root / task_dir.name)
        print("  %-40s %5d cells, agent timeout %.0fs"
              % (task_dir.name, cells, timeout))
    print("%d task(s) -> %s" % (len(task_dirs), out_root.resolve()))


if __name__ == "__main__":
    main()

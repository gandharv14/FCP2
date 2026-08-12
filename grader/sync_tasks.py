#!/usr/bin/env python3
"""Synchronize the finance grader runtime into existing Harbor bundles."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

TEST_SH = """\
#!/bin/bash
set -uo pipefail
mkdir -p /logs/verifier
exec python3 /tests/run_grader.py \\
    --workspace /app \\
    --answer-key /tests/answer_key.json \\
    --output-dir /logs/verifier \\
    --mode continuous
"""


def sync_bundle(task_dir: Path, grader_root: Path) -> None:
    tests_dir = task_dir / "tests"
    if not (tests_dir / "answer_key.json").is_file():
        raise ValueError(f"{task_dir} is not a finance output task")

    test_script = tests_dir / "test.sh"
    test_script.write_text(TEST_SH, encoding="utf-8")
    test_script.chmod(0o755)

    runner = tests_dir / "run_grader.py"
    shutil.copy2(grader_root / "run_grader.py", runner)
    runner.chmod(0o755)

    package = tests_dir / "finance_grader"
    if package.exists():
        shutil.rmtree(package)
    shutil.copytree(
        grader_root / "finance_grader",
        package,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )

    legacy_scorer = tests_dir / "score.py"
    if legacy_scorer.exists():
        legacy_scorer.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "tasks_root",
        nargs="?",
        type=Path,
        default=Path("tasks_outputs"),
    )
    args = parser.parse_args(argv)

    grader_root = Path(__file__).resolve().parent
    task_dirs = sorted(
        path
        for path in args.tasks_root.iterdir()
        if path.is_dir() and (path / "tests" / "answer_key.json").is_file()
    )
    for task_dir in task_dirs:
        sync_bundle(task_dir, grader_root)
        print(task_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

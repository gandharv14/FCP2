#!/usr/bin/env python3
"""Filesystem runner for the local finance grader API."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

try:
    from finance_grader import Grade, grade
except ModuleNotFoundError:  # imported as grader.run_grader in local tests
    from .finance_grader import Grade, grade


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _failure_grade(message: str, *, mode: str) -> Grade:
    return Grade(
        score=0.0,
        subscores={"grader": 0.0},
        weights={"grader": 1.0},
        scoring_mode="weighted" if mode == "continuous" else "binary",
        metadata={
            "kind": "cell_value",
            "grader_mode": mode,
            "curve": "grader_failure",
            "valid_answers_json": False,
            "n_targets": 0,
            "n_answered": 0,
            "n_exact": 0,
            "n_close_1pct": 0,
            "coverage": 0.0,
            "accuracy_exact": 0.0,
            "accuracy_close_1pct": 0.0,
            "passed": False,
            "grader_error": message,
        },
        cells=(),
    )


def write_outputs(output_dir: Path, result: Grade) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    details = result.to_dict()
    reward = {"score": float(details["score"])}
    for entry in details.get("structured_subscores", ()):
        name = entry.get("name")
        if isinstance(name, str) and name and name != "score":
            reward[name] = float(entry.get("score", 0.0))
    (output_dir / "reward.json").write_text(
        json.dumps(reward, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "reward.txt").write_text(
        f"{float(details['score']):.6f}\n",
        encoding="utf-8",
    )
    (output_dir / "reward-details.json").write_text(
        json.dumps(details, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (output_dir / "score_details.json").write_text(
        json.dumps(result.score_details(), indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def run(
    *,
    workspace: Path,
    answer_key_path: Path,
    output_dir: Path,
    mode: str = "continuous",
) -> int:
    answer_key = _read_json(answer_key_path)
    if not isinstance(answer_key, dict):
        message = f"answer key is missing or invalid: {answer_key_path}"
        write_outputs(output_dir, _failure_grade(message, mode=mode))
        print(f"[grader failure] {message}", file=sys.stderr)
        return 1

    answer_path = workspace / "answers.json"
    answers = _read_json(answer_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    if answer_path.is_file():
        try:
            shutil.copy2(answer_path, output_dir / "answers.json")
        except OSError:
            pass

    try:
        result = grade(answers, answer_key, mode=mode)
    except Exception as exc:  # defensive boundary for Harbor
        message = f"{type(exc).__name__}: {exc}"
        result = _failure_grade(message, mode=mode)
        write_outputs(output_dir, result)
        print(f"[grader failure] {message}", file=sys.stderr)
        return 1

    write_outputs(output_dir, result)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Grade finance answers for Harbor")
    parser.add_argument("--workspace", type=Path, default=Path("/app"))
    parser.add_argument(
        "--answer-key",
        type=Path,
        default=Path("/tests/answer_key.json"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("/logs/verifier"),
    )
    parser.add_argument(
        "--mode",
        choices=("continuous", "discrete"),
        default="continuous",
    )
    args = parser.parse_args(argv)
    return run(
        workspace=args.workspace,
        answer_key_path=args.answer_key,
        output_dir=args.output_dir,
        mode=args.mode,
    )


if __name__ == "__main__":
    raise SystemExit(main())

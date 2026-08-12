#!/usr/bin/env python3
"""Score one final Harbor attempt per task with continuous and pass@1 metrics."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from xl_harbor_score import headline, read_json
from xl_passk_score import (
    final_trial_names,
    score_passk_trial,
    summarize_task,
)


def write_report(payload: dict, out_dir: Path) -> str:
    lines = [
        "# %s pass@1 on %s" % (payload["model"], payload["tasks_root"]),
        "",
        "- Harbor jobs: %s" % ", ".join(
            "`%s`" % job for job in payload["jobs"]
        ),
        "- agent/model: `%s` / `%s`" % (
            payload["agent"], payload["model"]),
        "- attempts: %d (%d tasks x 1)" % (
            len(payload["attempts"]), len(payload["tasks"])),
        "- aggregate pass@1: %.1f%%" % (
            100 * payload["computed_pass_at_1"]),
        "- overall continuous score mean: %.6f" % (
            payload["overall_continuous_score_mean"]),
        "- overall continuous score population variance: %.6f" % (
            payload["overall_continuous_score_variance"]),
        "",
        "## Results by task",
        "",
        "| task | pass | score | exact | within 1% | coverage | cost |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for summary in payload["tasks"]:
        lines.append(
            "| %s | %s | %.4f | %.1f%% | %.1f%% | %.1f%% | $%.2f |"
            % (
                summary["task"],
                "yes" if summary["n_passed"] else "no",
                summary["continuous_score_mean"],
                summary["mean_accuracy_exact"],
                summary["mean_accuracy_close_1pct"],
                summary["mean_coverage"],
                summary["cost_usd"],
            )
        )
    lines += [
        "",
        "## Attempt detail",
        "",
        "| task | trial | result | steps | cost | elapsed |",
        "| --- | --- | --- | ---: | ---: | ---: |",
    ]
    for row in sorted(payload["attempts"], key=lambda item: item["task"]):
        lines.append(
            "| %s | `%s` | %s | %s | %s | %s |"
            % (
                row["task"],
                row["trial"],
                headline(row),
                row.get("n_steps") if row.get("n_steps") is not None else "-",
                "$%.2f" % row["cost_usd"]
                if row.get("cost_usd") is not None else "-",
                "%.0fs" % row["elapsed_sec"]
                if row.get("elapsed_sec") is not None else "-",
            )
        )
    lines += [
        "",
        "## Resources",
        "",
        "- cumulative prompt tokens: %s" % format(
            sum(task["prompt_tokens"] for task in payload["tasks"]), ","),
        "- total cost: $%.2f" % sum(
            task["cost_usd"] for task in payload["tasks"]),
        "",
    ]
    report = "\n".join(lines)
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_dir", nargs="+")
    parser.add_argument("--tasks-root", required=True)
    parser.add_argument("-o", "--out", required=True)
    parser.add_argument("--expected-tasks", type=int, default=14)
    args = parser.parse_args(argv)

    job_dirs = [Path(path) for path in args.job_dir]
    trial_dirs = []
    for job_dir in job_dirs:
        result = read_json(job_dir / "result.json") or {}
        official = final_trial_names(result)
        trial_dirs.extend(
            path for path in sorted(job_dir.iterdir())
            if path.is_dir()
            and (path / "result.json").is_file()
            and (not official or path.name in official)
        )
    if not trial_dirs:
        sys.exit("no final trials found")

    rows = [
        score_passk_trial(path, args.tasks_root) for path in trial_dirs
    ]
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["task"]].append(row)

    selected = []
    for task, task_rows in sorted(grouped.items()):
        preferred = sorted(
            task_rows,
            key=lambda row: (
                bool((row.get("assessment") or {}).get("error")),
                not bool(row.get("has_trajectory")),
            ),
        )
        selected.append(preferred[0])
    if len(selected) != args.expected_tasks:
        sys.exit("expected %d tasks, got %d" % (
            args.expected_tasks, len(selected)))

    failures = [
        row["trial"] for row in selected
        if (row.get("assessment") or {}).get("error")
        or not row.get("has_trajectory")
    ]
    if failures:
        sys.exit("selected attempts need replacement: %r" % failures)

    summaries = [
        summarize_task(row["task"], [row], args.tasks_root)
        for row in selected
    ]
    scores = [float(row["assessment"]["score"]) for row in selected]
    payload = {
        "jobs": [str(path) for path in job_dirs],
        "tasks_root": str(args.tasks_root),
        "agent": selected[0].get("agent") or "unknown",
        "model": selected[0].get("model") or "unknown",
        "computed_pass_at_1": statistics.mean(
            summary["pass_at_1"] for summary in summaries),
        "overall_continuous_score_mean": statistics.mean(scores),
        "overall_continuous_score_variance": statistics.pvariance(scores),
        "n_continuous_scores": len(scores),
        "tasks": summaries,
        "attempts": selected,
    }
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "scores.json").write_text(
        json.dumps(payload, indent=1), encoding="utf-8")
    print(write_report(payload, out_dir))


if __name__ == "__main__":
    main()

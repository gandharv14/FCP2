#!/usr/bin/env python3
"""Score a multi-attempt Harbor job without collapsing attempts by task.

``xl_harbor_score.py`` intentionally keeps one successful row per task, which
is right for retry recovery but wrong for pass@k. This scorer instead reads the
job-level final trial lists, excluding superseded infrastructure retries while
preserving every independent ``--n-attempts`` sample. Each retained attempt is
graded continuously for quality statistics and discretely for pass@k.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from xl_harbor_score import headline, read_json, score_trial


def final_trial_names(job_result):
    """Names still counted in final JobStats (replaced retries are absent)."""
    names = set()
    for stats in (job_result.get("stats") or {}).get("evals", {}).values():
        for trials in (stats.get("reward_stats") or {}).values():
            for trial_names in trials.values():
                names.update(trial_names)
        for trial_names in (stats.get("exception_stats") or {}).values():
            names.update(trial_names)
    return names


def native_pass_at_k(job_result):
    found = []
    for stats in (job_result.get("stats") or {}).get("evals", {}).values():
        value = (stats.get("pass_at_k") or {}).get("5")
        if value is None:
            value = (stats.get("pass_at_k") or {}).get(5)
        if value is not None:
            found.append(float(value))
    return statistics.mean(found) if found else None


def passed(row):
    assessment = row.get("assessment") or {}
    return (
        not assessment.get("error")
        and assessment.get("kind") == "cell_value"
        and row.get("discrete_score") == 1.0
    )


def pass_at_k(n, c, k):
    if n < k:
        return None
    if n - c < k:
        return 1.0
    product = 1.0
    for i in range(k):
        product *= (n - c - i) / (n - i)
    return 1.0 - product


def score_passk_trial(trial_dir, tasks_root):
    """Attach continuous quality and a separate binary pass decision."""
    row = score_trial(trial_dir, tasks_root, grader_mode="continuous")
    discrete = score_trial(trial_dir, tasks_root, grader_mode="discrete")
    discrete_assessment = discrete.get("assessment") or {}
    row["discrete_score"] = discrete_assessment.get("score", 0.0)
    row["has_trajectory"] = (Path(trial_dir) / "agent" / "trajectory.json").is_file()
    return row


def output_reliability(task_dir, rows):
    outputs = read_json(task_dir / "tests" / "outputs.json") or []
    result = []
    for output in outputs:
        refs = set(output.get("refs") or [])
        n_solved = 0
        for row in rows:
            cells = {
                cell["ref"]: cell
                for cell in (row.get("assessment") or {}).get("cells") or []
            }
            if refs and all(cells.get(ref, {}).get("exact") for ref in refs):
                n_solved += 1
        result.append({
            "name": output.get("name"),
            "refs": sorted(refs),
            "n_solved": n_solved,
            "n_attempts": len(rows),
        })
    return result


def summarize_task(task, rows, tasks_root):
    valid = [
        row for row in rows
        if not (row.get("assessment") or {}).get("error")
    ]
    accuracies = [
        100.0 * row["assessment"]["accuracy_exact"] for row in valid
    ]
    close = [
        100.0 * row["assessment"]["accuracy_close_1pct"] for row in valid
    ]
    coverage = [100.0 * row["assessment"]["coverage"] for row in valid]
    continuous_scores = [
        float(row["assessment"]["score"]) for row in valid
    ]
    successes = sum(passed(row) for row in rows)
    n = len(rows)
    return {
        "task": task,
        "workbook": rows[0]["workbook"] if rows else task.split("-", 1)[0],
        "family": rows[0]["family"] if rows else "",
        "n_attempts": n,
        "n_graded": len(valid),
        "n_passed": successes,
        "pass_at_1": successes / n if n else 0.0,
        "pass_at_5": pass_at_k(n, successes, 5),
        "continuous_score_mean": (
            statistics.mean(continuous_scores) if continuous_scores else 0.0
        ),
        "continuous_score_variance": (
            statistics.pvariance(continuous_scores)
            if continuous_scores else 0.0
        ),
        "n_continuous_scores": len(continuous_scores),
        "mean_accuracy_exact": statistics.mean(accuracies) if accuracies else 0.0,
        "median_accuracy_exact": (
            statistics.median(accuracies) if accuracies else 0.0
        ),
        "best_accuracy_exact": max(accuracies) if accuracies else 0.0,
        "mean_accuracy_close_1pct": statistics.mean(close) if close else 0.0,
        "mean_coverage": statistics.mean(coverage) if coverage else 0.0,
        "prompt_tokens": sum(row.get("prompt_tokens") or 0 for row in rows),
        "completion_tokens": sum(
            row.get("completion_tokens") or 0 for row in rows
        ),
        "cost_usd": sum(row.get("cost_usd") or 0 for row in rows),
        "agent_hours": sum(row.get("elapsed_sec") or 0 for row in rows) / 3600.0,
        "outputs": output_reliability(Path(tasks_root) / task, rows),
    }


def write_report(payload, out_dir, job_dirs):
    rows = payload["attempts"]
    summaries = payload["tasks"]
    native = payload["native_pass_at_5"]
    computed = payload["computed_pass_at_5"]
    overall_mean = payload["overall_continuous_score_mean"]
    overall_variance = payload["overall_continuous_score_variance"]

    lines = [
        "# %s pass@5 on %s" % (payload["model"], payload["tasks_root"]),
        "",
        "- Harbor jobs: %s" % ", ".join("`%s`" % job for job in job_dirs),
        "- agent/model: `%s` / `%s`" % (
            payload["agent"], payload["model"]),
        "- attempts: %d (%d tasks x 5)" % (len(rows), len(summaries)),
        "- computed aggregate pass@5: %.1f%%" % (100 * computed),
        "- overall continuous score mean: %.6f" % overall_mean,
        "- overall continuous score population variance: %.6f" % overall_variance,
        "- Harbor native pass@5: %s" % (
            "%.1f%%" % (100 * native) if native is not None else "unavailable"
        ),
        "- native/computed cross-check: %s" % (
            "unavailable for continuous Harbor rewards" if native is None else
            "match" if payload["native_matches_computed"] else "different"
        ),
        "",
        "## Results by task",
        "",
        "| task | full passes | pass@1 | pass@5 | score mean | score variance | "
        "mean exact | median | best | within 1% | coverage | cost |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for summary in summaries:
        lines.append(
            "| %s | %d/%d | %.0f%% | %.0f%% | %.4f | %.6f | %.1f%% | "
            "%.1f%% | %.1f%% | %.1f%% | %.1f%% | $%.2f |"
            % (
                summary["task"], summary["n_passed"], summary["n_attempts"],
                100 * summary["pass_at_1"],
                100 * (summary["pass_at_5"] or 0),
                summary["continuous_score_mean"],
                summary["continuous_score_variance"],
                summary["mean_accuracy_exact"],
                summary["median_accuracy_exact"],
                summary["best_accuracy_exact"],
                summary["mean_accuracy_close_1pct"],
                summary["mean_coverage"],
                summary["cost_usd"],
            )
        )

    lines += [
        "",
        "## Attempt detail",
        "",
        "| task | trial | result | steps | prompt tokens | output tokens | "
        "cost | elapsed |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in sorted(rows, key=lambda item: (item["task"], item["trial"])):
        lines.append(
            "| %s | `%s` | %s | %s | %s | %s | %s | %s |"
            % (
                row["task"], row["trial"], headline(row),
                row.get("n_steps") if row.get("n_steps") is not None else "-",
                format(row.get("prompt_tokens"), ",")
                if row.get("prompt_tokens") is not None else "-",
                format(row.get("completion_tokens"), ",")
                if row.get("completion_tokens") is not None else "-",
                "$%.2f" % row["cost_usd"]
                if row.get("cost_usd") is not None else "-",
                "%.0fs" % row["elapsed_sec"]
                if row.get("elapsed_sec") is not None else "-",
            )
        )

    lines += ["", "## Output reliability", ""]
    for summary in summaries:
        def label(output):
            return "%s [%s]" % (
                output["name"], ", ".join(output["refs"])
            )

        always = [
            output for output in summary["outputs"]
            if output["n_solved"] == output["n_attempts"]
        ]
        sometimes = [
            output for output in summary["outputs"]
            if 0 < output["n_solved"] < output["n_attempts"]
        ]
        never = [
            output for output in summary["outputs"]
            if output["n_solved"] == 0
        ]
        lines += [
            "### %s" % summary["task"],
            "",
            "- solved in all five: %s" % (
                ", ".join(label(output) for output in always) or "none"
            ),
            "- solved in some attempts: %s" % (
                ", ".join(
                    "%s (%d/5)" % (label(output), output["n_solved"])
                    for output in sometimes
                ) or "none"
            ),
            "- solved in no attempts: %s" % (
                ", ".join(label(output) for output in never) or "none"
            ),
            "",
        ]

    total_cost = sum(summary["cost_usd"] for summary in summaries)
    total_tokens = sum(summary["prompt_tokens"] for summary in summaries)
    lines += [
        "## Resources",
        "",
        "- cumulative prompt tokens: %s" % format(total_tokens, ","),
        "- total cost: $%.2f" % total_cost,
        "",
    ]
    report = "\n".join(lines)
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Score Harbor pass@k job")
    parser.add_argument("job_dir", nargs="+")
    parser.add_argument("--tasks-root", required=True)
    parser.add_argument("-o", "--out", required=True)
    args = parser.parse_args(argv)

    job_dirs = [Path(path) for path in args.job_dir]
    job_results = [read_json(job / "result.json") or {} for job in job_dirs]
    trial_dirs = []
    for job_dir, job_result in zip(job_dirs, job_results):
        official = final_trial_names(job_result)
        trial_dirs.extend(
            path for path in sorted(job_dir.iterdir())
            if path.is_dir()
            and (path / "result.json").is_file()
            and (not official or path.name in official)
        )
    if not trial_dirs:
        sys.exit("no final trials found under %s" % ", ".join(args.job_dir))

    rows = [score_passk_trial(path, args.tasks_root) for path in trial_dirs]
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["task"]].append(row)
    # A supplemental job can replace an infrastructure failure. Prefer graded
    # attempts with a readable trajectory (the viewer needs it), then any
    # artifact-only graded attempt, and retain failed attempts only if needed.
    for task, task_rows in grouped.items():
        graded_with_trajectory = [
            row for row in task_rows
            if not (row.get("assessment") or {}).get("error")
            and row.get("has_trajectory")
        ]
        graded_without_trajectory = [
            row for row in task_rows
            if not (row.get("assessment") or {}).get("error")
            and not row.get("has_trajectory")
        ]
        failed = [
            row for row in task_rows
            if (row.get("assessment") or {}).get("error")
        ]
        grouped[task] = (
            graded_with_trajectory + graded_without_trajectory + failed
        )[:5]
    rows = [
        row for task in sorted(grouped) for row in grouped[task]
    ]
    summaries = [
        summarize_task(task, task_rows, args.tasks_root)
        for task, task_rows in sorted(grouped.items())
    ]
    if any(summary["n_attempts"] != 5 for summary in summaries):
        counts = {
            summary["task"]: summary["n_attempts"] for summary in summaries
        }
        sys.exit("expected exactly five final attempts per task, got %r" % counts)

    computed = statistics.mean(
        summary["pass_at_5"] or 0.0 for summary in summaries
    )
    continuous_scores = [
        float(row["assessment"]["score"])
        for row in rows
        if not (row.get("assessment") or {}).get("error")
    ]
    overall_mean = (
        statistics.mean(continuous_scores) if continuous_scores else 0.0
    )
    overall_variance = (
        statistics.pvariance(continuous_scores) if continuous_scores else 0.0
    )
    native = native_pass_at_k(job_results[0])
    native_matches = (
        native is not None
        and math.isclose(native, computed, rel_tol=0.0, abs_tol=1e-12)
    )

    payload = {
        "jobs": [str(job) for job in job_dirs],
        "tasks_root": str(args.tasks_root),
        "agent": rows[0].get("agent") or "unknown",
        "model": rows[0].get("model") or "unknown",
        "native_pass_at_5": native,
        "computed_pass_at_5": computed,
        "native_matches_computed": native_matches,
        "overall_continuous_score_mean": overall_mean,
        "overall_continuous_score_variance": overall_variance,
        "n_continuous_scores": len(continuous_scores),
        "tasks": summaries,
        "attempts": rows,
    }
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "scores.json").write_text(
        json.dumps(payload, indent=1), encoding="utf-8")
    print(write_report(payload, out_dir, job_dirs))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Report a Harbor agent job with the shared finance grading API.

For each trial we take the agent's ``/app/answers.json`` deliverable, load the
task's private ``tests/answer_key.json``, and replay the same continuous grader
used by the in-task Harbor verifier. This also supports older job directories
that predate ``reward-details.json``.

Alongside accuracy we report what the harness itself bought us: agent steps,
cumulative prompt tokens (which run far past a single context window) and cost.

    python3 xl_harbor_score.py jobs/openhands20 -o runs/openhands20
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from grader.finance_grader import grade as grade_finance
from xl_eval_run import assess

CUTOFF_RE = re.compile(r"-L(\d+)-")
TAXONOMY_PATH = Path("taxonomy_out/workbooks.json")


def read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def load_taxonomy():
    """{workbook stem: model family} so results can be grouped by family."""
    data = read_json(TAXONOMY_PATH) or {}
    return {Path(name).stem: entry.get("primary", "")
            for name, entry in data.items()}


TAXONOMY = load_taxonomy()


def family_for(workbook):
    return TAXONOMY.get(workbook, "")


def find_answers(trial_dir):
    """The deliverable arrives as a downloaded artifact or via verifier logs."""
    for rel in ("artifacts/app/answers.json", "verifier/answers.json"):
        data = read_json(trial_dir / rel)
        if data is not None:
            return data, rel
    return None, ""


def trajectory_stats(trial_dir):
    traj = read_json(trial_dir / "agent" / "trajectory.json") or {}
    metrics = traj.get("final_metrics") or {}
    steps = traj.get("steps") or []
    return {
        "n_steps": len(steps),
        "n_tool_calls": sum(len(s.get("tool_calls") or []) for s in steps),
        "prompt_tokens": metrics.get("total_prompt_tokens"),
        "completion_tokens": metrics.get("total_completion_tokens"),
        "cached_tokens": metrics.get("total_cached_tokens"),
        "cost_usd": metrics.get("total_cost_usd"),
    }


def elapsed_sec(result):
    from datetime import datetime

    def parse(value):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    exec_block = result.get("agent_execution") or {}
    try:
        return round((parse(exec_block["finished_at"])
                      - parse(exec_block["started_at"])).total_seconds(), 1)
    except (KeyError, TypeError, ValueError):
        return None


def score_trial(trial_dir, tasks_root, grader_mode="continuous"):
    config = read_json(trial_dir / "config.json") or {}
    result = read_json(trial_dir / "result.json") or {}
    agent = config.get("agent") or {}
    # a trial that failed before setup has no config.json, so fall back to the
    # directory name, which Harbor derives from the bundle name either way
    task_name = Path((config.get("task") or {}).get("path", "")).name \
        or trial_dir.name.split("__")[0]
    task_path = Path(tasks_root) / task_name if tasks_root \
        else Path((config.get("task") or {}).get("path", ""))

    row = {
        "task": task_name,
        "registry_name": result.get("task_name"),
        "trial": trial_dir.name,
        "agent": agent.get("name"),
        "model": agent.get("model_name"),
        "elapsed_sec": elapsed_sec(result),
        "exception": (result.get("exception_info") or {}).get("exception_type"),
    }
    row.update(trajectory_stats(trial_dir))

    match = CUTOFF_RE.search(task_name)
    row["cutoff"] = int(match.group(1)) if match else None
    row["template"] = task_name.split("-", 2)[-1] if "-" in task_name else ""
    row["workbook"] = task_name.split("-", 1)[0] if "-" in task_name else task_name
    row["family"] = family_for(row["workbook"])

    key = read_json(task_path / "tests" / "answer_key.json")
    answers, source = find_answers(trial_dir)
    row["answers_source"] = source
    if key is None:
        row["assessment"] = {"error": "answer key not found at %s" % task_path}
        return row
    if answers is None:
        row["assessment"] = {"kind": key.get("kind"),
                             "error": "agent produced no answers.json",
                             "correct": False}
        return row
    if key.get("kind") == "cell_value":
        row["assessment"] = grade_finance(
            answers, key, mode=grader_mode).score_details()
    else:
        row["assessment"] = assess(answers, key)
    return row


def headline(row):
    a = row.get("assessment") or {}
    if a.get("error"):
        return a["error"]
    if a.get("kind") == "cell_value":
        return ("score %.3f; %d/%d exact (%.0f%%), %d/%d within 1%%, "
                "coverage %.0f%%"
                % (a["score"], a["n_exact"], a["n_targets"],
                   100 * a["accuracy_exact"],
                   a["n_close_1pct"], a["n_targets"], 100 * a["coverage"]))
    return "correct" if a.get("correct") else "incorrect"


def write_report(rows, out_dir, job_dir):
    def num(value, fmt="%s"):
        return "-" if value is None else fmt % value

    lines = [
        "# OpenHands harness eval",
        "",
        "- job: `%s`" % job_dir,
        "- trials: %d" % len(rows),
        "",
        "| task | result | steps | tool calls | prompt tok | out tok | cost | elapsed |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in sorted(rows, key=lambda r: (r["workbook"], r["cutoff"] or 0,
                                           r["template"])):
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
            row["task"], headline(row), num(row["n_steps"]),
            num(row["n_tool_calls"]), num(row["prompt_tokens"], "%,d".replace(",", "")),
            num(row["completion_tokens"]), num(row["cost_usd"], "$%.2f"),
            num(row["elapsed_sec"], "%.0fs")))

    cell_rows = [r for r in rows
                 if (r["assessment"] or {}).get("kind") == "cell_value"
                 and not (r["assessment"] or {}).get("error")]

    def accuracy_table(title, key, label):
        if not cell_rows:
            return []
        out = ["", "## %s" % title, "",
               "| %s | tasks | cells | continuous | exact | within 1%% | "
               "coverage |" % label,
               "| --- | --- | --- | --- | --- | --- | --- |"]
        for value in sorted({key(r) for r in cell_rows}):
            group = [r["assessment"] for r in cell_rows if key(r) == value]
            n_targets = sum(a["n_targets"] for a in group)
            continuous = sum(a["score"] * a["n_targets"] for a in group)
            n_exact = sum(a["n_exact"] for a in group)
            n_close = sum(a["n_close_1pct"] for a in group)
            n_answered = sum(a["n_answered"] for a in group)
            out.append("| %s | %d | %d | %.1f%% | %d (%.0f%%) | "
                       "%d (%.0f%%) | %.0f%% |"
                       % (value, len(group), n_targets,
                          100.0 * continuous / max(n_targets, 1),
                          n_exact, 100.0 * n_exact / max(n_targets, 1),
                          n_close, 100.0 * n_close / max(n_targets, 1),
                          100.0 * n_answered / max(n_targets, 1)))
        return out

    lines += accuracy_table("Reconstruction accuracy by workbook",
                            lambda r: "%s (%s)" % (r["workbook"], r["family"]),
                            "workbook")
    if any(r["cutoff"] is not None for r in cell_rows):
        lines += accuracy_table("Reconstruction accuracy by cutoff",
                                lambda r: "L%02d" % r["cutoff"], "cutoff")
    lines += accuracy_table("Reconstruction accuracy by template",
                            lambda r: r["template"], "template")

    total_cost = sum(r["cost_usd"] or 0 for r in rows)
    total_prompt = sum(r["prompt_tokens"] or 0 for r in rows)
    lines += ["", "- cumulative prompt tokens: %d" % total_prompt,
              "- total cost: $%.2f" % total_cost, ""]

    report = "\n".join(lines)
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    return report


def keep_best(rows):
    """Collapse retried trials, keeping the attempt that actually scored.

    Harbor leaves the failed attempt's directory in place next to the retry,
    so scoring every directory would double count and drag the totals down.
    """
    best = {}
    for row in rows:
        graded = not (row.get("assessment") or {}).get("error")
        current = best.get(row["task"])
        if current is None:
            best[row["task"]] = (graded, row)
            continue
        if graded and not current[0]:
            best[row["task"]] = (graded, row)
    return [row for _, row in best.values()]


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Score Harbor agent jobs against the golden workbook")
    parser.add_argument("job_dir", nargs="+",
                        help="one or more Harbor job directories to merge")
    parser.add_argument("-o", "--out", default="")
    parser.add_argument("--tasks-root", action="append", default=[],
                        help="task bundle directory to resolve answer keys "
                             "against; repeat to search several")
    args = parser.parse_args(argv)

    job_dirs = [Path(p) for p in args.job_dir]
    trial_dirs = [p for job in job_dirs for p in sorted(job.iterdir())
                  if p.is_dir() and (p / "result.json").is_file()]
    if not trial_dirs:
        sys.exit("no trials found under %s" % ", ".join(args.job_dir))

    roots = args.tasks_root or [""]
    rows = []
    for trial_dir in trial_dirs:
        for root in roots:
            row = score_trial(trial_dir, root)
            if not (row["assessment"] or {}).get("error", "").startswith(
                    "answer key not found"):
                break
        rows.append(row)
    rows = keep_best(rows)

    job_dir = ", ".join(args.job_dir)
    out_dir = Path(args.out) if args.out else job_dirs[0]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "scores.json").write_text(
        json.dumps(rows, indent=1, default=str), encoding="utf-8")
    print(write_report(rows, out_dir, job_dir))


if __name__ == "__main__":
    main()

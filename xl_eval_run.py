#!/usr/bin/env python3
"""Run Harbor task bundles against a chat model through the Labelbox LiteLLM
proxy and record every step for later review.

This is a chat-level stand-in for `harbor run`: the agent model cannot execute
code here, so the artifact workbook is serialized to text and sent with the
instruction, and the model returns the JSON it would have written to
/app/answers.json. Every step of every trial is persisted:

    runs/<run_id>/
        run.json                     config, timings, per-task status
        report.md                    quantitative summary table
        <task>/
            steps.jsonl              timestamped step log
            01_task_meta.json        task.toml metadata + eval config
            02_instruction.md        the instruction shown to the model
            03_artifact_serialized.txt
            04_request.json          full chat payload
            05_response_raw.json     raw proxy response
            06_response.md           the model's text
            07_answers.json          extracted answer JSON
            08_assessment.json       quantitative comparison vs answer key

The assessment is comparative against tests/answer_key.json, whose expected
values come from the golden (fully calculated) workbook.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    import tomli
except ImportError:  # pragma: no cover
    try:
        import tomllib as tomli
    except ImportError:
        sys.exit("tomli is required:  python3 -m pip install tomli")

import openpyxl

from grader.finance_grader import grade_continuous
from xl_task_build import DEFAULT_PROJECT_ID, PROD_ENDPOINT, read_env_key

JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)

ADAPTER_NOTE = """\
## Evaluation adapter

For this evaluation you cannot execute code or read files. The full content of
the workbook is serialized below: one `Sheet!Cell = value` line per populated
cell; any cell not listed is blank (these are the calculated cells you must
reconstruct). Instead of writing a file, return the exact JSON content you
would have written to `/app/answers.json` inside a single ```json code block
at the end of your reply. Keep any explanation to a few sentences at most:
the JSON code block must be the bulk of your reply, with compact formatting
(no per-cell commentary).

## Serialized workbook
"""


def fmt_cell_value(value):
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        return "%.10g" % value
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return str(value)


def serialize_workbook(path):
    wb = openpyxl.load_workbook(path, data_only=True)
    lines = []
    for ws in wb.worksheets:
        lines.append("### sheet: %s" % ws.title)
        for row in ws.iter_rows():
            for cell in row:
                if cell.value is not None:
                    lines.append("%s!%s = %s"
                                 % (ws.title, cell.coordinate,
                                    fmt_cell_value(cell.value)))
        lines.append("")
    return "\n".join(lines)


def call_model(config, messages, max_tokens):
    """Streaming chat call, returned in non-streaming response shape.

    Streaming is required: Anthropic models refuse or stall on long
    non-streaming generations, and the proxy holds the connection silently.
    The socket timeout applies between chunks rather than to the whole call.
    """
    # the proxy rejects explicit temperature for several models; default only
    body = {"model": config["model"], "messages": messages, "stream": True,
            "stream_options": {"include_usage": True}}
    if max_tokens:
        body["max_tokens"] = max_tokens
    if config.get("no_thinking"):
        # the proxy enables Anthropic extended thinking by default; on large
        # tasks the whole token budget goes to thinking and no answer arrives
        body["thinking"] = {"type": "disabled"}
    elif config.get("reasoning_effort"):
        # the proxy rejects explicit thinking budgets but honors LiteLLM's
        # reasoning_effort, which caps the thinking budget so the visible
        # answer still fits inside max_tokens
        body["reasoning_effort"] = config["reasoning_effort"]
    request = urllib.request.Request(
        config["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"), method="POST",
        headers={
            "Authorization": "Bearer " + config["api_key"],
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "x-labelbox-context": json.dumps(
                {"project_id": config["project_id"]}),
        })
    content, finish, usage = [], "", {}
    with urllib.request.urlopen(request, timeout=config["timeout"]) as resp:
        for raw_line in resp:
            line = raw_line.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if chunk.get("usage"):
                usage = chunk["usage"]
            for choice in chunk.get("choices", []):
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    content.append(delta["content"])
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]
    return {"choices": [{"message": {"role": "assistant",
                                     "content": "".join(content)},
                         "finish_reason": finish}],
            "usage": usage}


def salvage_truncated_json(text):
    """Recover the complete key/value pairs of a truncated flat JSON object."""
    start = text.find("{")
    if start < 0:
        return None
    body = text[start:]
    end = body.rfind(",")
    while end > 0:
        try:
            return json.loads(body[:end] + "}")
        except json.JSONDecodeError:
            end = body.rfind(",", 0, end)
    return None


def extract_answers(text):
    blocks = JSON_BLOCK_RE.findall(text)
    for block in reversed(blocks):
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            continue
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass
    # a generation cut off by max_tokens leaves an unterminated JSON block;
    # salvage the complete entries so truncation degrades to partial coverage
    fence = text.rfind("```json")
    tail = text[fence:] if fence >= 0 else text
    return salvage_truncated_json(tail)


# ---------------------------------------------------------------------------
# quantitative assessment vs the answer key (golden workbook values)
# ---------------------------------------------------------------------------

def norm_ref(ref):
    return str(ref).replace("'", "").replace("$", "").strip()


def as_number(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except ValueError:
        return None


def numeric_match(got, expected, abs_tol, rel_tol):
    if got is None or expected is None:
        return False
    if abs(got - expected) <= abs_tol:
        return True
    scale = max(abs(expected), 1e-12)
    return abs(got - expected) / scale <= rel_tol


def as_datetime(value):
    """Parse the datetime spellings openpyxl and json.dumps produce.

    A date cell round-trips as ``2025-05-31T00:00:00`` through the answer key
    but agents commonly write ``2025-05-31 00:00:00`` or bare ``2025-05-31``.
    Those are the same instant and must not be graded as a miss.
    """
    if isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.date):
        return dt.datetime(value.year, value.month, value.day)
    if not isinstance(value, str):
        return None
    text = value.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%m/%d/%Y", "%m/%d/%y"):
        try:
            return dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def same_text(got, expected):
    got_dt, expected_dt = as_datetime(got), as_datetime(expected)
    if got_dt is not None and expected_dt is not None:
        return got_dt == expected_dt
    return str(got).strip() == str(expected).strip()


def assess_cell_values(answers, key):
    return grade_continuous(answers, key).score_details()


def assess_derived(answers, key):
    tol = key.get("tolerance", {})
    expected = key.get("answer")
    got = None
    if isinstance(answers, dict):
        got = answers.get("answer")
        if got is None and "change" in answers:      # trace_driver
            got = answers.get("change")
    got_num, expected_num = as_number(got), as_number(expected)
    correct = numeric_match(got_num, expected_num,
                            tol.get("numeric_abs", 1e-6),
                            tol.get("numeric_rel", 1e-6))
    out = {"kind": "derived", "expected": expected, "got": got,
           "correct": bool(correct)}
    if "line_item" in key and isinstance(answers, dict):
        want = str(key["line_item"]).strip().lower()
        have = str(answers.get("line_item", "")).strip().lower()
        out["line_item_expected"] = key["line_item"]
        out["line_item_got"] = answers.get("line_item")
        out["line_item_correct"] = want == have
        out["correct"] = out["correct"] and out["line_item_correct"]
    return out


def assess_boolean(answers, key):
    tol = key.get("tolerance", {})
    got_verdict = ""
    if isinstance(answers, dict):
        got_verdict = str(answers.get("verdict", "")).strip().lower()
    verdict_ok = got_verdict == str(key.get("verdict", "")).strip().lower()
    out = {"kind": "boolean+value",
           "verdict_expected": key.get("verdict"), "verdict_got": got_verdict,
           "verdict_correct": verdict_ok}
    for field in ("value", "first", "last"):
        if field in key:
            expected_num = as_number(key[field])
            got_num = as_number(answers.get(field)) \
                if isinstance(answers, dict) else None
            out[field + "_expected"] = key[field]
            out[field + "_got"] = answers.get(field) \
                if isinstance(answers, dict) else None
            out[field + "_correct"] = numeric_match(
                got_num, expected_num, tol.get("numeric_abs", 1e-6),
                tol.get("numeric_rel", 1e-6))
    value_fields = [k for k in ("value", "first", "last") if k in key]
    out["correct"] = verdict_ok and all(out[f + "_correct"]
                                        for f in value_fields)
    return out


def assess(answers, key):
    if answers is None:
        return {"kind": key.get("kind"), "error": "no JSON answers extracted",
                "correct": False}
    kind = key.get("kind")
    if kind == "cell_value":
        return assess_cell_values(answers, key)
    if kind == "derived":
        return assess_derived(answers, key)
    if kind == "boolean+value":
        return assess_boolean(answers, key)
    return {"kind": kind, "error": "unknown answer kind", "correct": False}


# ---------------------------------------------------------------------------
# trial
# ---------------------------------------------------------------------------

class StepLog:
    def __init__(self, path):
        self.path = path
        self.steps = []

    def add(self, step, detail=""):
        self.steps.append({
            "ts": dt.datetime.now(dt.timezone.utc).isoformat(),
            "step": step, "detail": detail,
        })
        with open(self.path, "w", encoding="utf-8") as fh:
            for row in self.steps:
                fh.write(json.dumps(row) + "\n")


def run_trial(task_dir, run_dir, config):
    name = task_dir.name
    out = run_dir / name
    out.mkdir(parents=True, exist_ok=True)
    log = StepLog(out / "steps.jsonl")
    started = time.time()
    result = {"task": name, "status": "ok", "elapsed_sec": 0.0}
    try:
        log.add("load_task", str(task_dir))
        meta = tomli.loads((task_dir / "task.toml").read_text(encoding="utf-8"))
        instruction = (task_dir / "instruction.md").read_text(encoding="utf-8")
        artifact = next((task_dir / "environment").glob("L*.xls*"))
        key = json.loads((task_dir / "tests" / "answer_key.json")
                         .read_text(encoding="utf-8"))
        with open(out / "01_task_meta.json", "w", encoding="utf-8") as fh:
            json.dump({"task_toml": meta, "eval_model": config["model"],
                       "artifact": artifact.name}, fh, indent=1, default=str)
        (out / "02_instruction.md").write_text(instruction, encoding="utf-8")

        log.add("serialize_artifact", artifact.name)
        serialized = serialize_workbook(artifact)
        (out / "03_artifact_serialized.txt").write_text(serialized,
                                                        encoding="utf-8")

        user_content = instruction + "\n" + ADAPTER_NOTE + "\n" + serialized
        messages = [{"role": "user", "content": user_content}]
        with open(out / "04_request.json", "w", encoding="utf-8") as fh:
            json.dump({"model": config["model"],
                       "max_tokens": config["max_tokens"],
                       "messages": messages}, fh, indent=1)

        log.add("call_model", config["model"])
        response = None
        for attempt in range(2):
            try:
                response = call_model(config, messages, config["max_tokens"])
                break
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", "replace")[:400]
                log.add("model_error", "HTTP %s: %s" % (exc.code, body))
                if exc.code == 400 and attempt == 0 and config["max_tokens"]:
                    log.add("retry", "without max_tokens")
                    response = call_model(config, messages, None)
                    break
                if attempt == 0 and exc.code >= 500:
                    time.sleep(5)
                    continue
                raise
        with open(out / "05_response_raw.json", "w", encoding="utf-8") as fh:
            json.dump(response, fh, indent=1)
        text = response["choices"][0]["message"]["content"] or ""
        finish = response["choices"][0].get("finish_reason", "")
        if not text and not response.get("usage", {}).get("completion_tokens"):
            # instant empty completion: transient provider rejection, retry once
            log.add("empty_completion_retry", "finish_reason=%s" % finish)
            response = call_model(config, messages, config["max_tokens"])
            text = response["choices"][0]["message"]["content"] or ""
            finish = response["choices"][0].get("finish_reason", "")
        (out / "06_response.md").write_text(text, encoding="utf-8")
        if not text:
            log.add("empty_content", "finish_reason=%s" % finish)
        log.add("response", "finish_reason=%s chars=%d" % (finish, len(text)))

        answers = extract_answers(text)
        with open(out / "07_answers.json", "w", encoding="utf-8") as fh:
            json.dump(answers, fh, indent=1)
        log.add("extract_answers",
                "none" if answers is None else "%d keys" % len(answers))

        verdict = assess(answers, key)
        verdict["finish_reason"] = finish
        verdict["usage"] = response.get("usage", {})
        with open(out / "08_assessment.json", "w", encoding="utf-8") as fh:
            json.dump(verdict, fh, indent=1)
        log.add("assess", json.dumps({k: v for k, v in verdict.items()
                                      if k != "cells"})[:400])
        result["assessment"] = {k: v for k, v in verdict.items()
                                if k != "cells"}
    except Exception as exc:  # keep other trials alive
        result["status"] = "error"
        result["error"] = "%s: %s" % (type(exc).__name__, exc)
        log.add("trial_error", result["error"])
    result["elapsed_sec"] = round(time.time() - started, 1)
    log.add("done", result["status"])
    return result


def summarize(results, run_dir, config):
    lines = ["# Eval run report", "",
             "- model: `%s`" % config["model"],
             "- endpoint: %s" % config["base_url"],
             "- concurrency: %d" % config["concurrency"],
             "- run dir: %s" % run_dir, "",
             "| task | status | headline | elapsed |",
             "| --- | --- | --- | --- |"]
    for res in results:
        a = res.get("assessment", {})
        if res["status"] != "ok":
            headline = res.get("error", "error")
        elif a.get("error"):
            headline = a["error"]
        elif a.get("kind") == "cell_value":
            headline = ("score %.3f; %d/%d exact (%.0f%%), %d/%d within 1%%, "
                        "coverage %.0f%%"
                        % (a["score"], a["n_exact"], a["n_targets"],
                           100 * a["accuracy_exact"],
                           a["n_close_1pct"], a["n_targets"],
                           100 * a["coverage"]))
        else:
            headline = "correct" if a.get("correct") else "incorrect"
        lines.append("| %s | %s | %s | %ss |"
                     % (res["task"], res["status"], headline,
                        res["elapsed_sec"]))
    report = "\n".join(lines) + "\n"
    (run_dir / "report.md").write_text(report, encoding="utf-8")
    print("\n" + report)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Evaluate Harbor task bundles against a chat model")
    parser.add_argument("tasks", help="directory of task bundles")
    parser.add_argument("--model", default="anthropic/claude-opus-5")
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--max-tokens", type=int, default=32000)
    parser.add_argument("--timeout", type=float, default=1800.0)
    parser.add_argument("--runs-dir", default="runs")
    parser.add_argument("--no-thinking", action="store_true",
                        help="disable Anthropic extended thinking (needed for "
                             "large-output tasks)")
    parser.add_argument("--reasoning-effort", default="",
                        choices=["", "none", "minimal", "low", "medium", "high"],
                        help="bound extended thinking via LiteLLM's "
                             "reasoning_effort (explicit thinking budgets are "
                             "rejected by the proxy)")
    parser.add_argument("--dev", action="store_true")
    parser.add_argument("--project-id", default=DEFAULT_PROJECT_ID)
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args(argv)

    api_key = read_env_key(args.env_file)
    if not api_key:
        sys.exit("no lbx_api_key found in %s or environment" % args.env_file)
    config = {
        "model": args.model,
        "base_url": "https://litellm.lb-dev.xyz" if args.dev else PROD_ENDPOINT,
        "project_id": args.project_id,
        "api_key": api_key,
        "max_tokens": args.max_tokens,
        "timeout": args.timeout,
        "concurrency": args.concurrency,
        "no_thinking": args.no_thinking,
        "reasoning_effort": args.reasoning_effort,
    }

    task_dirs = sorted(p for p in Path(args.tasks).iterdir()
                       if p.is_dir() and (p / "task.toml").is_file())
    if not task_dirs:
        sys.exit("no task bundles under %s" % args.tasks)

    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = Path(args.runs_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    print("run %s: %d task(s), model %s, concurrency %d"
          % (run_id, len(task_dirs), args.model, args.concurrency), flush=True)

    started = time.time()
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=args.concurrency) as pool:
        futures = {pool.submit(run_trial, td, run_dir, config): td
                   for td in task_dirs}
        results = []
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            results.append(res)
            print("  %-40s %s (%.0fs)" % (res["task"], res["status"],
                                          res["elapsed_sec"]), flush=True)
    results.sort(key=lambda r: r["task"])

    with open(run_dir / "run.json", "w", encoding="utf-8") as fh:
        json.dump({"run_id": run_id, "model": args.model,
                   "endpoint": config["base_url"],
                   "concurrency": args.concurrency,
                   "max_tokens": args.max_tokens,
                   "thinking_disabled": args.no_thinking,
                   "reasoning_effort": args.reasoning_effort,
                   "elapsed_sec": round(time.time() - started, 1),
                   "results": results}, fh, indent=1)
    summarize(results, run_dir, config)


if __name__ == "__main__":
    main()

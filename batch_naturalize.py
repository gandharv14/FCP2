#!/usr/bin/env python3
"""Batch instruction naturalisation with mechanical fidelity enforcement.

Rewriting prose by hand does not scale to a full wave, and free-form rewrites
break the disclosure gate: the verifier requires every disclosed cell band and
the exact `## Output` anchor to survive verbatim. So each rewrite is checked
against the original and reverted unless it preserves:

  * the `## Output` anchor,
  * every backtick-quoted cell reference,
  * every numeric literal.

Only instruction.md is touched; workbooks, answer keys and tests are never read.
"""
import json
import re
import sys
import shutil
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PROXY_URL = "https://litellm.labelbox.com/chat/completions"
PROXY_MODEL = "openai/gpt-5.6-sol"
PROXY_PROJECT_ID = "cms6m4urm006n07z8ecxi1oi2"
ENV_FILE = Path(".env")
ANCHOR = "\n## Output\n"

PROMPT = """Rewrite this task instruction so it reads like a human analyst wrote it, without changing any factual content.

YOU MAY: improve prose phrasing, sentence flow and paragraph structure in the narrative parts; remove pipeline artifacts (mentions of gates, normalizers, internal snake_case slugs used as prose, robotic repetition, duplicated boilerplate); make the opening read like a real assignment brief.

YOU MUST NOT CHANGE, and an automated checker will reject you if you do:
- The exact heading line `## Output` must survive verbatim on its own line.
- Every backtick-quoted cell reference (e.g. `Model!L48:W48`, `'Acc-Dil'!F59`) must survive verbatim. Do not reorder, merge, summarise or drop any.
- Every number, date, percentage, currency amount and unit must survive verbatim.
- Every sheet name, file name and range.
- Every tool/MCP call name and the mechanics of how to fetch inputs.
- Every stated assumption, convention or method constraint.
- The set of required outputs and where they go.
- Any section listing disclosed assumptions/parameters: keep its heading and every bullet byte-identical.

Do not add new facts, hints, formulas or worked examples. Do not reveal answers. Do not remove a constraint; if unsure, keep it.

Reply with ONLY the rewritten Markdown. No commentary, no code fence.

--- INSTRUCTION TO REWRITE ---
{body}
"""


def env_value(wanted):
    if not ENV_FILE.exists():
        return ""
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        name, _, value = line.partition("=")
        if name.strip() == wanted:
            return value.strip().strip("'\"")
    return ""


def call_proxy(prompt, api_key, timeout=420, attempts=3):
    body = {"model": PROXY_MODEL, "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(
        PROXY_URL,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "x-labelbox-context": json.dumps({"project_id": PROXY_PROJECT_ID}),
        },
    )
    last = None
    for i in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            return payload["choices"][0]["message"]["content"]
        except Exception as e:
            last = e
            time.sleep(4 * (i + 1))
    raise RuntimeError(f"proxy failed: {last}")


CELLREF = re.compile(r"`[^`\n]*![$A-Z]{1,3}\$?\d{1,6}(?::[$A-Z]{1,3}\$?\d{1,6})?`")
NUMBER = re.compile(r"-?\d[\d,]*\.?\d*%?")


def facts(text):
    return {
        "refs": sorted(CELLREF.findall(text)),
        "nums": sorted(NUMBER.findall(text)),
    }


def strip_fence(t):
    t = t.strip()
    if t.startswith("```"):
        parts = t.split("```")
        if len(parts) >= 3:
            t = parts[1]
            if t.startswith("markdown"):
                t = t[len("markdown"):]
            elif t.startswith("md"):
                t = t[2:]
    return t.strip() + "\n"


def do_one(wb, api_key):
    p = Path(f"tasks_outputs_mcp/{wb}-outputs/instruction.md")
    if not p.exists():
        return wb, "MISSING", ""
    original = p.read_text(encoding="utf-8")
    bak = p.with_suffix(".md.orig")
    if not bak.exists():
        shutil.copy2(p, bak)

    try:
        new = strip_fence(call_proxy(PROMPT.format(body=original), api_key))
    except Exception as e:
        return wb, "PROXY-FAIL", str(e)[:80]

    if len(new) < 200:
        return wb, "REVERT", "reply too short"
    if ANCHOR not in new:
        return wb, "REVERT", "lost ## Output anchor"

    a, b = facts(original), facts(new)
    missing_refs = [r for r in a["refs"] if r not in b["refs"]]
    if missing_refs:
        return wb, "REVERT", f"dropped {len(missing_refs)} cell refs"
    missing_nums = [n for n in set(a["nums"]) if n not in set(b["nums"])]
    if missing_nums:
        return wb, "REVERT", f"dropped {len(missing_nums)} numbers"

    p.write_text(new, encoding="utf-8")
    return wb, "OK", f"{len(original.splitlines())}->{len(new.splitlines())} lines"


def main():
    ids = [l.strip() for l in open(sys.argv[1]) if l.strip()]
    workers = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    api_key = env_value("lbx_api_key")
    if not api_key:
        print("NATURALIZE-ABORT no lbx_api_key")
        return 1
    ok = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(do_one, wb, api_key): wb for wb in ids}
        for f in futs:
            wb = futs[f]
            try:
                wb, status, note = f.result()
            except Exception as e:
                status, note = "ERROR", f"{type(e).__name__}: {e}"
            print(f"N {wb} {status} {note}", flush=True)
            if status == "OK":
                ok += 1
    print(f"NATURALIZE-COMPLETE ok={ok}/{len(ids)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

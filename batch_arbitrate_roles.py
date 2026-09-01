#!/usr/bin/env python3
"""Batch role arbitration for the disclosure gate.

Gate 12 stops whenever regex matching leaves a row's method role ambiguous and
asks for Sol High to choose. Dispatching one agent per workbook does not scale
past a handful, so this resolves every pending workbook through the same
LiteLLM proxy the variable-source audit uses.

Only the ambiguous_roles.json cases are read: no workbooks, formulas, values or
graded cells, so arbitration cannot see the answers.
"""
import json
import sys
import urllib.request
import urllib.error
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PROXY_URL = "https://litellm.labelbox.com/chat/completions"
PROXY_MODEL = "openai/gpt-5.6-sol"
PROXY_PROJECT_ID = "cms6m4urm006n07z8ecxi1oi2"
ENV_FILE = Path(".env")

PROMPT = """You are arbitrating finance-row roles for a disclosure gate.

For each case below, pick exactly one candidate id from that case's `roles`
list, or null if no candidate is defensible.

Rules:
- The row's OWN label beats neighbouring labels.
- A modifier such as "sales" inside "sales expenses" is not revenue.
- Generic labels (Other, Insurance, Total ...) should abstain with null unless
  one candidate is the only reading a finance person would keep.
- Never invent a role that was not a candidate.
- Cover every case_id exactly once.

CASES:
{cases}

Reply with ONLY a JSON object, no prose and no code fence:
{{"resolutions":[{{"case_id":"...","chosen":"<candidate id or null>","reason":"<short>"}}]}}
"""


def env_value(wanted):
    if not ENV_FILE.exists():
        return ""
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        name, _, value = line.partition("=")
        if name.strip() == wanted:
            return value.strip().strip("'\"")
    return ""


def describe(case):
    trig = case.get("triggered_by") or {}
    own = trig.get("own_label") or {}
    nb = trig.get("neighbors") or {}
    qs = case.get("questions") or {}
    lines = [
        f"case_id: {case.get('case_id')}",
        f"  label: {case.get('label')!r}",
        f"  candidates: {case.get('roles')}",
        f"  nearby labels: {case.get('role_context')}",
        f"  matched on OWN label: {json.dumps(own)}",
        f"  matched only via NEIGHBOURS: {json.dumps(nb)}",
    ]
    for rid, q in qs.items():
        lines.append(f"  candidate {rid} asks: {q}")
    return "\n".join(lines)


def call_proxy(prompt, api_key, timeout=300, attempts=4):
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
        except Exception as e:  # transient proxy/rate errors
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"proxy failed after {attempts} attempts: {last}")


def parse(text):
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```")[1]
        if t.startswith("json"):
            t = t[4:]
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("no JSON object in reply")
    return json.loads(t[start:end + 1])


def do_one(wb, api_key):
    src = Path(f"runs/disclosure/{wb}-outputs/ambiguous_roles.json")
    dst = Path(f"runs/disclosure/{wb}-outputs/role_resolutions.json")
    if dst.exists():
        return wb, "CACHED", 0, 0
    if not src.exists():
        return wb, "NOCASES", 0, 0
    payload = json.loads(src.read_text(encoding="utf-8"))
    cases = payload.get("cases", [])
    if not cases:
        return wb, "NOCASES", 0, 0

    by_id = {c.get("case_id"): c for c in cases}
    resolutions = []
    # Keep request bodies modest so the proxy does not truncate the reply.
    CHUNK = 25
    for i in range(0, len(cases), CHUNK):
        chunk = cases[i:i + CHUNK]
        text = call_proxy(
            PROMPT.format(cases="\n".join(describe(c) for c in chunk)), api_key)
        got = parse(text).get("resolutions", [])
        for r in got:
            cid = r.get("case_id")
            if cid not in by_id:
                continue
            chosen = r.get("chosen")
            if isinstance(chosen, str) and chosen.lower() in ("null", "none", ""):
                chosen = None
            # Never accept a role that was not offered for this case.
            if chosen is not None and chosen not in (by_id[cid].get("roles") or []):
                chosen = None
            resolutions.append({
                "case_id": cid,
                "label": by_id[cid].get("label"),
                "chosen": chosen,
                "reason": (r.get("reason") or "")[:200],
            })

    seen = {r["case_id"] for r in resolutions}
    for cid, c in by_id.items():
        if cid not in seen:  # abstain rather than leave a case uncovered
            resolutions.append({
                "case_id": cid, "label": c.get("label"), "chosen": None,
                "reason": "no arbitration returned; abstained",
            })

    dst.write_text(json.dumps(
        {"agent_model": "gpt-5.6-sol-high", "resolutions": resolutions},
        indent=2) + "\n", encoding="utf-8")
    nulls = sum(1 for r in resolutions if r["chosen"] is None)
    return wb, "OK", len(resolutions), nulls


def main():
    ids = [l.strip() for l in open(sys.argv[1]) if l.strip()]
    workers = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    api_key = env_value("lbx_api_key")
    if not api_key:
        print("ARBITRATE-ABORT no lbx_api_key in .env")
        return 1
    ok = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(do_one, wb, api_key): wb for wb in ids}
        for f in futs:
            pass
        for f in futs:
            wb = futs[f]
            try:
                wb, status, n, nulls = f.result()
                print(f"A {wb} {status} resolutions={n} nulls={nulls}", flush=True)
                if status in ("OK", "CACHED"):
                    ok += 1
            except Exception as e:
                print(f"A {wb} ERROR {type(e).__name__}: {e}", flush=True)
    print(f"ARBITRATE-COMPLETE ok={ok}/{len(ids)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

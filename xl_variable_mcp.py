#!/usr/bin/env python3
"""Turn an audited variable-sources table into a mock MCP research environment.

Three subcommands, run in order:

    # 1. Preserve every Markdown row as a draft review queue (audit artifact)
    python3 xl_variable_mcp.py import \\
        runs/0233-variable-sources/0233-inputs-variable-sources.md \\
        runs/0233-variable-sources/draft.json

    # 2. Build the environment from a hand-normalized spec. Every variable's
    #    raw cell value is verified against the golden workbook before anything
    #    is emitted -- the MCP must serve exactly what the masked cells held.
    python3 xl_variable_mcp.py build \\
        runs/0233-variable-sources/normalized.json \\
        runs/0233-variable-sources/mcp \\
        --workbook 0233 --source "4-10 100"

    # 3. Exercise the generated server with an in-memory MCP client
    python3 xl_variable_mcp.py smoke runs/0233-variable-sources/mcp

``build`` writes, under the output directory:

    runtime/            the served data (sources, documents, datasets, records)
    eval/tasks.jsonl    prompts, typed answers, evidence ids -- NEVER shipped
    server.py           standalone FastMCP sidecar (streamable-http :8000/mcp)
    Dockerfile          sidecar image
    mask_cells.json     every workbook cell the masker must additionally blank
    masked_inputs.json  audit map: variable -> refs, raw value, MCP evidence

The normalized spec follows the scenario schema of the build-variable-source-mcp
skill, extended with per-variable ``workbook`` metadata::

    "workbook": {"cells": ["Model!F44", "Control!I62:J62"], "value": 25}

``workbook.value`` is the raw value the cells hold. When omitted it is derived
from ``value``: percent units divide by 100, ISO date strings compare against
date cells, everything else compares as-is.
"""

from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp_env.build import build, load, validate_spec, write_json
from mcp_env.import_table import import_table
from mcp_env.server_assets import SERVER_PY, SIDECAR_DOCKERFILE
from mcp_env.validate import validate


# ---------------------------------------------------------------------------
# golden-workbook verification
# ---------------------------------------------------------------------------

def split_ref(ref):
    sheet, _, coords = ref.rpartition("!")
    return sheet.strip("'"), coords


def expand_cells(refs):
    """['Model!F44', 'Control!I62:J62'] -> ['Model!F44', 'Control!I62', ...]."""
    from openpyxl.utils import get_column_letter, range_boundaries
    out = []
    for ref in refs:
        sheet, coords = split_ref(ref)
        min_col, min_row, max_col, max_row = range_boundaries(coords)
        for row in range(min_row, max_row + 1):
            for col in range(min_col, max_col + 1):
                out.append("%s!%s%d" % (sheet, get_column_letter(col), row))
    return out


def raw_value(variable):
    workbook = variable.get("workbook") or {}
    if "value" in workbook:
        return workbook["value"]
    value = variable["value"]
    if str(variable.get("unit", "")).casefold() == "percent" and \
            isinstance(value, (int, float)) and not isinstance(value, bool):
        return value / 100.0
    return value


def value_matches(cell_value, expected):
    if cell_value is None:
        return False
    if isinstance(expected, str):
        try:
            wanted = datetime.date.fromisoformat(expected)
        except ValueError:
            return str(cell_value).strip().casefold() == expected.strip().casefold()
        if isinstance(cell_value, datetime.datetime):
            return cell_value.date() == wanted
        if isinstance(cell_value, datetime.date):
            return cell_value == wanted
        return False
    if isinstance(cell_value, bool) or not isinstance(cell_value, (int, float)):
        return False
    return abs(float(cell_value) - float(expected)) <= \
        1e-9 * max(1.0, abs(float(expected)))


def verify_against_golden(spec, golden_path):
    """Every masked cell must hold exactly the value the MCP will serve."""
    import openpyxl
    warnings.simplefilter("ignore")
    book = openpyxl.load_workbook(golden_path, data_only=True)
    faults = []
    for variable in spec["variables"]:
        workbook = variable.get("workbook") or {}
        refs = workbook.get("cells") or []
        if not refs:
            continue
        expected = raw_value(variable)
        for ref in expand_cells(refs):
            sheet, coords = split_ref(ref)
            if sheet not in book.sheetnames:
                faults.append("%s: unknown sheet in %s" % (variable["id"], ref))
                continue
            actual = book[sheet][coords].value
            if not value_matches(actual, expected):
                faults.append("%s: %s holds %r, spec says %r"
                              % (variable["id"], ref, actual, expected))
    book.close()
    return faults


# ---------------------------------------------------------------------------
# subcommands
# ---------------------------------------------------------------------------

def cmd_import(args):
    result = import_table(Path(args.input))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                      encoding="utf-8")
    print(json.dumps({"rows": result["row_count"], "output": str(output)}, indent=2))
    return 0


def cmd_build(args):
    spec = load(Path(args.spec))
    validate_spec(spec)

    golden = None
    if args.workbook:
        for suffix in (".xlsx", ".xlsm"):
            candidate = Path(args.source) / (args.workbook + suffix)
            if candidate.exists():
                golden = candidate
                break
        if golden is None:
            sys.exit("no golden workbook %s under %s" % (args.workbook, args.source))
        faults = verify_against_golden(spec, golden)
        if faults:
            for fault in faults:
                print("  GOLDEN MISMATCH  %s" % fault)
            sys.exit("%d golden-value mismatch(es); fix the spec" % len(faults))

    output = Path(args.output)
    summary = build(spec, output)
    (output / "server.py").write_text(SERVER_PY, encoding="utf-8")
    (output / "Dockerfile").write_text(SIDECAR_DOCKERFILE, encoding="utf-8")

    mask_refs = sorted({
        ref
        for variable in spec["variables"]
        for ref in expand_cells(
            ((variable.get("workbook") or {}).get("cells") or [])
            + ((variable.get("workbook") or {}).get("extra_cells") or []))
    })
    write_json(output / "mask_cells.json", mask_refs)

    tasks = {row["task_id"]: row for row in (
        json.loads(line)
        for line in (output / "eval" / "tasks.jsonl").read_text(
            encoding="utf-8").splitlines() if line.strip())}
    audit = []
    for variable in spec["variables"]:
        workbook = variable.get("workbook") or {}
        refs = workbook.get("cells") or []
        if not refs:
            continue
        raw = raw_value(variable)
        if isinstance(raw, (datetime.date, datetime.datetime)):
            raw = raw.isoformat()
        audit.append({
            "variable_id": variable["id"],
            "name": variable["name"],
            "refs": expand_cells(refs + (workbook.get("extra_cells") or [])),
            "cell_value": raw,
            "mcp_value": variable["value"],
            "unit": variable["unit"],
            "question": variable["question"],
            "evidence": tasks[variable["id"]]["evidence"],
        })
    write_json(output / "masked_inputs.json", audit)

    report = validate(output)
    summary.update({
        "validation": report,
        "mask_cells": len(mask_refs),
        "golden_checked_against": str(golden) if golden else None,
    })
    print(json.dumps(summary, indent=2))
    return 0


def cmd_smoke(args):
    import asyncio

    bundle = Path(args.bundle)
    spec = importlib.util.spec_from_file_location("generated_mcp_server",
                                                  bundle / "server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    server = module.build_server(bundle / "runtime")

    async def run():
        from fastmcp import Client
        async with Client(server) as client:
            tools = sorted(tool.name for tool in await client.list_tools())
            datasets = await client.call_tool("list_datasets", {"limit": 100})
            checks = {"tools": tools, "datasets": len(datasets.data), "resolved": 0,
                      "broad_conflicts": 0, "failures": []}
            tasks = [json.loads(line) for line in
                     (bundle / "eval" / "tasks.jsonl").read_text(
                         encoding="utf-8").splitlines() if line.strip()]
            for task in tasks:
                wanted = task["required_dimensions"]
                evidence = task["evidence"]
                metric = wanted["metric"]
                result = await client.call_tool("query_records", {
                    "dataset_id": evidence["dataset_id"], "metric": metric,
                    "entity": wanted["entity"], "period": str(wanted["period"]),
                    "scenario": wanted["scenario"], "basis": wanted["basis"],
                    "unit": wanted["unit"], "status": wanted["status"],
                    "limit": 100,
                })
                rows = result.data["rows"]
                exact = [row for row in rows if row["id"] == evidence["record_id"]]
                if len(exact) == 1 and \
                        exact[0]["value"] == task["answer"]["value"]:
                    checks["resolved"] += 1
                else:
                    checks["failures"].append(task["task_id"])
                broad = await client.call_tool("query_records", {
                    "dataset_id": evidence["dataset_id"], "metric": metric,
                    "limit": 100,
                })
                values = {json.dumps(row["value"], sort_keys=True)
                          for row in broad.data["rows"]}
                if len(values) > 1:
                    checks["broad_conflicts"] += 1
            return checks

    checks = asyncio.run(run())
    print(json.dumps(checks, indent=2))
    return 1 if checks["failures"] else 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_import = sub.add_parser("import", help="Markdown table -> draft review queue")
    p_import.add_argument("input")
    p_import.add_argument("output")
    p_import.set_defaults(func=cmd_import)

    p_build = sub.add_parser("build", help="normalized spec -> MCP bundle")
    p_build.add_argument("spec")
    p_build.add_argument("output")
    p_build.add_argument("--workbook", default="",
                         help="golden workbook id to verify raw cell values against")
    p_build.add_argument("--source", default="4-10 100",
                         help="folder holding the golden <wb>.xlsx")
    p_build.set_defaults(func=cmd_build)

    p_smoke = sub.add_parser("smoke", help="exercise the server with an MCP client")
    p_smoke.add_argument("bundle")
    p_smoke.set_defaults(func=cmd_smoke)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

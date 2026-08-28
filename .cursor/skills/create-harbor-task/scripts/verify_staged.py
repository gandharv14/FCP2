#!/usr/bin/env python3
"""Fail-closed verifier and atomic promoter for staged Harbor task bundles.

Understands both supported modes:
* MCP tasks require a successful HTTP-oracle report.
* Plain tasks require a successful closed-world environment check.

For an additional-assumptions dialogue applied in round two, the documented
policy requires factual/cast accuracy; a remaining naturalness flag is recorded
but is not a promotion blocker.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path


PIPELINE_ROOT = Path(__file__).resolve().parents[4]
DIALOGUE_SCRIPTS = (
    PIPELINE_ROOT / ".cursor" / "skills" / "additional-assumptions-dialogue" / "scripts"
)
for path in (PIPELINE_ROOT, DIALOGUE_SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import plain_eligibility  # noqa: E402
import validate_dialogue  # noqa: E402
from xl_mcp_oracle import check_environment  # noqa: E402


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def section(text: str, heading: str) -> str | None:
    match = re.search(
        r"^#{2,3}\s+%s\s*\n(.*?)(?=^#{2,3}\s|\Z)"
        % re.escape(heading),
        text,
        flags=re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    return match.group(1).strip() if match else None


def has_none(text: str | None) -> bool:
    return bool(text and re.search(r"\bnone\b", text, flags=re.IGNORECASE))


def audit_inventory_matches(metadata: dict, inventory: Path) -> bool:
    """The audit hashes canonical JSON, not the pretty-printed file bytes."""
    canonical = json.dumps(
        load_json(inventory), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return metadata.get("inventory_sha256") == hashlib.sha256(canonical).hexdigest()


def faithfulness_fault(path: Path) -> str | None:
    if not path.is_file():
        return "missing fresh faithfulness review"
    text = path.read_text(encoding="utf-8")
    verdict = section(text, "Verdict")
    blocking = section(text, "Blocking findings")
    # Some approved reviewer templates contain only the findings sections; that
    # is an explicit PASS when blocking findings are "None." Do not require a
    # cosmetic Verdict heading, but when it exists, require it to say PASS.
    if verdict is not None and not re.match(r"\**\s*PASS\b", verdict, flags=re.IGNORECASE):
        return "fresh faithfulness review does not PASS"
    if verdict is None and (blocking is None or not has_none(blocking)):
        return "fresh faithfulness review has blocking findings"
    if blocking is not None and not has_none(blocking):
        return "fresh faithfulness review has blocking findings"
    repairs = section(text, "Repairable label findings")
    if repairs is not None and not has_none(repairs):
        return "unrepaired disclosure label"
    return None


def add_json_check(
    faults: list[str], path: Path, label: str, predicate
) -> dict | None:
    if not path.is_file():
        faults.append(f"missing {label}")
        return None
    try:
        payload = load_json(path)
    except (OSError, json.JSONDecodeError):
        faults.append(f"invalid {label}")
        return None
    if not predicate(payload):
        faults.append(f"failed {label}")
    return payload


def verify_bundle(root: Path, stage: Path) -> dict:
    workbook = stage.name.removesuffix("-outputs")
    run = root / "runs" / f"{workbook}-variable-sources"
    faults: list[str] = []

    add_json_check(
        faults,
        root / "runs" / "preflight" / f"{workbook}.json",
        "preflight",
        lambda data: data.get("healthy") is True
        and data.get("classification") == "healthy",
    )
    add_json_check(
        faults,
        root / "seg_out" / workbook / "segments.json",
        "segmentation",
        lambda data: (data.get("verification") or {}).get("passed") is True
        and not (data.get("verification") or {}).get("skipped")
        and (data.get("verification") or {}).get("seeded_inside_output_cone_count", 0)
        == 0,
    )

    inventory = run / f"{workbook}-inputs-variable-sources.inventory.json"
    metadata = add_json_check(
        faults,
        run / f"{workbook}-inputs-variable-sources.metadata.json",
        "variable-source audit",
        lambda data: data.get("status") == "complete"
        and data.get("model") == "openai/gpt-5.6-sol"
        and (data.get("validation") or {}).get("status") == "passed",
    )
    if metadata is not None:
        if not inventory.is_file():
            faults.append("missing audit inventory")
        elif not audit_inventory_matches(metadata, inventory):
            faults.append("audit inventory hash mismatch")

    disclosure = add_json_check(
        faults,
        stage / "tests" / "disclosure.json",
        "disclosure records",
        lambda data: isinstance(data.get("agent_records"), list),
    )
    add_json_check(
        faults,
        root / "runs" / "disclosure" / stage.name / "verify.json",
        "disclosure verification",
        lambda data: data.get("passed") is True and not data.get("faults"),
    )
    add_json_check(
        faults,
        root / "runs" / "disclosure" / stage.name / "faithcheck.json",
        "mechanical faithcheck",
        lambda data: data.get("passed") is True and not data.get("faults"),
    )
    faith_fault = faithfulness_fault(run / "disclosure-faithfulness.md")
    if faith_fault:
        faults.append(faith_fault)
    add_json_check(
        faults,
        root / "runs" / f"{workbook}-instruction-naturalization" / "validation.json",
        "instruction naturalization",
        lambda data: data.get("valid") is True
        and data.get("applied") is True
        and data.get("model") == "gpt-5.6-sol-high"
        and data.get("prompt_version") == "finance-instruction-naturalizer-v3",
    )

    eligibility = add_json_check(
        faults,
        run / "plain_eligibility.json",
        "plain/MCP eligibility",
        lambda data: data.get("mode") in {"mcp", "plain"}
        and (data.get("mode") != "plain" or data.get("eligible") is True),
    )
    if eligibility and eligibility.get("mode") == "mcp":
        add_json_check(
            faults,
            run / "oracle-report.json",
            "HTTP oracle",
            lambda data: data.get("valid") is True
            and (data.get("environment") or {}).get("valid") is True
            and (data.get("workbook_checks") or {}).get("valid") is True
            and (data.get("mcp_checks") or {}).get("valid") is True,
        )
        environment = stage / "environment"
        workbooks = [
            path
            for path in environment.iterdir()
            if path.is_file() and path.suffix.casefold() in {".xlsx", ".xlsm"}
        ] if environment.is_dir() else []
        if len(workbooks) != 1 or not check_environment(stage, workbooks[0]).get("valid"):
            faults.append("MCP environment hygiene")
    elif eligibility and eligibility.get("mode") == "plain":
        if not plain_eligibility.check_plain_environment(stage, workbook).get("valid"):
            faults.append("plain environment hygiene")

    add_json_check(
        faults,
        run / "grader-smoke" / "output" / "reward.json",
        "exact-answer grader",
        lambda data: data.get("score") == 1.0,
    )

    agent_records = (disclosure or {}).get("agent_records") or []
    if agent_records:
        dialogue_run = root / "runs" / f"{workbook}-additional-assumptions"
        apply = add_json_check(
            faults,
            dialogue_run / "apply.json",
            "dialogue application",
            lambda data: data.get("applied") is True
            and data.get("draft_passed") is True
            and data.get("docker_smoke") is True,
        )
        review_path = dialogue_run / "review.json"
        claims_path = dialogue_run / "claims.json"
        if not review_path.is_file() or not claims_path.is_file():
            faults.append("missing dialogue review evidence")
        else:
            review, claims = load_json(review_path), load_json(claims_path)
            if review.get("round") == 2:
                if not validate_dialogue.review_accuracy_passed(review, claims):
                    faults.append("dialogue accuracy")
            elif not validate_dialogue.review_passed(review, claims):
                faults.append("dialogue review")
        add_json_check(
            faults,
            stage / "tests" / "dialogue-applied.json",
            "dialogue marker",
            lambda data: data.get("applied") is True and data.get("draft_passed") is True,
        )
        if not (stage / "environment" / "additional-assumptions.md").is_file():
            faults.append("missing dialogue notes")
        if "## Workbook disclosure" in (stage / "instruction.md").read_text(encoding="utf-8"):
            faults.append("disclosure heading remains after dialogue application")

    return {
        "valid": not faults,
        "mode": eligibility.get("mode") if eligibility else None,
        "agent_records": len(agent_records),
        "faults": faults,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--stage-root", type=Path, default=Path("tasks_outputs_mcp"))
    parser.add_argument("--task-root", type=Path, default=Path("tasks_outputs"))
    parser.add_argument("--ids", nargs="*", help="Workbook ids to verify")
    parser.add_argument("--promote", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    stage_root = (root / args.stage_root).resolve()
    task_root = (root / args.task_root).resolve()
    stages = (
        [stage_root / f"{workbook}-outputs" for workbook in args.ids]
        if args.ids
        else sorted(stage_root.glob("*-outputs"))
    )
    report = {"valid": True, "workbooks": {}, "promoted": []}
    for stage in stages:
        workbook = stage.name.removesuffix("-outputs")
        if not stage.is_dir():
            result = {"valid": False, "mode": None, "agent_records": 0, "faults": ["missing stage"]}
        else:
            result = verify_bundle(root, stage)
        destination = task_root / stage.name
        if destination.exists():
            result["valid"] = False
            result["faults"].append("promotion destination already exists")
        report["workbooks"][workbook] = result
    report["valid"] = all(item["valid"] for item in report["workbooks"].values())

    if args.promote and report["valid"]:
        moved: list[tuple[Path, Path]] = []
        try:
            for stage in stages:
                destination = task_root / stage.name
                os.replace(stage, destination)
                moved.append((stage, destination))
                report["promoted"].append(stage.name.removesuffix("-outputs"))
        except Exception:
            for stage, destination in reversed(moved):
                if destination.exists() and not stage.exists():
                    os.replace(destination, stage)
            raise
    elif args.promote:
        report["valid"] = False

    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0 if report["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

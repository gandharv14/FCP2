#!/usr/bin/env python3
"""Classify a workbook's source health before spending model budget on it.

Reads the segmentation verification evidence in ``seg_out/<wb>/segments.json``
(the recompute-from-frontier proof) and the source directory, and assigns one
closed classification per workbook:

  healthy                 verification passed; safe to run the full pipeline
  frontier_unsafe         seeded or cache-fed cells sit inside the output cone
  unsafe_circular         circular logic with no unique reachable fixed point
  stale_cache_repairable  golden cells lack cached answers to verify against;
                          recalculate and re-save the source file in Excel
  cached_mismatch         cached answers contradict their own formulas at the
                          divergence roots; investigate the roots -- source
                          repair or evaluator gap
  unverified              segmentation has not produced a verification verdict
  missing_source          no .xlsx/.xlsm source under the source directory

The classifier never mutates anything; it writes one JSON verdict per workbook
under ``--out`` and exits nonzero when any requested workbook is not healthy,
so orchestration can require a healthy verdict before step 3 of the
create-harbor-task skill.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import tempfile
from pathlib import Path

from xl_artifact_paths import resolve_workbook_artifact

CLASSIFICATIONS = (
    "healthy",
    "frontier_unsafe",
    "unsafe_circular",
    "stale_cache_repairable",
    "cached_mismatch",
    "unverified",
    "missing_source",
)

_CIRCULAR_MARKERS = ("circular", "non-unique")


def classify(verification: dict | None, source_exists: bool) -> tuple[str, dict]:
    """(classification, evidence) for one workbook, fail-closed ordering."""
    if not source_exists:
        return "missing_source", {}
    if not verification or verification.get("skipped"):
        return "unverified", {}

    evidence = {
        "passed": verification.get("passed"),
        "outputs": verification.get("outputs") or {},
        "seeded_inside_output_cone_count":
            verification.get("seeded_inside_output_cone_count", 0),
        "oracle_fallback_inside_output_cone_count":
            verification.get("oracle_fallback_inside_output_cone_count", 0),
        "divergence_roots": verification.get("divergence_roots") or [],
        "unconverged_blocks": [
            block for block in (verification.get("iterative_blocks") or [])
            if not block.get("converged", True) or not block.get("unique", True)
        ],
    }

    if verification.get("passed") is True:
        return "healthy", evidence

    if (evidence["seeded_inside_output_cone_count"]
            or evidence["oracle_fallback_inside_output_cone_count"]):
        return "frontier_unsafe", evidence

    failures = verification.get("failures") or []
    circular = bool(evidence["unconverged_blocks"]) or any(
        marker in str(record.get("recomputed", "")).lower()
        for record in failures for marker in _CIRCULAR_MARKERS
    )
    if circular:
        return "unsafe_circular", evidence

    outputs = evidence["outputs"]
    if outputs.get("unverifiable", 0) and not outputs.get("mismatch", 0) \
            and not outputs.get("unresolved", 0):
        return "stale_cache_repairable", evidence

    return "cached_mismatch", evidence


def preflight(workbook: str, source_dir: Path, seg_root: Path, out_dir: Path) -> dict:
    source = resolve_workbook_artifact(source_dir, workbook)
    segments = seg_root / workbook / "segments.json"
    verification = None
    if segments.is_file():
        try:
            verification = json.load(open(segments, encoding="utf-8")) \
                .get("verification")
        except (json.JSONDecodeError, OSError) as exc:
            verification = None
    classification, evidence = classify(verification, source.exists())
    verdict = {
        "workbook": workbook,
        "source": str(source) if source.exists() else None,
        "segments": str(segments) if segments.is_file() else None,
        "classification": classification,
        "healthy": classification == "healthy",
        "evidence": evidence,
        "generated_at": _dt.datetime.now(_dt.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / ("%s.json" % workbook)
    fd, tmp = tempfile.mkstemp(dir=str(out_dir), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(verdict, fh, indent=2, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, target)
    return verdict


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("workbooks", nargs="+")
    parser.add_argument("--source", default="4-10 100")
    parser.add_argument("--seg-root", default="seg_out")
    parser.add_argument("--out", default="runs/preflight")
    args = parser.parse_args(argv)

    unhealthy = 0
    for workbook in args.workbooks:
        verdict = preflight(
            workbook, Path(args.source), Path(args.seg_root), Path(args.out))
        print("%s %s%s" % (
            workbook, verdict["classification"],
            "" if verdict["healthy"] else "  (not eligible)"))
        if not verdict["healthy"]:
            unhealthy += 1
    return 1 if unhealthy else 0


if __name__ == "__main__":
    sys.exit(main())

"""Report corpus stage attempts without confusing reconstruction with Harbor yield."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from scripts.corpus import read_inventory, digest
from scripts.corpus_qualification import selected_harbor_roots, tracker_candidates, _unique_sources
from scripts.batch.hardened_runner import atomic_write_json


def build_report(manifest, run, *, harbor_roots=()):
    _unique_sources(manifest)
    run = Path(run)
    roots = selected_harbor_roots(run, harbor_roots)
    rows = []
    for source in manifest["sources"]:
        wb = source["workbook_id"]
        path = run / "workbooks" / wb / "ledger.json"
        ledger = json.loads(path.read_text()) if path.is_file() else {}
        build = ledger.get("build", {})
        candidates = tracker_candidates(source, roots)
        conflict = len(candidates) > 1
        tracker_path = Path(candidates[0]['path']) if len(candidates) == 1 else None
        tracker = json.loads(tracker_path.read_text()) if tracker_path is not None else {}
        if tracker_candidates(source, roots) != candidates:
            raise ValueError('selected tracker set changed during stage reporting')
        tracker_fields = {}
        if harbor_roots and tracker_path is not None:
            tracker_fields['harbor_root'] = candidates[0]['harbor_root']
        if conflict:
            tracker_fields.update(harbor_state='EVIDENCE_INVALID',
                harbor_failure='ambiguous_original_tracker', harbor_candidates=candidates)
        rows.append({"workbook_id": wb, "original_path": source["path"],
            "original_sha256": source["sha256"], "format": source["format"],
            "ledger_sha256": digest(path) if path.is_file() else None,
            "intake_state": ledger.get("state", "NOT_RUN"),
            "intake_failure": ledger.get("failure"),
            "reconstruction_state": build.get("state", "NOT_RUN"),
            "reconstruction_stage": build.get("stage"),
            "reconstruction_failure": build.get("failure"),
            "attempts": build.get("attempts", 0), "resources": build.get("resources"),
            "build_code_sha256": build.get("bindings", {}).get("code_sha256"),
            "source_generation_id": build.get("source_generation_id"),
            "segmentation_generation_id": build.get("segmentation_generation_id"),
            "harbor_state": tracker.get("execution_state", "NOT_RUN"),
            "harbor_failure": tracker.get("promotion_failure"),
            "release_id": tracker.get("promotion_ids", {}).get("release_id"),
            "tracker_sha256": digest(tracker_path) if tracker_path is not None else None, **tracker_fields})
    # This stage reporter has no authority to certify final task quality. A
    # tracker or ledger flag alone must never become a promoted-yield numerator.
    report = {"schema_version": "corpus-stage-report/v1",
        "inventory_sha256": manifest["inventory_sha256"],
        "raw_denominator": len(rows), "measurement_state": "stage_attempts_only",
        "verified_promoted": None, "harbor_yield": None,
        "promotion_note": "Final release and all required quality receipts must be independently checked before reporting Harbor yield.",
        "intake_states": dict(Counter(r["intake_state"] for r in rows)),
        "reconstruction_states": dict(Counter(r["reconstruction_state"] for r in rows)),
        "intake_blockers": dict(Counter(r["intake_failure"] for r in rows if r["intake_failure"])),
        "harbor_states": dict(Counter(r["harbor_state"] for r in rows)),
        "workbooks": rows}
    if harbor_roots:
        report["harbor_roots"] = [str(root) for root in roots]
        report["run_root"] = str(run.resolve())
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--harbor-root", action="append", default=[],
        help="Additional explicit Harbor namespace beneath RUN/harbor; repeatable. Default root remains selected.")
    args = parser.parse_args()
    run = Path(args.run_root).resolve()
    report = build_report(read_inventory(args.inventory), run, harbor_roots=args.harbor_root)
    atomic_write_json(run / "yield-report.json", report)
    print(json.dumps({k: v for k, v in report.items() if k != "workbooks"}, indent=2))


if __name__ == "__main__":
    main()

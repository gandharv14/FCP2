#!/usr/bin/env python3
"""Explicit preparation of unverified binary conversion candidates, never tasks."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from xl_legacy_conversion import (ConverterIdentity, LegacyConversionError, canonical,
                                  file_hash, produce_candidate)


def prepare(*, inventory: Path, inventory_sha256: str, config: Path,
            config_sha256: str, workbook_ids: list[str], output_root: Path) -> dict:
    """Sequential opt-in conversion. No automatic discovery, adoption or retry."""
    if file_hash(inventory) != inventory_sha256 or file_hash(config) != config_sha256:
        raise LegacyConversionError("selected inventory or converter config hash mismatch")
    settings = json.loads(config.read_bytes())
    if type(settings) is not dict or set(settings) != {"schema_version", "converter", "timeout_seconds"}:
        raise LegacyConversionError("invalid closed converter configuration")
    if settings["schema_version"] != "legacy-candidate-config/v1":
        raise LegacyConversionError("unsupported converter configuration")
    try:
        identity = ConverterIdentity(**settings["converter"])
    except (TypeError, ValueError) as exc:
        raise LegacyConversionError("invalid converter identity") from exc
    document = json.loads(inventory.read_bytes())
    if document.get("schema_version") != "raw-corpus/v1":
        raise LegacyConversionError("a frozen raw corpus inventory is required")
    rows = document.get("sources")
    if type(rows) is not list or not rows:
        raise LegacyConversionError("empty source inventory")
    sources = {}
    identities = set()
    for row in rows:
        if (type(row) is not dict or type(row.get("workbook_id")) is not str
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", row["workbook_id"])
                or row["workbook_id"].casefold() in identities):
            raise LegacyConversionError("ambiguous original workbook identity")
        identities.add(row["workbook_id"].casefold())
        sources[row["workbook_id"]] = row
    if (not workbook_ids or any(type(x) is not str for x in workbook_ids)
            or len({x.casefold() for x in workbook_ids}) != len(workbook_ids)):
        raise LegacyConversionError("unique explicit workbook IDs are required")
    selected = []
    for workbook_id in workbook_ids:
        row = sources.get(workbook_id)
        if row is None or row.get("format") not in {".xls", ".xlsb"}:
            raise LegacyConversionError("selection must contain only frozen legacy originals")
        selected.append(row)
    # Validate all caller authority before any converter execution.
    identity.validate()
    if output_root.exists() or output_root.is_symlink():
        raise LegacyConversionError("candidate campaign destination must be fresh")
    from xl_legacy_conversion import _path
    _path(output_root)
    output_root.mkdir(mode=0o700)
    results = []
    for row in selected:
        if file_hash(inventory) != inventory_sha256 or file_hash(config) != config_sha256:
            raise LegacyConversionError("selected authority changed during preparation")
        try:
            result = produce_candidate(row, output_root / row["workbook_id"], identity,
                                       timeout_seconds=settings["timeout_seconds"])
            results.append({"workbook_id": row["workbook_id"], "classification": "conversion_unverified",
                            "candidate_manifest": str(output_root / row["workbook_id"] / "candidate.json"),
                            "record_sha256": result["record_sha256"]})
        except (LegacyConversionError, OSError) as exc:
            results.append({"workbook_id": row["workbook_id"], "classification": "conversion_failed",
                            "error": str(exc)})
    if file_hash(inventory) != inventory_sha256 or file_hash(config) != config_sha256:
        raise LegacyConversionError("selected authority changed during preparation")
    report = {"schema_version": "legacy-candidate-preparation/v1", "inventory_file_sha256": inventory_sha256,
              "config_sha256": config_sha256, "original_denominator": len(rows), "results": results,
              "verified_promotions": 0, "inventory_adoption_supported": False}
    with (output_root / "preparation.json").open("xb") as handle:
        handle.write(canonical(report))
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--inventory-sha256", required=True, help="SHA256 of exact inventory file bytes")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--config-sha256", required=True)
    parser.add_argument("--workbook-id", action="append", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = prepare(inventory=args.inventory, inventory_sha256=args.inventory_sha256,
                         config=args.config, config_sha256=args.config_sha256,
                         workbook_ids=args.workbook_id, output_root=args.output_root)
    except (LegacyConversionError, OSError, json.JSONDecodeError) as exc:
        parser.exit(2, f"candidate preparation failed: {exc}\n")
    print(canonical(report).decode(), end="")
    return 1 if any(r["classification"] == "conversion_failed" for r in report["results"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())

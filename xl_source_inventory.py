"""Freeze an explicit source-workbook cohort with live health observations."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import tempfile
from collections import Counter
from pathlib import Path

from xl_source_health import inspect_workbook, sha256_file, validate_report


SCHEMA_VERSION = "source-inventory/v2"
CONVERSION_CERTIFICATE_SCHEMA_VERSION = "source-conversion-equivalence/v1"
ROUTES = (
    "insufficient_evidence",
    "pass",
    "recalc_candidate",
    "restricted_pass",
    "unsupported",
)


class SourceInventoryError(ValueError):
    """The inventory inputs are unsafe, ambiguous, or inconsistent."""


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def _object_hash(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _valid_file_record(record: object, *, rooted: bool = False) -> bool:
    if not isinstance(record, dict):
        return False
    path = record.get("path")
    digest = record.get("sha256")
    size = record.get("size_bytes")
    if (
        not isinstance(path, str)
        or not path
        or Path(path).is_absolute()
        or ".." in Path(path).parts
        or not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        or not isinstance(size, int)
        or size < 0
    ):
        return False
    return not rooted or isinstance(record.get("root_index"), int)


def _hash_file(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    digest = hashlib.sha256()
    with os.fdopen(descriptor, "rb") as handle:
        metadata = os.fstat(handle.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise SourceInventoryError(f"inventory input is not a file: {path}")
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _record(path: Path, root: Path, *, root_index: int | None = None) -> dict:
    if path.is_symlink() or not path.is_file():
        raise SourceInventoryError(f"inventory input is unsafe: {path}")
    record = {
        "path": path.relative_to(root).as_posix(),
        "sha256": _hash_file(path),
        "size_bytes": path.stat().st_size,
    }
    if root_index is not None:
        record["root_index"] = root_index
    return record


def _validated_ids(values: list[str] | tuple[str, ...]) -> list[str]:
    if not values:
        raise SourceInventoryError("an explicit workbook cohort is required")
    normalized = []
    seen: set[str] = set()
    for value in values:
        workbook_id = str(value).strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]+", workbook_id):
            raise SourceInventoryError(f"unsafe workbook ID: {workbook_id!r}")
        if workbook_id in seen:
            raise SourceInventoryError(f"duplicate workbook ID: {workbook_id}")
        seen.add(workbook_id)
        normalized.append(workbook_id)
    return sorted(normalized)


def read_workbook_ids(path: str | Path) -> list[str]:
    """Read a JSON array/object or one-ID-per-line cohort file."""
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise SourceInventoryError(f"cannot read workbook ID manifest: {exc}") from exc
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        values = [
            line.strip()
            for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
    else:
        if isinstance(value, list):
            values = value
        elif isinstance(value, dict):
            values = value.get("workbook_ids")
        else:
            values = None
        if not isinstance(values, list) or not all(
            isinstance(item, str) for item in values
        ):
            raise SourceInventoryError(
                "workbook ID manifest must be a list or contain workbook_ids"
            )
    return _validated_ids(values)


def _paths_by_id(
    root: Path,
    *,
    suffixes: tuple[str, ...] | None = None,
) -> dict[str, list[Path]]:
    if root.is_symlink() or not root.is_dir():
        raise SourceInventoryError(f"inventory root is unsafe: {root}")
    result: dict[str, list[Path]] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file():
            continue
        if suffixes is not None and path.suffix.casefold() not in suffixes:
            continue
        result.setdefault(path.stem, []).append(path)
    return result


def _single_requested_sources(root: Path, workbook_ids: list[str]) -> dict[str, Path]:
    candidates = _paths_by_id(root, suffixes=(".xlsx",))
    selected = {}
    for workbook_id in workbook_ids:
        matches = candidates.get(workbook_id, [])
        if not matches:
            raise SourceInventoryError(f"requested workbook is absent: {workbook_id}")
        if len(matches) != 1:
            raise SourceInventoryError(
                f"requested workbook ID is ambiguous: {workbook_id}"
            )
        selected[workbook_id] = matches[0]
    return selected


def _matching_records(
    workbook_id: str,
    roots: list[Path],
    *,
    suffixes: tuple[str, ...] | None = None,
) -> list[dict]:
    records = []
    for root_index, root in enumerate(roots):
        matches = [
            path
            for paths in _paths_by_id(root, suffixes=suffixes).values()
            for path in paths
            if (
                path.stem == workbook_id
                or path.name.startswith(
                    (f"{workbook_id}.", f"{workbook_id}-", f"{workbook_id}_")
                )
            )
        ]
        for path in matches:
            records.append(_record(path, root, root_index=root_index))
    return sorted(
        records,
        key=lambda item: (item["root_index"], item["path"], item["sha256"]),
    )


def _read_json(path: Path, description: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SourceInventoryError(f"cannot read {description}: {exc}") from exc
    if not isinstance(value, dict):
        raise SourceInventoryError(f"{description} must be a JSON object")
    return value


def _validate_equivalence_certificate(
    certificate: dict,
    *,
    workbook_id: str,
    original_sha256: str,
    converted_sha256: str,
) -> dict:
    unsigned = dict(certificate)
    claimed = unsigned.pop("certificate_sha256", None)
    verification = certificate.get("verification")
    failures = []
    if certificate.get("schema_version") != CONVERSION_CERTIFICATE_SCHEMA_VERSION:
        failures.append("schema_version")
    if claimed != _object_hash(unsigned):
        failures.append("certificate_sha256")
    if certificate.get("workbook_id") != workbook_id:
        failures.append("workbook_id")
    if certificate.get("original_source_sha256") != original_sha256:
        failures.append("original_source_sha256")
    if certificate.get("converted_source_sha256") != converted_sha256:
        failures.append("converted_source_sha256")
    if certificate.get("equivalent") is not True:
        failures.append("equivalent")
    if (
        not isinstance(verification, dict)
        or verification.get("deterministic") is not True
        or not isinstance(verification.get("method"), str)
        or not verification["method"]
    ):
        failures.append("verification")
    if failures:
        raise SourceInventoryError(
            "conversion equivalence certificate validation failed: "
            + ", ".join(sorted(set(failures)))
        )
    return certificate


def _conversion_record(
    workbook_id: str,
    converted: dict,
    legacy_roots: list[Path],
    report_roots: list[Path],
    certificate_roots: list[Path],
) -> dict | None:
    originals = _matching_records(
        workbook_id,
        legacy_roots,
        suffixes=(".xls", ".xlsb", ".xlsm"),
    )
    reports = _matching_records(workbook_id, report_roots)
    certificate_matches = []
    for root_index, root in enumerate(certificate_roots):
        for paths in _paths_by_id(root, suffixes=(".json",)).values():
            for path in paths:
                if not (
                    path.stem == workbook_id
                    or path.name.startswith(
                        (
                            f"{workbook_id}.",
                            f"{workbook_id}-",
                            f"{workbook_id}_",
                        )
                    )
                ):
                    continue
                certificate_matches.append((root_index, root, path))
    if not originals and not reports and not certificate_matches:
        return None
    if len(originals) > 1:
        raise SourceInventoryError(
            f"multiple original legacy sources for {workbook_id}"
        )
    if len(certificate_matches) > 1:
        raise SourceInventoryError(
            f"multiple equivalence certificates for {workbook_id}"
        )
    conversion = {
        "status": "conversion_unverified",
        "original_source": originals[0] if originals else None,
        "reports": reports,
        "equivalence_certificate": None,
    }
    if certificate_matches:
        if not originals:
            raise SourceInventoryError(
                f"equivalence certificate lacks original source for {workbook_id}"
            )
        root_index, root, certificate_path = certificate_matches[0]
        document = _read_json(
            certificate_path, f"equivalence certificate for {workbook_id}"
        )
        _validate_equivalence_certificate(
            document,
            workbook_id=workbook_id,
            original_sha256=originals[0]["sha256"],
            converted_sha256=converted["sha256"],
        )
        conversion["status"] = "conversion_equivalent"
        conversion["equivalence_certificate"] = {
            "record": _record(
                certificate_path, root, root_index=root_index
            ),
            "document": document,
            "document_sha256": _object_hash(document),
        }
    return conversion


def _derived_workbook_record(
    workbook_id: str,
    source: Path,
    source_root: Path,
    conversion: dict | None,
) -> dict:
    source_record = _record(source, source_root)
    health = inspect_workbook(source)
    try:
        validate_report(health, source)
    except ValueError as exc:
        raise SourceInventoryError(
            f"source health failed for {workbook_id}: {exc}"
        ) from exc
    reasons = list(health["reason_codes"])
    mixed_tags = sorted(
        reason for reason in reasons if reason.startswith("mixed_")
    )
    classification = (
        conversion["status"] if conversion is not None else "native_source"
    )
    return {
        "workbook_id": workbook_id,
        **source_record,
        "route": health["route"],
        "reason_codes": reasons,
        "route_cohort": health["route"],
        "classification": classification,
        "mixed_recalc_tags": mixed_tags,
        "volatile_functions": health.get("volatile_functions", {}),
        "function_counts": health.get("volatile_functions", {}),
        "restriction_events_sha256": health["restriction_events_sha256"],
        "health_report_sha256": health["report_sha256"],
        "health_report": health,
        "conversion": conversion,
    }


def build_inventory_manifest(
    source_root: str | Path,
    *,
    workbook_ids: list[str] | tuple[str, ...] | None = None,
    report_roots: list[str | Path] | tuple[str | Path, ...] = (),
    lineage_roots: list[str | Path] | tuple[str | Path, ...] = (),
    legacy_source_roots: list[str | Path] | tuple[str | Path, ...] = (),
    conversion_report_roots: list[str | Path] | tuple[str | Path, ...] = (),
    equivalence_certificate_roots: list[str | Path] | tuple[str | Path, ...] = (),
) -> dict:
    """Observe exactly the requested cohort without modifying workbook bytes."""
    ids = _validated_ids(list(workbook_ids or ()))
    source_directory = Path(source_root).resolve()
    sources = _single_requested_sources(source_directory, ids)
    # Preserve the first version's argument names as aliases for conversion inputs.
    legacy_roots = [
        Path(value).resolve()
        for value in legacy_source_roots
    ]
    conversion_reports = [
        Path(value).resolve()
        for value in (*conversion_report_roots, *report_roots, *lineage_roots)
    ]
    certificate_roots = [
        Path(value).resolve() for value in equivalence_certificate_roots
    ]
    workbooks = []
    for workbook_id in ids:
        source = sources[workbook_id]
        source_record = _record(source, source_directory)
        conversion = _conversion_record(
            workbook_id,
            source_record,
            legacy_roots,
            conversion_reports,
            certificate_roots,
        )
        workbooks.append(
            _derived_workbook_record(
                workbook_id,
                source,
                source_directory,
                conversion,
            )
        )
    cohorts = {
        route: [
            item["workbook_id"] for item in workbooks if item["route"] == route
        ]
        for route in ROUTES
    }
    classifications = dict(sorted(Counter(
        item["classification"] for item in workbooks
    ).items()))
    classification_cohorts = {
        classification: [
            item["workbook_id"]
            for item in workbooks
            if item["classification"] == classification
        ]
        for classification in (
            "conversion_equivalent",
            "conversion_unverified",
            "native_source",
        )
    }
    cohort = {
        "workbook_ids": ids,
        "size": len(ids),
    }
    cohort["cohort_sha256"] = _object_hash(cohort["workbook_ids"])
    core = {
        "schema_version": SCHEMA_VERSION,
        "cohort": cohort,
        "cohorts": cohorts,
        "classifications": classifications,
        "classification_cohorts": classification_cohorts,
        "mixed_recalc_cohort": [
            item["workbook_id"]
            for item in workbooks
            if item["mixed_recalc_tags"]
        ],
        "workbooks": workbooks,
    }
    manifest = {**core, "inventory_sha256": _object_hash(core)}
    return validate_inventory_manifest(
        manifest,
        source_root=source_directory,
        expected_workbook_ids=ids,
    )


def _safe_source_path(root: Path, relative: object) -> Path:
    if not isinstance(relative, str):
        raise SourceInventoryError("source record path is invalid")
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise SourceInventoryError("source record path is unsafe")
    path = root / candidate
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError) as exc:
        raise SourceInventoryError("source record escapes source root") from exc
    return path


def validate_inventory_manifest(
    manifest: dict,
    *,
    source_root: str | Path | None = None,
    expected_workbook_ids: list[str] | tuple[str, ...] | None = None,
) -> dict:
    if not isinstance(manifest, dict):
        raise SourceInventoryError("source inventory must be an object")
    unsigned = dict(manifest)
    claimed = unsigned.pop("inventory_sha256", None)
    failures = []
    if unsigned.get("schema_version") != SCHEMA_VERSION:
        failures.append("schema_version")
    if claimed != _object_hash(unsigned):
        failures.append("inventory_sha256")
    cohort = unsigned.get("cohort")
    workbooks = unsigned.get("workbooks")
    if not isinstance(cohort, dict) or not isinstance(workbooks, list):
        raise SourceInventoryError(
            "source inventory validation failed: cohort, workbooks"
        )
    try:
        ids = _validated_ids(cohort.get("workbook_ids") or [])
    except SourceInventoryError:
        ids = []
        failures.append("cohort_ids")
    record_ids = [
        item.get("workbook_id") for item in workbooks if isinstance(item, dict)
    ]
    if len(record_ids) != len(workbooks) or record_ids != ids:
        failures.append("cohort_membership")
    if cohort.get("size") != len(ids):
        failures.append("cohort_size")
    if cohort.get("cohort_sha256") != _object_hash(ids):
        failures.append("cohort_sha256")
    if expected_workbook_ids is not None:
        try:
            expected = _validated_ids(list(expected_workbook_ids))
        except SourceInventoryError:
            expected = []
        if ids != expected:
            failures.append("expected_cohort")

    root = Path(source_root).resolve() if source_root is not None else None
    derived_cohorts = {route: [] for route in ROUTES}
    derived_classifications = Counter()
    derived_classification_cohorts = {
        "conversion_equivalent": [],
        "conversion_unverified": [],
        "native_source": [],
    }
    derived_mixed_cohort = []
    for item in workbooks:
        if not isinstance(item, dict):
            continue
        workbook_id = item.get("workbook_id")
        health = item.get("health_report")
        if not _valid_file_record(item):
            failures.append(f"source_record:{workbook_id}")
        try:
            validate_report(health)
        except ValueError:
            failures.append(f"health:{workbook_id}")
            continue
        if (
            item.get("sha256") != health.get("source_sha256")
            or item.get("size_bytes") != health.get("source_size_bytes")
            or item.get("health_report_sha256") != health.get("report_sha256")
            or item.get("route") != health.get("route")
            or item.get("route_cohort") != health.get("route")
            or item.get("reason_codes") != health.get("reason_codes")
            or item.get("restriction_events_sha256")
            != health.get("restriction_events_sha256")
            or item.get("volatile_functions")
            != health.get("volatile_functions", {})
            or item.get("function_counts")
            != health.get("volatile_functions", {})
            or (health.get("source") or {}).get("name")
            != Path(str(item.get("path", ""))).name
        ):
            failures.append(f"classification:{workbook_id}")
        expected_mixed = sorted(
            reason for reason in health.get("reason_codes", [])
            if reason.startswith("mixed_")
        )
        if item.get("mixed_recalc_tags") != expected_mixed:
            failures.append(f"mixed_tags:{workbook_id}")
        if expected_mixed:
            derived_mixed_cohort.append(workbook_id)
        route = item.get("route")
        if route in derived_cohorts:
            derived_cohorts[route].append(workbook_id)
        else:
            failures.append(f"route:{workbook_id}")

        conversion = item.get("conversion")
        expected_classification = "native_source"
        if conversion is not None:
            if not isinstance(conversion, dict):
                failures.append(f"conversion:{workbook_id}")
            else:
                status = conversion.get("status")
                expected_classification = (
                    status
                    if status in {
                        "conversion_equivalent",
                        "conversion_unverified",
                    }
                    else "invalid_conversion"
                )
                certificate = conversion.get("equivalence_certificate")
                original = conversion.get("original_source")
                reports = conversion.get("reports")
                if (
                    original is None
                    and not reports
                    and certificate is None
                ):
                    failures.append(f"conversion_evidence:{workbook_id}")
                if original is not None and not _valid_file_record(
                    original, rooted=True
                ):
                    failures.append(f"original_record:{workbook_id}")
                if (
                    not isinstance(reports, list)
                    or any(
                        not _valid_file_record(report, rooted=True)
                        for report in reports
                    )
                ):
                    failures.append(f"conversion_reports:{workbook_id}")
                if status == "conversion_unverified":
                    if certificate is not None:
                        failures.append(f"conversion_status:{workbook_id}")
                elif status == "conversion_equivalent":
                    try:
                        _validate_equivalence_certificate(
                            certificate["document"],
                            workbook_id=workbook_id,
                            original_sha256=original["sha256"],
                            converted_sha256=item["sha256"],
                        )
                        if certificate.get("document_sha256") != _object_hash(
                            certificate["document"]
                        ):
                            failures.append(f"certificate_document:{workbook_id}")
                        if not _valid_file_record(
                            certificate.get("record"), rooted=True
                        ):
                            failures.append(f"certificate_record:{workbook_id}")
                    except (KeyError, TypeError, SourceInventoryError):
                        failures.append(f"certificate:{workbook_id}")
                else:
                    failures.append(f"conversion_status:{workbook_id}")
        if item.get("classification") != expected_classification:
            failures.append(f"source_classification:{workbook_id}")
        derived_classifications[expected_classification] += 1
        if expected_classification in derived_classification_cohorts:
            derived_classification_cohorts[expected_classification].append(
                workbook_id
            )

        if root is not None:
            try:
                source = _safe_source_path(root, item.get("path"))
                if (
                    source.is_symlink()
                    or not source.is_file()
                    or sha256_file(source) != item.get("sha256")
                    or source.stat().st_size != item.get("size_bytes")
                    or inspect_workbook(source) != health
                ):
                    failures.append(f"source_binding:{workbook_id}")
            except (OSError, ValueError):
                failures.append(f"source_binding:{workbook_id}")
    if unsigned.get("cohorts") != derived_cohorts:
        failures.append("cohorts")
    if unsigned.get("classifications") != dict(
        sorted(derived_classifications.items())
    ):
        failures.append("classifications")
    if unsigned.get("classification_cohorts") != derived_classification_cohorts:
        failures.append("classification_cohorts")
    if unsigned.get("mixed_recalc_cohort") != derived_mixed_cohort:
        failures.append("mixed_recalc_cohort")
    if failures:
        raise SourceInventoryError(
            "source inventory validation failed: "
            + ", ".join(sorted(set(failures)))
        )
    return manifest


def write_inventory_manifest(path: str | Path, manifest: dict) -> Path:
    validate_inventory_manifest(manifest)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=str(destination.parent)
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_canonical_bytes(manifest))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
        try:
            directory_descriptor = os.open(str(destination.parent), os.O_RDONLY)
        except OSError:
            directory_descriptor = None
        if directory_descriptor is not None:
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root")
    parser.add_argument("-o", "--output", required=True)
    parser.add_argument("--id-manifest")
    parser.add_argument("--workbook", action="append", default=[])
    parser.add_argument("--legacy-source-root", action="append", default=[])
    parser.add_argument("--conversion-report-root", action="append", default=[])
    parser.add_argument(
        "--equivalence-certificate-root", action="append", default=[]
    )
    args = parser.parse_args(argv)
    try:
        ids = list(args.workbook)
        if args.id_manifest:
            ids.extend(read_workbook_ids(args.id_manifest))
        manifest = build_inventory_manifest(
            args.source_root,
            workbook_ids=ids,
            legacy_source_roots=args.legacy_source_root,
            conversion_report_roots=args.conversion_report_root,
            equivalence_certificate_roots=args.equivalence_certificate_root,
        )
        write_inventory_manifest(args.output, manifest)
        print(json.dumps(manifest, sort_keys=True))
        return 0
    except (OSError, SourceInventoryError) as exc:
        print(f"source inventory FAIL: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

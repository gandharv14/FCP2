"""Validate commit-pinned approvals for immutable source inventories."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path


REGISTRY_SCHEMA_VERSION = "source-inventory-approval-registry/v1"
APPROVAL_SCHEMA_VERSION = "source-inventory-approval/v1"
DEFAULT_REGISTRY = (
    Path(__file__).resolve().parent
    / "verification_manifests"
    / "approved_source_inventories.v1.json"
)
HASH_RE = re.compile(r"[0-9a-f]{64}")
WORKBOOK_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class InventoryApprovalError(ValueError):
    """The inventory has no valid approval in the pinned registry."""


def canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def object_hash(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def inventory_artifact_sha256(inventory: dict) -> str:
    """Hash the exact canonical inventory artifact stored in a generation."""
    return hashlib.sha256(canonical_bytes(inventory)).hexdigest()


def original_source_ledger(inventory: dict) -> list[dict]:
    """Return the complete source-format lineage used by an inventory cohort."""
    ledger = []
    for item in inventory.get("workbooks", []):
        if not isinstance(item, dict):
            raise InventoryApprovalError("inventory workbook record is invalid")
        conversion = item.get("conversion")
        original = conversion.get("original_source") if isinstance(
            conversion, dict
        ) else None
        if not isinstance(original, dict):
            original = {
                "path": item.get("path"),
                "sha256": item.get("sha256"),
                "size_bytes": item.get("size_bytes"),
            }
        ledger.append({
            "classification": item.get("classification"),
            "conversion_status": (
                conversion.get("status") if isinstance(conversion, dict) else None
            ),
            "original_source": {
                "path": original.get("path"),
                "sha256": original.get("sha256"),
                "size_bytes": original.get("size_bytes"),
            },
            "source_sha256": item.get("sha256"),
            "workbook_id": item.get("workbook_id"),
        })
    return ledger


def original_source_ledger_sha256(inventory: dict) -> str:
    return object_hash(original_source_ledger(inventory))


def _read_regular_json(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise InventoryApprovalError("approval registry is not a regular file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        with os.fdopen(descriptor, "rb") as handle:
            value = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InventoryApprovalError(
            f"cannot read inventory approval registry: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise InventoryApprovalError("inventory approval registry must be an object")
    return value


def read_inventory_artifact(path: str | Path) -> dict:
    """Snapshot one regular inventory file through a no-follow descriptor."""
    requested = Path(path)
    if requested.is_symlink():
        raise InventoryApprovalError("source inventory cannot be a symlink")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(requested, flags)
    except OSError as exc:
        raise InventoryApprovalError(
            f"cannot open source inventory: {exc}"
        ) from exc
    try:
        with os.fdopen(descriptor, "rb") as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise InventoryApprovalError(
                    "source inventory is not a regular file"
                )
            payload = handle.read()
    except OSError as exc:
        raise InventoryApprovalError(
            f"cannot read source inventory: {exc}"
        ) from exc
    try:
        value = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise InventoryApprovalError(
            f"source inventory JSON is invalid: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise InventoryApprovalError("source inventory must be an object")
    return value


def validate_registry(registry: dict) -> dict:
    if registry.get("schema_version") != REGISTRY_SCHEMA_VERSION:
        raise InventoryApprovalError("inventory approval registry schema is invalid")
    unsigned = dict(registry)
    claimed = unsigned.pop("registry_sha256", None)
    if claimed != object_hash(unsigned):
        raise InventoryApprovalError("inventory approval registry hash is invalid")
    approvals = registry.get("approvals")
    if not isinstance(approvals, list) or not approvals:
        raise InventoryApprovalError("inventory approval registry is empty")
    identities = set()
    for approval in approvals:
        if not isinstance(approval, dict):
            raise InventoryApprovalError("inventory approval entry is invalid")
        required_hashes = (
            "inventory_artifact_sha256",
            "inventory_sha256",
            "cohort_sha256",
            "original_source_ledger_sha256",
            "batch_source_ledger_sha256",
        )
        if (
            not isinstance(approval.get("batch_id"), str)
            or not approval["batch_id"]
            or not isinstance(approval.get("policy_version"), str)
            or any(
                not isinstance(approval.get(key), str)
                or HASH_RE.fullmatch(approval[key]) is None
                for key in required_hashes
            )
        ):
            raise InventoryApprovalError("inventory approval entry is incomplete")
        batch_ledger = approval.get("batch_source_ledger")
        if (
            not isinstance(batch_ledger, list)
            or not batch_ledger
            or approval.get("batch_source_count") != len(batch_ledger)
            or approval.get("batch_source_ledger_sha256")
            != object_hash(batch_ledger)
        ):
            raise InventoryApprovalError(
                "approved batch source ledger is incomplete"
            )
        workbook_ids = [
            row.get("workbook_id")
            for row in batch_ledger
            if isinstance(row, dict)
        ]
        if (
            len(workbook_ids) != len(batch_ledger)
            or any(
                not isinstance(workbook_id, str) or not workbook_id
                or WORKBOOK_ID_RE.fullmatch(workbook_id) is None
                for workbook_id in workbook_ids
            )
            or len(set(workbook_ids)) != len(workbook_ids)
        ):
            raise InventoryApprovalError(
                "approved batch source ledger IDs are invalid"
            )
        for row in batch_ledger:
            original = row.get("original_source")
            classification = row.get("classification")
            conversion_status = row.get("conversion_status")
            source_sha256 = row.get("source_sha256")
            if (
                any(
                    key not in row
                    for key in (
                        "classification",
                        "conversion_status",
                        "original_source",
                        "source_sha256",
                        "workbook_id",
                    )
                )
                or
                classification not in {
                    "native_source",
                    "conversion_equivalent",
                    "conversion_unverified",
                    "conversion_failed",
                }
                or conversion_status not in {
                    None,
                    "conversion_equivalent",
                    "conversion_unverified",
                    "conversion_failed",
                }
                or not isinstance(original, dict)
                or not isinstance(original.get("path"), str)
                or not original["path"]
                or not isinstance(original.get("sha256"), str)
                or HASH_RE.fullmatch(original["sha256"]) is None
                or not isinstance(original.get("size_bytes"), int)
                or isinstance(original.get("size_bytes"), bool)
                or original["size_bytes"] < 0
                or (
                    source_sha256 is not None
                    and (
                        not isinstance(source_sha256, str)
                        or HASH_RE.fullmatch(source_sha256) is None
                    )
                )
                or (
                    classification != "conversion_failed"
                    and source_sha256 is None
                )
            ):
                raise InventoryApprovalError(
                    "approved batch source ledger row is incomplete"
                )
        identity = (
            approval["batch_id"],
            approval["inventory_artifact_sha256"],
        )
        if identity in identities:
            raise InventoryApprovalError("duplicate inventory approval entry")
        identities.add(identity)
    return registry


def load_pinned_registry(path: str | Path | None = None) -> dict:
    """Load only the registry bytes shipped beside this trusted code."""
    pinned = _read_regular_json(DEFAULT_REGISTRY)
    validate_registry(pinned)
    if path is not None:
        supplied_path = Path(path)
        supplied = _read_regular_json(supplied_path)
        validate_registry(supplied)
        if canonical_bytes(supplied) != canonical_bytes(pinned):
            raise InventoryApprovalError(
                "supplied approval registry is not the commit-pinned registry"
            )
    return pinned


def _inventory_policy_version(inventory: dict) -> str:
    versions = {
        (item.get("health_report") or {}).get("policy_version")
        for item in inventory.get("workbooks", [])
        if isinstance(item, dict)
    }
    if len(versions) != 1 or not isinstance(next(iter(versions), None), str):
        raise InventoryApprovalError(
            "inventory does not have one complete source-health policy version"
        )
    return next(iter(versions))


def approval_claims(inventory: dict, *, batch_id: str) -> dict:
    cohort = inventory.get("cohort") or {}
    batch_ledger = original_source_ledger(inventory)
    return {
        "batch_id": batch_id,
        "batch_source_count": len(batch_ledger),
        "batch_source_ledger": batch_ledger,
        "batch_source_ledger_sha256": object_hash(batch_ledger),
        "cohort_sha256": cohort.get("cohort_sha256"),
        "inventory_artifact_sha256": inventory_artifact_sha256(inventory),
        "inventory_sha256": inventory.get("inventory_sha256"),
        "original_source_ledger_sha256": original_source_ledger_sha256(inventory),
        "policy_version": _inventory_policy_version(inventory),
    }


def resolve_inventory_approval(
    inventory: dict,
    *,
    batch_id: str,
    registry_path: str | Path | None = None,
) -> dict:
    """Resolve one exact inventory approval from the trusted registry."""
    from xl_source_inventory import validate_inventory_manifest

    validate_inventory_manifest(inventory)
    registry = load_pinned_registry(registry_path)
    expected = approval_claims(inventory, batch_id=batch_id)
    inventory_claim_keys = {
        "batch_id",
        "cohort_sha256",
        "inventory_artifact_sha256",
        "inventory_sha256",
        "original_source_ledger_sha256",
        "policy_version",
    }
    matches = [
        approval
        for approval in registry["approvals"]
        if all(
            approval.get(key) == expected.get(key)
            for key in inventory_claim_keys
        )
    ]
    if len(matches) != 1:
        raise InventoryApprovalError(
            "source inventory is not approved by the commit-pinned registry"
        )
    approved_by_id = {
        row["workbook_id"]: row
        for row in matches[0]["batch_source_ledger"]
    }
    for row in expected["batch_source_ledger"]:
        if approved_by_id.get(row["workbook_id"]) != row:
            raise InventoryApprovalError(
                "inventory lineage does not match the approved batch ledger"
            )
    core = {
        "schema_version": APPROVAL_SCHEMA_VERSION,
        "approval": matches[0],
    }
    return {
        **core,
        "inventory_approval_sha256": object_hash(core),
    }


def validate_inventory_approval(
    document: dict,
    inventory: dict,
    *,
    registry_path: str | Path | None = None,
) -> dict:
    if not isinstance(document, dict):
        raise InventoryApprovalError("inventory approval document must be an object")
    approval = document.get("approval")
    if (
        document.get("schema_version") != APPROVAL_SCHEMA_VERSION
        or not isinstance(approval, dict)
    ):
        raise InventoryApprovalError("inventory approval document schema is invalid")
    unsigned = dict(document)
    claimed = unsigned.pop("inventory_approval_sha256", None)
    if claimed != object_hash(unsigned):
        raise InventoryApprovalError("inventory approval document hash is invalid")
    expected = resolve_inventory_approval(
        inventory,
        batch_id=approval.get("batch_id"),
        registry_path=registry_path,
    )
    if document != expected:
        raise InventoryApprovalError(
            "inventory approval does not match the approved inventory"
        )
    return document

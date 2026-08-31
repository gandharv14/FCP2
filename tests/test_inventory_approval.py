from __future__ import annotations

import json
from pathlib import Path

import pytest
from openpyxl import Workbook

import xl_inventory_approval
from xl_inventory_approval import (
    InventoryApprovalError,
    approval_claims,
    object_hash,
    read_inventory_artifact,
    resolve_inventory_approval,
)
from xl_source_inventory import (
    assemble_inventory_manifest,
    build_inventory_manifest,
)


def _inventory(tmp_path: Path) -> dict:
    sources = tmp_path / "sources"
    sources.mkdir()
    workbook = Workbook()
    workbook.active["A1"] = 1
    workbook.save(sources / "0001.xlsx")
    return build_inventory_manifest(sources, workbook_ids=["0001"])


def _registry(path: Path, approvals: list[dict]) -> Path:
    core = {
        "schema_version": "source-inventory-approval-registry/v1",
        "approvals": approvals,
    }
    document = {**core, "registry_sha256": object_hash(core)}
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_commit_pinned_inventory_approval_accepts_exact_claims(
    tmp_path,
    monkeypatch,
):
    inventory = _inventory(tmp_path)
    batch_id = "batch-test"
    pinned = _registry(
        tmp_path / "pinned.json",
        [approval_claims(inventory, batch_id=batch_id)],
    )
    monkeypatch.setattr(xl_inventory_approval, "DEFAULT_REGISTRY", pinned)

    approval = resolve_inventory_approval(inventory, batch_id=batch_id)

    assert approval["approval"]["inventory_sha256"] == (
        inventory["inventory_sha256"]
    )
    assert approval["inventory_approval_sha256"]


@pytest.mark.parametrize(
    "workbook_id",
    [
        "../x",
        "/tmp/x",
        "bad\x00id",
        "bad id",
        "bad:id",
        "bad\nid",
        r"bad\id",
    ],
)
def test_registry_rejects_unsafe_batch_ledger_ids(
    tmp_path,
    workbook_id,
):
    inventory = _inventory(tmp_path)
    claim = approval_claims(inventory, batch_id="batch-test")
    row = dict(claim["batch_source_ledger"][0])
    row["workbook_id"] = workbook_id
    claim["batch_source_ledger"] = [row]
    claim["batch_source_ledger_sha256"] = object_hash([row])
    registry = {
        "schema_version": "source-inventory-approval-registry/v1",
        "approvals": [claim],
    }
    registry["registry_sha256"] = object_hash(registry)

    with pytest.raises(
        InventoryApprovalError,
        match="ledger IDs",
    ):
        xl_inventory_approval.validate_registry(registry)


def test_registry_rejects_incomplete_batch_ledger_row(tmp_path):
    inventory = _inventory(tmp_path)
    claim = approval_claims(inventory, batch_id="batch-test")
    row = dict(claim["batch_source_ledger"][0])
    row.pop("conversion_status")
    claim["batch_source_ledger"] = [row]
    claim["batch_source_ledger_sha256"] = object_hash([row])
    registry = {
        "schema_version": "source-inventory-approval-registry/v1",
        "approvals": [claim],
    }
    registry["registry_sha256"] = object_hash(registry)

    with pytest.raises(
        InventoryApprovalError,
        match="ledger row",
    ):
        xl_inventory_approval.validate_registry(registry)


def test_self_authored_registry_cannot_override_pinned_approval(
    tmp_path,
    monkeypatch,
):
    inventory = _inventory(tmp_path)
    pinned = _registry(
        tmp_path / "pinned.json",
        [approval_claims(inventory, batch_id="approved")],
    )
    supplied = _registry(
        tmp_path / "supplied.json",
        [approval_claims(inventory, batch_id="attacker")],
    )
    monkeypatch.setattr(xl_inventory_approval, "DEFAULT_REGISTRY", pinned)

    with pytest.raises(InventoryApprovalError, match="commit-pinned"):
        resolve_inventory_approval(
            inventory,
            batch_id="attacker",
            registry_path=supplied,
        )


def test_inventory_drift_after_approval_is_rejected(tmp_path, monkeypatch):
    inventory = _inventory(tmp_path)
    pinned = _registry(
        tmp_path / "pinned.json",
        [approval_claims(inventory, batch_id="batch-test")],
    )
    monkeypatch.setattr(xl_inventory_approval, "DEFAULT_REGISTRY", pinned)
    changed = json.loads(json.dumps(inventory))
    changed["workbooks"][0]["classification"] = "conversion_unverified"
    unsigned = dict(changed)
    unsigned.pop("inventory_sha256")
    changed["inventory_sha256"] = object_hash(unsigned)

    with pytest.raises(ValueError):
        resolve_inventory_approval(changed, batch_id="batch-test")


def test_symlinked_registry_is_rejected(tmp_path, monkeypatch):
    inventory = _inventory(tmp_path)
    real = _registry(
        tmp_path / "real.json",
        [approval_claims(inventory, batch_id="batch-test")],
    )
    link = tmp_path / "registry.json"
    link.symlink_to(real)
    monkeypatch.setattr(xl_inventory_approval, "DEFAULT_REGISTRY", link)

    with pytest.raises(InventoryApprovalError, match="regular file"):
        resolve_inventory_approval(inventory, batch_id="batch-test")


def test_symlinked_inventory_artifact_is_rejected(tmp_path):
    inventory = _inventory(tmp_path)
    real = tmp_path / "inventory.json"
    real.write_text(json.dumps(inventory), encoding="utf-8")
    link = tmp_path / "inventory-link.json"
    link.symlink_to(real)

    with pytest.raises(InventoryApprovalError, match="symlink"):
        read_inventory_artifact(link)


def test_inventory_shards_merge_into_one_exact_cohort(tmp_path):
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    for root, workbook_id in ((first_root, "0001"), (second_root, "0002")):
        workbook = Workbook()
        workbook.active["A1"] = 1
        workbook.save(root / f"{workbook_id}.xlsx")
    first = build_inventory_manifest(first_root, workbook_ids=["0001"])
    second = build_inventory_manifest(second_root, workbook_ids=["0002"])

    merged = assemble_inventory_manifest(
        first["workbooks"] + second["workbooks"]
    )

    assert merged["cohort"]["workbook_ids"] == ["0001", "0002"]
    assert merged["cohort"]["size"] == 2
    assert merged["classifications"] == {"native_source": 2}

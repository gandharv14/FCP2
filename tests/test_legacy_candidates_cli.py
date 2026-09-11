"""Explicit candidate-only entry point; conversion boundary is synthetic."""
import json

import pytest

from scripts import legacy_candidates as cli
from xl_legacy_conversion import LegacyConversionError, canonical, file_hash


def inputs(tmp_path, monkeypatch):
    inventory = tmp_path / "inventory.json"
    rows = [{"workbook_id": f"{i:04d}", "format": ".xls" if i < 3 else ".xlsx",
             "path": str(tmp_path / f"{i:04d}.xls"), "sha256": "c" * 64} for i in range(1, 701)]
    inventory.write_bytes(canonical({"schema_version": "raw-corpus/v1", "sources": rows}))
    config = tmp_path / "config.json"
    config.write_bytes(canonical({"schema_version": "legacy-candidate-config/v1", "timeout_seconds": 90,
        "converter": {"executable": str(tmp_path / "app" / "soffice"), "sha256": "a" * 64,
                      "application_root": str(tmp_path / "app"), "sandbox_sha256": "b" * 64}}))
    monkeypatch.setattr(cli.ConverterIdentity, "validate", lambda self: None)
    return dict(inventory=inventory, inventory_sha256=file_hash(inventory),
                config=config, config_sha256=file_hash(config), workbook_ids=["0001", "0002"],
                output_root=tmp_path / "campaign")


def test_selected_candidates_are_sequential_unverified_and_preserve_denominator(tmp_path, monkeypatch):
    args = inputs(tmp_path, monkeypatch)
    calls = []
    def convert(row, destination, identity, *, timeout_seconds):
        calls.append(row["workbook_id"])
        assert timeout_seconds == 90
        return {"record_sha256": "d" * 64}
    monkeypatch.setattr(cli, "produce_candidate", convert)
    result = cli.prepare(**args)
    assert calls == ["0001", "0002"]
    assert result["original_denominator"] == 700
    assert result["verified_promotions"] == 0
    assert result["inventory_adoption_supported"] is False
    assert all(r["classification"] == "conversion_unverified" for r in result["results"])


@pytest.mark.parametrize("fault", ["config_hash", "inventory_hash", "duplicate_selection", "unknown", "native", "duplicate_inventory"])
def test_unpinned_or_ambiguous_selection_never_executes(tmp_path, monkeypatch, fault):
    args = inputs(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "produce_candidate", lambda *a, **k: pytest.fail("no conversion"))
    if fault == "config_hash":
        args["config_sha256"] = "f" * 64
    elif fault == "inventory_hash":
        args["inventory_sha256"] = "f" * 64
    elif fault == "duplicate_selection":
        args["workbook_ids"] = ["0001", "0001"]
    elif fault == "unknown":
        args["workbook_ids"] = ["9999"]
    elif fault == "native":
        args["workbook_ids"] = ["0003"]
    else:
        doc = json.loads(args["inventory"].read_bytes())
        doc["sources"].append(doc["sources"][0])
        args["inventory"].write_bytes(canonical(doc))
        args["inventory_sha256"] = file_hash(args["inventory"])
    with pytest.raises(LegacyConversionError):
        cli.prepare(**args)
    assert not args["output_root"].exists()


def test_failed_workbook_isolated_without_becoming_source_invalid(tmp_path, monkeypatch):
    args = inputs(tmp_path, monkeypatch)
    def convert(row, *a, **k):
        if row["workbook_id"] == "0001":
            raise LegacyConversionError("sandbox unavailable")
        return {"record_sha256": "d" * 64}
    monkeypatch.setattr(cli, "produce_candidate", convert)
    result = cli.prepare(**args)
    assert [r["classification"] for r in result["results"]] == ["conversion_failed", "conversion_unverified"]
    assert result["verified_promotions"] == 0


def test_changed_configuration_stops_remaining_execution(tmp_path, monkeypatch):
    args = inputs(tmp_path, monkeypatch)
    calls = []
    def convert(row, *a, **k):
        calls.append(row["workbook_id"])
        args["config"].write_bytes(b"changed authority")
        return {"record_sha256": "d" * 64}
    monkeypatch.setattr(cli, "produce_candidate", convert)
    with pytest.raises(LegacyConversionError, match="changed during"):
        cli.prepare(**args)
    assert calls == ["0001"]
    assert not (args["output_root"] / "preparation.json").exists()


def test_cli_requires_both_explicit_pin_arguments():
    with pytest.raises(SystemExit) as exc:
        cli.main(["--inventory", "/tmp/inventory", "--config", "/tmp/config",
                  "--output-root", "/tmp/output", "--workbook-id", "0001"])
    assert exc.value.code == 2


@pytest.mark.parametrize("ids", [["Case", "case"], ["../escape", "0002"],
                                 ["A/B", "0002"], ["name\n", "0002"], ["_name", "0002"]])
def test_inventory_identity_grammar_and_casefold_ambiguity_reject_before_mutation(tmp_path, monkeypatch, ids):
    args = inputs(tmp_path, monkeypatch)
    doc = json.loads(args["inventory"].read_bytes())
    for row, value in zip(doc["sources"], ids):
        row["workbook_id"] = value
    args["inventory"].write_bytes(canonical(doc))
    args["inventory_sha256"] = file_hash(args["inventory"])
    monkeypatch.setattr(cli, "produce_candidate", lambda *a, **k: pytest.fail("no conversion"))
    with pytest.raises(LegacyConversionError, match="ambiguous original workbook identity"):
        cli.prepare(**args)
    assert not args["output_root"].exists()


def test_dot_dash_underscore_ids_follow_frozen_inventory_grammar(tmp_path, monkeypatch):
    args = inputs(tmp_path, monkeypatch)
    doc = json.loads(args["inventory"].read_bytes())
    doc["sources"][0]["workbook_id"] = "A.b-c_d"
    args["inventory"].write_bytes(canonical(doc))
    args["inventory_sha256"] = file_hash(args["inventory"])
    args["workbook_ids"] = ["A.b-c_d"]
    monkeypatch.setattr(cli, "produce_candidate", lambda *a, **k: {"record_sha256": "d" * 64})
    assert cli.prepare(**args)["results"][0]["workbook_id"] == "A.b-c_d"

"""Hermetic converter/receipt contracts; no installed Excel or LO execution."""
import base64
import json
from pathlib import Path
import subprocess
import time
import zipfile
from xml.etree import ElementTree as ET

import pytest
from openpyxl import Workbook
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

import xl_legacy_conversion as legacy
import xl_source_recalc as recalc


def workbook(path):
    wb = Workbook()
    wb.active.title = "2026 O'Brien"
    wb.active["A1"] = 10
    wb.active["A2"] = "=A1*2"
    wb.save(path)
    return path


def source(tmp_path, suffix=".xls"):
    original = tmp_path / ("0001" + suffix)
    if suffix == ".xls":
        original.write_bytes(bytes.fromhex("d0cf11e0a1b11ae1") + b"synthetic fixture, not a BIFF parser proof")
    else:
        with zipfile.ZipFile(original, "w") as archive:
            archive.writestr("xl/workbook.bin", b"synthetic XLSB fixture")
    return {"workbook_id": "0001", "path": str(original), "sha256": legacy.file_hash(original), "format": suffix}


def configured(tmp_path, monkeypatch):
    identity = legacy.ConverterIdentity(str(tmp_path / "app" / "soffice"), "a" * 64,
                                       str(tmp_path / "app"), "b" * 64)
    # Command tests only: no claim that synthetic executable is installed or safe.
    monkeypatch.setattr(legacy.ConverterIdentity, "validate", lambda self: None)
    monkeypatch.setattr(legacy.platform, "system", lambda: "Darwin")
    return identity


def conversion_output(argv, **kwargs):
    output = Path(argv[argv.index("--outdir") + 1])
    workbook(output / "0001.xlsx")
    return subprocess.CompletedProcess(argv, 0)


def test_ole_magic_precedes_incidental_zip(tmp_path):
    row = source(tmp_path)
    with zipfile.ZipFile(row["path"], "a") as archive:
        archive.writestr("xl/workbook.xml", "incidental embedding")
    assert zipfile.is_zipfile(row["path"])
    assert legacy.detect_format(Path(row["path"])) == ".xls"


def test_binary_package_and_misnamed_ooxml_detected(tmp_path):
    assert legacy.detect_format(Path(source(tmp_path, ".xlsb")["path"])) == ".xlsb"
    assert legacy.detect_format(workbook(tmp_path / "mislabeled.XLS")) == ".xlsx"


def test_actual_command_contract_retains_unverified_immutable_evidence(tmp_path, monkeypatch):
    identity = configured(tmp_path, monkeypatch)
    row = source(tmp_path)
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return conversion_output(argv, **kwargs)
    monkeypatch.setattr(legacy.subprocess, "run", run)
    destination = tmp_path / "candidate"
    record = legacy.produce_candidate(row, destination, identity)
    assert record["classification"] == "conversion_unverified"
    assert record["original"]["sha256"] == row["sha256"]
    assert legacy.file_hash(Path(record["candidate"]["path"])) == record["candidate"]["sha256"]
    assert calls[0][0][:2] == ["/usr/bin/sandbox-exec", "-f"]
    assert calls[0][1]["env"]["HOME"].startswith(str(destination))
    assert calls[0][1]["timeout"] == 120
    assert "(deny network*)" in (destination / "sandbox.sb").read_text()
    assert "(deny mach-lookup)" in (destination / "sandbox.sb").read_text()
    assert "(deny default)" in (destination / "sandbox.sb").read_text()
    assert not list(destination.glob(".conversion-*"))
    assert not Path(record["candidate"]["path"]).stat().st_mode & 0o222
    for artifact in record["evidence"]:
        assert legacy.file_hash(Path(artifact["path"])) == artifact["sha256"]
    assert legacy.validate_candidate(destination / "candidate.json") == record
    with pytest.raises(FileExistsError):
        legacy.produce_candidate(row, destination, identity)


@pytest.mark.parametrize("failure", ["nonzero", "timeout", "missing", "extra", "source_changed"])
def test_interrupted_or_changed_conversion_never_completes(tmp_path, monkeypatch, failure):
    identity = configured(tmp_path, monkeypatch)
    row = source(tmp_path)
    def run(argv, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, 120)
        if failure == "nonzero":
            return subprocess.CompletedProcess(argv, 2)
        if failure != "missing":
            conversion_output(argv, **kwargs)
        if failure == "extra":
            output = Path(argv[argv.index("--outdir") + 1])
            (output / "extra.xlsx").write_bytes(b"unexpected")
        if failure == "source_changed":
            Path(row["path"]).write_bytes(b"changed")
        return subprocess.CompletedProcess(argv, 0)
    monkeypatch.setattr(legacy.subprocess, "run", run)
    with pytest.raises(legacy.LegacyConversionError):
        legacy.produce_candidate(row, tmp_path / "candidate", identity)
    assert not (tmp_path / "candidate" / "candidate.json").exists()
    assert (tmp_path / "candidate" / "command.json").exists()


def test_identity_and_container_rejected_before_execution(tmp_path, monkeypatch):
    identity = configured(tmp_path, monkeypatch)
    row = source(tmp_path)
    row["sha256"] = "c" * 64
    monkeypatch.setattr(legacy.subprocess, "run", lambda *a, **k: pytest.fail("must not execute"))
    with pytest.raises(legacy.LegacyConversionError, match="identity changed"):
        legacy.produce_candidate(row, tmp_path / "candidate", identity)
    assert not (tmp_path / "candidate").exists()


@pytest.mark.parametrize("value", ["/tmp/../converter", "/tmp//converter", "/tmp/line\nbreak", "relative"])
def test_unsafe_paths_rejected(value):
    with pytest.raises(legacy.LegacyConversionError):
        legacy._path(value)


@pytest.mark.parametrize("feature", ["macro", "external", "shared", "name", "table"])
def test_unsupported_semantics_rejected(tmp_path, feature):
    path = workbook(tmp_path / "candidate.xlsx")
    with zipfile.ZipFile(path) as source_zip:
        parts = {n: source_zip.read(n) for n in source_zip.namelist()}
    if feature == "macro":
        parts["xl/vbaProject.bin"] = b"macro"
    elif feature == "table":
        parts["xl/tables/table1.xml"] = b"<table/>"
    elif feature == "external":
        parts["xl/_rels/external.rels"] = b'<Relationships><Relationship TargetMode="External" Target="file:///private/input"/></Relationships>'
    elif feature == "shared":
        parts["xl/worksheets/sheet1.xml"] = parts["xl/worksheets/sheet1.xml"].replace(b"<f>", b'<f t="shared" si="0">')
    else:
        parts["xl/workbook.xml"] = parts["xl/workbook.xml"].replace(b"</workbook>", b'<definedNames><definedName name="rate">0.1</definedName></definedNames></workbook>')
    with zipfile.ZipFile(path, "w") as destination:
        for name, content in parts.items():
            destination.writestr(name, content)
    with pytest.raises(legacy.LegacyConversionError):
        legacy.scalar_snapshot(path)


def signed_proof(tmp_path, monkeypatch):
    row = source(tmp_path)
    candidate = workbook(tmp_path / "candidate.xlsx")
    snapshot = legacy.scalar_snapshot(candidate)
    observation = {"schema_version": legacy.OBSERVATION_SCHEMA, "source_sha256": row["sha256"],
                   "capture_method": "excel-original-before-save-as", "snapshot": snapshot}
    # Synthetic observation fixture is deliberately not a real binary read.
    key = Ed25519PrivateKey.generate()
    public = tmp_path / "public.pem"
    public.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    # Real cryptographic verification, with fixture key protected by this tempdir
    # instead of the production root-owned trust store.
    original_key_validator = recalc._trusted_public_key
    monkeypatch.setattr(recalc, "_trusted_public_key", lambda path, **kwargs:
                        original_key_validator(path, require_root_owner=False))
    payload = {"request_sha256": "d" * 64, "source_sha256": row["sha256"],
        "output_sha256": legacy.file_hash(candidate), "engine": "excel-macos", "engine_version": "fixture",
        "calculation_complete": True, "isolation_enforced": True,
        "network_isolation_mechanism": "macos-pf-anchor", "network_isolation_rules_sha256": recalc.PF_NETWORK_RULES_SHA256,
        "completed_at_ns": time.time_ns(), "isolation_controls": {x: True for x in (
            "dedicated_session", "network_disabled", "macros_disabled", "add_ins_disabled", "link_updates_disabled", "prompts_suppressed")},
        "conversion_policy": legacy.POLICY, "observation_schema": legacy.OBSERVATION_SCHEMA,
        "original_observation_sha256": legacy.digest(observation), "candidate_observation_sha256": legacy.digest(snapshot),
        "observer_implementation_sha256": "e" * 64, "observed_original_before_save_as": True,
        "original_recalculation_complete": True, "candidate_recalculation_complete": True}
    def sign():
        return {"schema_version": "excel-runner-receipt/v1", "signed_payload": payload,
                "signature_base64": base64.b64encode(key.sign(recalc._canonical_bytes(payload))).decode()}
    args = dict(original=Path(row["path"]), candidate=candidate, original_observation=observation,
                trusted_public_key=public, request_sha256="d" * 64, observer_implementation_sha256="e" * 64)
    return args, payload, sign


def test_signed_closed_semantic_contract_passes_without_inventory_adoption(tmp_path, monkeypatch):
    args, payload, sign = signed_proof(tmp_path, monkeypatch)
    result = legacy.verify_equivalence(**args, receipt=sign())
    assert result["financial_semantics_equivalent"] is True
    assert result["inventory_adoption_supported"] is False


@pytest.mark.parametrize("alteration", ["unsigned", "missing_observation", "wrong_producer", "cached_answer", "changed_original", "not_before_saveas"])
def test_tampered_or_incomplete_proof_rejected(tmp_path, monkeypatch, alteration):
    args, payload, sign = signed_proof(tmp_path, monkeypatch)
    receipt = sign()
    if alteration == "unsigned":
        receipt["signature_base64"] = base64.b64encode(b"0" * 64).decode()
    elif alteration == "missing_observation":
        payload.pop("original_observation_sha256")
        receipt = sign()
    elif alteration == "wrong_producer":
        args["observer_implementation_sha256"] = "f" * 64
    elif alteration == "cached_answer":
        args["original_observation"]["snapshot"]["sheets"][0]["cells"][1]["value"] = 20
    elif alteration == "changed_original":
        args["original"].write_bytes(b"altered")
    else:
        args["original_observation"]["capture_method"] = "converted-workbook"
    with pytest.raises((legacy.LegacyConversionError, recalc.RecalculationError)):
        legacy.verify_equivalence(**args, receipt=receipt)


def change_calculation(path, attributes):
    with zipfile.ZipFile(path) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    root = ET.fromstring(parts["xl/workbook.xml"])
    props = root.find(f"{legacy.NS}calcPr")
    if props is None:
        props = ET.SubElement(root, f"{legacy.NS}calcPr")
    props.attrib.update(attributes)
    parts["xl/workbook.xml"] = ET.tostring(root)
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)


def test_neutral_financial_settings_use_effective_values_not_xml_presence(tmp_path):
    path = workbook(tmp_path / "candidate.xlsx")
    before = legacy.scalar_snapshot(path)
    change_calculation(path, {"calcMode": "auto", "refMode": "A1", "iterate": "false",
                             "iterateCount": "100", "iterateDelta": "0.001", "fullPrecision": "true"})
    assert legacy.scalar_snapshot(path) == before
    assert before["schema_version"] == "legacy-scalar-semantics/v2"
    assert before["calculation"] == {"calculation_mode": "auto", "reference_mode": "A1",
        "iteration_enabled": False, "iteration_count": 100, "iteration_delta": 0.001,
        "full_precision": True}


@pytest.mark.parametrize("field,value", [("calcMode", "manual"), ("refMode", "R1C1"),
    ("iterate", "1"), ("iterateCount", "77"), ("iterateDelta", "0.0001"), ("fullPrecision", "0")])
def test_financial_setting_change_cannot_pass_signed_equivalence(tmp_path, monkeypatch, field, value):
    args, payload, sign = signed_proof(tmp_path, monkeypatch)
    change_calculation(args["candidate"], {field: value})
    payload["output_sha256"] = legacy.file_hash(args["candidate"])
    with pytest.raises(legacy.LegacyConversionError, match="financial semantics differ"):
        legacy.verify_equivalence(**args, receipt=sign())


@pytest.mark.parametrize("field,value", [("calcId", "999"), ("fullCalcOnLoad", "0"),
    ("forceFullCalc", "1"), ("calcCompleted", "0"), ("calcOnSave", "0"),
    ("concurrentCalc", "0"), ("concurrentManualCount", "1")])
def test_serialization_hints_stay_provenance_and_do_not_replace_signed_byte_binding(tmp_path, monkeypatch, field, value):
    args, payload, sign = signed_proof(tmp_path, monkeypatch)
    old_receipt = sign()
    before = legacy.scalar_snapshot(args["candidate"])
    before_provenance = legacy.candidate_provenance(args["candidate"])
    change_calculation(args["candidate"], {field: value})
    assert legacy.scalar_snapshot(args["candidate"]) == before
    assert legacy.candidate_provenance(args["candidate"]) != before_provenance
    with pytest.raises(recalc.RecalculationError):
        legacy.verify_equivalence(**args, receipt=old_receipt)
    # A fresh real test signature binds the changed exact output bytes. Original
    # observation remains unchanged; hints alone never supply that observation.
    payload["output_sha256"] = legacy.file_hash(args["candidate"])
    assert legacy.verify_equivalence(**args, receipt=sign())["financial_semantics_equivalent"] is True
    payload.pop("original_observation_sha256")
    with pytest.raises(legacy.LegacyConversionError, match="independent conversion observations"):
        legacy.verify_equivalence(**args, receipt=sign())


def test_legacy_observation_schema_cannot_replay_new_semantics(tmp_path, monkeypatch):
    args, payload, sign = signed_proof(tmp_path, monkeypatch)
    args["original_observation"]["schema_version"] = "legacy-binary-observation/v1"
    payload["observation_schema"] = "legacy-binary-observation/v1"
    payload["original_observation_sha256"] = legacy.digest(args["original_observation"])
    with pytest.raises(legacy.LegacyConversionError, match="original binary"):
        legacy.verify_equivalence(**args, receipt=sign())

"""Explicit quarantine-chain inventory adapter; no implicit source approval.

Host policy paths are code constants, not caller/record/receipt parameters. The
controller must resolve_approved_quarantine_record before using this new route,
then still run ordinary final source publication and segmentation gates.
"""
from pathlib import Path
import re

import xl_table_quarantine_lineage as chain
from scripts.recalc import excel_runner as runner

q = chain.quarantine
SCHEMA = "source-transformation-lineage/v2"
POLICY = "quarantined-table-source-inventory/v1"
PRODUCER_CONFIG = Path("/etc/harbor/excel-table-quarantine.json")
BASE_CONFIG = Path("/etc/harbor/excel-runner.json")
PRODUCER = Path("/Library/Application Support/FCP2/recalc/table_quarantine_runner.py")


def _protected(path):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("protected host policy path must be absolute")
    try:
        runner.validate_root_file(path)
    except runner.RunnerError as exc:
        raise ValueError(str(exc)) from exc
    return chain.record(path)


def protected_policy():
    """Read public trust configuration without loading private keys or code."""
    files = {"producer_config": _protected(PRODUCER_CONFIG),
             "base_config": _protected(BASE_CONFIG), "producer": _protected(PRODUCER)}
    config = chain.load(PRODUCER_CONFIG)
    base = chain.load(BASE_CONFIG)
    q.same(config.get("schema_version"), "excel-table-quarantine-config/v1", "producer configuration")
    q.same(base.get("schema_version"), "excel-runner-config/v1", "base configuration")
    q.same(config.get("base_config"), str(BASE_CONFIG), "fixed base configuration")
    q.same(config.get("dispatcher_sha256"), files["producer"]["sha256"], "protected producer")
    q.same(config.get("selected_policy_code"), q.code_identity(files["producer"]["sha256"]), "current selected policy")
    implementation = Path(base.get("runner_implementation", ""))
    q.same(config.get("base_module"), str(implementation), "protected observer path")
    files["observer"] = _protected(implementation)
    for expected in (config.get("base_module_sha256"), base.get("runner_implementation_sha256"),
                     q.file_hash(q.observer.__file__)):
        q.same(files["observer"]["sha256"], expected, "protected observer bytes")
    key = Path(base.get("receipt_public_key", ""))
    files["public_key"] = _protected(key)
    q.same(files["public_key"]["sha256"], base.get("receipt_public_key_sha256"), "protected public key")
    controls = base.get("isolation_controls")
    if not isinstance(controls, dict) or any(controls.get(k) is not True for k in runner.REQUIRED_CONTROLS):
        raise ValueError("protected isolation controls incomplete")
    versions = config.get("permitted_engine_versions")
    if (not isinstance(versions, list) or not versions
            or any(not isinstance(x, str) or re.fullmatch(r"[0-9]+\.[0-9]+(?:\.[0-9]+){0,2}", x) is None for x in versions)
            or len(set(versions)) != len(versions)):
        raise ValueError("protected engine versions must be unique concrete versions")
    return {"files": files, "selected_policy_code": config["selected_policy_code"],
            "permitted_engine_versions": versions}


PATH_KEYS = ("original", "staged", "intermediate", "candidate", "curation",
             "request_path", "result_path", "materialization_path")
EXPECTED_KEYS = ("expected_workbook", "expected_original_sha256", "expected_curation_sha256",
                 "expected_ordered_outputs")


def _make_record(*, lineage_path, bindings):
    from xl_source_inventory import _derived_workbook_record, _record
    q.keys(bindings, (*PATH_KEYS, *EXPECTED_KEYS))
    trust = protected_policy()
    selected = {**bindings, "producer_sha256": trust["files"]["producer"]["sha256"],
                "public_key": Path(trust["files"]["public_key"]["path"])}
    request = chain.load(bindings["request_path"])
    if request.get("engine_version") not in trust["permitted_engine_versions"]:
        raise ValueError("request engine is not permitted by protected policy")
    lineage_file = chain.record(lineage_path)
    chain.validate_lineage(chain.load(lineage_path), **selected)
    candidate = Path(bindings["candidate"]).absolute()
    original = Path(bindings["original"]).absolute()
    record = _derived_workbook_record(bindings["expected_workbook"], candidate, candidate.parent, None)
    if record["route"] not in {"pass", "restricted_pass"}:
        raise ValueError("final candidate requires an unsupported route")
    core = {"schema_version": SCHEMA, "policy_version": POLICY,
            "source_approval": False, "task_approval": False,
            "original_source": _record(original, original.parent),
            "bindings": {**{k: str(Path(bindings[k]).absolute()) for k in PATH_KEYS},
                         **{k: bindings[k] for k in EXPECTED_KEYS}},
            "lineage_file": lineage_file, "protected_policy": trust,
            "adapter_sha256": q.file_hash(__file__)}
    # Recheck immutable evidence and host trust after the expensive proof.
    q.same(chain.record(lineage_path), lineage_file, "retained lineage changed")
    q.same(protected_policy(), trust, "protected policy changed during validation")
    core["manifest_sha256"] = q.digest(core)
    record["transformation"] = core
    return record


def quarantine_workbook_record(*, lineage_path, **bindings):
    """Construct an explicit, unapproved record from independent caller identities."""
    return _make_record(lineage_path=lineage_path, bindings=bindings)


def validate_quarantine_transformation(record):
    lineage = record["transformation"]
    q.keys(lineage, ("schema_version", "policy_version", "source_approval", "task_approval",
                     "original_source", "bindings", "lineage_file", "protected_policy",
                     "adapter_sha256", "manifest_sha256"))
    expected = _make_record(lineage_path=lineage["lineage_file"]["path"], bindings=lineage["bindings"])
    # Full record equality rejects stale health, conversions, self-selected trust,
    # altered identities and additional fields, even if the outer hash is reset.
    q.same(record, expected, "quarantine inventory record")
    return {"status": "verified_chain_inventory_evidence", "source_approval": False,
            "task_approval": False, "manifest_sha256": lineage["manifest_sha256"]}


def resolve_approved_quarantine_record(inventory, *, workbook_id, batch_id):
    """Require the existing commit-pinned full-cohort approval even for final pass.

    This is a prerequisite only; it does not publish source/segmentation/releases.
    No registry override is accepted and no approval is generated or written.
    """
    from xl_source_inventory import validate_inventory_manifest
    from xl_inventory_approval import resolve_inventory_approval
    validate_inventory_manifest(inventory)
    selected = [r for r in inventory["workbooks"] if r["workbook_id"] == workbook_id]
    if len(selected) != 1:
        raise ValueError("exactly one original workbook must be selected")
    validate_quarantine_transformation(selected[0])
    approval = resolve_inventory_approval(inventory, batch_id=batch_id)
    return {"record": selected[0], "inventory_approval": approval,
            "required_next_gates": ["source_publication", "strict_segmentation", "Harbor_qualification"],
            "task_approval": False}


def adopt_approved_inventory(inventory_path, *, batch_id, workbook_id, original, curation):
    """Return a hash-bound controller checkpoint after explicit full approval."""
    from xl_inventory_approval import read_inventory_artifact
    inventory_file = chain.record(inventory_path)
    approved = resolve_approved_quarantine_record(read_inventory_artifact(inventory_path),
                                                 workbook_id=workbook_id, batch_id=batch_id)
    record = approved["record"]
    transformation = record["transformation"]
    binding = transformation["bindings"]
    q.same(str(Path(original).absolute()), binding["original"], "caller original path")
    q.same(q.file_hash(original), binding["expected_original_sha256"], "caller original bytes")
    q.same(str(Path(curation).absolute()), binding["curation"], "caller retained curation path")
    q.same(q.file_hash(curation), binding["expected_curation_sha256"], "caller retained curation bytes")
    q.same(chain.record(inventory_path), inventory_file, "inventory changed during adoption")
    return {"policy_version": POLICY, "original_sha256": binding["expected_original_sha256"],
            "candidate": binding["candidate"], "candidate_sha256": record["sha256"],
            "manifest": transformation["lineage_file"]["path"],
            "manifest_sha256": transformation["lineage_file"]["sha256"],
            "curation": binding["curation"], "curation_sha256": binding["expected_curation_sha256"],
            "ordered_outputs": binding["expected_ordered_outputs"], "workbook_id": workbook_id,
            "inventory": inventory_file["path"], "inventory_sha256": inventory_file["sha256"],
            "inventory_batch_id": batch_id,
            "inventory_approval_sha256": approved["inventory_approval"]["inventory_approval_sha256"],
            "transformation_sha256": transformation["manifest_sha256"],
            "protected_policy_sha256": q.digest(transformation["protected_policy"])}


def validate_adoption(original, candidate, normalization):
    """Revalidate controller/source-lineage evidence, including current approval."""
    q.same(q.file_hash(normalization["inventory"]), normalization["inventory_sha256"], "adopted inventory")
    expected = adopt_approved_inventory(normalization["inventory"], batch_id=normalization["inventory_batch_id"],
        workbook_id=normalization["workbook_id"], original=original, curation=normalization["curation"])
    q.same(normalization, expected, "quarantine adoption checkpoint")
    q.same(q.file_hash(candidate), expected["candidate_sha256"], "immutable generation candidate")
    return {"original_sha256": expected["original_sha256"], "candidate_sha256": expected["candidate_sha256"],
            "manifest_sha256": expected["manifest_sha256"],
            "inventory_approval_sha256": expected["inventory_approval_sha256"]}

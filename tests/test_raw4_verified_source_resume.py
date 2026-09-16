"""Closed pre-admission continuation guard regressions, without executing work."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from test_response_guidance import fixture, save
from scripts import corpus
from scripts.fleet.migration import binding


@pytest.fixture
def boundary(fixture, monkeypatch):
    root, context, context_binding, *_ = fixture
    helper_path = Path(__file__).resolve().parents[1] / "diagnostics/raw-workbooks4-20260916/resume_verified_sources_v2.py"
    spec = importlib.util.spec_from_file_location("raw4_resume_guard_test", helper_path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    oldroot, newroot = root / "old-code", root / "new-code"
    for folder, data in ((oldroot, "old admission"), (newroot, "new admission")):
        source = folder / "scripts/batch/harbor_admission.py"
        source.parent.mkdir(parents=True)
        source.write_text(data)
        dispatcher = folder / "scripts/fleet/advance_authoring_v8.py"
        dispatcher.parent.mkdir(parents=True)
        dispatcher.write_text("unchanged dispatcher")
        save(folder / ".pipeline-code.json", {"scripts/batch/harbor_admission.py": binding(source)["sha256"],
                                               "scripts/fleet/advance_authoring_v8.py": binding(dispatcher)["sha256"]})
    monkeypatch.setattr(corpus, "code_hash", lambda: newroot.name)
    inventory = save(root / "inventory.json", {"sources": [{"workbook_id": "4001", **context["source"]}]})
    source = json.loads(Path(inventory["path"]).read_text())["sources"][0]
    ledger = save(root / "ledger.json", {"source": source, "workbook_id": "4001", "build": {"attempts": 1, "state": "VERIFIED"}})
    source_plan = save(root / "source-plan.json", {"home": str(root / "source-attempt-001")})
    source_result = save(root / "source-attempt-001/result.json", {"status": "RECONSTRUCTION_VERIFIED", "ledger": ledger,
                         "models_dispatched": 0, "plan": source_plan, "automatic_retry": False, "publication_performed": False})
    handoff = save(root / "handoff.json", {"state": "VERIFIED", "workbook": "4001", "ledger": ledger})
    old = {"home": str(root / "author-attempt-001"), "code_root": str(oldroot), "dispatcher": binding(oldroot / "scripts/fleet/advance_authoring_v8.py"),
           "reconstruction": {"mode": "source-first", "plan": source_plan}, "workbook": "4001", "inventory": inventory}
    old_binding = save(root / "old-plan.json", old)
    partial = Path(old["home"]) / "authoring"
    for name in ("frozen-code.json", "survey.json", "candidate.json", "dependency.json"):
        save(partial / name, {})
    new = {**old, "home": str(root / "author-attempt-002"), "code_root": str(newroot),
           "dispatcher": binding(newroot / "scripts/fleet/advance_authoring_v8.py"), "reconstruction": {"mode": "existing", "handoff": handoff}}
    new_binding = save(root / "new-plan.json", new)
    job = save(root / "job.json", {"inputs": [old_binding], "workbook": "4001", "source": context["source"]})
    intent = save(root / "intent.json", {"job": job})
    log = root / "worker.log"
    log.write_text("Traceback\nAdmissionError: ambiguous duplicate original bytes\n")
    result = save(root / "original-result.json", {"plan": old_binding, "status": "RECONCILIATION_REQUIRED", "failure_type": "AdmissionError"})
    outcome = save(root / "outcome.json", {"intent": intent, "status": "RECONCILIATION_REQUIRED", "returncode": 1,
                   "failure_type": None, "automatic_retry": False, "publication_performed": False, "log": binding(log), "result": result})
    index = save(root / "guidance-index.json", {"contexts": {"4001": context_binding}})
    repair = save(root / "repair.json", {"old_root": str(oldroot), "new_root": str(newroot), "changed_files": ["scripts/batch/harbor_admission.py"]})
    plan = {"schema_version": "raw4-verified-source-author-continuation/v1", "helper": binding(helper_path), "code_root": str(newroot),
            "original_job": job, "original_plan": old_binding, "prior_outcome": outcome, "continued_plan": new_binding,
            "guidance_index": index, "repair": repair, "source_handoff": handoff}
    return helper, root, plan, old, new, ledger, source_result, partial


def test_exact_pre_admission_boundary_is_read_only_and_reuses_context(boundary, monkeypatch):
    helper, root, plan, old, new, *_ = boundary
    monkeypatch.delenv("FCP_RESPONSE_GUIDANCE_INDEX", raising=False)
    monkeypatch.delenv("FCP_RESPONSE_GUIDANCE_INDEX_SHA256", raising=False)
    before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    selected = save(root / "continuation.json", plan)
    assert helper.load(selected)[2] == new
    assert not Path(new["home"]).exists()
    assert all(Path(p).read_bytes() == data for p, data in before.items())


@pytest.mark.parametrize("mutation", ["extra_field", "extra_model", "tracker", "consumed_home", "source_unverified", "source_two_attempts", "changed_numerical_code", "changed_scope", "foreign_dispatcher", "source_plan", "source_retry", "source_published", "wrong_failure"])
def test_any_nonmatching_boundary_is_held(boundary, mutation):
    helper, root, plan, old, new, ledger, source_result, partial = boundary
    if mutation == "extra_field":
        plan["reset_attempts"] = True
    elif mutation == "extra_model":
        save(partial / "model-campaign/response.json", {"completed": True})
    elif mutation == "tracker":
        save(partial / "trackers/workbooks/4001.json", {"revision": 0})
    elif mutation == "consumed_home":
        Path(new["home"]).mkdir()
    elif mutation in ("source_unverified", "source_two_attempts"):
        document = json.loads(Path(ledger["path"]).read_text())
        document["build"]["state" if mutation == "source_unverified" else "attempts"] = "HELD" if mutation == "source_unverified" else 2
        Path(ledger["path"]).write_text(json.dumps(document))
    elif mutation == "changed_numerical_code":
        (Path(new["code_root"]) / "scripts/batch/harbor_admission.py").write_text("tampered")
    elif mutation == "foreign_dispatcher":
        new["dispatcher"] = save(root / "foreign-dispatcher.json", {})
        plan["continued_plan"] = save(root / "changed-plan.json", new)
    elif mutation in ("source_plan", "source_retry", "source_published"):
        document = json.loads(Path(source_result["path"]).read_text())
        field = {"source_plan": "plan", "source_retry": "automatic_retry", "source_published": "publication_performed"}[mutation]
        document[field] = {"different": "source plan"} if mutation == "source_plan" else True
        Path(source_result["path"]).write_text(json.dumps(document))
    elif mutation == "wrong_failure":
        outcome = json.loads(Path(plan["prior_outcome"]["path"]).read_text())
        original_result = json.loads(Path(outcome["result"]["path"]).read_text())
        original_result["failure_type"] = "OtherError"
        outcome["result"] = save(root / "other-result.json", original_result)
        plan["prior_outcome"] = save(root / "other-outcome.json", outcome)
    else:
        new["workbook"] = "4002"
        plan["continued_plan"] = save(root / "changed-plan.json", new)
    with pytest.raises(ValueError):
        helper.load(save(root / "continuation.json", plan))

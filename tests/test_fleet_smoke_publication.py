import copy
import json
import pytest
from scripts.fleet import smoke_publication as s
from scripts.fleet.migration import retain


def custody():
    history = {"path": "/retained/history.json", "sha256": "a"*64}
    selection = {"workbook": "0270", "expected_fence": 2, "release": s.RELEASE, "history": history}
    outcome = {"path": "/retained/worker/outcome.json", "sha256": "b"*64}
    probe = {"status": "STOPPED_AND_READY", "active_pids": [], "claim": {"owner": "vm-worker", "fence": 2},
        "source": {"path": "/source.xlsx", "sha256": "c"*64}, "worker_outcome": outcome,
        "history": history, "code_root": str(s.REMOTE / "releases" / s.RELEASE / "code")}
    status = {"model_limit": 1, "publication_limit": 1, "operations": [], "workbooks": [
        {"workbook": "0270", "state": "hold", "fence": 2, "owner": "vm-worker", "source_sha256": "c"*64, "reason": json.dumps(outcome)}]}
    return selection, probe, status


def test_exact_completed_held_fence_can_prepare():
    selected, probe, status = custody()
    assert s.validate_custody(probe, status, selected)["fence"] == 2


def test_continuation_requires_new_selected_fence_and_its_own_durable_finish():
    selected, probe, status = custody()
    selected["expected_fence"] = 3
    probe["claim"]["fence"] = 3
    status["workbooks"][0]["fence"] = 3
    continued = {"path": "/retained/continuation/outcome.json", "sha256": "d"*64}
    probe["worker_outcome"] = continued
    with pytest.raises(ValueError, match="exact outcome"):
        s.validate_custody(probe, status, selected)
    status["workbooks"][0]["reason"] = json.dumps(continued)
    assert s.validate_custody(probe, status, selected)["fence"] == 3
    selected["expected_fence"] = 2
    with pytest.raises(ValueError, match="custody differs"):
        s.validate_custody(probe, status, selected)


@pytest.mark.parametrize("change", ["running", "new-fence", "uncertain-call", "unknown-finish", "other-history", "model-cap"])
def test_unsafe_publication_boundary_rejected(change):
    selected, probe, status = custody()
    if change == "running": probe["active_pids"] = [99]
    elif change == "new-fence": status["workbooks"][0]["fence"] = 3
    elif change == "uncertain-call": status["operations"] = [{"kind": "model", "state": "hold"}]
    elif change == "unknown-finish": status["workbooks"][0]["reason"] = json.dumps({"path": "other", "sha256": "b"*64})
    elif change == "other-history": probe["history"] = {"path": "/other", "sha256": "a"*64}
    elif change == "model-cap": status["model_limit"] = 2
    with pytest.raises(ValueError): s.validate_custody(probe, status, selected)


def test_launch_preview_cannot_mutate_coordinator_or_remote(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    selected = retain(root / "selection.json", {"release": s.RELEASE})
    plan = retain(root / "plan.json", {"workbook": "0270"})
    prepared = retain(root / "prepared.json", {"selection": selected, "plan": plan,
        "helper": {"path": "/retained/publisher.py", "sha256": s.PUBLISHER}})
    class RejectMutations:
        def call(self, *_args, **_kw): pytest.fail("preview contacted coordinator")
    monkeypatch.setattr(s, "ssh", lambda *_a, **_kw: pytest.fail("preview contacted remote"))
    result = s.launch(prepared, client=RejectMutations())
    assert result["execute"] is False and "remote" in result["command"]
    assert not (root / "execution-intent.json").exists()

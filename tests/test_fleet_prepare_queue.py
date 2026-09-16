from pathlib import Path
import pytest
from scripts.fleet import prepare_queue as p
from scripts.fleet.migration import retain, binding, checked


def fixture(root):
    root.mkdir(); q = root / "diagnostics/authoring-queue-extension-20260911T185027Z"
    runner = root / "diagnostics/authoring-resume-20260910/runner"; runner.mkdir(parents=True)
    for name in ("author_workbook_v3.py", "advance_authoring_v7.py"):
        (runner/name).write_text("# selected test fixture\n")
    retain(root / "taxonomy_out/workbooks.json", {})
    originals = []
    for wb in p.WORKBOOKS:
        source = root.parent / "sources" / (wb+".xlsx"); source.parent.mkdir(exist_ok=True); source.write_bytes(wb.encode())
        originals.append({"workbook_id": wb, **binding(source)})
    inventory = retain(root / "diagnostics/authoring-resume-20260910/root/inventory.json", {"sources": originals})
    queue = retain(q / "queue.json", {"items": [{"workbook_id": r["workbook_id"], "original_source": r, "history_class": "no_history"} for r in originals]})
    handoffs = []
    for r in originals:
        prep = retain(q / "fresh28-current-preparations-v2" / (r["workbook_id"]+".json"), {
            "queue": queue, "inventory": inventory, "restriction_inventory": {}, "inventory_batch_id": "test"})
        handoffs.append({"workbook_id": r["workbook_id"], "original_source": r, "preparation": prep, "status": "unstarted_reconstruction"})
    handoff = retain(root / "handoff.json", {"workbooks": handoffs})
    history = retain(root / "history.json", {"fresh_ids": p.WORKBOOKS, "roots": [str(root.parent / "old-history")]})
    bundle = retain(root / "bundle.json", {"release": "f"*64, "files": {"scripts/fleet/fresh.py": {"sha256": "a"*64}, "scripts/fleet/worker.py": {"sha256": "b"*64}}})
    return bundle, handoff, history


def test_nine_jobs_are_unique_and_guarded_without_launch(tmp_path):
    root = tmp_path.resolve() / "repo"; bundle, handoff, history = fixture(root)
    result = p.prepare(root, "f"*64, bundle, handoff, history, tmp_path.resolve() / "plans")
    value = checked(result)
    assert value["claims"] == 0 and value["workers_started"] == 0
    jobs = [checked(b) for batch in value["assignments"].values() for b in batch]
    assert len(jobs) == 9 and len({j["workbook"] for j in jobs}) == 9
    assert sorted(map(len, value["assignments"].values())) == [1]*7+[2]
    assert all(j["memory_limit_bytes"] == 8*1024**3 and j["minimum_disk_bytes"] == 12*1024**3 for j in jobs)
    assert all(checked(j["inputs"][0])["reconstruction"]["mode"] == "fresh" for j in jobs)
    assert not (root / "diagnostics/vm-fleet-20260912/workbooks").exists()


def test_consumed_original_attempt_blocks_new_plan(tmp_path):
    root = tmp_path.resolve() / "repo"; bundle, handoff, history = fixture(root)
    prior = root / "diagnostics/authoring-queue-extension-20260911T185027Z/reconstruction-0085-attempt-001"
    prior.mkdir(); (prior / "retained-attempt").write_text("consumed")
    with pytest.raises(ValueError, match="prior reconstruction consumed"):
        p.prepare(root, "f"*64, bundle, handoff, history, tmp_path.resolve() / "plans")
    assert (prior / "retained-attempt").read_text() == "consumed"


def test_wrong_release_cannot_be_assigned(tmp_path):
    root = tmp_path.resolve() / "repo"; bundle, handoff, history = fixture(root)
    with pytest.raises(ValueError, match="bundle version differs"):
        p.prepare(root, "e"*64, bundle, handoff, history, tmp_path.resolve() / "plans")


def test_rebind_all_nine_commands_select_the_new_plan_bytes(tmp_path):
    from scripts.fleet.worker import selected_plan
    root = tmp_path.resolve() / "repo"; bundle, handoff, history = fixture(root)
    old = p.prepare(root, "f"*64, bundle, handoff, history, tmp_path.resolve() / "plans")
    before = checked(old)
    successor = retain(root / "successor.json", {**checked(bundle), "release": "e"*64})
    new = p.rebind(old, successor, tmp_path.resolve() / "successor-plans")
    after = checked(new)
    assert checked(old) == before
    for host, rows in after["assignments"].items():
        for original, row in zip(before["assignments"][host], rows):
            previous, current = checked(original), checked(row)
            chosen = selected_plan(current)
            assert chosen == current["inputs"][0] and chosen != previous["inputs"][0]
            assert checked(chosen)["code_root"].endswith("/"+"e"*64+"/code")
            assert current["source"] == previous["source"]
            assert current["run"] == previous["run"] and current["result"] == previous["result"]
            assert current["memory_limit_bytes"] == previous["memory_limit_bytes"]
            # Reproduces the actual defect: valid new input, stale command.
            current["command"] = previous["command"]
            with pytest.raises(ValueError, match="selected plan differs"):
                selected_plan(current)


def test_rebind_rejects_a_consumed_home_before_writing_new_plans(tmp_path):
    root = tmp_path.resolve() / "repo"; bundle, handoff, history = fixture(root)
    old = p.prepare(root, "f"*64, bundle, handoff, history, tmp_path.resolve() / "plans")
    first = checked(checked(old)["assignments"]["hhu-server"][0])
    Path(checked(first["inputs"][0])["home"]).mkdir(parents=True)
    successor = retain(root / "successor.json", {**checked(bundle), "release": "e"*64})
    output = tmp_path.resolve() / "must-not-exist"
    with pytest.raises(ValueError, match="consumed"):
        p.rebind(old, successor, output)
    assert not output.exists()

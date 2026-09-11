import json
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import corpus
from scripts.recalc import offline_window


def test_attempt_log_history_survives_compatibility_path_reuse(tmp_path):
    from scripts.corpus_build import archive_build_log
    assert archive_build_log(tmp_path) is None
    path = tmp_path / "build.log"
    path.write_bytes(b"first interrupted attempt")
    first = archive_build_log(tmp_path)
    path.write_bytes(b"second attempt")
    second = archive_build_log(tmp_path)
    assert first != second
    assert Path(first["path"]).read_bytes() == b"first interrupted attempt"
    assert corpus.digest(second["path"]) == second["sha256"]
    assert archive_build_log(tmp_path) == second
    Path(second["path"]).write_bytes(b"altered evidence")
    with pytest.raises(ValueError, match="archived workbook build log changed"):
        archive_build_log(tmp_path)


def test_interrupted_table_retry_retains_selection_and_rejects_conflicts(tmp_path):
    from argparse import Namespace
    from scripts.corpus_build import retained_curation
    args = Namespace(curation_root=str(tmp_path / "curated"), run_root=str(tmp_path / "run"))
    source = dict(workbook_id="0001", sha256=corpus.object_hash("original"))
    original_bytes = b"original"
    import hashlib
    source["sha256"] = hashlib.sha256(original_bytes).hexdigest()
    first = tmp_path / "run/normalization/0001/tables-attempt-1"
    original = first / "inspection-source/0001.xlsx"
    original.parent.mkdir(parents=True)
    original.write_bytes(original_bytes)
    selection = first / "inspection-segments/0001/curation.toml"
    selection.parent.mkdir(parents=True)
    selection.write_text('targets = ["Sheet!A1"]\n')
    assert retained_curation(args, source, {}) == selection
    original.write_bytes(b"different original")
    with pytest.raises(ValueError, match="original source"):
        retained_curation(args, source, {})
    original.write_bytes(original_bytes)
    later = tmp_path / "run/segments/0001/curation.toml"
    later.parent.mkdir(parents=True)
    later.write_text('targets = ["Sheet!B1"]\n')
    with pytest.raises(ValueError, match="conflicting retained"):
        retained_curation(args, source, {})
    later.write_bytes(selection.read_bytes())
    assert retained_curation(args, source, {}) == selection


def test_completed_table_selection_revalidates_receipt_and_curation(tmp_path):
    from argparse import Namespace
    from scripts.corpus_build import retained_curation
    args = Namespace(curation_root=str(tmp_path / "curated"), run_root=str(tmp_path / "run"))
    source = dict(workbook_id="0001", sha256="original")
    selection = tmp_path / "saved-curation.toml"
    selection.write_text('targets = ["Sheet!A1"]\n')
    receipt = tmp_path / "manifest.json"
    receipt.write_text(json.dumps(dict(original_sha256="original", curation_sha256=corpus.digest(selection))))
    ledger = {"build_history": [{"normalization": dict(curation=str(selection),
        manifest=str(receipt), manifest_sha256=corpus.digest(receipt))}]}
    assert retained_curation(args, source, ledger) == selection
    selection.write_text('targets = ["Sheet!B1"]\n')
    with pytest.raises(ValueError, match="curation changed"):
        retained_curation(args, source, ledger)
    receipt.write_text('{}')
    with pytest.raises(ValueError, match="receipt changed"):
        retained_curation(args, source, ledger)


def test_inventory_preserves_uppercase_legacy_and_rejects_duplicates(tmp_path):
    root = tmp_path / "raw"
    root.mkdir()
    (root / "0001.XLS").write_bytes(b"legacy")
    manifest = corpus.inventory([root], tmp_path / "inventory.json", 1)
    assert manifest["sources"][0]["format"] == ".xls"
    assert corpus.read_inventory(tmp_path / "inventory.json") == manifest
    (root / "0001.xlsx").write_bytes(b"different")
    with pytest.raises(ValueError, match="ambiguous"):
        corpus.inventory([root], tmp_path / "other.json")


def test_controller_failure_replaces_stale_success_and_preserves_corrupt_bytes(tmp_path):
    from scripts.corpus_build import record_orchestration_failure
    source = dict(workbook_id='0001', path='/raw/0001.xlsx', sha256='frozen')
    directory = tmp_path / 'workbooks/0001'; directory.mkdir(parents=True)
    path = directory / 'ledger.json'
    path.write_text(json.dumps({'source': source, 'state': 'READY',
        'build': {'state': 'VERIFIED', 'attempts': 2}}))
    failure = dict(workbook_id='0001', state='STOPPED', stage='orchestration', failure='changed curation')
    record_orchestration_failure(tmp_path, source, failure, 'new-code')
    value = json.loads(path.read_text())
    assert value['build']['state'] == 'STOPPED' and value['build']['attempts'] == 3
    assert value['build_history'][0]['state'] == 'VERIFIED'
    path.write_bytes(b'{interrupted')
    record_orchestration_failure(tmp_path, source, failure, 'new-code')
    value = json.loads(path.read_text())
    assert value['source'] == source and value['build']['state'] == 'STOPPED'
    assert Path(value['corrupt_checkpoint']['path']).read_bytes() == b'{interrupted'


def test_worker_code_snapshot_includes_policy_json_and_rejects_tampering(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    policy = root / "policy.json"
    policy.write_text('{"version": 1}')
    monkeypatch.setattr(corpus, "ROOT", root)
    monkeypatch.setattr(corpus, "code_files", lambda: {"policy.json": corpus.digest(policy)})
    snapshot, fingerprint = corpus.snapshot_code(tmp_path / "snapshots")
    policy.write_text('{"version": 2}')
    monkeypatch.setattr(corpus, "ROOT", snapshot)
    assert corpus.code_hash() == fingerprint
    (snapshot / "policy.json").write_text('{"version": 3}')
    with pytest.raises(ValueError, match="snapshot changed"):
        corpus.code_hash()


def test_stage_report_keeps_blocked_sources_and_does_not_trust_promotion_flags(tmp_path):
    from scripts.corpus_report import build_report
    manifest = {"inventory_sha256": "frozen", "sources": [
        {"workbook_id": "0001", "path": "/original.xls", "sha256": "legacy", "format": ".xls"},
        {"workbook_id": "0002", "path": "/original.xlsx", "sha256": "modern", "format": ".xlsx"}]}
    directory = tmp_path / "workbooks/0002"
    directory.mkdir(parents=True)
    (directory / "ledger.json").write_text(json.dumps({"state": "READY", "promoted": True,
        "build": {"state": "VERIFIED"}}))
    report = build_report(manifest, tmp_path)
    assert report["raw_denominator"] == 2
    assert len(report["workbooks"]) == 2
    assert report["verified_promoted"] is None
    assert report["harbor_yield"] is None


def test_legacy_resume_and_tampered_source(tmp_path):
    source = tmp_path / "0001.xls"
    source.write_bytes(b"original")
    item = dict(workbook_id="0001", path=str(source), sha256=corpus.digest(source), format=".xls")
    record = corpus.screen_one(item, tmp_path / "run", "code", 10)
    assert record["attempts"] == 1
    assert record["state"] == "STOPPED"
    assert corpus.screen_one(item, tmp_path / "run", "code", 10)["attempts"] == 1
    source.write_bytes(b"changed")
    changed = corpus.screen_one(item, tmp_path / "run", "code", 10)
    assert changed["attempts"] == 2
    assert "source_hash_changed" in changed["failure"]


def test_interrupted_and_corrupt_checkpoint_restarts(tmp_path):
    source = tmp_path / "0001.xls"
    source.write_bytes(b"original")
    item = dict(workbook_id="0001", path=str(source), sha256=corpus.digest(source), format=".xls")
    run = tmp_path / "run"
    record = corpus.screen_one(item, run, "code", 10)
    ledger = run / "workbooks/0001/ledger.json"
    record["state"] = "RUNNING"
    ledger.write_text(json.dumps(record))
    assert corpus.screen_one(item, run, "code", 10)["attempts"] == 2
    ledger.write_text("partial {")
    assert corpus.screen_one(item, run, "code", 10)["state"] == "STOPPED"


def test_intake_refresh_preserves_downstream_history_and_original_evidence(tmp_path):
    source = tmp_path / "0001.xls"
    source.write_bytes(b"original")
    item = dict(workbook_id="0001", path=str(source), sha256=corpus.digest(source), format=".xls")
    run = tmp_path / "run"
    previous = corpus.screen_one(item, run, "old-code", 10)
    directory = run / "workbooks/0001"
    ledger = directory / "ledger.json"
    previous.update(build={"state": "VERIFIED", "attempts": 3, "source_generation_id": "old-source"},
                    build_history=[{"state": "STOPPED", "attempts": 2}],
                    harbor={"release_id": "preserved-release"}, route="old-route", reason_codes=["old-reason"])
    ledger.write_text(json.dumps(previous))
    (directory / "source-health.json").write_bytes(b"previous health evidence")
    (directory / "health.log").write_bytes(b"previous observation log")
    refreshed = corpus.screen_one(item, run, "new-code", 10)
    assert refreshed["harbor"] == previous["harbor"]
    assert refreshed["build_history"] == previous["build_history"] + [previous["build"]]
    assert refreshed["build"]["state"] == "STALE" and refreshed["build"]["attempts"] == 3
    assert not {"route", "reason_codes", "health_sha256"}.intersection(refreshed)
    evidence = refreshed["history"][-1]["evidence"]
    assert len(evidence) == 3
    for record in evidence:
        assert corpus.digest(record["path"]) == record["sha256"]
    archived = {Path(e["path"]).name: Path(e["path"]) for e in evidence}
    assert json.loads(archived["ledger.json"].read_text()) == previous
    assert archived["source-health.json"].read_bytes() == b"previous health evidence"


@pytest.mark.parametrize("bad", [b"{interrupted", b"[]", b'{"history": null}', b'{"attempts": "invalid"}'])
def test_intake_preserves_corrupted_checkpoint_bytes(tmp_path, bad):
    source = tmp_path / "0001.xls"
    source.write_bytes(b"original")
    item = dict(workbook_id="0001", path=str(source), sha256=corpus.digest(source), format=".xls")
    directory = tmp_path / "run/workbooks/0001"
    directory.mkdir(parents=True)
    (directory / "ledger.json").write_bytes(bad)
    result = corpus.screen_one(item, tmp_path / "run", "code", 10)
    assert Path(result["corrupt_checkpoint"]["path"]).read_bytes() == bad
    assert corpus.digest(result["corrupt_checkpoint"]["path"]) == result["corrupt_checkpoint"]["sha256"]


def test_cached_intake_missing_source_gets_isolated_failure(tmp_path):
    source = tmp_path / "0001.xls"
    source.write_bytes(b"original")
    item = dict(workbook_id="0001", path=str(source), sha256=corpus.digest(source), format=".xls")
    run = tmp_path / "run"
    corpus.screen_one(item, run, "code", 10)
    source.unlink()
    result = corpus.screen_one(item, run, "code", 10)
    assert result["state"] == "STOPPED" and "FileNotFoundError" in result["failure"]


def test_isolation_restores_only_previously_active_interfaces_after_failure():
    with patch.object(offline_window, "active_interfaces", side_effect=[["lo0", "en0"], ["lo0"]]), \
         patch.object(offline_window.time, "sleep"), \
         patch.object(offline_window.subprocess, "run") as run:
        run.return_value.returncode = 0
        with pytest.raises(ValueError, match="execution failed"):
            with offline_window.isolated_interfaces():
                raise ValueError("execution failed")
    assert [c.args[0] for c in run.call_args_list] == [
        ["/sbin/ifconfig", "en0", "down"], ["/sbin/ifconfig", "en0", "up"]]


def test_isolation_cannot_run_when_network_remains_active():
    with patch.object(offline_window, "active_interfaces", side_effect=[["lo0", "en0"], ["lo0", "en0"]]), \
         patch.object(offline_window.time, "sleep"), \
         patch.object(offline_window.subprocess, "run") as run:
        run.return_value.returncode = 0
        with pytest.raises(RuntimeError, match="loopback-only"):
            with offline_window.isolated_interfaces():
                pytest.fail("must not execute without isolation")
        assert run.call_args.args[0][-1] == "up"


def test_macos_memory_observation_counts_all_group_members():
    from scripts.batch import hardened_runner as runner
    with patch.object(runner.Path, "is_dir", return_value=False), \
         patch.object(runner.subprocess, "run") as probe:
        probe.return_value.returncode = 0
        probe.return_value.stdout = " 123 100\n 456 500\n 123 300\n"
        assert runner._process_group_rss_bytes(123) == 400 * 1024


def test_memory_limit_cannot_silently_disable_itself(tmp_path):
    import sys
    from scripts.batch import hardened_runner as runner
    metrics = {}
    with patch.object(runner, "_process_group_rss_bytes", return_value=None), \
         (tmp_path / "log").open("wb") as log:
        with pytest.raises(runner.ConversionTimeout, match="cannot enforce"):
            runner._run_in_process_group([sys.executable, "-c", "import time; time.sleep(60)"],
                stdout=log, timeout_seconds=1, grace_seconds=0.01,
                memory_limit_bytes=1000000, metrics=metrics)
    assert metrics["limit_reason"] == "memory_observation_unavailable"

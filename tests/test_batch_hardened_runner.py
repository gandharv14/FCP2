from __future__ import annotations

import json
import shutil
import sys
import threading
import time
from pathlib import Path

import openpyxl
import pytest

import restricted_recovery_worker
from scripts.batch import hardened_runner
from scripts.batch.hardened_runner import (
    ConversionTimeout,
    LedgerError,
    ReadyRecord,
    convert_and_publish,
    discover_ready_records,
    publish_diagnostic_snapshot,
    reconcile_expected_ledger,
    run_ready_batch,
    run_ready_attempt,
)


def _xlsx(path: Path, value: str = "ready") -> None:
    workbook = openpyxl.Workbook()
    workbook.active["A1"] = value
    workbook.save(path)
    workbook.close()


def _record(workbook_id: str = "0042") -> ReadyRecord:
    return ReadyRecord.create(
        workbook_id=workbook_id,
        source_file=f"{workbook_id}.xlsx",
        source_sha256="1" * 64,
        xlsx_file=f"{workbook_id}.xlsx",
        xlsx_sha256="2" * 64,
        xlsx_size=1,
        conversion_engine="test",
    )


def test_invalid_native_xlsx_is_never_published(tmp_path: Path) -> None:
    source = tmp_path / "invalid.xlsx"
    source.write_text("not a workbook", encoding="utf-8")
    published = tmp_path / "published"
    ready = tmp_path / "ready"

    with pytest.raises(ValueError, match="OOXML ZIP"):
        convert_and_publish(
            source,
            workbook_id="0042",
            private_root=tmp_path / "private",
            published_dir=published,
            ready_dir=ready,
        )

    assert not (published / "0042.xlsx").exists()
    assert not (ready / "0042.ready.json").exists()


def test_timed_out_converter_kills_descendants_and_cannot_publish(
    tmp_path: Path,
) -> None:
    source = tmp_path / "legacy.xls"
    source.write_bytes(b"legacy")
    marker = tmp_path / "late-child-output"
    converter = tmp_path / "fake-libreoffice"
    converter.write_text(
        "#!/usr/bin/env python3\n"
        "import os, pathlib, time\n"
        "if os.fork() == 0:\n"
        "    time.sleep(0.3)\n"
        f"    pathlib.Path({str(marker)!r}).write_text('late')\n"
        "    os._exit(0)\n"
        "time.sleep(5)\n",
        encoding="utf-8",
    )
    converter.chmod(0o755)
    published = tmp_path / "published"
    ready = tmp_path / "ready"

    with pytest.raises(ConversionTimeout):
        convert_and_publish(
            source,
            workbook_id="0042",
            private_root=tmp_path / "private",
            published_dir=published,
            ready_dir=ready,
            libreoffice_binary=str(converter),
            timeout_seconds=0.05,
            terminate_grace_seconds=0.05,
        )

    time.sleep(0.4)
    assert not marker.exists()
    assert not (published / "0042.xlsx").exists()
    assert not (ready / "0042.ready.json").exists()


def test_queue_cannot_observe_xlsx_before_atomic_ready_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.xlsx"
    _xlsx(source)
    published = tmp_path / "published"
    ready = tmp_path / "ready"
    before_ready = threading.Event()
    release_ready = threading.Event()
    original_write = hardened_runner.atomic_write_json

    def delayed_ready_write(path: Path, payload: dict[str, object]) -> None:
        before_ready.set()
        assert release_ready.wait(timeout=2)
        original_write(path, payload)

    monkeypatch.setattr(hardened_runner, "atomic_write_json", delayed_ready_write)
    errors: list[BaseException] = []

    def produce() -> None:
        try:
            convert_and_publish(
                source,
                workbook_id="0042",
                private_root=tmp_path / "private",
                published_dir=published,
                ready_dir=ready,
            )
        except BaseException as error:  # pragma: no cover - surfaced below
            errors.append(error)

    producer = threading.Thread(target=produce)
    producer.start()
    assert before_ready.wait(timeout=2)
    assert (published / "0042.xlsx").is_file()
    assert discover_ready_records(ready, published) == []

    release_ready.set()
    producer.join(timeout=2)
    assert not producer.is_alive()
    assert errors == []
    assert [item.workbook_id for item in discover_ready_records(ready, published)] == [
        "0042"
    ]


def test_conversion_retry_is_idempotent_and_never_overwrites_ready_bytes(
    tmp_path,
):
    source = tmp_path / "source.xlsx"
    _xlsx(source, "first")
    arguments = {
        "workbook_id": "0042",
        "private_root": tmp_path / "private",
        "published_dir": tmp_path / "published",
        "ready_dir": tmp_path / "ready",
    }
    first = convert_and_publish(source, **arguments)
    published_bytes = (tmp_path / "published" / "0042.xlsx").read_bytes()

    assert convert_and_publish(source, **arguments) == first
    _xlsx(source, "changed")
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        convert_and_publish(source, **arguments)
    assert (tmp_path / "published" / "0042.xlsx").read_bytes() == published_bytes


def test_conversion_retry_recovers_xlsx_published_before_ready_record(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source.xlsx"
    _xlsx(source)
    arguments = {
        "workbook_id": "0042",
        "private_root": tmp_path / "private",
        "published_dir": tmp_path / "published",
        "ready_dir": tmp_path / "ready",
    }
    original_write = hardened_runner.atomic_write_json

    def crash_before_ready(path, payload):
        if path.name == "0042.ready.json":
            raise RuntimeError("injected crash")
        return original_write(path, payload)

    monkeypatch.setattr(hardened_runner, "atomic_write_json", crash_before_ready)
    with pytest.raises(RuntimeError, match="injected crash"):
        convert_and_publish(source, **arguments)
    assert (tmp_path / "published" / "0042.xlsx").is_file()
    assert not (tmp_path / "ready" / "0042.ready.json").exists()

    monkeypatch.setattr(hardened_runner, "atomic_write_json", original_write)
    recovered = convert_and_publish(source, **arguments)
    recovered.validate_workbook(tmp_path / "published")
    assert (tmp_path / "ready" / "0042.ready.json").is_file()


def test_diagnostics_same_path_and_resolved_symlink_are_noops(
    tmp_path: Path,
) -> None:
    diagnostics = tmp_path / "diagnostics"
    diagnostics.mkdir()
    evidence = diagnostics / "evidence.txt"
    evidence.write_text("unchanged", encoding="utf-8")

    assert publish_diagnostic_snapshot(diagnostics, diagnostics) == "same_path"
    alias = tmp_path / "diagnostics-alias"
    alias.symlink_to(diagnostics, target_is_directory=True)
    assert publish_diagnostic_snapshot(diagnostics, alias) == "same_path"
    assert evidence.read_text(encoding="utf-8") == "unchanged"


def test_failed_diagnostic_snapshot_preserves_prior_state(tmp_path: Path) -> None:
    source = tmp_path / "new"
    source.mkdir()
    (source / "evidence.txt").write_text("new", encoding="utf-8")
    destination = tmp_path / "published"
    destination.mkdir()
    prior = destination / "evidence.txt"
    prior.write_text("prior", encoding="utf-8")

    def failing_copy(source_path: Path, temporary: Path, **_: object) -> None:
        shutil.copytree(source_path, temporary)
        raise OSError("injected snapshot failure")

    with pytest.raises(OSError, match="injected"):
        publish_diagnostic_snapshot(
            source,
            destination,
            copy_tree=failing_copy,
        )

    assert prior.read_text(encoding="utf-8") == "prior"
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "new",
        "published",
    ]


def test_retries_use_new_attempts_and_preserve_old_logs(tmp_path: Path) -> None:
    record = _record()
    first = run_ready_attempt(
        record,
        [
            sys.executable,
            "-c",
            "import json; print(json.dumps({'result': 'first'}))",
        ],
        state_dir=tmp_path / "state",
    )
    second = run_ready_attempt(
        record,
        [
            sys.executable,
            "-c",
            "import json; print(json.dumps({'result': 'second'}))",
        ],
        state_dir=tmp_path / "state",
    )

    assert first["attempt"] == "attempt-0001"
    assert second["attempt"] == "attempt-0002"
    attempts = tmp_path / "state" / "attempts" / "0042"
    assert (attempts / "attempt-0001" / "final.txt").read_text() == "first\n"
    assert (attempts / "attempt-0002" / "final.txt").read_text() == "second\n"


def test_process_group_rss_limit_records_timed_out_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        hardened_runner,
        "_process_group_rss_bytes",
        lambda _process_group: 2 * 1024 * 1024,
    )

    result = run_ready_attempt(
        _record(),
        [sys.executable, "-c", "import time; time.sleep(5)"],
        state_dir=tmp_path / "state",
        timeout_seconds=2,
        memory_limit_bytes=1024 * 1024,
        terminate_grace_seconds=0.05,
    )

    assert result["status"] == "timed_out"
    assert "RSS bytes" in result["error"]
    assert result["execution_limits"] == {
        "peak_rss_bytes": 2 * 1024 * 1024,
        "limit_reason": "process_group_rss",
    }


def test_timeout_reconciliation_reads_publication_without_rewriting_pointer(
    tmp_path: Path,
) -> None:
    ready = tmp_path / "ready"
    published = tmp_path / "published"
    private = tmp_path / "private"
    pointers = tmp_path / "pointers"
    pointers.mkdir()
    for workbook_id in ("0001", "0002", "0003", "0004"):
        source = tmp_path / f"{workbook_id}.xlsx"
        _xlsx(source)
        convert_and_publish(
            source,
            workbook_id=workbook_id,
            private_root=private,
            published_dir=published,
            ready_dir=ready,
        )

    def command_builder(record, _workbook):
        pointer = pointers / f"{record.workbook_id}.json"
        generation = pointers / f"{record.workbook_id}.generation"
        staging = pointers / f"{record.workbook_id}.staging"
        script = "import pathlib,time;"
        if record.workbook_id == "0001":
            script += (
                f"pathlib.Path({str(generation)!r}).write_text('immutable');"
                f"pathlib.Path({str(pointer)!r}).write_text('current-v1');"
            )
        elif record.workbook_id == "0002":
            script += (
                f"pathlib.Path({str(generation)!r}).write_text('immutable');"
            )
        elif record.workbook_id == "0003":
            script += f"pathlib.Path({str(staging)!r}).write_text('partial');"
        script += "time.sleep(5)"
        return [sys.executable, "-c", script]

    def reconcile(record, result):
        pointer = pointers / f"{record.workbook_id}.json"
        if result["status"] == "timed_out" and pointer.is_file():
            return "completed"
        return str(result["status"])

    ledger = run_ready_batch(
        ["0001", "0002", "0003", "0004"],
        ready_dir=ready,
        published_dir=published,
        state_dir=tmp_path / "state",
        command_builder=command_builder,
        workers=1,
        timeout_seconds=0.15,
        terminal_reconciler=reconcile,
    )

    assert ledger["counts"] == {"completed": 1, "timed_out": 3}
    assert (pointers / "0001.json").read_text() == "current-v1"
    assert (pointers / "0002.generation").read_text() == "immutable"
    assert not (pointers / "0002.json").exists()
    assert (pointers / "0003.staging").read_text() == "partial"
    assert not (pointers / "0003.generation").exists()
    assert not (pointers / "0004.staging").exists()
    attempts = tmp_path / "state" / "attempts"
    assert (attempts / "0001" / "attempt-0001" / "terminal-result.json").is_file()
    assert (attempts / "0002" / "attempt-0001" / "terminal-result.json").is_file()
    assert (attempts / "0003" / "attempt-0001" / "terminal-result.json").is_file()
    assert (attempts / "0004" / "attempt-0001" / "terminal-result.json").is_file()

    retry = run_ready_attempt(
        discover_ready_records(ready, published)[1],
        [sys.executable, "-c", "print('retry-completed')"],
        state_dir=tmp_path / "state",
    )
    assert retry["status"] == "completed"
    assert retry["attempt"] == "attempt-0002"


def test_expected_ledger_has_exactly_one_terminal_row_per_id() -> None:
    ledger = reconcile_expected_ledger(
        ["0003", "0001", "0002"],
        [
            {"workbook_id": "0001", "status": "completed"},
            {"workbook_id": "0002", "status": "failed"},
        ],
    )

    assert ledger["expected_count"] == 3
    assert ledger["terminal_count"] == 3
    assert ledger["counts"] == {
        "completed": 1,
        "failed": 1,
        "missing_ready": 1,
    }
    assert [row["workbook_id"] for row in ledger["records"]] == [
        "0001",
        "0002",
        "0003",
    ]
    assert sum(ledger["counts"].values()) == 3

    with pytest.raises(LedgerError, match="multiple terminal statuses"):
        reconcile_expected_ledger(
            ["0001"],
            [
                {"workbook_id": "0001", "status": "failed"},
                {"workbook_id": "0001", "status": "completed"},
            ],
        )


def test_batch_accounts_for_invalid_ready_record_without_aborting(tmp_path):
    ready = tmp_path / "ready"
    published = tmp_path / "published"
    ready.mkdir()
    published.mkdir()
    (ready / "0001.ready.json").write_text("{bad", encoding="utf-8")
    complete = tmp_path / "producer.complete"
    complete.touch()

    ledger = run_ready_batch(
        ["0001", "0002"],
        ready_dir=ready,
        published_dir=published,
        state_dir=tmp_path / "state",
        command_builder=lambda _record, _workbook: ["false"],
        producer_complete_marker=complete,
    )

    assert ledger["counts"] == {
        "invalid_ready": 1,
        "missing_ready": 1,
    }


def test_batch_accounts_for_wrong_ready_field_types(tmp_path):
    ready = tmp_path / "ready"
    published = tmp_path / "published"
    ready.mkdir()
    published.mkdir()
    payload = {
        **hardened_runner.asdict(_record("0001")),
        "xlsx_size": "1",
    }
    payload["record_sha256"] = hardened_runner._record_binding(payload)
    (ready / "0001.ready.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    ledger = run_ready_batch(
        ["0001"],
        ready_dir=ready,
        published_dir=published,
        state_dir=tmp_path / "state",
        command_builder=lambda _record, _workbook: ["false"],
    )

    assert ledger["counts"] == {"invalid_ready": 1}


def test_batch_combines_conversion_failures_and_classified_results(tmp_path):
    ready = tmp_path / "ready"
    published = tmp_path / "published"
    source = tmp_path / "0002.xlsx"
    _xlsx(source)
    convert_and_publish(
        source,
        workbook_id="0002",
        private_root=tmp_path / "private",
        published_dir=published,
        ready_dir=ready,
    )

    ledger = run_ready_batch(
        ["0001", "0002"],
        ready_dir=ready,
        published_dir=published,
        state_dir=tmp_path / "state",
        command_builder=lambda _record, _workbook: ["true"],
        initial_terminal_records=[{
            "workbook_id": "0001",
            "status": "conversion_failed",
        }],
        result_classifier=lambda _record, _result: "blocked",
    )

    assert ledger["counts"] == {
        "blocked": 1,
        "conversion_failed": 1,
    }


def test_recovery_worker_rejects_malformed_release_pointer(
    tmp_path,
    monkeypatch,
):
    repo = tmp_path / "repo"
    output = tmp_path / "output"
    pointer = repo / "release_out" / "0001" / "current-release.json"
    pointer.parent.mkdir(parents=True)
    pointer.write_text("{malformed", encoding="utf-8")
    task = output / "0001-outputs"
    (task / "environment").mkdir(parents=True)
    (task / "tests").mkdir()
    for relative in (
        "instruction.md",
        "task.toml",
        "environment/Dockerfile",
        "tests/run_grader.py",
        "tests/answer_key.json",
    ):
        (task / relative).write_text("placeholder", encoding="utf-8")
    monkeypatch.setattr(restricted_recovery_worker, "REPO", repo)
    monkeypatch.setattr(restricted_recovery_worker, "OUTPUT", output)

    assert restricted_recovery_worker.promoted("0001") is False

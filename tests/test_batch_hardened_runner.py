from __future__ import annotations

import json
import os
import shutil
import subprocess
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


def _assistant_event(text: str) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
        }
    )


def test_final_text_keeps_assistant_messages_on_separate_lines() -> None:
    # The CLI result glues messages together with nothing between them, so
    # the declaration line at the end of one message ran into the next
    # ("RECOVERY_USED: YESNo follow-up") and the anchored regex read MISSING.
    stream = "\n".join(
        [
            json.dumps({"type": "system", "subtype": "init"}),
            _assistant_event("Published the release.\nRECOVERY_USED: YES"),
            json.dumps({"type": "user", "message": {"content": "tool result"}}),
            _assistant_event("No follow-up is needed."),
            json.dumps(
                {
                    "type": "result",
                    "result": "Published the release.\nRECOVERY_USED: YESNo follow-up is needed.",
                }
            ),
        ]
    )

    final = hardened_runner._extract_final_text(stream)

    assert final == "Published the release.\nRECOVERY_USED: YES\nNo follow-up is needed."
    assert hardened_runner.extract_assistant_segments(stream) == [
        "Published the release.\nRECOVERY_USED: YES",
        "No follow-up is needed.",
    ]
    import re

    assert re.findall(r"(?m)^RECOVERY_USED: (YES|NO)$", final) == ["YES"]


def test_final_text_falls_back_to_result_without_assistant_events() -> None:
    stream = json.dumps({"type": "result", "result": "only result"})

    assert hardened_runner._extract_final_text(stream) == "only result"
    assert hardened_runner.extract_assistant_segments(stream) == []


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


def test_observe_only_records_the_peak_without_killing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        hardened_runner,
        "_process_group_rss_bytes",
        lambda _process_group: 8 * 1024 * 1024,
    )

    result = run_ready_attempt(
        _record(),
        [sys.executable, "-c", "print('done')"],
        state_dir=tmp_path / "state",
        timeout_seconds=30,
        memory_limit_bytes=1024,
        memory_observe_only=True,
        memory_poll_seconds=0.01,
    )

    assert result["status"] == "completed"
    assert result["execution_limits"]["peak_rss_bytes"] == 8 * 1024 * 1024
    assert "limit_reason" not in result["execution_limits"]


def test_an_injected_probe_overrides_the_process_group_sum(
    tmp_path: Path,
) -> None:
    result = run_ready_attempt(
        _record(),
        [sys.executable, "-c", "print('done')"],
        state_dir=tmp_path / "state",
        timeout_seconds=30,
        memory_limit_bytes=1024 * 1024,
        memory_observe_only=True,
        memory_poll_seconds=0.01,
        memory_probe=lambda _pid: 4096,
    )

    assert result["execution_limits"]["peak_rss_bytes"] == 4096


def test_memory_poll_seconds_must_be_positive(tmp_path: Path) -> None:
    result = run_ready_attempt(
        _record(),
        [sys.executable, "-c", "print('done')"],
        state_dir=tmp_path / "state",
        memory_poll_seconds=0,
    )

    assert result["status"] == "orchestrator_error"
    assert "memory_poll_seconds" in result["error"]


def test_an_unlimited_run_never_samples_memory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def _probe(process_group: int) -> int | None:
        calls.append(process_group)
        return 1024

    monkeypatch.setattr(hardened_runner, "_process_group_rss_bytes", _probe)

    result = run_ready_attempt(
        _record(),
        [sys.executable, "-c", "print('done')"],
        state_dir=tmp_path / "state",
        timeout_seconds=30,
    )

    assert result["status"] == "completed"
    assert calls == []
    assert "execution_limits" not in result


def test_cgroup_peak_prefers_memory_peak(tmp_path: Path) -> None:
    uid = os.getuid()
    directory = (
        tmp_path
        / "user.slice"
        / f"user-{uid}.slice"
        / f"user@{uid}.service"
        / "app.slice"
        / "harbor-lane-a-0001-0.scope"
    )
    directory.mkdir(parents=True)
    (directory / "memory.peak").write_text("4096\n", encoding="utf-8")
    (directory / "memory.current").write_text("2048\n", encoding="utf-8")

    peak = hardened_runner.cgroup_peak_bytes("harbor-lane-a-0001-0", tmp_path)

    assert peak == 4096


def test_cgroup_peak_falls_back_to_memory_current(tmp_path: Path) -> None:
    uid = os.getuid()
    directory = (
        tmp_path
        / "user.slice"
        / f"user-{uid}.slice"
        / f"user@{uid}.service"
        / "harbor-old.scope"
    )
    directory.mkdir(parents=True)
    (directory / "memory.current").write_text("777\n", encoding="utf-8")

    assert hardened_runner.cgroup_peak_bytes("harbor-old", tmp_path) == 777


def test_cgroup_peak_is_none_without_a_live_scope(tmp_path: Path) -> None:
    assert hardened_runner.cgroup_peak_bytes("harbor-missing", tmp_path) is None


def test_cgroup_anon_and_cpu_are_read_from_stat_files(tmp_path: Path) -> None:
    uid = os.getuid()
    directory = (
        tmp_path
        / "user.slice"
        / f"user-{uid}.slice"
        / f"user@{uid}.service"
        / "app.slice"
        / "harbor-lane-a-0003-0.scope"
    )
    directory.mkdir(parents=True)
    (directory / "memory.stat").write_text(
        "anon 1234\nfile 999999\nkernel 5\n", encoding="utf-8"
    )
    (directory / "cpu.stat").write_text(
        "usage_usec 777000\nuser_usec 700000\nsystem_usec 77000\n",
        encoding="utf-8",
    )

    assert hardened_runner.cgroup_anon_bytes("harbor-lane-a-0003-0", tmp_path) == 1234
    assert hardened_runner.cgroup_cpu_usec("harbor-lane-a-0003-0", tmp_path) == 777000
    assert hardened_runner.cgroup_anon_bytes("harbor-missing", tmp_path) is None
    assert hardened_runner.cgroup_cpu_usec("harbor-missing", tmp_path) is None


def test_cgroup_events_are_parsed(tmp_path: Path) -> None:
    uid = os.getuid()
    directory = (
        tmp_path
        / "user.slice"
        / f"user-{uid}.slice"
        / f"user@{uid}.service"
        / "app.slice"
        / "harbor-lane-a-0002-1.scope"
    )
    directory.mkdir(parents=True)
    (directory / "memory.events").write_text(
        "low 0\nhigh 12\nmax 3\noom 1\noom_kill 1\n", encoding="utf-8"
    )

    events = hardened_runner.cgroup_events("harbor-lane-a-0002-1", tmp_path)

    assert events["high"] == 12
    assert events["oom_kill"] == 1


def test_memory_scope_is_unsupported_without_delegation(tmp_path: Path) -> None:
    assert hardened_runner.memory_scope_supported(tmp_path) is False


def test_scope_command_carries_a_ceiling_and_no_throttle_band() -> None:
    command = hardened_runner._scope_command(
        "harbor-lane-a-0001-0",
        ["agent", "--run"],
        memory_max_bytes=1000,
    )

    assert command[:5] == [
        "systemd-run",
        "--user",
        "--scope",
        "--quiet",
        "--unit=harbor-lane-a-0001-0",
    ]
    assert "--property=MemoryMax=1000" in command
    # Swapless host, anonymous footprint: a throttle band only delays the
    # same OOM kill, so MemoryHigh is pinned to MemoryMax.
    assert hardened_runner.MEMORY_HIGH_FRACTION == 1.0
    assert "--property=MemoryHigh=1000" in command
    assert "--property=MemorySwapMax=0" in command
    assert command[-2:] == ["agent", "--run"]


def test_scope_command_explicit_memory_high_still_wins() -> None:
    command = hardened_runner._scope_command(
        "harbor-lane-a-0001-0",
        ["agent"],
        memory_max_bytes=1000,
        memory_high_bytes=800,
    )

    assert "--property=MemoryMax=1000" in command
    assert "--property=MemoryHigh=800" in command


def test_scope_command_without_a_limit_omits_memory_properties() -> None:
    command = hardened_runner._scope_command(
        "harbor-x", ["agent"], memory_max_bytes=None
    )

    assert not [item for item in command if item.startswith("--property=Memory" "Max")]
    assert command[-1] == "agent"


def test_scope_unit_names_are_sanitized() -> None:
    unit = hardened_runner.scope_unit_name("state/lane a", "00/01", 2)

    assert unit.startswith("harbor-state-lane-a-00-01-")
    assert unit.endswith("-2")
    assert " " not in unit and "/" not in unit


def test_scope_unit_names_never_truncate_the_attempt_index() -> None:
    # The attempt index is the last part, so naive truncation would cut it
    # first and two attempts of one row would share a unit name -- which
    # either fails against a lingering scope or reads a stale peak.
    lane = "l" * 64
    workbook = "w" * 128
    names = {
        hardened_runner.scope_unit_name(lane, workbook, index) for index in range(6)
    }

    assert len(names) == 6
    for index, name in enumerate(
        hardened_runner.scope_unit_name(lane, workbook, i) for i in range(6)
    ):
        assert name.endswith(f"-{index}")
        assert len(name) < 200


def _passthrough_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the command unwrapped, since systemd-run is absent off-fleet."""

    monkeypatch.setattr(
        hardened_runner,
        "_scope_command",
        lambda _scope, command, **_kwargs: list(command),
    )


def test_a_scoped_attempt_reads_its_peak_from_the_cgroup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _passthrough_scope(monkeypatch)
    monkeypatch.setattr(hardened_runner, "cgroup_peak_bytes", lambda _s: 5000)
    monkeypatch.setattr(hardened_runner, "cgroup_anon_bytes", lambda _s: None)
    monkeypatch.setattr(hardened_runner, "cgroup_events", lambda _s: {"high": 4})
    monkeypatch.setattr(
        hardened_runner,
        "_process_group_rss_bytes",
        lambda _process_group: 99,
    )

    result = run_ready_attempt(
        _record(),
        [sys.executable, "-c", "print('done')"],
        state_dir=tmp_path / "state",
        timeout_seconds=30,
        memory_limit_bytes=8192,
        memory_scope="harbor-lane-a-0001-0",
        memory_poll_seconds=0.01,
    )

    limits = result["execution_limits"]
    assert result["status"] == "completed"
    # The cgroup reading wins over the process-group sum, and the kernel owns
    # enforcement so this loop must not kill anything itself.
    assert limits["peak_rss_bytes"] == 5000
    assert limits["cgroup_peak_bytes"] == 5000
    assert limits["memory_events"] == {"high": 4}
    assert limits["memory_scope"] == "harbor-lane-a-0001-0"
    assert limits["memory_max_bytes"] == 8192
    assert "limit_reason" not in limits


def test_a_scoped_attempt_calibrates_on_anon_not_page_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # memory.peak charges page cache to the scope. A row streaming a large
    # AST through the cache would otherwise report a peak near MemoryMax and
    # teach the calibrator that every row needs the whole ceiling.
    _passthrough_scope(monkeypatch)
    monkeypatch.setattr(hardened_runner, "cgroup_peak_bytes", lambda _s: 30_000)
    monkeypatch.setattr(hardened_runner, "cgroup_anon_bytes", lambda _s: 6_000)
    monkeypatch.setattr(hardened_runner, "cgroup_events", lambda _s: {})

    result = run_ready_attempt(
        _record(),
        [sys.executable, "-c", "print('done')"],
        state_dir=tmp_path / "state",
        timeout_seconds=30,
        memory_limit_bytes=32_000,
        memory_scope="harbor-lane-a-0001-0",
        memory_poll_seconds=0.01,
    )

    limits = result["execution_limits"]
    assert limits["peak_rss_bytes"] == 6_000
    assert limits["cgroup_peak_bytes"] == 30_000


def test_a_silent_scope_with_no_cpu_is_killed_as_a_stall(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _passthrough_scope(monkeypatch)
    monkeypatch.setattr(hardened_runner, "cgroup_peak_bytes", lambda _s: 1)
    monkeypatch.setattr(hardened_runner, "cgroup_anon_bytes", lambda _s: 1)
    monkeypatch.setattr(hardened_runner, "cgroup_events", lambda _s: {})
    # Flat CPU: the scope never burns anything.
    monkeypatch.setattr(hardened_runner, "cgroup_cpu_usec", lambda _s: 100)

    started = time.monotonic()
    result = run_ready_attempt(
        _record(),
        [sys.executable, "-c", "import time; time.sleep(30)"],
        state_dir=tmp_path / "state",
        timeout_seconds=30,
        memory_limit_bytes=8192,
        memory_scope="harbor-lane-a-0001-0",
        memory_poll_seconds=0.01,
        stall_seconds=0.3,
    )

    assert result["status"] == "timed_out"
    assert result["execution_limits"]["limit_reason"] == "stall"
    assert time.monotonic() - started < 10


def test_a_silent_scope_that_burns_cpu_is_not_a_stall(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An agent blocked on a long xl_*.py child writes nothing to its stream
    # while the child computes inside the same scope. Rising CPU proves life.
    _passthrough_scope(monkeypatch)
    monkeypatch.setattr(hardened_runner, "cgroup_peak_bytes", lambda _s: 1)
    monkeypatch.setattr(hardened_runner, "cgroup_anon_bytes", lambda _s: 1)
    monkeypatch.setattr(hardened_runner, "cgroup_events", lambda _s: {})
    ticks = {"usec": 0}

    def busy() -> int:
        ticks["usec"] += hardened_runner.STALL_CPU_BUDGET_USEC + 1
        return ticks["usec"]

    monkeypatch.setattr(hardened_runner, "cgroup_cpu_usec", lambda _s: busy())

    result = run_ready_attempt(
        _record(),
        [sys.executable, "-c", "import time; time.sleep(0.8)"],
        state_dir=tmp_path / "state",
        timeout_seconds=30,
        memory_limit_bytes=8192,
        memory_scope="harbor-lane-a-0001-0",
        memory_poll_seconds=0.01,
        stall_seconds=0.2,
    )

    assert result["status"] == "completed"
    assert "limit_reason" not in result["execution_limits"]


def test_a_stall_detector_without_a_cpu_probe_never_fires(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _passthrough_scope(monkeypatch)
    monkeypatch.setattr(hardened_runner, "cgroup_peak_bytes", lambda _s: 1)
    monkeypatch.setattr(hardened_runner, "cgroup_anon_bytes", lambda _s: 1)
    monkeypatch.setattr(hardened_runner, "cgroup_events", lambda _s: {})
    monkeypatch.setattr(hardened_runner, "cgroup_cpu_usec", lambda _s: None)

    result = run_ready_attempt(
        _record(),
        [sys.executable, "-c", "import time; time.sleep(0.6)"],
        state_dir=tmp_path / "state",
        timeout_seconds=30,
        memory_limit_bytes=8192,
        memory_scope="harbor-lane-a-0001-0",
        memory_poll_seconds=0.01,
        stall_seconds=0.2,
    )

    assert result["status"] == "completed"


def test_an_oom_loop_is_killed_within_its_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # oom climbing with oom_kill pinned at zero is the immunity-loop
    # signature. The fix in _oom_killable_command prevents it; this catches a
    # regression in seconds instead of a six-hour wall clock.
    _passthrough_scope(monkeypatch)
    monkeypatch.setattr(hardened_runner, "cgroup_peak_bytes", lambda _s: 1)
    monkeypatch.setattr(hardened_runner, "cgroup_anon_bytes", lambda _s: 1)
    monkeypatch.setattr(hardened_runner, "cgroup_cpu_usec", lambda _s: None)
    counter = {"oom": 0}

    def looping() -> dict[str, int]:
        counter["oom"] += 1000
        return {"oom": counter["oom"], "oom_kill": 0}

    monkeypatch.setattr(hardened_runner, "cgroup_events", lambda _s: looping())
    monkeypatch.setattr(hardened_runner, "OOM_LOOP_SECONDS", 0.2)

    result = run_ready_attempt(
        _record(),
        [sys.executable, "-c", "import time; time.sleep(30)"],
        state_dir=tmp_path / "state",
        timeout_seconds=30,
        memory_limit_bytes=8192,
        memory_scope="harbor-lane-a-0001-0",
        memory_poll_seconds=0.01,
    )

    assert result["status"] == "timed_out"
    assert result["execution_limits"]["limit_reason"] == "oom_loop"


def test_a_cgroup_kill_is_attributed_from_the_exit_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _passthrough_scope(monkeypatch)
    monkeypatch.setattr(hardened_runner, "cgroup_peak_bytes", lambda _s: None)
    monkeypatch.setattr(hardened_runner, "cgroup_events", lambda _s: {})

    result = run_ready_attempt(
        _record(),
        [sys.executable, "-c", "raise SystemExit(137)"],
        state_dir=tmp_path / "state",
        timeout_seconds=30,
        memory_limit_bytes=8192,
        memory_scope="harbor-lane-a-0001-0",
        memory_poll_seconds=0.01,
    )

    assert result["status"] == "failed"
    assert result["execution_limits"]["limit_reason"] == "cgroup_oom"


def test_a_scope_request_is_recorded_even_when_systemd_run_is_missing(
    tmp_path: Path,
) -> None:
    result = run_ready_attempt(
        _record(),
        [sys.executable, "-c", "print('done')"],
        state_dir=tmp_path / "state",
        timeout_seconds=30,
        memory_limit_bytes=8192,
        memory_scope="harbor-lane-a-0001-0",
        memory_poll_seconds=0.01,
    )

    assert result["execution_limits"]["memory_scope"] == "harbor-lane-a-0001-0"


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


# -- OOM immunity reset -------------------------------------------------------
#
# oom_score_adj is inherited across fork and exec. The fleet gives sshd -1000,
# every lane starts from an SSH session, and a MemoryMax scope whose tasks are
# all immune makes the kernel print an OOM report and retry forever. These
# tests pin the reset that stops it.


def test_oom_killable_command_wraps_and_preserves_the_payload() -> None:
    wrapped = hardened_runner._oom_killable_command(["agent", "--run", "a b"])

    assert wrapped[:2] == ["/bin/sh", "-c"]
    assert "/proc/self/oom_score_adj" in wrapped[2]
    assert wrapped[2].endswith('exec "$@"')
    # $0 is a label, $1 is the adjust value, the payload follows verbatim.
    assert wrapped[3] == "harbor-oom-adjust"
    assert wrapped[4] == str(hardened_runner.CHILD_OOM_SCORE_ADJ)
    assert wrapped[-3:] == ["agent", "--run", "a b"]


def test_oom_killable_command_rejects_an_empty_payload() -> None:
    # The bare prologue exits 0 having run nothing, which would report a
    # phantom success.
    with pytest.raises(ValueError, match="empty"):
        hardened_runner._oom_killable_command([])


def test_oom_wrapper_runs_the_payload_with_nothing_on_stderr() -> None:
    # On a host without /proc (macOS) the guard skips the write and the
    # payload still runs; on Linux the write succeeds. Either way, silence.
    completed = subprocess.run(
        hardened_runner._oom_killable_command(
            [sys.executable, "-c", "import sys; print('ok'); sys.exit(3)"]
        ),
        capture_output=True,
        text=True,
    )

    assert completed.stdout.strip() == "ok"
    assert completed.stderr == ""
    assert completed.returncode == 3


@pytest.mark.skipif(
    not Path("/proc/self/oom_score_adj").exists(),
    reason="oom_score_adj is Linux-only",
)
def test_oom_wrapper_pulls_an_inherited_score_back_to_zero() -> None:
    # The delta test: a parent raises itself to 500 (always permitted), and
    # the wrapped child must report 0. A plain "child is not immune" check
    # would pass vacuously on any host already at 0.
    probe = [
        "/bin/sh",
        "-c",
        'printf 500 > /proc/self/oom_score_adj 2>/dev/null || exit 97; exec "$@"',
        "harbor-oom-selftest",
        *hardened_runner._oom_killable_command(
            ["/bin/cat", "/proc/self/oom_score_adj"]
        ),
    ]
    completed = subprocess.run(probe, capture_output=True, text=True)
    if completed.returncode == 97:
        pytest.skip("host forbids raising oom_score_adj")

    assert completed.returncode == 0
    assert completed.stdout.strip() == "0"


def test_scope_command_adds_oom_policy_only_when_supported() -> None:
    with_policy = hardened_runner._scope_command(
        "harbor-x", ["agent"], memory_max_bytes=1000, oom_policy=True
    )
    without_policy = hardened_runner._scope_command(
        "harbor-x", ["agent"], memory_max_bytes=1000, oom_policy=False
    )

    assert "--property=OOMPolicy=kill" in with_policy
    # After the positional prefix the existing test pins, and before the
    # payload.
    assert with_policy.index("--property=OOMPolicy=kill") >= 5
    assert with_policy[-1] == "agent"
    assert "--property=OOMPolicy=kill" not in without_policy


def test_scope_command_probes_the_host_when_oom_policy_is_unspecified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hardened_runner, "oom_policy_supported", lambda: True)
    assert "--property=OOMPolicy=kill" in hardened_runner._scope_command(
        "harbor-x", ["agent"], memory_max_bytes=None
    )
    monkeypatch.setattr(hardened_runner, "oom_policy_supported", lambda: False)
    assert "--property=OOMPolicy=kill" not in hardened_runner._scope_command(
        "harbor-x", ["agent"], memory_max_bytes=None
    )


def test_oom_policy_probe_requires_systemd_253(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hardened_runner, "_OOM_POLICY_SUPPORTED", None)
    monkeypatch.setattr(hardened_runner, "systemd_version", lambda: 252)
    assert hardened_runner.oom_policy_supported() is False
    monkeypatch.setattr(hardened_runner, "_OOM_POLICY_SUPPORTED", None)
    monkeypatch.setattr(hardened_runner, "systemd_version", lambda: 255)
    assert hardened_runner.oom_policy_supported() is True
    monkeypatch.setattr(hardened_runner, "_OOM_POLICY_SUPPORTED", None)
    monkeypatch.setattr(hardened_runner, "systemd_version", lambda: None)
    assert hardened_runner.oom_policy_supported() is False


def test_child_oom_score_adj_is_recorded_only_for_scoped_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _passthrough_scope(monkeypatch)
    monkeypatch.setattr(hardened_runner, "cgroup_peak_bytes", lambda _s: 1)
    monkeypatch.setattr(hardened_runner, "cgroup_events", lambda _s: {})
    # The first sample can beat the shell's write and see the inherited
    # -1000; the running maximum must converge on the reset value.
    readings = iter([-1000, 0, 0])
    monkeypatch.setattr(
        hardened_runner,
        "read_oom_score_adj",
        lambda _pid: next(readings, 0),
    )

    scoped = run_ready_attempt(
        _record(),
        [sys.executable, "-c", "import time; time.sleep(0.2)"],
        state_dir=tmp_path / "state",
        timeout_seconds=30,
        memory_limit_bytes=8192,
        memory_scope="harbor-lane-a-0001-0",
        memory_poll_seconds=0.01,
    )
    unscoped = run_ready_attempt(
        _record(),
        [sys.executable, "-c", "print('done')"],
        state_dir=tmp_path / "state",
        timeout_seconds=30,
        memory_limit_bytes=8192,
        memory_observe_only=True,
        memory_poll_seconds=0.01,
    )

    assert scoped["execution_limits"]["child_oom_score_adj"] == 0
    assert "child_oom_score_adj" not in unscoped.get("execution_limits", {})

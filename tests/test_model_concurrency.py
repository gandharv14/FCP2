"""Exercise real process locks, including release after a killed caller."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

import xl_transport


WORKER = """
import json,os,sys,time
from pathlib import Path
from xl_transport import model_slot
with model_slot():
    fd=os.open(sys.argv[1],os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
    os.write(fd,(json.dumps([time.monotonic_ns(),1,os.getpid()])+"\\n").encode())
    Path(sys.argv[2]).write_text('entered')
    time.sleep(float(sys.argv[3]))
    os.write(fd,(json.dumps([time.monotonic_ns(),-1,os.getpid()])+"\\n").encode())
    os.close(fd)
"""


def launch(tmp_path, index, duration, capacity="2"):
    env = dict(os.environ, HARBOR_MODEL_SLOT_ROOT=str(tmp_path / "slots"),
               HARBOR_MODEL_SLOTS=capacity)
    return subprocess.Popen([sys.executable, "-c", WORKER, str(tmp_path / "events"),
                             str(tmp_path / f"entered-{index}"), str(duration)],
                            cwd=Path(xl_transport.__file__).parent, env=env)


def wait_file(path):
    deadline = time.monotonic() + 10
    while not path.exists():
        if time.monotonic() >= deadline:
            raise AssertionError("worker did not acquire slot")
        time.sleep(0.02)


def test_actual_processes_never_exceed_two_models(tmp_path):
    processes = [launch(tmp_path, i, 0.3) for i in range(6)]
    try:
        for process in processes:
            assert process.wait(timeout=15) == 0
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait()
    active = maximum = 0
    events = sorted(json.loads(line) for line in (tmp_path / "events").read_text().splitlines())
    assert len(events) == 12
    for _, change, _ in events:
        active += change
        maximum = max(maximum, active)
        assert 0 <= active <= 2
    assert active == 0 and maximum == 2


def test_process_death_releases_slot(tmp_path):
    holder = launch(tmp_path, 0, 120, "1")
    waiter = None
    try:
        wait_file(tmp_path / "entered-0")
        waiter = launch(tmp_path, 1, 0, "1")
        time.sleep(0.2)
        assert not (tmp_path / "entered-1").exists()
        holder.kill()
        holder.wait(timeout=5)
        assert waiter.wait(timeout=10) == 0
        assert (tmp_path / "entered-1").exists()
    finally:
        for process in (holder, waiter):
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait()


def test_exception_releases_slot_and_mixed_capacity_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("HARBOR_MODEL_SLOT_ROOT", str(tmp_path / "slots"))
    monkeypatch.setenv("HARBOR_MODEL_SLOTS", "1")
    with pytest.raises(RuntimeError), xl_transport.model_slot():
        raise RuntimeError("transport failed")
    with xl_transport.model_slot():
        pass
    monkeypatch.setenv("HARBOR_MODEL_SLOTS", "2")
    with pytest.raises(ValueError, match="campaign capacity"), xl_transport.model_slot():
        pass


@pytest.mark.parametrize("capacity", ["0", "3", "-1", "true", "2.0"])
def test_invalid_capacity_rejected(tmp_path, monkeypatch, capacity):
    monkeypatch.setenv("HARBOR_MODEL_SLOT_ROOT", str(tmp_path / "slots"))
    monkeypatch.setenv("HARBOR_MODEL_SLOTS", capacity)
    with pytest.raises(ValueError, match="one or two"), xl_transport.model_slot():
        pass


def test_relative_or_symlink_root_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("HARBOR_MODEL_SLOT_ROOT", "relative")
    with pytest.raises(ValueError, match="absolute path"), xl_transport.model_slot():
        pass
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    monkeypatch.setenv("HARBOR_MODEL_SLOT_ROOT", str(link / "slots"))
    with pytest.raises(ValueError, match="without symlinks"), xl_transport.model_slot():
        pass


def test_read_response_holds_slot_for_transport(tmp_path, monkeypatch):
    monkeypatch.setenv("HARBOR_MODEL_SLOT_ROOT", str(tmp_path / "slots"))
    monkeypatch.setenv("HARBOR_MODEL_SLOTS", "1")
    def transport(request, *, timeout):
        import fcntl
        with (tmp_path / "slots" / "slot-0.lock").open("r+") as probe:
            with pytest.raises(BlockingIOError):
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert request == "request" and timeout == 12
        return b"response"
    monkeypatch.setattr(xl_transport, "_read_response", transport)
    assert xl_transport.read_response("request", timeout=12) == b"response"

"""Code snapshots must not acquire unrelated locks for the same corpus ledgers."""
from pathlib import Path
import subprocess
import sys
import time

import pytest

from scripts import corpus_build


def test_same_campaign_excludes_another_snapshot_and_path_alias(tmp_path, monkeypatch):
    run = tmp_path / "campaign"
    run.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(run, target_is_directory=True)
    entered = tmp_path / "entered"
    monkeypatch.setattr(corpus_build, "ROOT", tmp_path / "snapshot-one")
    command = """
from pathlib import Path
import sys
from scripts import corpus_build
corpus_build.ROOT=Path(sys.argv[3])
with corpus_build.cohort_lock(sys.argv[1]):
    Path(sys.argv[2]).write_text('entered')
"""
    process = None
    try:
        with corpus_build.cohort_lock(run):
            process = subprocess.Popen([sys.executable, "-c", command,
                str(alias), str(entered), str(tmp_path / "snapshot-two")],
                cwd=Path(corpus_build.__file__).resolve().parents[1])
            time.sleep(0.3)
            assert process.poll() is None
            assert not entered.exists()
        assert process.wait(timeout=10) == 0
        assert entered.read_text() == "entered"
        assert (run / ".workbook-build.lock").is_file()
        assert not (tmp_path / "snapshot-one").exists()
        assert not (tmp_path / "snapshot-two").exists()
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait()


def test_campaign_lock_refuses_symlink_file(tmp_path):
    target = tmp_path / "foreign"
    target.write_text("retained")
    (tmp_path / ".workbook-build.lock").symlink_to(target)
    with pytest.raises(OSError), corpus_build.cohort_lock(tmp_path):
        pass
    assert target.read_text() == "retained"

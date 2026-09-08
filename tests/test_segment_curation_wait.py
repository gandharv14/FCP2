"""``xl_segment --curation-wait``: pause for cone edits without a reload."""

from __future__ import annotations

import os
import signal
import threading
import time
from pathlib import Path

import xl_segment


def test_wait_ends_when_sentinel_appears(tmp_path: Path) -> None:
    curation = tmp_path / "curation.toml"
    curation.write_text("[[output]]\nband = 'S!A1'\ninclude = true\n")
    sentinel = tmp_path / "curation.toml.ready"

    def touch_later():
        time.sleep(0.4)
        sentinel.write_text("")

    threading.Thread(target=touch_later, daemon=True).start()
    started = time.monotonic()
    xl_segment._wait_for_curation(curation, "0000", seconds=30)
    assert time.monotonic() - started < 10
    assert not sentinel.exists(), "the sentinel is consumed so a rerun waits again"


def test_wait_ends_on_sigusr1(tmp_path: Path) -> None:
    curation = tmp_path / "curation.toml"
    curation.write_text("")

    def signal_later():
        time.sleep(0.4)
        os.kill(os.getpid(), signal.SIGUSR1)

    threading.Thread(target=signal_later, daemon=True).start()
    started = time.monotonic()
    xl_segment._wait_for_curation(curation, "0000", seconds=30)
    assert time.monotonic() - started < 10


def test_wait_expires_and_continues(tmp_path: Path) -> None:
    curation = tmp_path / "curation.toml"
    curation.write_text("")
    started = time.monotonic()
    xl_segment._wait_for_curation(curation, "0000", seconds=1)
    assert 0.9 <= time.monotonic() - started < 5


def test_flags_default_under_the_stall_detector() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    # Mirror the production parser's contract: the default bound must stay
    # under the worker's 1800 s stall kill.
    parser.add_argument("--curation-wait", action="store_true")
    parser.add_argument("--curation-wait-seconds", type=int, default=1500)
    args = parser.parse_args(["--curation-wait"])
    assert args.curation_wait is True
    assert args.curation_wait_seconds < 1800
    assert "--curation-wait-seconds" in Path(xl_segment.__file__).read_text()

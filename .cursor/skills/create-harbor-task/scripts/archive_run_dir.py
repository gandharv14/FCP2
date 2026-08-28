#!/usr/bin/env python3
"""Archive stale run/stage directories so reruns are first-class.

Every attempt gets a clean path without deleting anything: an existing
directory (or file) is atomically renamed to
``<name>.archived-<UTC timestamp>[-N]`` beside itself and reported as retained
diagnostics. Non-existent paths are a no-op, so the command is safe to run
unconditionally before rebuilding.

    python3 archive_run_dir.py runs/0525-instruction-naturalization \
        tasks_outputs_mcp/0525-outputs runs/0525-variable-sources/mcp

Exit code 0 on success (whether or not anything was archived); nonzero when a
rename fails.
"""

from __future__ import annotations

import datetime as _dt
import os
import sys
from pathlib import Path


def archive(path: Path) -> Path | None:
    """Rename ``path`` aside if it exists; return the archive path or None."""
    if not path.exists() and not path.is_symlink():
        return None
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d%H%M%S")
    for attempt in range(1000):
        suffix = stamp if attempt == 0 else "%s-%d" % (stamp, attempt)
        candidate = path.with_name("%s.archived-%s" % (path.name, suffix))
        if candidate.exists() or candidate.is_symlink():
            continue
        os.rename(path, candidate)
        return candidate
    raise RuntimeError("could not find a free archive name for %s" % path)


def main(argv=None):
    paths = [Path(p) for p in (argv if argv is not None else sys.argv[1:])]
    if not paths:
        print("usage: archive_run_dir.py PATH [PATH ...]", file=sys.stderr)
        return 2
    for path in paths:
        archived = archive(path)
        if archived is None:
            print("absent    %s" % path)
        else:
            print("archived  %s -> %s" % (path, archived))
    return 0


if __name__ == "__main__":
    sys.exit(main())

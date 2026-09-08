"""In-process memo for file SHA-256 digests.

The publication and worker code hash the same artifacts several times per
attempt: a source generation is hashed when it is published, again when the
segmentation binds to it, again when the release validates its bindings, and
the worker hashes ``source_out``/``seg_out``/``release_out`` twice around the
fairness verifier. On a 12 MB AST that is cheap; on the 47 GB outliers it is
most of the wall time.

This memo keys a digest on the file's identity and change stamps
``(st_dev, st_ino, st_size, st_mtime_ns, st_ctime_ns)``. A first sight is
always a real read. Any rewrite in place moves ``ctime_ns`` (and normally
``mtime_ns``); a replace-by-rename changes the inode; a truncate changes the
size. The memo lives only inside one interpreter, so a fresh process (for
example the fail-closed inspect interpreter) still hashes everything.

Callers that must prove bytes right now, such as the release publisher's
"revalidate immediately before CAS" step, call :func:`clear` first so the next
sight re-reads the bytes.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

CHUNK_BYTES = 1024 * 1024
# Bound the memo so a very long-lived worker cannot grow it without limit. A
# fleet attempt touches a few thousand distinct files; this is far above that.
MAX_ENTRIES = 250_000

_Key = tuple[int, int, int, int, int]
_MEMO: dict[_Key, tuple[str, int]] = {}
_STATS = {"hits": 0, "misses": 0, "unstable": 0}


def _key(st: os.stat_result) -> _Key:
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _digest(handle) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = handle.read(CHUNK_BYTES)
        if not chunk:
            break
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def sha256_and_size(path) -> tuple[str, int]:
    """Return ``(hexdigest, size_bytes)`` for ``path``, memoised by identity.

    Raises the same ``OSError`` family a plain ``open()`` would (missing file,
    permission, directory). The stat is taken on the open descriptor before
    and after reading; if the stamps moved during the read the result is
    returned but not memoised, because it may describe a torn mixture.
    """

    path = Path(path)
    with path.open("rb") as handle:
        before = _key(os.fstat(handle.fileno()))
        hit = _MEMO.get(before)
        if hit is not None:
            _STATS["hits"] += 1
            return hit
        result = _digest(handle)
        after = _key(os.fstat(handle.fileno()))
    if after != before:
        _STATS["unstable"] += 1
        return result
    _STATS["misses"] += 1
    if len(_MEMO) >= MAX_ENTRIES:
        _MEMO.clear()
    _MEMO[before] = result
    return result


def sha256_file(path) -> str:
    """Return the hex SHA-256 of ``path``; see :func:`sha256_and_size`."""

    return sha256_and_size(path)[0]


def clear() -> None:
    """Forget every memoised digest so the next sight re-reads the bytes."""

    _MEMO.clear()


def stats() -> dict[str, int]:
    """Counters for tests and diagnostics: hits, misses, unstable, entries."""

    return {**_STATS, "entries": len(_MEMO)}


def reset_stats() -> None:
    for name in _STATS:
        _STATS[name] = 0

"""xl_hash_cache: first sight hashes, repeats hit, any change misses."""

from __future__ import annotations

import hashlib
import os
import time

import pytest

import xl_hash_cache
import xl_release_publication as release
import xl_source_publication as source_pub
from xl_seg import diagnostics, publication as seg_pub


@pytest.fixture(autouse=True)
def _fresh_memo():
    xl_hash_cache.clear()
    xl_hash_cache.reset_stats()
    yield
    xl_hash_cache.clear()
    xl_hash_cache.reset_stats()


def _plain(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bump_stamps(path):
    """Move mtime and ctime forward so a same-size rewrite is visible."""
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))


def test_first_sight_reads_and_second_sight_hits(tmp_path):
    target = tmp_path / "blob.bin"
    target.write_bytes(b"x" * 4096)
    assert xl_hash_cache.sha256_file(target) == _plain(target)
    assert xl_hash_cache.sha256_file(target) == _plain(target)
    stats = xl_hash_cache.stats()
    assert stats["misses"] == 1
    assert stats["hits"] == 1
    assert stats["entries"] == 1


def test_size_change_misses(tmp_path):
    target = tmp_path / "blob.bin"
    target.write_bytes(b"a" * 10)
    first = xl_hash_cache.sha256_file(target)
    target.write_bytes(b"a" * 11)
    second = xl_hash_cache.sha256_file(target)
    assert second == _plain(target)
    assert second != first
    assert xl_hash_cache.stats()["misses"] == 2


def test_same_size_rewrite_with_new_stamps_misses(tmp_path):
    target = tmp_path / "blob.bin"
    target.write_bytes(b"same-size-1")
    first = xl_hash_cache.sha256_file(target)
    target.write_bytes(b"same-size-2")
    _bump_stamps(target)
    second = xl_hash_cache.sha256_file(target)
    assert second == _plain(target)
    assert second != first


def test_replace_by_rename_changes_inode_and_misses(tmp_path):
    target = tmp_path / "blob.bin"
    target.write_bytes(b"first-bytes")
    xl_hash_cache.sha256_file(target)
    staged = tmp_path / "blob.tmp"
    staged.write_bytes(b"other-bytes")
    os.replace(staged, target)
    assert xl_hash_cache.sha256_file(target) == _plain(target)
    assert xl_hash_cache.stats()["misses"] == 2


def test_clear_forces_a_real_reread(tmp_path):
    target = tmp_path / "blob.bin"
    target.write_bytes(b"payload")
    xl_hash_cache.sha256_file(target)
    xl_hash_cache.clear()
    assert xl_hash_cache.stats()["entries"] == 0
    xl_hash_cache.sha256_file(target)
    assert xl_hash_cache.stats()["misses"] == 2
    assert xl_hash_cache.stats()["hits"] == 0


def test_sha256_and_size_reports_true_size_on_hit(tmp_path):
    target = tmp_path / "blob.bin"
    target.write_bytes(b"0123456789")
    assert xl_hash_cache.sha256_and_size(target) == (_plain(target), 10)
    assert xl_hash_cache.sha256_and_size(target) == (_plain(target), 10)


def test_missing_file_raises_like_open(tmp_path):
    with pytest.raises(FileNotFoundError):
        xl_hash_cache.sha256_file(tmp_path / "absent")


def test_publication_hashers_share_the_memo(tmp_path):
    target = tmp_path / "nodes.csv"
    target.write_text("a,b\n1,2\n", encoding="utf-8")
    expected = _plain(target)
    assert source_pub._sha256(target) == expected
    assert seg_pub._sha256(target) == expected
    assert release._sha256(target) == expected
    record = diagnostics.fingerprint_file(target)
    assert record["sha256"] == expected
    assert record["size_bytes"] == target.stat().st_size
    stats = xl_hash_cache.stats()
    assert stats["misses"] == 1
    assert stats["hits"] == 3


def test_fingerprint_file_still_reports_unavailable(tmp_path):
    record = diagnostics.fingerprint_file(tmp_path / "gone.csv")
    assert record == {"path": str(tmp_path / "gone.csv"), "available": False}


def test_memo_bound_resets_instead_of_growing(tmp_path, monkeypatch):
    monkeypatch.setattr(xl_hash_cache, "MAX_ENTRIES", 2)
    for index in range(3):
        path = tmp_path / f"f{index}"
        path.write_bytes(bytes([index]))
        xl_hash_cache.sha256_file(path)
    assert xl_hash_cache.stats()["entries"] == 1


def test_unstable_read_is_not_memoised(tmp_path, monkeypatch):
    target = tmp_path / "blob.bin"
    target.write_bytes(b"stable")
    real_fstat = os.fstat
    calls = {"n": 0}

    def shifting_fstat(fd):
        st = real_fstat(fd)
        calls["n"] += 1
        if calls["n"] == 2:
            # Second stat of the first read: pretend the file moved under us.
            return os.stat_result(
                (
                    st.st_mode, st.st_ino, st.st_dev, st.st_nlink, st.st_uid,
                    st.st_gid, st.st_size, st.st_atime, st.st_mtime + 5,
                    st.st_ctime,
                )
            )
        return st

    monkeypatch.setattr(xl_hash_cache.os, "fstat", shifting_fstat)
    assert xl_hash_cache.sha256_file(target) == _plain(target)
    assert xl_hash_cache.stats()["unstable"] == 1
    assert xl_hash_cache.stats()["entries"] == 0


def test_stats_reflect_time_ordering_of_stamps(tmp_path):
    # Sanity: the stamps we key on really move forward on rewrite.
    target = tmp_path / "blob.bin"
    target.write_bytes(b"one")
    before = target.stat()
    time.sleep(0.01)
    target.write_bytes(b"two")
    after = target.stat()
    assert (after.st_mtime_ns, after.st_ctime_ns) != (
        before.st_mtime_ns, before.st_ctime_ns
    )

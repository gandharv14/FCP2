"""Byte hashing can be reused; changed evidence and qualification cannot."""
import hashlib
import os
from pathlib import Path
import pytest
from scripts.batch import harbor_tracker_lifecycle as L

@pytest.fixture(autouse=True)
def clear():
    L._DIGEST_CACHE.clear()
    yield
    L._DIGEST_CACHE.clear()

def row(p):return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def test_same_file_is_hashed_once_but_each_expected_digest_is_checked(tmp_path,monkeypatch):
    p=tmp_path/'evidence';p.write_bytes(b'authentic');r=row(p);calls=[];original=L.hashlib.file_digest
    def digest(*a,**kw):calls.append(1);return original(*a,**kw)
    monkeypatch.setattr(L.hashlib,'file_digest',digest)
    assert L.checked_record(r)==r and L.checked_record(r)==r
    with pytest.raises(L.Conflict,match='changed evidence'):L.checked_record({**r,'sha256':'0'*64})
    assert len(calls)==1

@pytest.mark.parametrize('change',['same_size_restored_mtime','replace_inode','symlink'])
def test_changed_evidence_cannot_reuse_cached_hash(tmp_path,change):
    p=tmp_path/'evidence';p.write_bytes(b'original');r=row(p);L.checked_record(r);old=p.stat()
    if change=='same_size_restored_mtime':
        p.write_bytes(b'tampered');os.utime(p,ns=(old.st_atime_ns,old.st_mtime_ns))
    elif change=='replace_inode':
        other=tmp_path/'new';other.write_bytes(b'tampered');os.utime(other,ns=(old.st_atime_ns,old.st_mtime_ns));os.replace(other,p)
    else:
        target=tmp_path/'target';target.write_bytes(b'original');p.unlink();p.symlink_to(target)
    with pytest.raises((L.Conflict,ValueError,OSError)):L.checked_record(r)

def test_mutation_during_hash_is_rejected_and_not_cached(tmp_path,monkeypatch):
    p=tmp_path/'evidence';p.write_bytes(b'original');r=row(p);original=L.hashlib.file_digest
    def changed(stream,*a,**kw):
        h=original(stream,*a,**kw);p.write_bytes(b'tampered');return h
    monkeypatch.setattr(L.hashlib,'file_digest',changed)
    with pytest.raises(L.Conflict,match='while hashing'):L.checked_record(r)
    assert not L._DIGEST_CACHE

def test_cache_is_bounded_and_fork_identity_forces_rehash(tmp_path,monkeypatch):
    monkeypatch.setattr(L,'_DIGEST_CACHE_LIMIT',2)
    for i in range(3):
        p=tmp_path/str(i);p.write_bytes(bytes([i]));L.checked_record(row(p))
    assert len(L._DIGEST_CACHE)==2
    monkeypatch.setattr(L,'_DIGEST_CACHE_PID',-1)
    L.checked_record(row(p));assert len(L._DIGEST_CACHE)==1

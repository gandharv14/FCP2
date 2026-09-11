"""Independent copies preserve bytes, never alias evidence or overwrite files."""
import os
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest
from scripts import harbor_verification_package as P


@pytest.fixture
def files(tmp_path):
    root = tmp_path.resolve()
    source = root/'source'; source.write_bytes(b'exact graph bytes\n' * 128)
    return source, root/'destination', P.digest(source)


@pytest.mark.parametrize('clone', [False, True])
def test_clone_or_fallback_preserves_independent_write_semantics(files, monkeypatch, clone):
    source, destination, expected = files
    calls = []
    def attempt(src, dst):
        calls.append((src, dst))
        if clone: shutil.copyfile(src, dst)
        return clone
    monkeypatch.setattr(P, '_clone_file', attempt)
    result = P._verified_copy(source, destination, expected)
    assert result['method'] == ('apfs-clone' if clone else 'copy')
    assert len(calls) == 1
    assert P.digest(source) == P.digest(destination) == expected
    assert source.stat().st_ino != destination.stat().st_ino
    assert destination.stat().st_nlink == 1
    destination.write_bytes(b'changed destination')
    assert P.digest(source) == expected
    source.write_bytes(b'changed source')
    assert destination.read_bytes() == b'changed destination'
    assert not list(source.parent.glob('.evidence-copy-*'))


def test_failed_clone_partial_output_is_removed_before_fallback(files, monkeypatch):
    source, destination, expected = files
    def partial(src, dst):
        dst.write_bytes(b'partial')
        return False
    monkeypatch.setattr(P, '_clone_file', partial)
    assert P._verified_copy(source, destination, expected)['method'] == 'copy'
    assert P.digest(source) == P.digest(destination) == expected
    assert not list(source.parent.glob('.evidence-copy-*'))


@pytest.mark.parametrize('existing', ['file', 'directory', 'symlink'])
def test_preexisting_destination_is_never_replaced(files, monkeypatch, existing):
    source, destination, expected = files
    if existing == 'file': destination.write_bytes(b'user file')
    elif existing == 'directory': destination.mkdir()
    else: destination.symlink_to(source)
    def forbidden(*args): raise AssertionError('must reject before clone')
    monkeypatch.setattr(P, '_clone_file', forbidden)
    with pytest.raises(P.PackageError): P._verified_copy(source, destination, expected)
    assert P.digest(source) == expected
    if existing == 'file': assert destination.read_bytes() == b'user file'
    elif existing == 'directory': assert destination.is_dir()
    else: assert destination.is_symlink()


@pytest.mark.parametrize('mutation', ['wrong_bytes', 'hardlink', 'symlink', 'source_changed'])
def test_invalid_clone_output_or_changed_source_is_rejected(files, monkeypatch, mutation):
    source, destination, expected = files
    def bad(src, dst):
        if mutation == 'wrong_bytes': dst.write_bytes(b'wrong')
        elif mutation == 'hardlink': os.link(src, dst)
        elif mutation == 'symlink': dst.symlink_to(src)
        else:
            shutil.copyfile(src, dst)
            src.write_bytes(b'changed source during clone')
        return True
    monkeypatch.setattr(P, '_clone_file', bad)
    with pytest.raises((P.PackageError, OSError)):
        P._verified_copy(source, destination, expected)
    assert not destination.exists()
    if mutation != 'source_changed': assert P.digest(source) == expected
    assert not list(source.parent.glob('.evidence-copy-*'))


def test_clone_command_is_fixed_argv_and_bounded(files, monkeypatch):
    source, destination, _ = files
    calls = []
    monkeypatch.setattr(P.sys, 'platform', 'darwin')
    def run(argv, **kwargs):
        calls.append((argv, kwargs)); return SimpleNamespace(returncode=1)
    monkeypatch.setattr(P.subprocess, 'run', run)
    assert P._clone_file(source, destination) is False
    assert calls == [(['/bin/cp', '-c', str(source), str(destination)],
                      {'capture_output':True,'timeout':300,'check':False})]


def test_non_macos_uses_portable_fallback_without_subprocess(files, monkeypatch):
    source, destination, expected = files
    monkeypatch.setattr(P.sys, 'platform', 'linux')
    def forbidden(*args, **kwargs): raise AssertionError('non-macOS must not invoke native cp')
    monkeypatch.setattr(P.subprocess, 'run', forbidden)
    assert P._verified_copy(source, destination, expected)['method'] == 'copy'


def test_destination_race_preserves_new_user_file(files, monkeypatch):
    source, destination, expected = files
    monkeypatch.setattr(P, '_clone_file', lambda *args: False)
    link = P.os.link
    def raced(src, dst, **kwargs):
        Path(dst).write_bytes(b'new user file during publication')
        return link(src, dst, **kwargs)
    monkeypatch.setattr(P.os, 'link', raced)
    with pytest.raises(FileExistsError): P._verified_copy(source, destination, expected)
    assert destination.read_bytes() == b'new user file during publication'
    assert P.digest(source) == expected


def test_wrong_expected_hash_rejects_before_copy(files, monkeypatch):
    source, destination, expected = files
    def forbidden(*args): raise AssertionError('incorrect expected bytes cannot reach clone')
    monkeypatch.setattr(P, '_clone_file', forbidden)
    with pytest.raises(P.PackageError, match='source differs'):
        P._verified_copy(source, destination, '0'*64)
    assert not destination.exists()

"""Standalone frozen code must stay reproducible without a Git checkout."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from scripts import corpus


def declared(root, entries=None):
    entries = entries or {'scripts/worker.py': 'print("worker")\n', 'policy.json': '{"version":1}'}
    for name, content in entries.items():
        path=root/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_text(content)
    records={name:corpus.digest(root/name) for name in entries}
    (root/'.pipeline-code.json').write_text(json.dumps(records))
    return records


def no_git(*args, **kwargs):
    pytest.fail('frozen code consulted Git')


def test_second_snapshot_preserves_declared_files_and_fingerprint(tmp_path,monkeypatch):
    root=tmp_path/'first'; root.mkdir(); records=declared(root)
    (root/'unlisted.py').write_text('not in the frozen build')
    monkeypatch.setattr(corpus,'ROOT',root)
    monkeypatch.setattr(corpus.subprocess,'check_output',no_git)
    second,fingerprint=corpus.snapshot_code(tmp_path/'second')
    assert fingerprint==corpus.object_hash(records)
    assert json.loads((second/'.pipeline-code.json').read_text())==records
    assert sorted(str(p.relative_to(second)) for p in second.rglob('*') if p.is_file())==sorted([*records,'.pipeline-code.json'])
    monkeypatch.setattr(corpus,'ROOT',second)
    third,again=corpus.snapshot_code(tmp_path/'third')
    assert again==fingerprint and corpus.code_hash()==fingerprint
    assert all((third/name).read_bytes()==(root/name).read_bytes() for name in records)


def test_ignored_directory_cannot_become_empty_snapshot(tmp_path,monkeypatch):
    root=tmp_path/'frozen';root.mkdir();records=declared(root)
    monkeypatch.setattr(corpus,'ROOT',root)
    monkeypatch.setattr(corpus.subprocess,'check_output',lambda *a,**kw:b'')
    second,fingerprint=corpus.snapshot_code(tmp_path/'second')
    assert fingerprint==corpus.object_hash(records)
    assert (second/'scripts/worker.py').is_file()


@pytest.mark.parametrize('records',[{},[],None,{'../escape.py':'a'*64},{'/absolute.py':'a'*64},{'a/../escape.py':'a'*64},{'./worker.py':'a'*64},{'a//worker.py':'a'*64},{'a\\worker.py':'a'*64},{'.pipeline-code.json':'a'*64},{'worker.py':'bad'},{'worker.py':7}])
def test_invalid_manifest_rejected_without_git_or_snapshot(tmp_path,monkeypatch,records):
    root=tmp_path/'source';root.mkdir();(root/'.pipeline-code.json').write_text(json.dumps(records))
    monkeypatch.setattr(corpus,'ROOT',root);monkeypatch.setattr(corpus.subprocess,'check_output',no_git)
    for operation in (corpus.code_files,corpus.code_hash,lambda:corpus.snapshot_code(tmp_path/'out')):
        with pytest.raises(ValueError):operation()
    assert not (tmp_path/'out').exists()


@pytest.mark.parametrize('damage',['missing','changed','file_symlink','parent_symlink','manifest_symlink','duplicate_record','malformed_json'])
def test_changed_snapshot_evidence_rejected(tmp_path,monkeypatch,damage):
    root=tmp_path/'source';root.mkdir();records=declared(root)
    source=root/'scripts/worker.py';manifest=root/'.pipeline-code.json'
    if damage=='missing':source.unlink()
    elif damage=='changed':source.write_text('changed')
    elif damage=='file_symlink':
        outside=tmp_path/'outside.py';shutil.copyfile(source,outside);source.unlink();source.symlink_to(outside)
    elif damage=='parent_symlink':
        outside=tmp_path/'outside';(root/'scripts').rename(outside);(root/'scripts').symlink_to(outside,target_is_directory=True)
    elif damage=='manifest_symlink':
        outside=tmp_path/'manifest';manifest.rename(outside);manifest.symlink_to(outside)
    elif damage=='duplicate_record':manifest.write_text('{"policy.json":"'+'a'*64+'","policy.json":"'+records['policy.json']+'"}')
    else:manifest.write_text('{incomplete')
    monkeypatch.setattr(corpus,'ROOT',root);monkeypatch.setattr(corpus.subprocess,'check_output',no_git)
    for operation in (corpus.code_files,corpus.code_hash,lambda:corpus.snapshot_code(tmp_path/'out')):
        with pytest.raises(ValueError):operation()
    assert not (tmp_path/'out').exists()


def test_existing_snapshot_is_validated_and_manifest_not_rewritten(tmp_path,monkeypatch):
    root=tmp_path/'source';root.mkdir();declared(root);monkeypatch.setattr(corpus,'ROOT',root)
    target,fingerprint=corpus.snapshot_code(tmp_path/'out');manifest=target/'.pipeline-code.json'
    before=manifest.read_bytes(),manifest.stat().st_ino,manifest.stat().st_mtime_ns
    assert corpus.snapshot_code(tmp_path/'out')==(target,fingerprint)
    assert (manifest.read_bytes(),manifest.stat().st_ino,manifest.stat().st_mtime_ns)==before
    manifest.write_text('{}')
    with pytest.raises(ValueError):corpus.snapshot_code(tmp_path/'out')
    assert manifest.read_text()=='{}'


@pytest.mark.parametrize('damage',['file','parent','root'])
def test_partial_destination_symlinks_cannot_escape(tmp_path,monkeypatch,damage):
    root=tmp_path/'source';root.mkdir();records=declared(root);monkeypatch.setattr(corpus,'ROOT',root)
    outside=tmp_path/'outside';outside.mkdir();sentinel=outside/'worker.py';sentinel.write_text('preserve')
    destination=tmp_path/'out';destination.mkdir();target=destination/corpus.object_hash(records)
    if damage=='root':target.symlink_to(outside,target_is_directory=True)
    else:
        target.mkdir()
        if damage=='parent':(target/'scripts').symlink_to(outside,target_is_directory=True)
        else:
            (target/'scripts').mkdir();(target/'scripts/worker.py').symlink_to(sentinel)
    with pytest.raises(ValueError,match='symlink'):corpus.snapshot_code(destination)
    assert sentinel.read_text()=='preserve'


def test_git_checkout_discovers_sources_but_rejects_empty_result(tmp_path,monkeypatch):
    root=tmp_path/'repo';root.mkdir();(root/'tracked.py').write_text('tracked');(root/'new.json').write_text('{}')
    (root/'release_out').mkdir();(root/'release_out/ignored.json').write_text('{}')
    monkeypatch.setattr(corpus,'ROOT',root)
    def listing(argv,**kwargs):
        assert kwargs['cwd']==root
        return b'new.json\0release_out/ignored.json\0' if '--others' in argv else b'tracked.py\0'
    monkeypatch.setattr(corpus.subprocess,'check_output',listing)
    assert set(corpus.code_files())=={'tracked.py','new.json'}
    monkeypatch.setattr(corpus.subprocess,'check_output',lambda *a,**kw:b'')
    with pytest.raises(ValueError,match='nonempty'):corpus.snapshot_code(tmp_path/'empty')
    assert not (tmp_path/'empty').exists()


def test_generated_evidence_is_not_copied_as_pipeline_code(tmp_path,monkeypatch):
    root=tmp_path/'repo';root.mkdir()
    for name in ('scripts/worker.py','verification_manifests/policy.json',
                 'reports/validation.json','diagnostics/attempt/response.json'):
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('{}')
    monkeypatch.setattr(corpus,'ROOT',root)
    monkeypatch.setattr(corpus.subprocess,'check_output',lambda *a,**k:
        b'scripts/worker.py\0verification_manifests/policy.json\0reports/validation.json\0diagnostics/attempt/response.json\0')
    before={str(p.relative_to(root)):p.read_bytes()for p in root.rglob('*')if p.is_file()}
    frozen,_=corpus.snapshot_code(tmp_path/'snapshots')
    assert set(corpus.code_files())=={'scripts/worker.py','verification_manifests/policy.json'}
    assert not(frozen/'reports').exists()and not(frozen/'diagnostics').exists()
    assert {str(p.relative_to(root)):p.read_bytes()for p in root.rglob('*')if p.is_file()}==before


def test_frozen_subprocess_can_snapshot_and_launch_without_git(tmp_path):
    import os
    root=tmp_path/'frozen';root.mkdir()
    repository=Path(corpus.__file__).resolve().parents[1]
    entries={name:(repository/name).read_text() for name in ('scripts/corpus.py','scripts/batch/hardened_runner.py')}
    entries['scripts/worker.py']='print("frozen worker started")\n'
    records=declared(root,entries)
    command='from scripts.corpus import snapshot_code,code_hash; import json; p,h=snapshot_code('+repr(str(tmp_path/'second'))+'); print(json.dumps([str(p),h,code_hash()]))'
    env={**os.environ,'PATH':'','PYTHONPATH':str(root)}
    result=subprocess.run([sys.executable,'-c',command],cwd=root,env=env,text=True,capture_output=True,check=True)
    second,fingerprint,initial=json.loads(result.stdout)
    assert initial==fingerprint==corpus.object_hash(records)
    worker=subprocess.run([sys.executable,'-m','scripts.worker'],cwd=second,env={**env,'PYTHONPATH':second},text=True,capture_output=True,check=True)
    assert worker.stdout=='frozen worker started\n'


def test_tampered_manifest_cannot_change_hash_named_snapshot_identity(tmp_path,monkeypatch):
    root=tmp_path/'source';root.mkdir();declared(root);monkeypatch.setattr(corpus,'ROOT',root)
    frozen,_=corpus.snapshot_code(tmp_path/'snapshots')
    manifest=frozen/'.pipeline-code.json';records=json.loads(manifest.read_text());records.pop('policy.json');manifest.write_text(json.dumps(records))
    monkeypatch.setattr(corpus,'ROOT',frozen);monkeypatch.setattr(corpus.subprocess,'check_output',no_git)
    for operation in (corpus.code_files,corpus.code_hash,lambda:corpus.snapshot_code(tmp_path/'out')):
        with pytest.raises(ValueError,match='identity changed'):operation()
    assert not (tmp_path/'out').exists()


def test_source_change_during_copy_never_publishes_completion_manifest(tmp_path,monkeypatch):
    root=tmp_path/'source';root.mkdir();records=declared(root);monkeypatch.setattr(corpus,'ROOT',root)
    original=shutil.copyfile
    def changed(source,target):
        result=original(source,target)
        source.write_text('changed while snapshotting')
        return result
    monkeypatch.setattr(shutil,'copyfile',changed)
    with pytest.raises(ValueError,match='snapshot changed'):corpus.snapshot_code(tmp_path/'out')
    assert not (tmp_path/'out'/corpus.object_hash(records)/'.pipeline-code.json').exists()

import hashlib,io,json,tarfile
from pathlib import Path
import pytest
from scripts.fleet import offline_custody as c


def archive(tmp_path,remote='/home/worker/evidence.json',*,corrupt=False,extra=False):
    raw=b'{"retained":true}';member=remote.lstrip('/')
    path=tmp_path/'evidence.tar.gz'
    with tarfile.open(path,'w:gz') as tar:
        info=tarfile.TarInfo(member);info.size=len(raw);info.mode=0o600
        tar.addfile(info,io.BytesIO(raw))
        if extra:
            info=tarfile.TarInfo('home/worker/late-link');info.type=tarfile.SYMTYPE;info.linkname='/etc/passwd';tar.addfile(info)
    value={'schema_version':c.SCHEMA,'path_policy':'preserve_absolute_paths',
           'archive_sha256':c.digest(path),'files':[{'path':remote,'member':member,'size':len(raw),'mode':0o600,
            'sha256':'0'*64 if corrupt else hashlib.sha256(raw).hexdigest()}]}
    manifest=c.retain(tmp_path/'manifest.json',value)
    return path,manifest,raw


def test_remote_home_is_provenance_and_bytes_are_preserved(tmp_path):
    path,manifest,raw=archive(tmp_path);root=tmp_path/'snapshot'
    receipt=c.checked(c.import_snapshot(path,manifest,tmp_path/'receipt.json',['/home/worker'],namespace_root=root))
    row=receipt['files'][0]
    assert row['original_path']=='/home/worker/evidence.json'
    assert Path(row['path'])==root/'home/worker/evidence.json'
    assert Path(row['path']).read_bytes()==raw
    assert receipt['absolute_paths_preserved'] is False
    assert receipt['execution_authorized'] is False and receipt['attempts_reset'] is False


def test_local_symlink_at_remote_name_is_never_followed(tmp_path):
    real=tmp_path/'real';real.mkdir();(real/'evidence.json').write_text('local bytes')
    link=tmp_path/'foreign';link.symlink_to(real,target_is_directory=True)
    remote=str(link/'evidence.json');path,manifest,raw=archive(tmp_path,remote)
    root=tmp_path/'snapshot'
    receipt=c.checked(c.import_snapshot(path,manifest,tmp_path/'receipt.json',[str(link)],namespace_root=root))
    assert Path(receipt['files'][0]['path']).read_bytes()==raw
    assert (real/'evidence.json').read_text()=='local bytes'


@pytest.mark.parametrize('case',['root','unauthorized','traversal','corrupt','extra','destination_link','existing_changed'])
def test_unsafe_or_changed_evidence_fails_before_install(tmp_path,case):
    remote='/home/worker/../escape' if case=='traversal' else '/home/worker/evidence.json'
    path,manifest,raw=archive(tmp_path,remote,corrupt=case=='corrupt',extra=case=='extra')
    root=Path('/') if case=='root' else tmp_path/'snapshot'
    allowed=['/other'] if case=='unauthorized' else ['/home/worker']
    if case=='destination_link':
        real=tmp_path/'real';real.mkdir();root.symlink_to(real,target_is_directory=True)
    if case=='existing_changed':
        target=root/'home/worker/evidence.json';target.parent.mkdir(parents=True);target.write_text('retained different')
    with pytest.raises(ValueError):c.import_snapshot(path,manifest,tmp_path/'receipt.json',allowed,namespace_root=root)
    assert not (tmp_path/'receipt.json').exists()
    if case not in {'root','existing_changed'}:assert not (root/'home/worker/evidence.json').exists()

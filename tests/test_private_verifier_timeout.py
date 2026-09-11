"""A timed-out Docker client must not leave its verification container running."""
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import harbor_verification_package as package


def test_timeout_removes_only_this_verification_container(tmp_path, monkeypatch):
    bundle=tmp_path/'bundle';bundle.mkdir()
    manifest={'verifier_root':'/workspace/code','release_id':'release'}
    monkeypatch.setattr(package,'inspect_package',lambda *args:manifest)
    monkeypatch.setattr(package,'_check_installed_verifier',lambda value:None)
    monkeypatch.setattr(package,'materialize_package',lambda bundle,digest,path:path.mkdir())
    monkeypatch.setattr(package.shutil,'which',lambda name:'/trusted/docker')
    calls=[];image='sha256:'+'a'*64
    def run(argv,**kwargs):
        calls.append((argv,kwargs))
        if argv[1:3]==['image','inspect']:
            return SimpleNamespace(returncode=0,stdout='[{"Id":"'+image+'","Os":"linux"}]')
        if argv[1]=='run':raise subprocess.TimeoutExpired(argv,kwargs['timeout'])
        assert argv[1:3]==['rm','--force']
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(package.subprocess,'run',run)
    with pytest.raises(subprocess.TimeoutExpired):package.verify_package(bundle,'b'*64,image)
    started=calls[1][0];name=started[started.index('--name')+1]
    assert name.startswith('harbor-private-proof-') and len(name.rsplit('-',1)[1])==32
    assert calls[2][0]==['/trusted/docker','rm','--force',name]
    assert calls[2][1]['timeout']==30

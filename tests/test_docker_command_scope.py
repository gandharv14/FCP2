import hashlib,json,os
from pathlib import Path
import pytest
from scripts.fleet import docker_command_scope as M


def bind(p):return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())


@pytest.fixture
def selected(tmp_path,monkeypatch):
    def put(n,data):
        p=tmp_path/n;p.write_bytes(data);return bind(p)
    entry=put('entry.py',b'original entry');source=put('source.xlsx',b'original source');auth=put('authorization.md',b'Explicit existing sudo Docker command; no persistent privilege changes.')
    sudo=put('native-sudo',b'sudo');docker=put('native-docker',b'docker');mapping={'/usr/bin/sudo':sudo,'/usr/bin/docker':docker}
    monkeypatch.setattr(M,'AUTHORIZATION_SHA256',auth['sha256'])
    original=M.check
    def check(row):
        if row['path']in mapping:
            expected=mapping[row['path']];assert row['sha256']==expected['sha256'];return original(expected)
        return original(row)
    monkeypatch.setattr(M,'check',check);observed=[]
    monkeypatch.setattr(M,'_binary',lambda r:(check(r),observed.append(r['path'])))
    directory=tmp_path/'command';directory.mkdir(mode=0o700);command=directory/'docker';command.write_bytes(M.wrapper('/usr/bin/sudo','/usr/bin/docker'));command.chmod(0o500)
    p=dict(schema_version=M.SCHEMA,helper=bind(Path(M.__file__).absolute()),entrypoint=entry,workbook='0496',source=source,command=bind(command),sudo=dict(sudo,path='/usr/bin/sudo'),docker=dict(docker,path='/usr/bin/docker'),authorization=auth,existing_sudo_only=True,persistent_privilege_changes=False)
    profile=tmp_path/'profile.json'
    def save():profile.write_text(json.dumps(p));return bind(profile)
    return p,save,dict(entrypoint=entry,workbook='0496',source=source),observed


def test_explicit_identity_and_restore(selected,monkeypatch):
    p,save,args,observed=selected;monkeypatch.setenv('PATH','/usr/bin:/bin');monkeypatch.delenv(M.MARKER,raising=False)
    with M.installed(save(),**args)as identity:
        assert identity=={k:p[k]for k in ('command','sudo','docker')}
        assert os.environ['PATH'].split(':')[0]==str(Path(p['command']['path']).parent)
        assert json.loads(os.environ[M.MARKER])['pid']==os.getpid()
    assert os.environ['PATH']=='/usr/bin:/bin'and M.MARKER not in os.environ
    assert observed==['/usr/bin/sudo','/usr/bin/docker']


def test_original_failure_restores_environment(selected,monkeypatch):
    p,save,args,_=selected;monkeypatch.setenv('PATH','/bin');monkeypatch.delenv(M.MARKER,raising=False)
    with pytest.raises(RuntimeError):
        with M.installed(save(),**args):raise RuntimeError('original container check failed')
    assert os.environ['PATH']=='/bin'and M.MARKER not in os.environ


def test_nested_scope_rejected(selected):
    _,save,args,_=selected;row=save()
    with M.installed(row,**args):
        with pytest.raises(ValueError,match='nested or foreign'):
            with M.installed(row,**args):pass


@pytest.mark.parametrize('field,value',[('workbook','0001'),('persistent_privilege_changes',True),('existing_sudo_only',False)])
def test_foreign_authority_rejected(selected,field,value):
    p,save,args,_=selected;p[field]=value
    with pytest.raises(ValueError,match='explicit source-bound'):
        with M.installed(save(),**args):pass


def test_altered_wrapper_rejected(selected):
    p,save,args,_=selected;command=Path(p['command']['path']);command.chmod(0o600);command.write_bytes(b'#!/bin/sh\nexec sudo sh\n');command.chmod(0o500);p['command']=bind(command)
    with pytest.raises(ValueError,match='declared existing-sudo'):
        with M.installed(save(),**args):pass


def test_changed_source_rejected_and_environment_restored(selected):
    p,save,args,_=selected;prior=os.environ.get('PATH')
    with pytest.raises(ValueError,match='input changed'):
        with M.installed(save(),**args):Path(p['source']['path']).write_bytes(b'changed')
    assert os.environ.get('PATH')==prior


def test_inherited_child_scope_same_identity_only(selected,monkeypatch):
    p,save,args,_=selected;row=save();identity=dict(profile=row,**args)
    monkeypatch.setenv(M.MARKER,json.dumps(dict(identity=identity,pid=os.getpid()+1)))
    previous=os.environ[M.MARKER]
    with M.installed(row,**args):assert json.loads(os.environ[M.MARKER])['pid']==os.getpid()
    assert os.environ[M.MARKER]==previous


def test_real_binary_refuses_user_writable(tmp_path):
    p=tmp_path/'executable';p.write_text('binary');p.chmod(0o777)
    with pytest.raises(ValueError,match='root-owned'):
        M._binary(bind(p))


def test_foreign_authorization_rejected(selected,tmp_path):
    p,save,args,_=selected;f=tmp_path/'foreign.md';f.write_text('different authority');p['authorization']=bind(f)
    with pytest.raises(ValueError,match='user deployment authorization'):
        with M.installed(save(),**args):pass

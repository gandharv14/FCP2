import copy,json
from pathlib import Path
import pytest
from scripts.fleet import coordinator_transport as T,publication_launch as L


def put(path,value):
    path.write_text(json.dumps(value));return T.binding(path)


@pytest.fixture
def alias(tmp_path,monkeypatch):
    code=tmp_path/'coordinator.py';code.write_text('original single authority')
    cr=put(tmp_path/'coordinator.json',dict(url=T.PRIMARY,pid=101,command=['python','-B',str(code),'serve','--database','/bound/ledger'],database='/bound/ledger',code_sha256=T.binding(code)['sha256']))
    identities={101:'original '+str(code),102:'Tue Sep 16 12:00:00 2026 python /bin/gcloud '+__import__('shlex').join(T.command('hhu-server-7')[1:])}
    monkeypatch.setattr(T,'identity',lambda pid:identities[pid])
    proc=dict(host='hhu-server-7',pid=102,pgid=102,command=T.command('hhu-server-7'),identity=identities[102],started_at=100)
    health=dict(status='AUTHENTICATED_SAME_AUTHORITY_ALIAS',host='hhu-server-7',url=T.ALIAS,coordinator=cr,at=100,received_at=101,seconds=1,age_seconds=1,workbooks=200,events=999,publication_limit=1,models_dispatched=0,mutations=0)
    p=dict(schema_version=T.SCHEMA,helper=T.binding(T.__file__),coordinator=cr,host='hhu-server-7',url=T.ALIAS,process=put(tmp_path/'process.json',proc),health=put(tmp_path/'health.json',health))
    return p,cr,proc,health,identities,tmp_path


def test_parallel_alias_exact_and_default_preserved(alias):
    p,cr,*_,root=alias
    assert T.validate(put(root/'plan.json',p),host='hhu-server-7',coordinator=cr)==T.ALIAS
    assert L.selected_coordinator_url({})==T.PRIMARY
    cmd=T.command('hhu-server-7');assert '-C'in cmd and cmd[-1]=='127.0.0.1:8766:127.0.0.1:8765'
    assert cmd.count('-R')==1 and '-L'not in cmd and '-D'not in cmd


@pytest.mark.parametrize('key,value',[('host','hhu-server-8'),('url','http://example.com:8766'),('url','http://127.0.0.1:8765'),('schema_version','other')])
def test_foreign_transport_rejected(alias,key,value):
    p,cr,*_,root=alias;p[key]=value
    with pytest.raises(ValueError):T.validate(put(root/'plan.json',p),host='hhu-server-7',coordinator=cr)


@pytest.mark.parametrize('change',['destination','compression','pid','group','command_identity','health_age','health_authority','changed_bytes','coordinator_code'])
def test_exact_live_authority_and_forwarder_required(alias,change):
    p,cr,proc,health,identities,root=alias
    if change=='destination':proc['command'][-1]='127.0.0.1:8766:evil:8765'
    if change=='compression':proc['command'].remove('-C')
    if change=='pid':proc['pid']=103
    if change=='group':proc['pgid']=999
    if change=='command_identity':identities[102]+=' --foreign'
    if change=='health_age':health['age_seconds']=31
    if change=='health_authority':health['coordinator']={'path':'/foreign','sha256':'a'*64}
    if change=='coordinator_code':(root/'coordinator.py').write_text('changed authority')
    p['process']=put(root/'process.json',proc);p['health']=put(root/'health.json',health)
    if change=='changed_bytes':(root/'process.json').write_text('{}')
    with pytest.raises((ValueError,KeyError)):T.validate(put(root/'plan.json',p),host='hhu-server-7',coordinator=cr)


def test_publication_connected_status_carries_selected_alias(monkeypatch):
    captured=[];monkeypatch.setattr(L,'selected_coordinator_url',lambda p:T.ALIAS)
    monkeypatch.setattr(L,'ssh',lambda p,code:captured.append(code)or{})
    L.connected_status({});assert "native_coordinator_status('http://127.0.0.1:8766')"in captured[0]


def test_native_status_freshness_not_relaxed(monkeypatch):
    import io,time,urllib.request
    monkeypatch.setattr(Path,'read_text',lambda p:'fixture-token')
    monkeypatch.setattr(time,'time',lambda:200)
    monkeypatch.setattr(urllib.request,'urlopen',lambda *a,**k:io.BytesIO(json.dumps(dict(schema_version='fleet-coordinator/v1',at=169)).encode()))
    with pytest.raises(ValueError,match='stale'):L.native_coordinator_status(T.ALIAS)
    with pytest.raises(ValueError,match='loopback'):L.native_coordinator_status('http://foreign:8766')

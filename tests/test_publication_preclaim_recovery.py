"""A tunnel failure before claim preserves custody and never replays its journal."""
import copy,json,time
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.fleet import publication_finalization_launch as F,publication_launch as L

def put(p,v):
    p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,sort_keys=True));return F.binding(p)

@pytest.fixture
def recovery(tmp_path):
    oldplan=dict(workbook='0303',source_sha256='source',code_root='/code/version',journal='/old/journal')
    newplan=dict(oldplan,journal='/new/journal')
    pub=dict(path='/publisher',sha256='p');op=dict(path='/reconciled',sha256='o')
    old=dict(expected_owner='old-claimed',expected_fence=8,owner='failed-preclaim',publisher=pub,reconciliation=op,host='hhu-server-5',plan=put(tmp_path/'old-plan.json',oldplan))
    prior=put(tmp_path/'old.json',old)
    intent=put(tmp_path/'root-intent.json',dict(profile=prior))
    ready=dict(workbook='0303',state='ready',owner=None,claim_id=None,fence=8,source_sha256='source',version='version',reason=json.dumps(intent,sort_keys=True),checkpoint=json.dumps({'publication':old['plan']}))
    remote=dict(action='remote',automatic_retry=False,owner=old['owner'],plan=old['plan'],driver=pub,version='version',operation_id='unstarted-op')
    v=dict(schema_version='fleet-publication-preclaim-recovery/v1',prior_profile=prior,root_intent=intent,ready=put(tmp_path/'ready.json',ready),response=put(tmp_path/'response.json',{}),remote_intent=put(tmp_path/'remote-intent.json',remote))
    p=dict(old,owner='fresh',preclaim_recovery=put(tmp_path/'recovery.json',v))
    return p,newplan,ready

@pytest.mark.parametrize('bad',[None,'claim','fence','operation','source','journal'])
def test_ready_recovery_requires_exact_never_claimed_custody(recovery,bad):
    p,plan,w=recovery;s=dict(at=time.time(),workbooks=[copy.deepcopy(w)],operations=[])
    if bad=='claim':s['workbooks'][0]['owner']='other'
    if bad=='fence':s['workbooks'][0]['fence']=9
    if bad=='operation':s['operations']=[dict(operation_id='unstarted-op')]
    if bad=='source':s['workbooks'][0]['source_sha256']='changed'
    if bad=='journal':plan['journal']='/old/journal'
    cap=SimpleNamespace(validate_occupancy=lambda s:None)
    if bad:
        with pytest.raises(ValueError):F.central(s,p,plan,cap)
    else:
        assert F.central(s,p,plan,cap)==w
        assert s['workbooks']==[w]

def test_remote_acquisition_file_forbids_preclaim_recovery(tmp_path,monkeypatch):
    original=Path.iterdir
    monkeypatch.setattr(Path,"iterdir",lambda p:iter(()) if str(p)=="/proc" else original(p))
    i=put(tmp_path/'intent.json',{})
    payload=dict(journal=str(tmp_path),intent=i,publisher='/never-publisher',plan='/never-plan')
    assert F.native_preclaim(payload)['status']=='STOPPED_BEFORE_CLAIM'
    put(tmp_path/'acquired.json',{})
    with pytest.raises(ValueError,match='progressed'):F.native_preclaim(payload)

def test_disconnected_remote_blocks_before_root_intent_or_reconcile(tmp_path,monkeypatch):
    p=dict(capacity_policy={},coordinator_receipt={'path':str(tmp_path/'coordinator')},reconciliation={},plan={},publisher={},host='hhu-server',python='/python',probe={'path':'/probe'},expected_owner='old',expected_fence=8)
    plan=dict(workbook='0303');rec=dict(result=dict(operation_id='prior'))
    class Client:
        def call(self,action,**kwargs):
            assert action=='status','no mutation before connectivity proof'
            return dict(operations=[dict(operation_id='prior',state='abandoned',workbook='0303',owner='old',fence=8)])
    fake=SimpleNamespace(module=lambda *a:None,Client=lambda *a:Client(),lock=L.lock,
        connected_status=lambda p:(_ for _ in ()).throw(ConnectionRefusedError()))
    monkeypatch.setattr(F,'load',lambda row:(p,plan,fake,rec))
    monkeypatch.setattr(F,'proofs',lambda *a:({'tracker':'tracker'},{'native':'context'}))
    monkeypatch.setattr(F,'read',lambda r:dict(url='url',private_directory='/private'))
    monkeypatch.setattr(F,'central',lambda *a:{})
    monkeypatch.setattr(F.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0,stderr=b'',stdout=b'{"native":"context"}'))
    with pytest.raises(ConnectionRefusedError):F.launch({},tmp_path/'journal',execute=True)
    assert not (tmp_path/'journal').exists()

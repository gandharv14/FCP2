from dataclasses import dataclass,asdict,replace
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.batch import harbor_scope_enqueue_requalification as q
from scripts.batch.harbor_admission import record,read

@dataclass
class Selected:
    content: object

@pytest.fixture
def scenario(tmp_path,monkeypatch):
    monkeypatch.setattr(q.codec,'encode',lambda s:{'selected':s.content.workbook})
    run=tmp_path/'worker';run.mkdir();q.w.keep(run/'initial.json',{'original':True})
    oldcode=tmp_path/'old.py';oldcode.write_text('old code')
    selected=Selected(SimpleNamespace(workbook='0066'))
    lease=q.w.Lease('owner','token',4,'a'*64)
    predecessor=q.w.keep(tmp_path/'previous.json',dict(step='dialogue-commit',status='completed',selection={'selected':'0066'},lease=asdict(lease)))
    failed=tmp_path/'failed';failed.mkdir()
    intent=q.w.keep(failed/'intent.json',dict(step='enqueue',previous=predecessor,selection={'selected':'0066'},lease=asdict(lease),initial=record(run/'initial.json'),code={str(oldcode):record(oldcode)['sha256']}))
    q.w.keep(failed/'exception.json',{'automatic_replay':False})
    q.w.keep(failed/'readiness.json',{'publication_ready':False,'blockers':['context_bindings','context_bindings_final']})
    tracker=q.w.keep(tmp_path/'tracker.json',{'revision':4});calls=[]
    facade=SimpleNamespace(transition=lambda *a,**k:calls.append((a,k)) or {'revision':5,'sha256':'b'*64})
    worker=SimpleNamespace(run=run,root=tmp_path)
    monkeypatch.setattr(q.w,'active',lambda *a:(facade,{'revision':4},tracker))
    monkeypatch.setattr(q.w,'code',lambda w:{str(oldcode):record(oldcode)['sha256']})
    monkeypatch.setattr(q.scope,'requalify_selected',lambda s,directory,root:(s,record(oldcode)))
    monkeypatch.setattr(q.w,'_runtime',lambda w,s,d:s)
    monkeypatch.setattr(q,'complete_readiness',lambda s,root:{'publication_ready':True,'blockers':[]})
    return worker,selected,lease,intent,predecessor,tmp_path,calls


def test_fresh_enqueue_uses_owning_cas_and_retains_failure(scenario):
    w,s,l,i,p,tmp,calls=scenario;old=Path(i['path']).read_bytes()
    selected,lease,complete=q.enqueue(w,s,l,failed_intent=i,predecessor=p,directory=tmp/'repair')
    assert lease.revision==5 and len(calls)==1
    assert read(complete)['step']=='enqueue' and read(complete)['status']=='completed'
    assert Path(i['path']).read_bytes()==old and not (Path(i['path']).parent/'complete.json').exists()
    with pytest.raises(ValueError,match='already attempted'):q.enqueue(w,s,l,failed_intent=i,predecessor=p,directory=tmp/'repair')


@pytest.mark.parametrize('fault',['substantive','old_code','wrong_checkpoint','runtime_gate'])
def test_enqueue_cannot_waive_other_failures(scenario,monkeypatch,fault):
    w,s,l,i,p,tmp,calls=scenario
    if fault=='substantive':
        path=Path(i['path']).parent/'readiness.json';path.write_text('{"publication_ready":false,"blockers":["dialogue_review_provenance"]}')
    elif fault=='old_code':(tmp/'old.py').write_text('changed')
    elif fault=='wrong_checkpoint':p=record(tmp/'tracker.json')
    else:monkeypatch.setattr(q,'complete_readiness',lambda s,root:{'publication_ready':False,'blockers':['runtime']})
    with pytest.raises((ValueError,KeyError)):q.enqueue(w,s,l,failed_intent=i,predecessor=p,directory=tmp/'repair')
    assert not calls

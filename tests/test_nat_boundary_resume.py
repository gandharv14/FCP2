from dataclasses import replace,asdict
from pathlib import Path
import json
import pytest
from test_nat_worker_retry import setup,modules,failed
from test_instruction_naturalization import RECOVERY as R
from scripts.batch import harbor_nat_boundary_recovery as B
from scripts.batch import harbor_nat_boundary_resume as M
from scripts.batch.harbor_admission import record,read
from scripts.batch import harbor_instruction_producer as P

@pytest.fixture
def ready(setup,modules,monkeypatch,tmp_path):
    # Build explicit retained legacy submissions even after production switches
    # fresh calls to boundary-aware mode. No production mode override exists.
    original_consume=P.consume_naturalization_writer
    def legacy_consume(task,request,campaign,**kw):
        call=P.observed(request,campaign);value=P.validate_call(call,P.read(request.prepared.request),policy=task.policy)['review']
        R.submit_attempt(task.run/'naturalization',value['preamble_body'],value['input_body'],submission_mode='frozen',expected_attempt=request.prepared.ordinal)
        return original_consume(task,request,campaign,**kw)
    def legacy_prompt(root,previous_report=None,*,legacy=False):
        if previous_report is not None:return B._legacy_prompt({'attempts':[previous_report]})
        return B._legacy_prompt({'attempts':[{'reason_codes':['fixture']}]}).split('\nYour previous writer attempt failed mechanical validation')[0]
    monkeypatch.setattr(P,'consume_naturalization_writer',legacy_consume)
    monkeypatch.setattr(P,'naturalization_writer_prompt',legacy_prompt)
    from test_nat_worker_retry import failed as legacy_fixture
    w,prior,worker,s,lease,blocked,t,respond,calls,a,b=legacy_fixture.__wrapped__(setup,modules,monkeypatch)
    worker,s,lease,completion=prior.authorize(worker,s,lease,blocked=blocked)
    pending=w.step(worker,s,lease,checkpoint=completion)
    request=w.request_saved(w.step_directory(worker,'nat-writer')/'request.json')
    respond(request,{'preamble_body':a.decode()+'\n\n','input_body':'\n'+b.decode()})
    result=w.step(worker,s,lease,checkpoint=pending.evidence)
    assert result.status=='NAT_REPAIR_REQUIRED'
    code=[]
    for i,row in enumerate(read(request.context)['code']):
        path=tmp_path/'old-code'/('%03d.py'%i);path.parent.mkdir(exist_ok=True);path.write_bytes(Path(row['path']).read_bytes());code.append(dict(original=row,retained=record(path)))
    originals={str(Path(x['original']['path']).relative_to(B.ROOT)):x['original']['sha256'] for x in code}
    monkeypatch.setattr(B,'KNOWN',{key:originals[key] for key in B.KNOWN})
    n=s.content.naturalization_root
    B.recover(n,expected_state=record(n/'state.json'),writer=request,campaign=worker.campaign,original_code=code,api=R)
    current=w.refreshed(result.selection)
    return w,worker,current,lease,result.evidence,t,calls


def test_full_current_prefix_cas_routes_only_to_fresh_review(ready):
    w,old,s,lease,blocked,t,calls=ready;before=read(record(t.tracker['path']))
    oldbytes=Path(blocked['path']).read_bytes()
    worker,s,lease,completion=M.authorize(old,s,lease,blocked=blocked)
    after=read(record(t.tracker['path']))
    assert after['revision']==before['revision']+1 and after['lane_state'][P.NAT]['attempt']==2
    assert Path(blocked['path']).read_bytes()==oldbytes and len(calls)==2
    assert M.validate(worker,after)['remaining_writers']==0
    result=w.step(worker,s,lease,checkpoint=completion)
    assert result.status=='MODEL_REQUIRED' and result.next_step=='nat-review' and len(calls)==2
    request=w.request_saved(worker.run/'steps/nat-review/request.json')
    assert request.prepared.ordinal==request.prepared.limit==1 and read(request.context)['writer_round']==2
    assert json.loads(read(request.prepared.request)['messages'][1]['content'])['candidate']==(s.content.naturalization_root/B.DIRECTORY/'candidate.md').read_text()
    with pytest.raises(ValueError):M.authorize(old,s,lease,blocked=blocked)


@pytest.mark.parametrize('point',['prepared','after_replace','committed'])
def test_worker_boundary_cas_interruptions_preserve_unknown_state(ready,point):
    w,worker,s,lease,blocked,t,calls=ready
    def fail(at):
        if at==point:raise KeyboardInterrupt('fixture stop')
    with pytest.raises(KeyboardInterrupt):M.authorize(worker,s,lease,blocked=blocked,_fault=fail)
    permit=record(worker.run/'boundary-resume-v1/permit.json')
    if point=='prepared':
        with pytest.raises(ValueError):M.recover(worker,permit,lease)
    else:
        new,current,newlease,completion=M.recover(worker,permit,lease)
        assert M.validate(new,read(record(t.tracker['path'])))['spent']==2
    assert len(calls)==2


@pytest.mark.parametrize('changed',['lease','blocked','candidate','receipt','old_archive','context','later'])
def test_unproved_scope_or_history_cannot_advance(ready,changed):
    w,worker,s,lease,blocked,t,calls=ready
    if changed=='lease':lease=replace(lease,sha256='0'*64)
    if changed=='blocked':blocked={**blocked,'sha256':'0'*64}
    if changed in {'candidate','receipt'}:
        path=s.content.naturalization_root/B.DIRECTORY/('candidate.md' if changed=='candidate' else 'receipt.json');path.chmod(0o600);path.write_bytes(path.read_bytes()+b' ')
    if changed=='old_archive':
        p=read(read(worker.retry)['permit']);path=Path(p['copies'][0]['retained']['path']);path.chmod(0o600);path.write_bytes(b'changed')
    if changed=='context':s=replace(s,content=replace(s.content,model_policy=replace(s.content.model_policy,model='unapproved')))
    if changed=='later':(worker.run/'steps/nat-review').mkdir()
    with pytest.raises(ValueError):M.authorize(worker,s,lease,blocked=blocked)
    assert len(calls)==2

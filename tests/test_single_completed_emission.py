from pathlib import Path
from types import SimpleNamespace
from copy import deepcopy
import json
import pytest
from scripts.fleet import normalization_completed_emission as S

def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return S.binding(path)

@pytest.fixture
def case(tmp_path):
    h=tmp_path/'old';run=tmp_path/'normalization';h.mkdir();run.mkdir()
    profile=dict(entrypoint={'path':'/original/helper.py','sha256':'a'*64},read_call={'args':[dict(home=str(h),single_helper={'original':'single'})]},symbols=[{'original':'review'}])
    old=dict(helper=profile['entrypoint'],single_helper={'original':'single'},single_author=save(tmp_path/'single.json',dict(research={'actual':'research'})),home=str(h),fence=2,source={'original':'source'},workbook='WB',owner='owner',prepared=save(tmp_path/'prepared.json',{}),tracker=save(tmp_path/'tracker.json',{}),runtime_profile=save(tmp_path/'runtime.json',profile))
    previous=save(tmp_path/'previous.json',old)
    phase=save(run/'normalized.phase1.json',{});outputs={n:save(run/n,{})for n in ('normalized.json','normalization_report.json','exclusions.json')}
    emitted=save(run/'phase1-result.json',dict(status='phase1_evidence_ready',prepared=old['prepared'],phase1=phase,outputs=outputs))
    ei=save(h/'emit/intent.json',dict(plan=previous,helper=old['helper'],prepared=old['prepared'],lease={},seconds=3300,memory=12*1024**3))
    log=h/'emit/step.log';log.write_text('passed')
    eg=save(h/'emit/resource.json',dict(intent=ei,returncode=0,failure_type=None,automatic_retry=False,log=S.binding(log)))
    er=save(h/'emit/result.json',dict(emitted=emitted,lease={},runtime=old['runtime_profile'],models_dispatched=0))
    args={k:save(h/(k+'.json'),{})for k in ('legacy','current','lease')};args['checkpoint']={'actual':'research'}
    ri=save(h/'refresh/intent.json',dict(action='refresh',arguments=args));rl=h/'refresh/step.log';rl.write_text('scripts.batch.harbor_admission.AdmissionError: non-proof financial binding changed\n')
    rg=save(h/'refresh/resource.json',dict(intent=ri,returncode=1,failure=None,log=S.binding(rl)))
    job=save(tmp_path/'job.json',dict(driver=old['helper'],source=old['source'],workbook='WB',inputs=[previous],command=['python','-I','-B',old['helper']['path'],'run','--plan',previous['path'],'--plan-sha256',previous['sha256']]))
    wi=save(tmp_path/'wi.json',dict(owner='owner',job=job));outer=save(tmp_path/'outer.json',dict(status='RECONCILIATION_REQUIRED',returncode=1,failure_type=None,automatic_retry=False,publication_performed=False,result=None,intent=wi))
    rows=[previous,er,eg,ei,S.binding(log),emitted,rg,ri,S.binding(rl),outer,wi,job]
    stop=save(tmp_path/'stop.json',dict(at=1,claims=0,models=0,processes=[],files=[dict(native=r,copy=r)for r in rows]))
    q=dict(schema_version='fleet-normalization-single-completed-emission/v1',previous=previous,emission_result=er,emission_guard=eg,refresh_guard=rg,outer=outer,stopped=stop)
    p=dict(old,home=str(tmp_path/'next'),fence=3,completed_helper={'selected':'helper'},completed_emission=save(tmp_path/'q.json',q))
    profile['read_call']['args'][0]['home']=p['home'];profile['symbols'] += [{'refresh':['unchanged','prepare']}];p['runtime_profile']=save(tmp_path/'next-runtime.json',profile)
    return p,q,SimpleNamespace(run=run),h

def test_retains_actual_single_emission(case):
    p,q,s,h=case
    assert S.validate_single(None,p,None,s,{})==S.result(p)

@pytest.mark.parametrize('fault',['fence','source','model','advanced','phase','output','runtime','lease'])
def test_rejects_changed_completed_single_evidence(case,tmp_path,fault):
    p,q,s,h=case;lease={}
    if fault=='fence':p['fence']+=1
    elif fault=='source':p['source']={'foreign':True}
    elif fault=='model':
        stop=S.read(q['stopped']);stop['models']=1;q['stopped']=save(tmp_path/'badstop.json',stop);p['completed_emission']=save(tmp_path/'q2.json',q)
    elif fault=='advanced':save(h/'refresh/result.json',{})
    elif fault=='phase':save(s.run/'normalized.phase1.json',{'changed':True})
    elif fault=='output':save(s.run/'normalized.json',{'changed':True})
    elif fault=='runtime':
        runtime=S.read(p['runtime_profile']);runtime['symbols'][0]={'different':True};p['runtime_profile']=save(tmp_path/'bad-runtime.json',runtime)
    elif fault=='lease':lease={'different':True}
    with pytest.raises(ValueError):S.validate_single(None,p,None,s,lease)

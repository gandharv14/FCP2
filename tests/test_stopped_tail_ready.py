from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from scripts.fleet import stopped_tail_ready as S

@pytest.fixture
def case(tmp_path):
    data={}
    def b(path):return dict(path=str(path),sha256='fixture')
    def put(path,value):data[str(path)]=value;return b(path)
    home=tmp_path/'tail';home.mkdir();run=tmp_path/'worker';run.mkdir();log=run/'worker.log';log.write_text('traceback\nAdmissionError: queued worker code changed\n')
    p=dict(output=str(home),operations=str(tmp_path/'ops'),helper=b('/helper'),source=b('/source'),workbook='w',owner='owner',fresh_plan=b('/fresh'),original_job=b('/original'))
    row=put(tmp_path/'plan',p);original=dict(config=b('/config'),driver=b('/driver'))
    data['/original']=original
    prior=put(tmp_path/'prior',{});descriptor=put(tmp_path/'descriptor',{})
    step=put(home/'enqueue-step/intent.json',dict(step='enqueue'))
    complete=put(home/'enqueue-step/complete.json',dict(step='enqueue',status='completed',intent=step))
    checkpoint=put(home/'first-step/result.json',dict(status='PUBLICATION_READY',next_step=None,publication_performed=False,checkpoint=complete,evidence=[prior,complete]))
    command=put(home/'first-step/intent.json',dict(action='step',models=True,commands=True,containers=True,arguments=dict(checkpoint=prior,descriptor=descriptor)))
    resource=put(home/'first-step/resource.json',dict(returncode=0,failure=None,intent=command))
    guard=put(home/'first-step/outcome.json',dict(status='COMPLETED_OPERATION',result=checkpoint,resource=resource))
    worker=put(tmp_path/'job',dict(run=str(run),driver=p['helper'],inputs=[row],source=p['source'],workbook='w',result=str(home/'result.json')))
    intent=put(run/'intent',dict(job=worker,owner='owner'))
    outer=put(run/'outcome.json',dict(intent=intent,status='RECONCILIATION_REQUIRED',returncode=1,failure_type=None,result=None,automatic_retry=False,log=b(log)))
    job=dict(original,outcome=guard,operations=p['operations']);jobrow=put(tmp_path/'author',job)
    ready=dict(schema_version='fleet-stopped-wrapper-ready-projection/v1',status='PUBLICATION_READY',workbook='w',plan=p['fresh_plan'],job=jobrow,config=original['config'],outcome=guard,checkpoint=checkpoint,continuation=row,intent=b(home/'intent.json'),publication_performed=False,automatic_retry=False,projection_path=str(tmp_path/'ready'))
    readyrow=put(tmp_path/'ready',ready)
    cert=put(tmp_path/'cert',dict(schema_version='fleet-stopped-wrapper-completed-ready/v1',worker_outcome=outer,job=worker,guard=guard,checkpoint=checkpoint,ready=readyrow,failure_log=b(log),enqueue_permission=dict(schema_version='fleet-completed-enqueue-permission/v1',intent=command,prior=prior,descriptor=descriptor,completion=complete,step_intent=step)))
    pub=NS(checked=lambda row:data[row['path']],binding=b)
    return pub,p,row,ready,job,cert,data,home,log

def test_actual_ready_projection_retains_original_job_and_never_creates_tail_work(case):
    pub,p,row,ready,job,cert,data,home,log=case
    assert S.validate(pub,p,row,ready,job,cert)==data[cert['path']]
    assert not(home/'result.json').exists()and not Path(p['operations']).exists()

@pytest.mark.parametrize('change',['unknown_failure','outer_result','guard_failed','checkpoint_not_ready','source_changed','author_changed','task_result','dispatcher','permission','ready_scope'])
def test_incomplete_detached_or_effectful_wrappers_are_rejected(case,change):
    pub,p,row,ready,job,cert,data,home,log=case;c=data[cert['path']]
    if change=='unknown_failure':log.write_text('AdmissionError: another failure\n')
    elif change=='outer_result':data[c['worker_outcome']['path']]['result']={}
    elif change=='guard_failed':data[c['guard']['path']]['status']='RECONCILIATION_REQUIRED'
    elif change=='checkpoint_not_ready':data[c['checkpoint']['path']]['status']='STEP_COMPLETE'
    elif change=='source_changed':p['source']={}
    elif change=='author_changed':job['config']={}
    elif change=='task_result':(home/'result.json').write_text('{}')
    elif change=='dispatcher':Path(p['operations']).mkdir()
    elif change=='permission':c['enqueue_permission']['prior']={}
    else:ready['workbook']='foreign'
    with pytest.raises(ValueError):S.validate(pub,p,row,ready,job,cert)

@pytest.mark.parametrize('change',[None,'extra_step','commit_not_complete','wrong_prior','different_job','changed_hold'])
def test_completed_commit_then_enqueue_preserves_both_original_guards(case,change):
    pub,p,row,ready,job,cert,data,home,log=case;c=data[cert['path']];b=pub.binding
    p.update(checkpoint_takeover={'helper':b('/takeover'),'profile':b('/takeover-profile')},interrupted_commit={})
    ops=Path(p['operations']);(ops/'000').mkdir(parents=True)
    # Move the completed enqueue to its actual dispatcher directory.
    mapping={str(home/'first-step'/name):str(ops/'000'/name)for name in ('intent.json','resource.json','outcome.json','result.json')}
    def replace(v):
        if isinstance(v,dict):
            if set(v)=={'path','sha256'}and v['path']in mapping:return b(mapping[v['path']])
            return {k:replace(x)for k,x in v.items()}
        if isinstance(v,list):return [replace(x)for x in v]
        return v
    for key,value in list(data.items()):data[mapping.get(key,key)]=replace(value)
    c=data[cert['path']];ready.clear();ready.update(data[c['ready']['path']])
    first=b(home/'first-step/outcome.json');first_result=b(home/'first-step/result.json')
    data[first['path']]=dict(status='COMPLETED_OPERATION',result=first_result)
    data[first_result['path']]=dict(status='STEP_COMPLETE',next_step='enqueue')
    command=data[data[c['guard']['path']]['resource']['path']]['intent']
    data[command['path']]['arguments']['checkpoint']=first_result
    c['enqueue_permission']['prior']=first_result
    data[c['checkpoint']['path']]['evidence'][0]=first_result
    job['outcome']=first;data[str(home/'authoring-job.json')]=dict(job)
    data[str(ops/'hold.json')]=dict(automatic_retry=False,error_type='AdmissionError',outcome=c['guard'],reason='prior evidence inspection failed')
    data['/config']=dict(run_root=str(home/'native-run'))
    step_home=home/'native-run/instruction/steps/enqueue'
    for key,name in [('step_intent','intent.json'),('completion','complete.json')]:
        old=c['enqueue_permission'][key];new=b(step_home/name);data[new['path']]=data[old['path']];c['enqueue_permission'][key]=new
    data[c['enqueue_permission']['completion']['path']]['intent']=c['enqueue_permission']['step_intent']
    checkpoint=data[c['checkpoint']['path']];checkpoint['checkpoint']=c['enqueue_permission']['completion'];checkpoint['evidence'][1]=checkpoint['checkpoint']
    data[command['path']]['models']=False;c.pop('enqueue_permission')
    if change=='extra_step':(ops/'001').mkdir()
    elif change=='commit_not_complete':data[first_result['path']]['status']='RETRY_READY'
    elif change=='wrong_prior':data[command['path']]['arguments']['checkpoint']=b('/foreign')
    elif change=='different_job':data[str(home/'authoring-job.json')]['outcome']=b('/foreign')
    elif change=='changed_hold':data[str(ops/'hold.json')]['error_type']='RuntimeError'
    if change:
        with pytest.raises(ValueError):S.validate(pub,p,row,ready,job,cert)
    else:assert S.validate(pub,p,row,ready,job,cert)==c

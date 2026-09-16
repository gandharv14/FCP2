from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.fleet import disclosure_adoption_continue as S,disclosure_adoption_interruption as K
from scripts.batch import harbor_disclosure_round_adoption as A
import json
def keep(p,v):
    p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,sort_keys=True));return S.binding(p)
from test_disclosure_prepared_cold_order import unstarted

@pytest.fixture
def interrupted(unstarted,tmp_path):
    old,v,original=unstarted
    first_resume=old['resume'];saved=S.resume_state(old,continued=True)
    oldplan=keep(tmp_path/'second-plan',old);home=Path(old['output']);keep(home/'intent.json',dict(schema_version=S.SCHEMA,plan=oldplan,automatic_retry=False))
    job=keep(tmp_path/'second-job',dict(driver=old['helper'],source=old['source'],command=['python','--plan',oldplan['path'],'--sha256',oldplan['sha256']]))
    wi=keep(tmp_path/'second-wi',dict(owner=old['owner'],job=job))
    outer=keep(tmp_path/'second-outer',dict(status='RECONCILIATION_REQUIRED',returncode=None,failure_type='TimeoutError',result=None,automatic_retry=False,intent=wi))
    process=keep(tmp_path/'process',dict(job=job,command=['worker','--owner',old['owner']],pid=999,start_ticks='1'))
    observation=keep(tmp_path/'observation',dict(schema_version='fleet-stopped-adoption-observation/v1',process=process,worker_outcome=outer,processes=[],tracker=saved['tracker'],observed_at_ns=1))
    ii=keep(home/'first-step/intent.json',dict(action='step',arguments=dict(descriptor=saved['descriptor'],checkpoint=saved['checkpoint']),models=False,commands=False,containers=False))
    side=keep(home/'first-step/aggregate-bootstrap.json',dict(plan=oldplan,helper=old['helper'],intent=ii,kind='instruction',automatic_retry=False))
    dependency=keep(home/'first-step/dependency.json',{})
    permit=S.read(saved['permit']);permit.update(selection={},lease={});saved['permit']=keep(Path(saved['permit']['path']),permit)
    # Keep the original prepared handoff bound to the now populated permit.
    cp=S.read(saved['checkpoint']);cp['checkpoint']=saved['permit'];saved['checkpoint']=keep(Path(saved['checkpoint']['path']),cp)
    proof=S.read(first_resume);proof.update(permit=saved['permit'],checkpoint=saved['checkpoint']);old['resume']=keep(Path(first_resume['path']),proof)
    oldplan=keep(tmp_path/'second-plan',old);keep(home/'intent.json',dict(schema_version=S.SCHEMA,plan=oldplan,automatic_retry=False))
    job=keep(tmp_path/'second-job',dict(driver=old['helper'],source=old['source'],command=['python','--plan',oldplan['path'],'--sha256',oldplan['sha256']]))
    wi=keep(tmp_path/'second-wi',dict(owner=old['owner'],job=job));outer=keep(tmp_path/'second-outer',dict(status='RECONCILIATION_REQUIRED',returncode=None,failure_type='TimeoutError',result=None,automatic_retry=False,intent=wi))
    process=keep(tmp_path/'process',dict(job=job,command=['worker','--owner',old['owner']],pid=999,start_ticks='1'))
    observation=keep(tmp_path/'observation',dict(schema_version='fleet-stopped-adoption-observation/v1',process=process,worker_outcome=outer,processes=[],tracker=saved['tracker'],observed_at_ns=1))
    ii=keep(home/'first-step/intent.json',dict(action='step',arguments=dict(descriptor=saved['descriptor'],checkpoint=saved['checkpoint']),models=False,commands=False,containers=False))
    side=keep(home/'first-step/aggregate-bootstrap.json',dict(plan=oldplan,helper=old['helper'],intent=ii,kind='instruction',automatic_retry=False))
    step=keep(original/'permit/steps/disclosure-review/intent.json',dict(step='disclosure-review',previous=saved['permit'],selection={},lease={}))
    worker=S.binding(__file__)
    proof=dict(schema_version=K.SCHEMA,plan=oldplan,worker_outcome=outer,process=process,observation=observation,step_intent=step,instruction_intent=ii,sidecar=side,dependency=dependency,worker=worker,step_directory_function='f'*64)
    old['owning_profile']=dict(worker=worker)
    # This test focuses the interruption contract; original prepared resume has its own closed regression suite.
    p={**old,'owner':'third','fence':8,'output':str(tmp_path/'third'),'interrupted_step':dict(helper=S.binding(K.__file__),proof=keep(tmp_path/'interrupted',proof))}
    return p,proof,saved,old,home

@pytest.mark.parametrize('fault',[None,'tracker','effect','owner','model','sidecar','process','source'])
def test_interrupted_step_requires_exact_no_effects_custody(interrupted,monkeypatch,fault):
    p,v,saved,old,home=interrupted
    original_read=S.read
    def read(row):
        if row==v['plan']:return old
        return original_read(row)
    monkeypatch.setattr(S,'read',read);monkeypatch.setattr(S,'resume_state',lambda *a,**k:saved)
    if fault=='tracker':Path(saved['tracker']['path']).write_text('drift')
    if fault=='effect':keep(Path(v['step_intent']['path']).parent/'aggregate-consumption.json',{})
    if fault=='owner':p['owner']=old['owner']
    if fault=='source':p['source']={'path':'/foreign','sha256':'f'*64}
    if fault in {'model','sidecar','process'}:
        key={'model':'instruction_intent','sidecar':'sidecar','process':'observation'}[fault];value=read(v[key])
        if fault=='model':value['models']=True
        elif fault=='sidecar':value['plan']={}
        else:value['processes']=[dict(pid=999)]
        v[key]=keep(Path(v[key]['path']),value);p['interrupted_step']['proof']=keep(Path(p['interrupted_step']['proof']['path']),v)
    if fault:
        with pytest.raises(Exception):K.validate(S,p)
    else:
        assert K.validate(S,p)[0]==saved
        Path(saved['tracker']['path']).write_text('later original checkpoint')
        assert K.validate(S,p,continued=True)[0]==saved

def test_mapping_scopes_exact_permit_and_restores(interrupted,monkeypatch):
    p,v,saved,old,home=interrupted
    monkeypatch.setattr(K,'validate',lambda *a,**k:(saved,v))
    def original(w,phase):return Path('/original')/phase
    W=SimpleNamespace(__file__=__file__,step_directory=original);v['step_directory_function']=A.function_sha(original)
    with K.installed(S,p,W,A):
        assert W.step_directory(SimpleNamespace(disclosure_repair=saved['permit']),'disclosure-review')==Path(p['output'])/'adoption-steps/disclosure-review'
        assert W.step_directory(SimpleNamespace(disclosure_repair=None),'disclosure-review')==Path('/original/disclosure-review')
        assert W.step_directory(SimpleNamespace(disclosure_repair=saved['permit']),'nat-review')==Path('/original/nat-review')
    assert W.step_directory is original

@pytest.fixture
def sealed_direct(tmp_path):
    home=tmp_path/'old';source=keep(tmp_path/'source',{});tracker=keep(tmp_path/'tracker',{})
    config=keep(tmp_path/'config',dict(run_root=str(tmp_path/'run')));job=keep(tmp_path/'original-job',dict(config=config))
    plan=keep(tmp_path/'round-plan',{});result=keep(tmp_path/'round-result',{})
    old=dict(helper=S.binding(S.__file__),owner='old',fence=4,output=str(home),source=source,
        context=dict(plan=plan),round_result=result,adoption=S.binding(A.__file__),original_job=job,
        owning_profile=dict(worker=S.binding(__file__)))
    row=keep(tmp_path/'old-plan',old);keep(home/'intent.json',dict(schema_version=S.SCHEMA,plan=row,automatic_retry=False))
    permit=keep(home/'permit/permit.json',dict(round_plan=plan,round_result=result,implementation=old['adoption'],tracker=tracker,selection={},lease={}))
    descriptor=keep(home/'permit/descriptor.json',{})
    cp=keep(home/'permit/result.json',dict(status='RETRY_READY',next_step='disclosure-review',checkpoint=permit,descriptor=descriptor))
    workerjob=keep(tmp_path/'worker-job',dict(driver=old['helper'],source=source,command=['python','--plan',row['path'],'--sha256',row['sha256']]))
    wi=keep(tmp_path/'worker-intent',dict(owner='old',job=workerjob));outer=keep(tmp_path/'worker-outcome',dict(status='RECONCILIATION_REQUIRED',failure_type=None,returncode=1,result=None,automatic_retry=False,intent=wi))
    process=keep(tmp_path/'process',dict(job=workerjob,command=['worker','--owner','old']))
    observation=keep(tmp_path/'observed',dict(schema_version='fleet-stopped-adoption-observation/v1',process=process,worker_outcome=outer,processes=[],tracker=tracker,observed_at_ns=1))
    ii=keep(home/'first-step/intent.json',dict(action='step',arguments=dict(descriptor=descriptor,checkpoint=cp),models=False,commands=False,containers=False))
    side=keep(home/'first-step/aggregate-bootstrap.json',dict(plan=row,helper=old['helper'],intent=ii,kind='instruction',automatic_retry=False))
    dep=keep(home/'first-step/dependency.json',{});step=keep(home/'permit/steps/disclosure-review/intent.json',dict(step='disclosure-review',previous=permit,selection={},lease={}))
    log=home/'first-step/step.log';log.write_text('consuming_aggregate\narchived_prefix_reader\nValueError: original typed inventory reader changed\n')
    guard=keep(home/'first-step/resource.json',dict(intent=ii,returncode=1,failure=None,log=S.binding(log)))
    failed=keep(home/'first-step/outcome.json',dict(status='RECONCILIATION_REQUIRED',result=None,resource=guard,automatic_retry=False))
    exception=keep(home/'permit/steps/disclosure-review/exception.json',dict(type='ValueError',message_sha256=__import__('hashlib').sha256(b'original typed inventory reader changed').hexdigest(),automatic_replay=False))
    proof=dict(schema_version=K.SEALED,step_exception=exception,plan=row,worker_outcome=outer,process=process,observation=observation,step_intent=step,instruction_intent=ii,sidecar=side,dependency=dep,worker=old['owning_profile']['worker'],step_directory_function='f'*64,failed_outcome=failed)
    p={**old,'owner':'new','fence':5,'output':str(tmp_path/'new'),'interrupted_step':dict(helper=S.binding(K.__file__),proof=keep(tmp_path/'proof',proof))}
    return p,proof,home,tracker

@pytest.mark.parametrize('fault',[None,'tracker','effect','outcome','guard','log','later','owner','archive'])
def test_sealed_direct_permit_preserves_original_attempt(sealed_direct,fault):
    p,v,home,tracker=sealed_direct
    if fault=='tracker':Path(tracker['path']).write_text('changed')
    if fault=='effect':keep(home/'permit/steps/disclosure-review/aggregate-consumption.json',{})
    if fault=='outcome':keep(home/'first-step/outcome.json',dict(status='COMPLETED_OPERATION'))
    if fault=='guard':keep(home/'first-step/resource.json',dict(returncode=0))
    if fault=='log':(home/'first-step/step.log').write_text('unrelated error')
    if fault=='later':keep(home/'first-step/result.json',{})
    if fault=='owner':p['owner']='old'
    if fault=='archive':Path(v['observation']['path']).write_text('{}')
    if fault:
        with pytest.raises((ValueError,KeyError)):K.validate(S,p)
    else:
        saved,_=K.validate(S,p);assert saved['permit']==S.binding(home/'permit/permit.json')
        assert S.adoption_home(p)==home
        Path(tracker['path']).write_text('later original operation owns current checkpoint')
        assert K.validate(S,p,continued=True)[0]==saved

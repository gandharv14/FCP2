from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.fleet import disclosure_adoption_continue as S,undispatched_campaign as N
from scripts.batch.harbor_admission import record
from test_disclosure_adoption_resume import keep


def test_real_dispatcher_rejects_replay_but_cold_location_load_accepts_existing_directory(tmp_path):
    from scripts.fleet import advance_authoring_v8 as D
    config=dict(run_root=str(tmp_path));home=tmp_path/'output';path=tmp_path/'dispatcher-v2/output';path.mkdir(parents=True)
    with pytest.raises(ValueError,match='exists'):D.operation_namespace(config,str(path))
    p=dict(output=str(home),namespace_module=record(N.__file__))
    assert S.operations_for(p,None,D,config)==path
    with pytest.raises(ValueError,match='exists'):D.operation_namespace(config,str(path))
    path.rmdir();path.write_text('not a namespace')
    with pytest.raises(ValueError):S.operations_for(p,None,D,config)


@pytest.fixture
def completed(tmp_path):
    # Full original failed permit handoff, followed by actual adopted checkpoint
    # and an independently guarded failure before the next worker phase.
    h=tmp_path/'old';h1=tmp_path/'resumed';source=keep(tmp_path/'source',{});tracker=keep(tmp_path/'tracker',{})
    rp=keep(tmp_path/'round',{});rr=keep(tmp_path/'round-result',{});cfg=keep(tmp_path/'config',dict(run_root=str(tmp_path/'authoring')))
    job=keep(tmp_path/'job',dict(config=cfg));old=dict(schema_version=S.SCHEMA,helper=record(S.__file__),owner='one',fence=1,output=str(h),adoption=record(S.__file__),source=source,context=dict(plan=rp),round_result=rr,original_job=job)
    oldp=keep(tmp_path/'p1',old);keep(h/'intent.json',dict(schema_version=S.SCHEMA,plan=oldp,automatic_retry=False))
    permit=keep(h/'permit/permit.json',dict(round_plan=rp,round_result=rr,tracker=tracker,implementation=old['adoption']))
    d=keep(h/'permit/descriptor.json',{});cp=keep(h/'permit/result.json',dict(status='RETRY_READY',next_step='disclosure-review',checkpoint=permit,descriptor=d))
    def outer(p,row,name,error):
        j=keep(tmp_path/(name+'-job'),dict(driver=p['helper'],source=source,command=['python','--plan',row['path'],'--sha256',row['sha256']]))
        i=keep(tmp_path/(name+'-intent'),dict(owner=p['owner'],job=j));log=tmp_path/(name+'-log');log.write_text(error+'\n')
        return keep(tmp_path/(name+'-out'),dict(schema_version='harbor-fleet-worker-outcome/v1',status='RECONCILIATION_REQUIRED',returncode=1,failure_type=None,result=None,automatic_retry=False,intent=i,log=record(log)))
    out=outer(old,oldp,'first','scripts.batch.harbor_admission.AdmissionError: disclosure repair cannot combine detached continuations')
    resume=keep(tmp_path/'resume',dict(schema_version='fleet-disclosure-adoption-unstarted-step/v1',plan=oldp,worker_outcome=out,permit=permit,checkpoint=cp,descriptor=d,tracker=tracker))
    p2={**old,'owner':'two','fence':2,'output':str(h1),'checkpoint_reader':{},'resume':resume};row2=keep(tmp_path/'p2',p2)
    keep(h1/'intent.json',dict(schema_version=S.SCHEMA,plan=row2,automatic_retry=False))
    res=keep(h1/'first-step/result.json',dict(status='STEP_COMPLETE',next_step='disclosure-commit',descriptor=d))
    guard=keep(h1/'first-step/resource.json',dict(returncode=0,failure=None))
    outcome=keep(h1/'first-step/outcome.json',dict(status='COMPLETED_OPERATION',result=res,resource=guard))
    ops=tmp_path/'authoring/dispatcher-v2/resumed';i=keep(ops/'000/intent.json',dict(action='step',arguments=dict(descriptor=d,checkpoint=res)))
    log=ops/'000/step.log';log.write_text('install_commands\nValueError: dispatcher namespace exists; no replay\n')
    resource=keep(ops/'000/resource.json',dict(returncode=1,failure=None,intent=i,log=record(log)))
    failed=keep(ops/'000/outcome.json',dict(status='RECONCILIATION_REQUIRED',resource=resource,result=None))
    keep(ops/'000/aggregate-bootstrap.json',dict(schema_version=S.SCHEMA,plan=row2,helper=p2['helper'],kind='instruction',intent=i,automatic_retry=False));keep(ops/'hold.json',dict(outcome=failed))
    outer2=outer(p2,row2,'second','retained failure')
    proof=keep(tmp_path/'completed',dict(schema_version='fleet-disclosure-adoption-completed-step/v1',plan=row2,worker_outcome=outer2,outcome=outcome,failed_outcome=failed,tracker=tracker))
    return {**p2,'helper':record(S.__file__),'owner':'three','fence':3,'output':str(tmp_path/'new'),'namespace_module':record(N.__file__),'completed_step':proof},ops,h,tracker


@pytest.mark.parametrize('fault',[None,'tracker','commit','next','source','fence','guard','checkpoint'])
def test_completed_step_reuses_only_exact_adoption_and_never_a_started_commit(completed,fault):
    p,ops,h,tracker=completed
    if fault=='tracker':Path(tracker['path']).write_text('{}')
    elif fault=='commit':(h/'permit/steps/disclosure-commit').mkdir(parents=True)
    elif fault=='next':(ops/'001').mkdir()
    elif fault=='source':p['source']={**p['source'],'sha256':'f'*64}
    elif fault=='fence':p['fence']=5
    elif fault=='guard':(ops/'000/resource.json').write_text('{}')
    elif fault=='checkpoint':(ops/'000/intent.json').write_text('{}')
    if fault:
        with pytest.raises((ValueError,FileNotFoundError)):S.completed_step(p)
    else:
        assert S.completed_step(p)['outcome']['path'].endswith('first-step/outcome.json')
        (h/'permit/steps/disclosure-commit').mkdir(parents=True)
        assert S.completed_step(p,continued=True)
    assert not Path(p['output']).exists()

from pathlib import Path
import pytest
from scripts.fleet import disclosure_adoption_continue as S
from scripts.batch.harbor_admission import read,record
from scripts.batch.harbor_model_executor import save

def keep(p,v):p.parent.mkdir(parents=True,exist_ok=True);save(p,v);return record(p)

@pytest.mark.parametrize('defect',[None,'first_child','task_step','tracker','source','permit','failure'])
def test_sealed_permit_resume_requires_exact_unstarted_failure(tmp_path,defect):
    oldhome=tmp_path/'old';newhome=tmp_path/'new';source=keep(tmp_path/'source.json',{});tracker=keep(tmp_path/'tracker.json',{})
    roundplan=keep(tmp_path/'round.json',{});roundresult=keep(tmp_path/'round-result.json',{});config=keep(tmp_path/'config.json',dict(run_root=str(tmp_path/'authoring')));job=keep(tmp_path/'original-job.json',dict(config=config))
    old=dict(schema_version=S.SCHEMA,helper=record(S.__file__),owner='old',fence=10,output=str(oldhome),adoption=record(S.__file__),source=source,context=dict(plan=roundplan),round_result=roundresult,original_job=job)
    oldplan=keep(tmp_path/'old-plan.json',old);keep(oldhome/'intent.json',dict(schema_version=S.SCHEMA,plan=oldplan,automatic_retry=False))
    permit=keep(oldhome/'permit/permit.json',dict(round_plan=roundplan,round_result=roundresult,tracker=tracker,implementation=old['adoption']))
    descriptor=keep(oldhome/'permit/descriptor.json',{});checkpoint=keep(oldhome/'permit/result.json',dict(status='RETRY_READY',next_step='disclosure-review',checkpoint=permit,descriptor=descriptor))
    workerjob=keep(tmp_path/'worker-job.json',dict(driver=old['helper'],source=source,command=['python','--plan',oldplan['path'],'--sha256',oldplan['sha256']]))
    intent=keep(tmp_path/'worker-intent.json',dict(owner=old['owner'],job=workerjob));log=tmp_path/'worker.log';log.write_text('scripts.batch.harbor_admission.AdmissionError: disclosure repair cannot combine detached continuations\n')
    out=dict(schema_version='harbor-fleet-worker-outcome/v1',status='RECONCILIATION_REQUIRED',returncode=1,failure_type=None,result=None,automatic_retry=False,intent=intent,log=record(log))
    if defect=='failure':out['returncode']=137
    outcome=keep(tmp_path/'outcome.json',out)
    proof=dict(schema_version='fleet-disclosure-adoption-unstarted-step/v1',plan=oldplan,worker_outcome=outcome,permit=permit,checkpoint=checkpoint,descriptor=descriptor,tracker=tracker)
    selected={**old,'owner':'new','fence':11,'output':str(newhome),'checkpoint_reader':{},'resume':keep(tmp_path/'resume.json',proof)}
    if defect=='first_child':(oldhome/'first-step').mkdir()
    elif defect=='task_step':(oldhome/'permit/steps').mkdir()
    elif defect=='tracker':Path(tracker['path']).write_text('{}')
    elif defect=='source':selected['source']={**source,'sha256':'f'*64}
    elif defect=='permit':Path(permit['path']).write_text('{}')
    if defect is None:assert S.resume_state(selected)==proof
    else:
        with pytest.raises((ValueError,FileNotFoundError)):S.resume_state(selected)
    assert not newhome.exists()

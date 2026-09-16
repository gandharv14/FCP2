import json
from pathlib import Path
import pytest
from scripts.fleet import prepare_queued_publication_renewal as P


def fixture(tmp_path):
    def save(name,value):return P.retain(tmp_path/name,value)
    prior=save('continuation.json',dict(schema_version='harbor-direct-warning-continuation/v1',helper={'path':'/helper','sha256':'h'},workbook='0064',source_sha256='source'))
    job=save('job.json',dict(driver=P.checked(prior)['helper'],workbook='0064',source={'sha256':'source'}))
    launch=save('launch.json',dict(root_plan=prior,job=job,remote_receipt='/native/process.json'))
    intent=save('intent.json',dict(plan=launch))
    launched=save('launched.json',dict(intent=intent,remote={'root_intent':intent}))
    ready={'path':'/native/ready.json','sha256':'r'}
    plan=save('publication.json',dict(ready=ready,retained_warning=dict(continuation_helper=P.checked(prior)['helper'],continuation_plan=prior,job=job,root_launch=launched)))
    local=save('process-copy.json',dict(job=job))
    process=dict(path='/native/process.json',sha256=local['sha256'])
    custody=dict(status='STOPPED_DIRECT_WARNING_CUSTODY',processes=[],process=process,ready=ready,copies=[dict(native=process,copy=local)])
    return dict(continuation=prior,launched=launched,plan=plan),custody,save


def test_direct_warning_uses_actual_launch_and_identical_process_copy(tmp_path):
    prepared,custody,_=fixture(tmp_path)
    launch,normalized=P.preparation_context(prepared,tmp_path,custody)
    assert launch['root_plan']==prepared['continuation']
    assert normalized['mapping']==[dict(original=custody['process'],local=custody['copies'][0]['copy'])]
    assert 'mapping' not in custody


@pytest.mark.parametrize('fault',['live','ready','copy','duplicate','process','source','publication'])
def test_direct_warning_rejects_changed_custody(tmp_path,fault):
    prepared,custody,save=fixture(tmp_path)
    if fault=='live':custody['processes']=[123]
    elif fault=='ready':custody['ready']={'path':'/foreign','sha256':'x'}
    elif fault=='copy':Path(custody['copies'][0]['copy']['path']).write_text('{}')
    elif fault=='duplicate':custody['copies']*=2
    elif fault=='process':custody['process']={'path':'/foreign','sha256':'x'}
    elif fault=='source':
        value=P.checked(prepared['continuation']);value['source_sha256']='foreign';prepared['continuation']=save('foreign-source.json',value)
    else:
        value=P.checked(prepared['plan']);value['retained_warning']['job']={'path':'/foreign','sha256':'x'};prepared['plan']=save('foreign-publication.json',value)
    with pytest.raises(ValueError):P.preparation_context(prepared,tmp_path,custody)


def test_direct_native_guard_requires_authentic_full_proof(tmp_path):
    proof=P.retain(tmp_path/'proof.json',dict(status='ACTUAL_READY_FULL_OWNING_PUBLICATION_PROOF_PASSED'))
    log=dict(status='PASS',proof=proof,models_dispatched=0,publication_mutations=0)
    guard=dict(returncode=0,failure=None,log=json.dumps(log))
    outer=dict(returncode=0,stdout=json.dumps(guard))
    assert P.guarded_proof({'continuation':{}},outer)[1]==P.checked(proof)
    Path(proof['path']).write_text('{}')
    with pytest.raises(ValueError,match='changed'):P.guarded_proof({'continuation':{}},outer)


@pytest.mark.parametrize('fault',['failed','model','mutation'])
def test_direct_native_guard_refuses_unqualified_execution(tmp_path,fault):
    log=dict(status='PASS',proof={},models_dispatched=0,publication_mutations=0)
    if fault=='model':log['models_dispatched']=1
    if fault=='mutation':log['publication_mutations']=1
    guard=dict(returncode=1 if fault=='failed'else 0,failure=None,log=json.dumps(log))
    with pytest.raises(ValueError):P.guarded_proof({'continuation':{}},dict(returncode=0,stdout=json.dumps(guard)))

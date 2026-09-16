"""Completed external renewal evidence must remain explicit at publication."""
from pathlib import Path
import copy
import pytest
from scripts.fleet import source_publication as S, publish as P


@pytest.fixture
def cold(tmp_path, monkeypatch):
    h=tmp_path.resolve();ops=h/'operations';d=ops/'000';d.mkdir(parents=True)
    def file(name):
        q=h/name;q.write_text('exact fixture '+name);return P.binding(q)
    source=P.retain(h/'source.json',{'workbook':'0200'})
    config=file('config.json');driver=file('driver.py')
    job=P.retain(h/'job.json',dict(operations=str(ops),config=config,driver=driver))
    p=dict(source_plan=source,authoring_job=job,operations=str(ops),bootstrap=file('bootstrap.py'),stdlib=file('stdlib.py'),runtime=file('runtime.py'))
    plan=P.retain(h/'plan.json',p)
    monkeypatch.setattr(S,'COLD_PLAN',plan);monkeypatch.setattr(S,'COLD_SOURCE_SHA',source['sha256'])
    intent=P.retain(d/'intent.json',dict(job=job,checkpoint=file('prior.json'),seconds=240,driver=driver))
    side=dict(schema_version='fleet-0200-cold-renewal-runtime/v1',plan=plan,job=job,intent=intent,driver=driver,
        bootstrap=p['bootstrap'],stdlib=p['stdlib'],original_command=[S.COLD_PYTHON,'-B',driver['path'],'renew-child',intent['path'],intent['sha256']],
        actual_command=[S.COLD_PYTHON,'-I','-B',p['bootstrap']['path'],'renew-child',intent['path'],intent['sha256']],
        timeout_seconds=240,memory_limit_bytes=8*1024**3,task_code_changed=False,model_budget_reset=False,automatic_retry=False)
    sidecar=P.retain(d/'runtime-bootstrap.json',side)
    (d/'step.log').write_text('actual child returned')
    resource=P.retain(d/'resource.json',dict(intent=intent,returncode=0,failure=None,log=P.binding(d/'step.log')))
    result=P.retain(d/'result.json',dict(status='RETRY_READY'))
    P.retain(d/'outcome.json',dict(status='COMPLETED_OPERATION',resource=resource,result=result,automatic_retry=False))
    ready=dict(workbook='0200',job=job,config=config,runtime_continuation=plan,runtime_sidecars=[sidecar])
    return ready,source,p,side,d


def check(c):return S.cold_runtime(c[0],c[1],checked=P.checked,binding=P.binding,require=P.require)


def test_completed_external_guard_is_read_as_evidence_only(cold):
    assert check(cold)==cold[0]['runtime_sidecars']


@pytest.mark.parametrize('field,value',[('automatic_retry',True),('model_budget_reset',True),('task_code_changed',True),('memory_limit_bytes',16*1024**3),('timeout_seconds',900),('actual_command',['python','unbound']),('original_command',['python','unbound']),('plan',{}),('intent',{})])
def test_rebound_sidecar_still_must_prove_exact_execution(cold,field,value):
    ready,_,_,side,d=cold;side[field]=value
    q=d/'runtime-bootstrap.json';q.unlink();ready['runtime_sidecars']=[P.retain(q,side)]
    with pytest.raises(ValueError):check(cold)


@pytest.mark.parametrize('fault',['missing','duplicate','foreign','dependency','uncertain','omitted_intent'])
def test_no_sidecar_gap_or_uncertain_completion(cold,fault):
    ready,source,p,side,d=cold
    if fault=='missing':ready['runtime_sidecars']=[]
    elif fault=='duplicate':ready['runtime_sidecars']*=2
    elif fault=='foreign':ready['runtime_continuation']={}
    elif fault=='dependency':Path(p['bootstrap']['path']).write_text('tampered')
    elif fault=='uncertain':
        q=d/'outcome.json';q.unlink();P.retain(q,dict(status='RECONCILIATION_REQUIRED'))
    else:
        q=d.parent/'001';q.mkdir();P.retain(q/'intent.json',P.checked(side['intent']))
    with pytest.raises((ValueError,FileNotFoundError)):check(cold)


def test_cold_metadata_cannot_attach_to_another_source(cold):
    cold[1]['sha256']='0'*64
    with pytest.raises(ValueError,match='unrelated'):check(cold)


def test_closed_source_cannot_omit_external_provenance(cold):
    cold[0].pop('runtime_sidecars');cold[0].pop('runtime_continuation')
    with pytest.raises(ValueError,match='missing'):check(cold)

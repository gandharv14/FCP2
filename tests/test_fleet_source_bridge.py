from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.fleet import source_continue as S,publish as P


def bridge(tmp_path,monkeypatch):
    root=tmp_path.resolve();home=root/'continuation';home.mkdir()
    def keep(name,value):return P.retain(root/name,value)
    config=keep('config.json',dict(run_root=str(home),workbook='0006'))
    descriptor=keep('descriptor.json',{})
    checkpoint=keep('checkpoint.json',dict(status='IMPORTED',step='source',descriptor=descriptor))
    history=keep('history.json',dict(roots=[],known_ids=['0006']))
    job=keep('job.json',dict(config=config,models=False,publish=False,driver=keep('driver.json',{})))
    plan=dict(schema_version='fleet-source-native-bridge/v1',workbook='0006',source_sha256='a'*64,code_root=str(root),continuation_history=history,
        source_continuation=dict(config=config,descriptor=descriptor,checkpoint=checkpoint,receipt=keep('receipt.json',{})),
        job=job,journal=str(home/'native-bridges/first'),publisher=P.binding(P.__file__))
    bound=keep('plan.json',plan);observed={}
    monkeypatch.setattr(S,'publisher',lambda b:(P,P.checked(b)))
    monkeypatch.setattr(P,'continuation_binding',lambda p:history)
    def native(action,args,controls,directory,**kwargs):
        observed.update(action=action,args=args,controls=controls,**kwargs)
        return keep('outcome.json',dict(status='COMPLETED_OPERATION'))
    base=SimpleNamespace(SECONDS=3300,MEMORY=8*1024**3,selected=lambda b:('worker',None,None),_prior=lambda *a:(P.checked(checkpoint),'lease'),worker=SimpleNamespace(_active=lambda *a:None),run=native)
    env=(base,None,None,None,None,P.checked(config),root,'controls')
    @contextmanager
    def slot(workbook):yield (19,23)
    def successful(outcome,e):
        observed['validated_outcome']=outcome
        return checkpoint,dict(status='STEP_COMPLETE',descriptor=descriptor)
    driver=SimpleNamespace(setup=lambda j:env,fleet_slot=slot,successful=successful)
    monkeypatch.setattr(P,'load',lambda *a:driver)
    from scripts.fleet import continuation_history as H
    monkeypatch.setattr(H,'projected_survey',lambda *a:({},{}))
    for name,value in dict(WORKBOOK='0006',CONTINUATION_HISTORY_PATH=history['path'],CONTINUATION_HISTORY_SHA256=history['sha256'],MODEL_JOURNAL='obsolete').items():monkeypatch.setenv('HARBOR_FLEET_'+name,value)
    monkeypatch.delenv('HARBOR_FLEET_CONTINUATION_READ_ONLY',raising=False)
    return bound,plan,observed,driver


def test_native_step_is_nonmodel_and_owning_outcome_checked(tmp_path,monkeypatch):
    import os
    bound,plan,observed,driver=bridge(tmp_path,monkeypatch)
    result=P.checked(S.run(bound))
    assert result['status']=='NATIVE_CONTINUATION_READY' and result['model_calls']==0
    assert observed['action']=='step' and observed['dispatch'] is False and observed['guardian_lock_fds']==(19,23)
    assert observed['args']==dict(descriptor=plan['source_continuation']['descriptor'],checkpoint=plan['source_continuation']['checkpoint'],captures=None)
    assert result['outcome']==observed['validated_outcome']
    assert 'HARBOR_FLEET_MODEL_JOURNAL' not in os.environ
    with pytest.raises(ValueError,match='namespace'):S.run(bound)


def test_uncertain_native_outcome_is_not_blessed(tmp_path,monkeypatch):
    bound,plan,observed,driver=bridge(tmp_path,monkeypatch)
    def reject(*a):raise ValueError('uncertain guard')
    driver.successful=reject
    with pytest.raises(ValueError,match='uncertain guard'):S.run(bound)
    assert not (Path(plan['journal'])/'result.json').exists()
    assert (Path(plan['journal'])/'intent.json').exists()


@pytest.mark.parametrize('defect',['models','publication','history','readonly','phase'])
def test_bridge_prohibited_authority_never_steps(tmp_path,monkeypatch,defect):
    bound,plan,observed,driver=bridge(tmp_path,monkeypatch)
    if defect in ('models','publication'):
        value=P.checked(plan['job']);value['models' if defect=='models' else 'publish']=True
        plan['job']=P.retain(tmp_path/'changed-job.json',value)
        bound=P.retain(tmp_path/'changed-plan.json',plan)
    elif defect=='history':monkeypatch.setenv('HARBOR_FLEET_CONTINUATION_HISTORY_SHA256','b'*64)
    elif defect=='readonly':monkeypatch.setenv('HARBOR_FLEET_CONTINUATION_READ_ONLY','1')
    else:driver.setup(None)[0]._prior=lambda *a:(dict(status='IMPORTED',step='normalization_phase1'),'lease')
    with pytest.raises(ValueError):S.run(bound)
    assert not observed and not Path(plan['journal']).exists()

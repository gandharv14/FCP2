from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts import harbor_instruction_campaign as C
from scripts.fleet import advance_authoring_v8 as D
from test_initial_lease_continuation import initial
from test_instruction_worker import case,instruction_setup


@pytest.fixture
def original_guard(initial,monkeypatch):
    v=initial;w=v['worker'];controls=v['controls'];recipe=C.selected(C.base.read(v['first'])['descriptor'])[2]
    env=(C.base,C,None,C.worker,None,dict(code={}),w.root,controls)
    monkeypatch.setattr(D,'selected',lambda *args:(w,recipe))
    monkeypatch.setattr(D,'setup',lambda *args:env)
    directory=v['home']/'owning-guard';directory.mkdir()
    job=C.base.keep(v['home']/'job.json',{'fixture':'original guarded job'})
    intent=C.base.keep(directory/'intent.json',dict(job=job,checkpoint=v['held'],seconds=900,driver=D.binding(D.__file__)))
    monkeypatch.setattr(C,'dependency',lambda recipe,directory:C.base.keep(directory/'dependency.json',dict(fixture='original dependency report')))
    return v,env,directory,intent


def test_original_initial_renewal_cas_is_accepted_by_dispatcher_guard(original_guard):
    v,env,directory,intent=original_guard
    before=C.base.read(C.base.record(v['path']))
    D.renewal_child(intent)
    log=directory/'step.log';log.write_text('')
    resource=C.base.keep(directory/'resource.json',dict(intent=intent,returncode=0,failure=None,log=C.base.record(log)))
    outcome=C.base.keep(directory/'outcome.json',dict(status='COMPLETED_OPERATION',resource=resource,result=C.base.record(directory/'result.json'),automatic_retry=False))
    _,result=D.successful(outcome,env)
    after=C.base.read(C.base.record(v['path']))
    assert result['status']=='INSTRUCTION_READY' and result['checkpoint']is None
    assert result['next_step']=='disclosure-build' and not v['worker'].run.exists()
    assert after['revision']==before['revision']+1 and after['bindings']==before['bindings']
    assert after['lane_state']==before['lane_state']


@pytest.mark.parametrize('fault',['started','pending','different-selection'])
def test_initial_dispatcher_renewal_rejects_changed_boundary(original_guard,fault):
    v,env,directory,intent=original_guard
    if fault=='started':v['worker'].run.mkdir()
    else:
        value=C.base.read(v['held'])
        if fault=='pending':value['checkpoint']=v['first']
        else:value['selection']={}
        v['held']=C.base.keep(v['home']/'changed.json',value)
    before=v['path'].read_bytes()
    with pytest.raises(ValueError):D.renewal_context(env,v['held'])
    assert v['path'].read_bytes()==before

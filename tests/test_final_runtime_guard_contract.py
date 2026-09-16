from pathlib import Path
from types import SimpleNamespace
import importlib.util,json
import pytest

@pytest.fixture
def driver(monkeypatch):
    p=Path(__file__).resolve().parents[1]/'diagnostics/authoring-resume-20260910/runner/advance_authoring_v6.py'
    monkeypatch.syspath_prepend(str(p.parent))
    spec=importlib.util.spec_from_file_location('phase_guard_dispatcher',p)
    d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d);return d

@pytest.mark.parametrize('phase,seconds,legacy,accepted',[
 ('final-runtime',1350,False,True),('dialogue-review',3300,False,True),
 ('final-runtime',900,False,False),('dialogue-review',1350,False,False),
 ('final-runtime',3300,False,False),('final-runtime',3300,True,True),
 ('final-runtime',1350,True,False)])
def test_current_guard_contract_and_legacy_are_distinct(driver,tmp_path,phase,seconds,legacy,accepted):
    from scripts import harbor_instruction_campaign as current
    def b(name,value):
        p=tmp_path/name;p.write_text(value if isinstance(value,str) else json.dumps(value));return driver.binding(p)
    module=b('coordinator.py','fixture');prior=b('prior.json',{'schema_version':current.SCHEMA,'status':'STEP_COMPLETE','next_step':phase})
    desc=b('descriptor.json',{})
    c=SimpleNamespace(SCHEMA=current.SCHEMA,__file__=module['path'],selected=lambda x:None,_prior=lambda *a:None)
    if not legacy:c.guard_seconds=current.guard_seconds
    base=SimpleNamespace(MEMORY=8*1024**3,SECONDS=3300,encode=lambda x:x,selected=lambda x:None)
    env=(base,c,None,None,None,{},tmp_path,{})
    result=b('result.json',{'schema_version':c.SCHEMA,'status':'STEP_COMPLETE','descriptor':desc,'evidence':[prior]})
    intent=b('intent.json',{'schema_version':c.SCHEMA,'code':module,'controls':{},'memory_limit_bytes':8*1024**3,'timeout_seconds':seconds,'action':'step','arguments':{'descriptor':desc,'checkpoint':prior}})
    log=b('step.log','retained');res=b('resource.json',{'returncode':0,'failure':None,'intent':intent,'log':log});out=b('outcome.json',{'status':'COMPLETED_OPERATION','resource':res,'result':result})
    if accepted:assert driver.successful(out,env)[0]==result
    else:
        with pytest.raises(ValueError,match='guard resource'):driver.successful(out,env)

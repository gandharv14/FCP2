"""Closed coverage history adapter rejects foreign inventory and worker bindings."""
import ast,copy,sys
from pathlib import Path
from types import SimpleNamespace
import pytest
ROOT=Path(__file__).resolve().parents[1]
PRIVATE=ROOT/'diagnostics/authoring-queue-extension-20260911T185027Z/0048-warning-repair/code/scripts/batch'
def function(name,file,env):
    fn=next(n for n in ast.parse((PRIVATE/file).read_text()).body if isinstance(n,ast.FunctionDef) and n.name==name)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),str(PRIVATE/file),'exec'),env)
    return env[name]
def require(ok,message):
    if not ok:raise ValueError(message)
@pytest.mark.parametrize('fault',[None,'root','inventory','code','source','worker','repair','warning'])
def test_historical_coverage_requires_exact_old_identity(monkeypatch,fault):
    schema='harbor-disclosure-coverage-revision/v1';binding={'path':'permit','sha256':'p'};snap={'path':'snapshot','sha256':'s'}
    worker=SimpleNamespace(disclosure_revision=binding,root=Path('/old'),warning_requalification=None,disclosure_repair=None,run=Path('/worker'))
    old={'root':'/old','files':[{'relative':'a','sha256':'a-sha'}]}
    permit={'schema_version':schema,'current_root':'/old','current_inventory':{'a':'a-sha'},'current_code':{'/old/a':'a-sha'},'worker_run':'/worker','original':{'path':'source','sha256':'source-sha'}}
    before={'bindings':{'original_raw_source':copy.deepcopy(permit['original'])}};observed=[]
    if fault=='root':worker.root=Path('/foreign')
    if fault=='inventory':old['files'][0]['sha256']='changed'
    if fault=='code':permit['current_code']['/old/a']='changed'
    if fault=='source':before['bindings']['original_raw_source']['sha256']='changed'
    if fault=='worker':worker.run=Path('/foreign')
    if fault=='repair':worker.disclosure_repair={'path':'foreign'}
    if fault=='warning':worker.warning_requalification={'path':'nested'}
    import scripts.batch
    fake=SimpleNamespace(_historical_inventory=lambda w,s:{'/old/a':'a-sha'})
    monkeypatch.setitem(sys.modules,'scripts.batch.harbor_instruction_lease_renewal',fake)
    monkeypatch.setattr(scripts.batch,'harbor_instruction_lease_renewal',fake,raising=False)
    env={'read':lambda x:permit if x==binding else old,'require':require,'SCHEMA':schema,'owned':lambda b,p:observed.append((b,p))}
    run=function('validate_historical_integrity','harbor_disclosure_coverage_revision.py',env)
    if fault:
        with pytest.raises(ValueError):run(worker,before,snap)
        assert observed==[]
    else:assert run(worker,before,snap)==permit and observed==[(binding,permit)]
def test_coverage_warning_dispatch_precedes_strict_current_coverage(monkeypatch):
    import scripts.batch
    observed=[];fake=SimpleNamespace(validate=lambda w,b:observed.append((w,b)))
    monkeypatch.setitem(sys.modules,'scripts.batch.harbor_dialogue_warning_requalification',fake)
    monkeypatch.setattr(scripts.batch,'harbor_dialogue_warning_requalification',fake,raising=False)
    worker=SimpleNamespace(disclosure_revision={'path':'p'},warning_requalification={'path':'q'})
    env={'read':lambda b:{'schema_version':'harbor-disclosure-coverage-revision/v1'}}
    run=function('validate','harbor_disclosure_revision.py',env)
    assert run(worker,{'before':1})['schema_version']=='harbor-disclosure-coverage-revision/v1'
    assert observed==[(worker,{'before':1})]

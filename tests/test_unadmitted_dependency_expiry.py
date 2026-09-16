import json,time
from dataclasses import dataclass,asdict
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.fleet import unadmitted_continue as U

@dataclass(frozen=True)
class Recipe:
    requirements:tuple=({'path':'original','sha256':'a'*64},)
    imports:tuple=('openpyxl','yaml')
    max_age_ns:int=300*10**9
    minimum_disk_bytes:int=8*1024**3
    maximum_workbooks:int=4
    maximum_models:int=2

@pytest.mark.parametrize('age',[301,900,1800])
def test_only_observation_lifetime_changes(age):
    r=Recipe();p=dict(old_max_age_ns=300*10**9,new_max_age_ns=age*10**9)
    out=U.renewed_dependencies(r,p)
    assert asdict(out)==dict(asdict(r),max_age_ns=age*10**9)
    assert r.max_age_ns==300*10**9

@pytest.mark.parametrize('old,new',[(300,300),(300,1801),(299,900),(300,True),(300,float('inf'))])
def test_refuses_unbounded_or_changed_prior_age(old,new):
    with pytest.raises(ValueError):U.renewed_dependencies(Recipe(),dict(old_max_age_ns=old*10**9,new_max_age_ns=new*10**9 if type(new)is int else new))

def fixture(tmp_path,monkeypatch):
    def save(path,v):path.write_text(json.dumps(v));return U.binding(path)
    auth=Path(__file__).parents[1]/'diagnostics/bulk-production/authorization-20260913.md'
    guard=tmp_path/'admission-001';guard.mkdir();intent=save(guard/'intent.json',dict(action='admit'))
    (guard/'step.log').write_text('Traceback\nscripts.batch.harbor_admission.AdmissionError: dependency observation expired or future dated\n')
    r=save(guard/'resource.json',dict(returncode=1,failure=None,intent=intent,log=U.binding(guard/'step.log')))
    save(guard/'outcome.json',dict(status='RECONCILIATION_REQUIRED',resource=r,automatic_retry=False,source_invalidity_claimed=False,terminal_verdict=None))
    p=dict(workbook='0215',source={'path':'source','sha256':'b'*64},original_plan={'path':'plan','sha256':'c'*64})
    q=dict(schema_version='fleet-admission-dependency-renewal/v1',**p,guard=r,authorization=U.binding(auth),old_max_age_ns=300*10**9,new_max_age_ns=1800*10**9,created_at_ns=time.time_ns()-10**9,expires_at_ns=time.time_ns()+3600*10**9)
    return p,q,guard,SimpleNamespace(bootstrap=SimpleNamespace(policy=SimpleNamespace(dependencies=Recipe()))),save

@pytest.mark.parametrize('fault',[None,'foreign_source','future','expired','extra','changed_log','wrong_guard','changed_terminal'])
def test_only_exact_sealed_pretracker_expiry_selects_renewal(tmp_path,monkeypatch,fault):
    p,q,guard,intake,save=fixture(tmp_path,monkeypatch)
    if fault=='foreign_source':q['source']=dict(q['source'],sha256='d'*64)
    if fault=='future':q['created_at_ns']=time.time_ns()+10**9
    if fault=='expired':q['expires_at_ns']=time.time_ns()-10**8
    if fault=='extra':q['skip_source']=True
    if fault=='changed_log':(guard/'step.log').write_text('different failure')
    if fault=='wrong_guard':q['guard']=q['original_plan']
    if fault=='changed_terminal':
        v=json.loads((guard/'outcome.json').read_bytes());v['terminal_verdict']='invalid';save(guard/'outcome.json',v)
    p['dependency_expiry']=save(tmp_path/'policy.json',q)
    if fault:
        with pytest.raises((ValueError,FileNotFoundError)):U.dependency_expiry(p,intake,guard)
    else:assert U.dependency_expiry(p,intake,guard)==q

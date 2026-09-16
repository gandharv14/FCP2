from dataclasses import dataclass
from types import SimpleNamespace as NS
import sys
import pytest
from scripts.fleet import review_takeover_lease as T

@dataclass
class Lease:
    owner:str;token:str;revision:int;sha256:str

@pytest.fixture
def case(monkeypatch):
    def b(path):return {'path':path,'sha256':path}
    source=b('/source');golden=b('/golden');old=Lease('old','old-token',4,'/before')
    before=dict(revision=4,leases={'task_agent':{'owner':'old','token':'old-token'}},bindings={'original_raw_source':source,'source_file':golden})
    after=dict(revision=5,leases={'task_agent':{'owner':'new','token':'new-token'}},bindings=dict(before['bindings']))
    selected=dict(helper=b(T.__file__),profile=b('/profile'),runtime=b('/runtime'),completion=b('/complete'))
    p=dict(workbook='w',result=b('/rejection'),outcome=b('/guard'),owner='new',tracker={'path':'/tracker','sha256':'/after'},source=source,golden=golden)
    sel=dict(checkpoint=p['result'],guard=b('/resource'),workbook='w',owner='new',tracker={'path':'/tracker','sha256':'/before'})
    profile=dict(schema_version='harbor-allowance-takeover-direct/v1',original_snapshot=b('/snapshot'),shared={k:b('/'+k)for k in('lifecycle','takeover','allowance','stdlib')},allowance=b('/authority'),selection=sel)
    stop=dict(selection=sel,before=b('/before'));complete=dict(stopped_owner_proof=b('/stop'),after=b('/after'))
    rows={'/profile':profile,'/complete':complete,'/stop':stop,'/guard':{'resource':b('/resource')},'/tracker':after};seen=[]
    monkeypatch.setattr(T,'read',lambda row:rows[row['path']]);monkeypatch.setattr(T,'binding',lambda path:p['tracker']if path=='/tracker'else b(path))
    native=NS(_selection=lambda s:s,_selection_value=lambda s:s,validate_completion=lambda row:seen.append(row)or(before,after,Lease('new','new-token',5,'/after')))
    def load(name,row):
        if row==profile['shared']['takeover']:return native
        value=NS();sys.modules[name]=value;return value
    monkeypatch.setattr(T,'module',load)
    return p,selected,old,before,after,profile,stop,seen,native

def test_exact_completed_takeover_returns_new_lease_after_original_validation_and_restores_modules(case):
    p,q,old,*_=case;prior={k:v for k,v in sys.modules.items()if k in ('scripts.batch.harbor_tracker_lifecycle','scripts.batch.harbor_continuation_allowance')}
    value,source,new=T.project(p,q,'original-result','original-selection',old)
    assert (value,source,new)==('original-result','original-selection',Lease('new','new-token',5,'/after'))
    assert case[-2]==[q['completion']]
    assert all(sys.modules.get(k)is v for k,v in prior.items())

@pytest.mark.parametrize('fault',['guard','checkpoint','owner','before','source','golden','validator'])
def test_foreign_or_unvalidated_takeover_cannot_supply_review_lease(case,fault):
    p,q,old,before,after,profile,stop,seen,native=case
    if fault=='guard':profile['selection']['guard']={}
    elif fault=='checkpoint':profile['selection']['checkpoint']={}
    elif fault=='owner':p['owner']='foreign'
    elif fault=='before':old.sha256='different'
    elif fault in('source','golden'):p[fault]={}
    else:
        def fail(row):raise ValueError('original completion rejected')
        native.validate_completion=fail
    with pytest.raises(ValueError):T.project(p,q,'original-result','original-selection',old)

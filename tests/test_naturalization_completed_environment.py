import types
import pytest
from scripts.fleet import naturalization_continue as N

def fixture(monkeypatch,defect=None):
    prior=dict(entry={'path':'/helper','sha256':'h'},original_worker_job={'path':'/worker','sha256':'w'},original_job={'path':'/job','sha256':'j'},source={'path':'/source','sha256':'s'},workbook='0198',package_recovery={'path':'/policy','sha256':'p'},home='/run/package')
    row={'path':'/plan','sha256':'x'};out={'path':'/run/package/bridge/outcome.json','sha256':'o'}
    p=dict(original_job=prior['original_job'],source=prior['source'],workbook='0198',history_context=dict(helper=prior['entry'],original_worker_job=prior['original_worker_job']),completed_environment_context=dict(plan=row,outcome=out))
    marker=dict(plan=row,entry=prior['entry'],automatic_retry=False)
    def old(*args):raise RuntimeError('old rejects passive links')
    def selected(*args):return 'selected'
    publication=types.ModuleType('test_nat_publication');publication._reject_external_package_artifacts=old
    env=(types.SimpleNamespace(worker=types.SimpleNamespace(environment=types.SimpleNamespace(readiness=types.SimpleNamespace(publication=publication)))),)
    monkeypatch.setitem(N.sys.modules,publication.__name__,publication)
    release=types.SimpleNamespace(_reject_external_package_artifacts=selected)
    helper=types.SimpleNamespace(package_policy=lambda prior,env:dict(release={'path':'/release','sha256':'r'}))
    monkeypatch.setattr(N,'module',lambda name,row:release if name.endswith('_release')else helper)
    monkeypatch.setattr(N,'binding',lambda path:dict(path=str(path),sha256='bound'))
    monkeypatch.setattr(N,'read',lambda binding:prior if binding==row else marker)
    def successful(*args):
        assert publication._reject_external_package_artifacts is selected
        return N.binding('/run/package/bridge/result.json'),dict(status='STEP_COMPLETE',step='environment' if defect!='incomplete'else 'source')
    D=types.SimpleNamespace(successful=successful)
    if defect=='source':prior['source']={'path':'/other','sha256':'z'}
    if defect=='helper':prior['entry']={'path':'/other','sha256':'z'}
    if defect=='foreign_outcome':out['path']='/another/outcome.json'
    if defect=='extra_context':p['completed_environment_context']['bypass']=True
    if defect=='intent':marker['automatic_retry']=True
    return p,D,env,publication,old,selected

def test_carries_exact_completed_policy_without_reexecuting_environment(monkeypatch):
    p,D,env,m,old,selected=fixture(monkeypatch)
    N.select_completed_environment(p,{},D,env)
    assert m._reject_external_package_artifacts is selected

@pytest.mark.parametrize('defect',['source','helper','foreign_outcome','extra_context','intent','incomplete'])
def test_foreign_or_incomplete_policy_rejected_and_validator_restored(monkeypatch,defect):
    p,D,env,m,old,selected=fixture(monkeypatch,defect)
    with pytest.raises(ValueError):N.select_completed_environment(p,{},D,env)
    assert m._reject_external_package_artifacts is old

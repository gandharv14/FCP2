from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts import harbor_instruction_campaign as C
from scripts.batch import harbor_tracker_lifecycle as L
from scripts.batch.harbor_admission import read,record
from scripts.batch.harbor_workbook_bootstrap import Lease
from scripts.fleet import initial_lease_renewal as F
from test_harbor_tracker_lifecycle import created,acquire
from test_instruction_worker import case,instruction_setup

@pytest.fixture
def initial(case,tmp_path,monkeypatch):
    worker,selection,*_=case
    root=tmp_path/'native';root.mkdir();path,start=created(root);value=acquire(path,start)
    current=value['tracker']['leases']['task_agent'];lease=Lease(current['owner'],current['token'],value['revision'],value['sha256'])
    worker=replace(worker,context=replace(worker.context,tracker_root=root))
    def active(w,s,l):
        value=read(record(path));native=value['leases']['task_agent']
        assert l.owner==native['owner'] and l.token==native['token'] and l.revision==value['revision'] and l.sha256==record(path)['sha256']
        return None,value,record(path)
    monkeypatch.setattr(C.worker,'active',active)
    controls=C.base.Controls(tmp_path/'workbook.lock')
    recipe=C.base.admission.DependencyRecipe(record(Path(C.base.sys.executable).resolve()),(),(),False,4,2)
    environment=C.base.keep(tmp_path/'environment.json',{'fixture':'completed environment'})
    descriptor=C.base.keep(tmp_path/'initial-descriptor.json',dict(schema_version=C.SCHEMA,worker=C.worker_encode(worker),
        controls=C.base.encode(controls),dependencies=C.base.encode(recipe),origin=[environment],code=record(C.__file__),
        initial=dict(selection=C.codec.encode(selection),lease=C.base.encode(lease))))
    first=C.base.keep(tmp_path/'initial.json',C._result('INSTRUCTION_READY',descriptor,selection,lease,
        next_step='disclosure-build',evidence=[environment]))
    held=C.base.keep(tmp_path/'held.json',C._result('LEASE_RENEWAL_REQUIRED',descriptor,selection,lease,
        next_step='disclosure-build',evidence=[first]))
    source=SimpleNamespace(bootstrap=SimpleNamespace(workbook='0245',policy=SimpleNamespace(tracker_root=root)),campaign=worker.campaign)
    monkeypatch.setattr(C.base,'selected',lambda row:(source,lease,controls) if row==environment else pytest.fail('foreign environment'))
    monkeypatch.setattr(C.base.worker,'_active',lambda s,l:active(worker,selection,l)[:2])
    monkeypatch.setattr(C,'check_completed_gate',lambda *args:None)
    original=L.lease_operation
    monkeypatch.setattr(L,'lease_operation',lambda *args,**kwargs:original(*args,**kwargs,clock=lambda:30))
    monkeypatch.setattr(C.base.time,'time_ns',lambda:30)
    return dict(first=first,held=held,controls=controls,worker=worker,selection=selection,lease=lease,path=path,home=tmp_path)

def renew(v):
    return F.renew(C.base,C,C.worker,initial=v['first'],held=v['held'],controls=v['controls'],
        directory=v['home']/'renewal',authority={'fixture':'root guard'},custody=lambda:None)

def test_actual_lifecycle_and_original_initial_reader_preserve_history(initial):
    v=initial;before=read(record(v['path']));old=Path(v['first']['path']).read_bytes()
    result=renew(v);value=read(result);_,selection,lease=C._prior(result,value['descriptor'])
    after=read(record(v['path']))
    assert selection==v['selection'] and lease.owner==v['lease'].owner and lease.token==v['lease'].token
    assert lease.revision==v['lease'].revision+1 and len(after['handoffs'])==len(before['handoffs'])+1
    assert after['lane_state']==before['lane_state'] and after['bindings']==before['bindings']
    assert value['status']=='INSTRUCTION_READY' and value['checkpoint'] is None
    assert Path(v['first']['path']).read_bytes()==old and not v['worker'].run.exists()
    with pytest.raises(ValueError,match='already attempted'):renew(v)

def test_uncertain_cas_is_retained_never_repeated(initial,monkeypatch):
    v=initial;before=read(record(v['path']));keep=C.base.keep
    def fail(path,value):
        if Path(path).name=='lease-renewal.json':raise RuntimeError('lost after actual CAS')
        return keep(path,value)
    monkeypatch.setattr(C.base,'keep',fail)
    with pytest.raises(RuntimeError,match='actual CAS'):renew(v)
    assert (v['home']/'renewal/intent.json').is_file()
    assert not (v['home']/'renewal/result.json').exists()
    after=v['path'].read_bytes()
    assert read(record(v['path']))['revision']==before['revision']+1
    with pytest.raises(ValueError,match='already attempted'):renew(v)
    assert v['path'].read_bytes()==after

@pytest.mark.parametrize('fault',['worker-started','foreign-initial','completed-checkpoint','gate-failed','old-lease'])
def test_invalid_boundary_never_reaches_lease_cas(initial,monkeypatch,fault):
    v=initial
    if fault=='worker-started':v['worker'].run.mkdir()
    elif fault=='gate-failed':monkeypatch.setattr(C,'check_completed_gate',lambda *args:(_ for _ in ()).throw(ValueError('failed financial gate')))
    else:
        value=read(v['held'])
        if fault=='foreign-initial':value['evidence']=[]
        elif fault=='completed-checkpoint':value['checkpoint']=v['first']
        else:value['lease']=C.base.encode(replace(v['lease'],revision=0))
        v['held']=C.base.keep(v['home']/'changed-held.json',value)
    before=v['path'].read_bytes()
    monkeypatch.setattr(C.base,'_lease',lambda *args:pytest.fail('invalid boundary reached CAS'))
    with pytest.raises(ValueError):renew(v)
    assert v['path'].read_bytes()==before and not (v['home']/'renewal').exists()

"""Owning controller boundaries preserve real review calls and stop sealed failures."""
import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from scripts.batch import harbor_disclosure_partition_continuation as P
from scripts.batch.harbor_admission import record
from test_disclosure_partition_review import reviewed,bind_json as original_bind_json,ROOT

def bind_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    return original_bind_json(path,value)

@pytest.fixture
def boundary(reviewed,monkeypatch):
    plan,p,projection,args,policy,calls=reviewed
    worker=NS(run=Path(p['worker_run']),root=ROOT)
    permit=dict(review_plan=plan)
    monkeypatch.setattr(P,'permit',lambda w:(record(worker.run/'permit.json'),permit))
    def seal(index,call=None):
        out=P.home(worker)/'batches'/f'{index:04d}';out.mkdir(parents=True)
        return bind_json(out/'complete.json',dict(index=index,plan=plan,call=asdict(call or calls[index])))
    return worker,permit,calls,seal


def test_completed_prefix_validates_real_independent_scope_and_coverage(boundary):
    w,p,calls,seal=boundary
    seal(0);got,total=P.completed_calls(w,count=1)
    assert got==calls[:1] and total==len(calls)>1
    with pytest.raises(ValueError,match='boundary changed'):P.completed_calls(w,count=0)

@pytest.mark.parametrize('action',['renew','dispatch'])
def test_unsealed_next_batch_blocks_renewal_and_dispatch(boundary,monkeypatch,tmp_path,action):
    w,p,calls,seal=boundary;seal(0)
    (P.home(w)/'batches/0001').mkdir()
    monkeypatch.setattr(P,'validate_lease',lambda *a:(p,{},{}))
    mutations=[]
    monkeypatch.setattr(P.life,'lease_operation',lambda *a,**k:mutations.append('CAS'))
    monkeypatch.setattr(P.W.executor,'execute',lambda *a,**k:mutations.append('model'))
    with pytest.raises(ValueError,match='unsealed partition'):
        if action=='renew':P.renew_boundary(w,'lease',tmp_path/'unused')
        else:P.execute_batch(w,'lease',1,credential='fixture-not-a-secret',directory=tmp_path/'unused')
    assert mutations==[] and not (tmp_path/'unused').exists()

@pytest.mark.parametrize('fault',['gap','foreign-plan','foreign-index','substituted-call','tampered-response'])
def test_completed_prefix_rejects_history_substitution(boundary,fault):
    w,p,calls,seal=boundary
    if fault=='gap':seal(1)
    else:
        binding=seal(0,calls[1] if fault=='substituted-call' else None)
        if fault=='tampered-response':Path(calls[0].response['path']).write_text('{}')
        elif fault!='substituted-call':
            value=P.read(binding)
            if fault=='foreign-plan':value['plan']={'path':'/foreign','sha256':'0'*64}
            else:value['index']=1
            Path(binding['path']).write_text(json.dumps(value))
    with pytest.raises((ValueError,OSError)):P.completed_calls(w)


def test_already_completed_index_never_replays(boundary,monkeypatch,tmp_path):
    w,p,calls,seal=boundary;seal(0)
    monkeypatch.setattr(P,'validate_lease',lambda *a:(p,{},{}))
    def forbidden(*a,**k):raise AssertionError('must not renew or dispatch a consumed index')
    monkeypatch.setattr(P,'renew_boundary',forbidden)
    with pytest.raises(ValueError,match='boundary changed'):
        P.execute_batch(w,'lease',0,credential='fixture',directory=tmp_path/'unused')


def test_finalize_requires_every_independent_call_before_renewing(boundary,monkeypatch):
    w,p,calls,seal=boundary;seal(0)
    monkeypatch.setattr(P,'validate_lease',lambda *a:(p,{},{}))
    def forbidden(*a,**k):raise AssertionError('cannot renew final lease before coverage')
    monkeypatch.setattr(P,'renew_boundary',forbidden)
    with pytest.raises(ValueError,match='complete review coverage'):P.finalize(w,'lease')


def test_completion_rejects_renewed_lease_substitution(boundary):
    w,p,calls,seal=boundary
    p.update(selection={'selected':'exact'},predecessor={'path':'prior','sha256':'0'*64})
    permit=record(w.run/'permit.json')
    renewal=bind_json(P.home(w)/'renewal.json',dict(lease={'revision':9}))
    bind_json(P.home(w)/'complete.json',dict(schema_version=P.SCHEMA,permit=permit,phase='disclosure-review',
        previous=p['predecessor'],selection=p['selection'],lease={'revision':10},aggregate={},renewal=renewal))
    with pytest.raises(ValueError,match='final renewal differs'):P.completion(w)

@pytest.mark.parametrize('fault',['permit','count','foreign-path'])
def test_final_renewal_requires_owning_permit_and_complete_boundary(boundary,fault):
    w,p,calls,seal=boundary
    p.update(selection={'selected':'exact'},predecessor={'path':'prior','sha256':'0'*64})
    permit=record(w.run/'permit.json')
    data=dict(schema_version=P.SCHEMA,permit=permit,completed_batches=len(calls),lease={'revision':10})
    if fault=='permit':data['permit']={'path':'/foreign','sha256':'0'*64}
    elif fault=='count':data['completed_batches']=0
    path=P.home(w)/('foreign-renewal.json' if fault=='foreign-path' else 'final-lease/lease-renewal.json')
    renewal=bind_json(path,data)
    bind_json(P.home(w)/'complete.json',dict(schema_version=P.SCHEMA,permit=permit,phase='disclosure-review',
        previous=p['predecessor'],selection=p['selection'],lease={'revision':10},aggregate={},renewal=renewal))
    with pytest.raises(ValueError):P.completion(w)

@pytest.fixture
def authority(tmp_path,monkeypatch):
    worker=NS(run=tmp_path/'worker',root=ROOT);worker.run.mkdir()
    initial=bind_json(worker.run/'initial.json',{'genuine':'initial'})
    evidence=bind_json(tmp_path/'evidence.json',{'retained':'history'})
    files={'approved':'hash'}
    value=dict(schema_version=P.SCHEMA,worker_run=str(worker.run),root=str(ROOT),implementation=record(P.__file__),
        global_round=1,global_limit=1,initial=initial,old_snapshot={},current_files=files,evidence=[evidence])
    def save():
        binding=bind_json(P.home(worker)/'permit.json',value)
        bind_json(P.marker(worker),dict(schema_version=P.SCHEMA,permit=binding))
        return binding
    save();monkeypatch.setattr(P,'profile',lambda *a:(ROOT,files))
    return worker,value,save,evidence

def test_current_authority_accepts_exact_retained_inventory(authority):
    w,v,save,evidence=authority
    assert P.permit(w)[1]==v

@pytest.mark.parametrize('fault',['root','code','limit','initial','inventory','evidence','marker'])
def test_current_authority_rejects_code_and_history_tampering(authority,fault):
    w,v,save,evidence=authority
    if fault=='root':v['root']='/foreign'
    elif fault=='code':v['implementation']={'path':'/foreign','sha256':'0'*64}
    elif fault=='limit':v['global_limit']=2
    elif fault=='initial':v['initial']={'path':'/foreign','sha256':'0'*64}
    elif fault=='inventory':v['current_files']={}
    elif fault=='evidence':Path(evidence['path']).write_text('{}')
    save()
    if fault=='marker':P.marker(w).write_text('{}')
    with pytest.raises((ValueError,KeyError,OSError)):P.permit(w)

def test_finalize_retains_every_real_call_and_returned_new_lease(boundary,monkeypatch):
    from scripts.batch.harbor_workbook_bootstrap import Lease
    w,p,calls,seal=boundary
    for i in range(len(calls)):seal(i)
    p.update(selection={'selected':'exact'},predecessor={'path':'prior','sha256':'0'*64})
    monkeypatch.setattr(P,'validate_lease',lambda *a:(p,{},{}))
    renewed=Lease('same-owner','same-token',12,'a'*64);observed=[]
    def renewal(worker,old,directory,*,allow_complete=False):
        observed.append((worker,old,directory,allow_complete))
        receipt=bind_json(directory/'lease-renewal.json',dict(schema_version=P.SCHEMA,
            permit=record(w.run/'permit.json'),completed_batches=len(calls),lease=asdict(renewed)))
        return renewed,receipt
    monkeypatch.setattr(P,'renew_boundary',renewal)
    complete=P.finalize(w,'original-lease');value=P.read(complete)
    assert value['lease']==asdict(renewed) and value['previous']==p['predecessor']
    assert observed==[(w,'original-lease',P.home(w)/'final-lease',True)]
    aggregate=P.read(value['aggregate'])
    assert len(aggregate['calls'])==len(calls)
    assert aggregate['calls']==[asdict(c) for c in calls]

@pytest.mark.parametrize('fault',[None,'result','after'])
def test_final_completion_requires_real_same_owner_cas_chain(boundary,tmp_path,fault):
    from test_harbor_tracker_lifecycle import created,acquire,kw
    from scripts.batch.harbor_workbook_bootstrap import Lease
    w,p,calls,seal=boundary
    for i in range(len(calls)):seal(i)
    p.update(selection={'selected':'exact'},predecessor={'path':'prior','sha256':'0'*64})
    directory=P.home(w)/'final-lease';directory.mkdir()
    trackdir=tmp_path/'tracker';trackdir.mkdir();path,start=created(trackdir);a=acquire(path,start)
    old=a['tracker']['leases']['task_agent'];previous=Lease(old['owner'],old['token'],a['revision'],a['sha256'])
    (directory/'lease-before.json').write_bytes(path.read_bytes())
    result=P.life.lease_operation(path,operation='renew',token=old['token'],duration_ns=200,**kw(a,clock=lambda:30))
    lease=Lease(old['owner'],old['token'],result['revision'],result['sha256'])
    (directory/'lease-after.json').write_bytes(path.read_bytes())
    data=dict(schema_version=P.SCHEMA,permit=record(w.run/'permit.json'),completed_batches=len(calls),
        before=record(directory/'lease-before.json'),after=record(directory/'lease-after.json'),previous=asdict(previous),lease=asdict(lease),result=result)
    if fault=='result':data['result']={**result,'revision':999}
    if fault=='after':
        (directory/'lease-after.json').write_bytes((directory/'lease-before.json').read_bytes())
        data['after']=record(directory/'lease-after.json')
    renewal=bind_json(directory/'lease-renewal.json',data)
    complete=bind_json(P.home(w)/'complete.json',dict(schema_version=P.SCHEMA,permit=data['permit'],phase='disclosure-review',
        previous=p['predecessor'],selection=p['selection'],lease=asdict(lease),aggregate={},renewal=renewal))
    if fault:
        with pytest.raises(ValueError):P.completion(w)
    else:assert P.completion(w)[0]==complete

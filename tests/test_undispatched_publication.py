import ast
import copy
import json
from pathlib import Path
import pytest
from scripts.fleet import undispatched_publication as U,undispatched_publication_child as A
from scripts.fleet.build_undispatched_publication import build


def test_successor_preserves_unaffected_publisher_functions(tmp_path):
    original=Path('scripts/fleet/publish.py').absolute();bound=build(tmp_path/'frozen',original)
    manifest=A.checked(bound);text=Path(manifest['publisher']['path']).read_text();compile(text,'selected','exec')
    before={n.name:ast.dump(n,include_attributes=False)for n in ast.parse(original.read_text()).body if isinstance(n,ast.FunctionDef)}
    after={n.name:ast.dump(n,include_attributes=False)for n in ast.parse(text).body if isinstance(n,ast.FunctionDef)}
    changed={k for k in before if before[k]!=after[k]}
    assert changed=={'history_binding','publication_environment','remote_child'}
    assert all(before[k]==after[k]for k in ('controlled','collect','pack','publication_only','main'))
    assert 'owning-publication' in text and 'driver.verify(job, env, verification_dir)'in text
    for row in manifest['dependencies'].values():assert A.binding(row['path'])==row
    with pytest.raises(FileExistsError):build(tmp_path/'frozen',original)


@pytest.fixture
def selected():
    # Explicit metadata fixture: the owning prefix/native runtime remains a
    # separate real validation and no fixture authorizes publication.
    db={};counter=iter(range(100))
    def put(value,name=None):
        key=name or str(next(counter));row=dict(path='/fixture/'+key,sha256=key);db[row['path']]=copy.deepcopy(value);return row
    driver=dict(path='/fixture/tail.py',sha256=U.TAIL_SHA)
    p=dict(schema_version='fleet-undispatched-normalization-authoring/v1',workbook='0173',source_sha256='source',version='version',home='/fixture/home')
    plan=put(p);intent=put({});adapter=put({});producer=put({});base=put({})
    sidecar=put(dict(schema_version='fleet-declared-owning-child-adapter/v1',reader_plan=plan,child_wrapper=driver,
        original_snapshot_unchanged=True,semantic_budget_reset=False,automatic_retry=False,adapter=adapter,
        producer_code=producer,base_code=base,intent=intent,reader_evidence=[]))
    config=put({});job=put(dict(config=config));outcome=put({});checkpoint=put({})
    ready=put(dict(status='PUBLICATION_READY',workbook='0173',plan=plan,job=job,config=config,outcome=outcome,
        checkpoint=checkpoint,runtime_adapters=[sidecar],publication_performed=False,automatic_retry=False,
        semantic_budget_reset=False),'home/result.json')
    outer=dict(normalization_authoring=dict(plan=plan,driver=driver),workbook='0173',source_sha256='source',code_root='/fixture/version',ready=ready)
    rows={row['path']:row for row in [plan,intent,adapter,producer,base,sidecar,config,job,outcome,checkpoint,ready,driver]}
    return outer,db,rows,dict(checked=lambda row:copy.deepcopy(db[row['path']]),binding=lambda path:rows[path],require=A.require)


def test_actual_completion_schema_is_required(selected):
    p,db,rows,kwargs=selected
    assert U.history_binding(p,**kwargs)==p['normalization_authoring']['plan']


@pytest.mark.parametrize('change',['source','version','workbook','notready','reset','missing-sidecar','foreign-reader','mixed-history','wrong-driver','wrong-config'])
def test_closed_completed_reader_rejects_incomplete_or_detached_evidence(selected,change):
    p,db,rows,kwargs=selected;r=db[p['ready']['path']]
    if change in ('source','version','workbook'):p[{'source':'source_sha256','version':'code_root','workbook':'workbook'}[change]]='different'
    elif change=='notready':r['status']='NORMALIZATION_PHASE1_READY'
    elif change=='reset':r['semantic_budget_reset']=True
    elif change=='missing-sidecar':r['runtime_adapters']=[]
    elif change=='foreign-reader':db[r['runtime_adapters'][0]['path']]['reader_plan']={}
    elif change=='mixed-history':p['history']={}
    elif change=='wrong-driver':p['normalization_authoring']['driver']['sha256']='wrong'
    else:db[r['job']['path']]['config']={}
    with pytest.raises(ValueError):U.history_binding(p,**kwargs)


def test_publication_context_checks_live_identity_then_blocks_all_model_and_fresh_interfaces(selected,monkeypatch):
    from types import SimpleNamespace
    import xl_transport
    from scripts.batch import harbor_model_executor as E
    from scripts.fleet import coordinator
    plan,db,rows,kwargs=selected
    checked=kwargs['checked'];calls=[]
    class Client:
        def __init__(self,*args):pass
        def call(self,action,**kw):calls.append((action,kw));return {'fixture':True}
    monkeypatch.setattr(coordinator,'Client',Client)
    for key,value in dict(PUBLICATION_ONLY='1',WORKBOOK='0173',OWNER='publication-fixture',CLAIM_TOKEN='fixture-only',FENCE='4',URL='fixture',TOKEN_FILE='fixture').items():
        monkeypatch.setenv('HARBOR_FLEET_'+key,value)
    monkeypatch.delenv('HARBOR_FLEET_MODEL_JOURNAL',raising=False);monkeypatch.delenv('HARBOR_FLEET_CONTINUATION_READ_ONLY',raising=False)
    def operation(*a,**k):raise AssertionError('fixture operation must not execute')
    bootstrap=SimpleNamespace(prepare=operation,admit=operation)
    base=SimpleNamespace(run=operation,_admit=operation,worker=SimpleNamespace(bootstrap=bootstrap))
    instruction=SimpleNamespace(run=operation)
    partition=SimpleNamespace(dispatch=operation,E=SimpleNamespace(execute=operation,_open=operation))
    recovery=SimpleNamespace(execute=operation,_execute=operation)
    env=(base,instruction,None,None,None,None,Path('/fixture/version'))
    context=(checked(plan['normalization_authoring']['plan']),None,None,None,env,None,partition,recovery,None,None,None,None)
    tail=SimpleNamespace(load=lambda row:context,run=operation,active=operation)
    monkeypatch.setattr(U,'load',lambda *a,**k:tail)
    observations=[]
    def status(*args,**kw):observations.append((args,kw))
    previous={(id(m),name):(m,getattr(m,name))for m,names in [(E,('execute','_open')),(xl_transport,('read_response','_read_response','model_slot'))]for name in names}
    try:
        assert U.context({'path':'fixture','sha256':'fixture'},plan,**kwargs,status_validator=status)==context
        assert [x[0]for x in calls]==['status','heartbeat']
        assert observations[0][0][4]=='source'
        for module,names in ((E,('execute','_open')),(xl_transport,('read_response','_read_response','model_slot')),
            (partition,('dispatch',)),(partition.E,('execute','_open')),(recovery,('execute','_execute')),
            (bootstrap,('prepare','admit')),(base,('run','_admit')),(instruction,('run',))):
            for name in names:
                with pytest.raises(RuntimeError,match='publication permits no'):getattr(module,name)()
    finally:
        for (_,name),(m,value)in previous.items():setattr(m,name,value)

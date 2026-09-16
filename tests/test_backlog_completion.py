from pathlib import Path
import json,shutil
import pytest
from scripts.fleet import backlog_completion as M

ROOT=Path(__file__).resolve().parents[1]
ORIGINAL=ROOT/'diagnostics/vm-fleet-20260912/control/source-pool-opaque-003/backlog-enrollment-001/enrollment-once-001'

def save(p,v):p.write_text(json.dumps(v));return M.binding(p)

@pytest.fixture
def actual(tmp_path):
    if not ORIGINAL.exists():pytest.skip('retained native enrollment fixture absent')
    home=tmp_path/'original';shutil.copytree(ORIGINAL,home)
    def plan():
        return save(tmp_path/'plan.json',dict(schema_version=M.SCHEMA,helper=M.binding(Path(M.__file__)),result=M.binding(home/'result.json'),journal_inventory=save(tmp_path/'inventory.json',M.inventory(home))))
    return home,plan

def test_actual_partial_enrollment_authenticates_four_heads_and_only_four_pending(actual):
    _,plan=actual;data=M.evidence(plan())
    assert set(data[-3])=={'hhu-server-2','hhu-server-5','hhu-server-6','hhu-server-8'}
    assert data[-2]=={'hhu-server','hhu-server-3','hhu-server-4','hhu-server-7'}
    assert len(data[-1])==11

@pytest.mark.parametrize('kind',['uncertain','timeout','nonempty_failure','unknown_error','missing_event','wrong_source','wrong_recorded_plan','changed_head_response','pending_intent','partial_events','wrong_host_intent'])
def test_resealed_changed_authority_or_attempt_refused(actual,kind):
    home,plan=actual
    if kind in {'uncertain','timeout'}:
        p=home/'hhu-server-transport/transport.json';v=json.loads(p.read_text());v['uncertain']=True if kind=='uncertain'else False;v['returncode']=None if kind=='timeout'else 1;save(p,v)
    if kind=='unknown_error':(home/'hhu-server-transport/stderr').write_text('RuntimeError: different failure')
    if kind=='nonempty_failure':
        p=home/'result.json';v=json.loads(p.read_text());v['hosts'][-1]['host']='hhu-server-3';save(p,v)
    if kind in {'missing_event','partial_events','wrong_source','wrong_recorded_plan'}:
        p=home/'events.jsonl';v=[json.loads(x)for x in p.read_text().splitlines()]
        if kind=='missing_event':v.pop()
        elif kind=='partial_events':v[1]['state']='INTENT'
        elif kind=='wrong_source':v[3]['result']['source_sha256']='0'*64
        else:v[3]['result']['reason']='{}'
        p.write_text(''.join(json.dumps(x)+'\n'for x in v))
    if kind=='changed_head_response':
        p=home/'hhu-server-2-transport/stdout';v=json.loads(p.read_text());v['head']['sha256']='0'*64;save(p,v)
    if kind=='pending_intent':save(home/'hhu-server-3-intent.json',{})
    if kind=='wrong_host_intent':
        p=home/'hhu-server-2-intent.json';v=json.loads(p.read_text());v['selection']['expected']={};save(p,v)
    with pytest.raises((ValueError,KeyError)):M.evidence(plan())

def test_original_inventory_cannot_change_after_selection(actual):
    home,plan=actual;row=plan();(home/'hhu-server-transport/stderr').write_text('changed')
    with pytest.raises(ValueError,match='journal changed'):M.evidence(row)

def test_remaining_execution_has_no_enrollment_and_preserves_committed_heads(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from scripts.fleet import source_pool_control as S
    home=tmp_path/'once';batch=save(tmp_path/'batch.json',[{}]);empty=save(tmp_path/'empty.json',[]);calls=[]
    E=SimpleNamespace(native=lambda selection,*args,**kwargs:(calls.append(selection['id'])or dict(status='BACKLOG_APPENDED')))
    selected={'pending':dict(id='pending',batch=batch),'empty':dict(id='empty',batch=empty),'done':dict(id='done',batch=batch)}
    data=({},E,S,{},dict(supervisor={}),{},dict(database=str(tmp_path/'db')),selected,{'done':{'sha256':'old'}},{'pending','empty'},[])
    monkeypatch.setattr(M,'evidence',lambda row:data);monkeypatch.setattr(M,'current',lambda data:None);monkeypatch.setattr(M,'proof',lambda *args:{})
    value=M.run({},home,execute=True)
    assert value['status']=='BACKLOG_COMPLETION_RECORDED'and value['enrollments']==0 and calls==['pending']
    assert value['preserved_heads']=={'done':{'sha256':'old'}}
    with pytest.raises(ValueError):M.run({},home,execute=True)
    assert calls==['pending']

@pytest.mark.parametrize('changed',[False,True])
def test_real_append_receipt_is_a_binding_to_the_loaded_cursor(tmp_path,monkeypatch,changed):
    import importlib.util,io,sys
    from scripts.fleet import backlog as B,pool_v5 as P
    spec=importlib.util.spec_from_file_location('backlog_fixture',ROOT/'tests/test_fleet_backlog.py');fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
    policy,p,job=fixture.setup(tmp_path);batch=fixture.save(tmp_path/'batch.json',[job(1)])
    committed=B.append(policy,batch,expected=None);cfg=fixture.save(tmp_path/'cfg.json',dict(backlog=policy))
    if changed:B.append(policy,fixture.save(tmp_path/'later.json',[job(2)]),expected=B.load(policy)[1])
    request=dict(selection=dict(config=cfg,batch=batch),supervisor=B.binding(Path(P.__file__).resolve()),head=committed)
    monkeypatch.setattr(sys,'stdin',io.StringIO(json.dumps(request)));output=io.StringIO();monkeypatch.setattr(sys,'stdout',output)
    if changed:
        with pytest.raises(AssertionError):exec(M.HEAD_CODE,{})
    else:
        exec(M.HEAD_CODE,{});assert json.loads(output.getvalue())['status']=='COMMITTED_HEAD_PRESERVED'

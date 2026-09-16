from contextlib import contextmanager
from types import SimpleNamespace
import sys
import pytest
from scripts.fleet import disclosure_adoption_continue as S
from scripts.batch import harbor_disclosure_round_adoption as A
from test_disclosure_adoption_resume import keep

@contextmanager
def completed_custody(row,p,env,worker,selection,current):
    S.require(len(env)==4,'original function global retained')
    env[3].active(worker,selection,current)
    yield current

@contextmanager
def original_context(binding):
    original=module('retained_disclosure_review_context',binding)
    state['root_loaded']=True
    with original.completed_custody(None,None,(None,None,None,None),None,None,None):yield 'completed'

def test_prepared_dependency_import_waits_for_original_cold_root(tmp_path,monkeypatch):
    global state
    state={'root_loaded':False};calls=[];helper=keep(tmp_path/'helper',{})
    P=SimpleNamespace(completed_custody=completed_custody)
    def loader(name,bound):
        calls.append(name);assert name=='retained_disclosure_review_context';return P
    M=SimpleNamespace(module=loader,context=original_context)
    selected=keep(tmp_path/'review',dict(retained=dict(helper=helper)))
    p=dict(prepared_context=dict(helper=helper,completed_custody=A.function_sha(completed_custody)),context=dict(plan=selected))
    class Stop(Exception):pass
    @contextmanager
    def boot(*args):
        assert state['root_loaded'] is True
        raise Stop()
        yield
    monkeypatch.setattr(S,'prepared_dependencies',boot)
    adapted=S.prepared_review_context(M,p,None,tmp_path)
    with pytest.raises(Stop):
        with adapted(helper):pass
    assert calls==['retained_disclosure_review_context']
    assert P.completed_custody is completed_custody

@pytest.fixture
def unstarted(tmp_path):
    from scripts.batch.harbor_admission import record
    tracker=keep(tmp_path/'tracker',{});source=keep(tmp_path/'source',{});rp=keep(tmp_path/'round',{});rr=keep(tmp_path/'review-result',{})
    config=keep(tmp_path/'config',dict(run_root=str(tmp_path/'author')));job=keep(tmp_path/'original-job',dict(config=config))
    old=dict(schema_version=S.SCHEMA,prepared_context={},helper=record(S.__file__),source=source,adoption=record(A.__file__),context=dict(plan=rp),round_result=rr,owner='old',fence=6,output=str(tmp_path/'original'),original_job=job)
    plan=keep(tmp_path/'plan',old);home=tmp_path/'original';keep(home/'intent.json',dict(schema_version=S.SCHEMA,plan=plan,automatic_retry=False))
    descriptor=keep(home/'permit/descriptor.json',{});permit=keep(home/'permit/permit.json',dict(round_plan=rp,round_result=rr,tracker=tracker,implementation=old['adoption']))
    checkpoint=keep(home/'permit/result.json',dict(status='RETRY_READY',next_step='disclosure-review',checkpoint=permit,descriptor=descriptor))
    wjob=keep(tmp_path/'job',dict(driver=old['helper'],source=source,command=['python','--plan',plan['path'],'--sha256',plan['sha256']]))
    wi=keep(tmp_path/'worker-intent',dict(owner='old',job=wjob));outer=keep(tmp_path/'outer',dict(status='RECONCILIATION_REQUIRED',returncode=1,failure_type=None,result=None,automatic_retry=False,intent=wi))
    intent=keep(home/'first-step/intent.json',dict(action='step',arguments=dict(descriptor=descriptor,checkpoint=checkpoint)))
    log=home/'first-step/step.log';log.write_text("selected_module\nModuleNotFoundError: No module named 'scripts'\n")
    resource=keep(home/'first-step/resource.json',dict(returncode=1,failure=None,intent=intent,log=record(log)))
    outcome=keep(home/'first-step/outcome.json',dict(status='RECONCILIATION_REQUIRED',result=None,resource=resource))
    keep(home/'first-step/aggregate-bootstrap.json',dict(schema_version=S.SCHEMA,plan=plan,helper=old['helper'],kind='instruction',intent=intent,automatic_retry=False))
    v=dict(schema_version='fleet-disclosure-adoption-prepared-unstarted/v1',plan=plan,worker_outcome=outer,permit=permit,checkpoint=checkpoint,descriptor=descriptor,tracker=tracker,failed_outcome=outcome)
    proof=keep(tmp_path/'resume',v);p={**old,'owner':'new','fence':7,'output':str(tmp_path/'new'),'resume':proof}
    return p,v,home

@pytest.mark.parametrize('fault',[None,'tracker','permit','sidecar','later','fence','source','log'])
def test_prepared_unstarted_preserves_original_permit_and_actual_failure(unstarted,fault):
    from pathlib import Path
    p,v,home=unstarted
    if fault=='tracker':Path(v['tracker']['path']).write_text('changed')
    if fault=='permit':Path(v['permit']['path']).write_text('{}')
    if fault=='sidecar':(home/'first-step/aggregate-bootstrap.json').write_text('{}')
    if fault=='later':(home/'permit/steps').mkdir()
    if fault=='fence':p['fence']=9
    if fault=='source':p['source']={'path':'/foreign','sha256':'f'*64}
    if fault=='log':(home/'first-step/step.log').write_text('A different failure')
    if fault:
        with pytest.raises(Exception):S.resume_state(p)
    else:
        assert S.resume_state(p)==v
        Path(v['tracker']['path']).write_text('later checkpoint has separate live validator')
        assert S.resume_state(p,continued=True)==v


@pytest.mark.parametrize('fault',[None,'checker','reader','evidence','module','helper'])
def test_semantic_witness_correction_cannot_change_original_gate(tmp_path,fault):
    import ast,hashlib,copy
    from scripts.batch.harbor_admission import record
    module=tmp_path/'complete.py';module.write_text('def disclosure_review(x):\n    return x\ndef check_disclosure(x):\n    return x == 1\n')
    nodes=ast.parse(module.read_text()).body;sha=lambda n:hashlib.sha256(ast.dump(n,include_attributes=False).encode()).hexdigest()
    profile=dict(schema_version='fleet-disclosure-semantic-archive/v1',complete=record(module),lanes=keep(tmp_path/'lanes',{}),functions=dict(disclosure=sha(nodes[0]),evidence='a'*64))
    old=dict(semantic_archive=dict(helper=keep(tmp_path/'helper',{}),profile=profile));p=copy.deepcopy(old);p['semantic_archive']['profile']['functions']['disclosure']=sha(nodes[1])
    if fault=='checker':p['semantic_archive']['profile']['functions']['disclosure']='0'*64
    if fault=='reader':old['semantic_archive']['profile']['functions']['disclosure']='0'*64
    if fault=='evidence':p['semantic_archive']['profile']['functions']['evidence']='0'*64
    if fault=='module':p['semantic_archive']['profile']['complete']=keep(tmp_path/'foreign',{})
    if fault=='helper':p['semantic_archive']['helper']=keep(tmp_path/'other',{})
    if fault:
        with pytest.raises(ValueError):S.semantic_witness_correction(old,p)
    else:S.semantic_witness_correction(old,p)


@pytest.mark.parametrize('tamper',[False,True])
def test_imported_semantic_reader_requires_original_snapshot(tmp_path,tamper):
    import ast,hashlib,json,copy
    from scripts.batch.harbor_admission import record
    source="def disclosure_review(x):\n    return x\n"
    relative='scripts/batch/harbor_semantic_readiness.py'
    raw=json.dumps({relative:hashlib.sha256(source.encode()).hexdigest()},sort_keys=True).encode()
    root=tmp_path/hashlib.sha256(raw).hexdigest();folder=root/'scripts/batch';folder.mkdir(parents=True)
    (root/'.pipeline-code.json').write_bytes(raw);(root/relative).write_text(source)
    complete=folder/'harbor_complete_readiness.py'
    complete.write_text('from scripts.batch.harbor_semantic_readiness import disclosure_review\ndef check_disclosure(x):\n    return x == 1\n')
    digest=lambda text:hashlib.sha256(ast.dump(ast.parse(text).body[-1],include_attributes=False).encode()).hexdigest()
    profile=dict(schema_version='fleet-disclosure-semantic-archive/v1',complete=record(complete),lanes=keep(tmp_path/'lanes',{}),functions=dict(disclosure=digest(source),evidence='a'*64))
    old=dict(semantic_archive=dict(helper=keep(tmp_path/'helper',{}),profile=profile));new=copy.deepcopy(old)
    new['semantic_archive']['profile']['functions']['disclosure']=digest(complete.read_text())
    if tamper:
        (root/relative).write_text(source+'# changed\n')
        with pytest.raises(ValueError):S.semantic_witness_correction(old,new)
    else:S.semantic_witness_correction(old,new)

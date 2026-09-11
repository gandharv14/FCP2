import urllib.error
import dataclasses
import http.client
import io
import json
import time
import uuid
from pathlib import Path
import pytest
import xl_variable_source_audit as audit
from grader.tests.test_variable_source_audit import write_two_variable_fixture,GOOD_MODEL_TABLE
from scripts.batch import harbor_audit_bridge as B
from scripts.batch import harbor_model_executor as E
from scripts.batch import harbor_audit_protocol as P
from scripts.batch.harbor_admission import record,read
from scripts.batch.harbor_model_review_provenance import Policy

class Response(io.BytesIO):
    def __enter__(self):return self
    def __exit__(self,*args):self.close()

def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(E.canonical(value));return record(path)

@pytest.fixture
def setup(tmp_path,monkeypatch):
    artifact,seg=write_two_variable_fixture(tmp_path);inventory=audit.build_inventory('0001',artifact,seg)
    home=tmp_path/'bootstrap';source=tmp_path/'sources/0001/generations'/('a'*64);source.mkdir(parents=True)
    prepared=write(home/'prepared.json',{'validated':{'workbook':'0001'}})
    bindings={'baseline_inputs':record(artifact),'original_raw_source':record(artifact),'source_generation':{'path':str(source)},'segmentation_generation':{'path':str(seg)}}
    baseline=write(home/'baseline.json',{'bindings':bindings})
    plan=write(tmp_path/'plan.json',dict(schema_version='harbor-bootstrap-audit-plan/v1',prepared=prepared,baseline=baseline,entry=record(audit.__file__),inventory_sha256=E.sha(E.canonical(inventory)),inherited_budget=None,batch_consumed={1:0}))
    campaign=E.Campaign(tmp_path/'executions',tmp_path/'slots');policy=Policy('fixture-project')
    monkeypatch.setenv('HARBOR_MODEL_SLOTS','2');monkeypatch.setenv('HARBOR_MODEL_SLOT_ROOT',str(campaign.slot_root))
    monkeypatch.setattr(audit,'resolve_segmentation_directory',lambda *a,**k:(seg,None))
    return plan,tmp_path/'bridge',campaign,policy,home/'audit/0001-variable-sources'


def transport(monkeypatch,texts,observed):
    def opened(request,timeout):
        observed.append(request.data);text=texts[len(observed)-1]
        raw={'id':str(uuid.uuid4()),'object':'chat.completion','model':'openai/gpt-5.6-sol','created':time.time_ns()//10**9,'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':text}}]}
        return Response(json.dumps(raw).encode())
    monkeypatch.setattr(E,'_open',opened)


def run(setup):
    plan,root,campaign,policy,target=setup
    return B.run(plan,root=root,campaign=campaign,policy=policy,credential='synthetic-test-token')


def test_complete_exact_payload_response_and_completed_reentry(setup,monkeypatch):
    seen=[];transport(monkeypatch,['\r\n'+GOOD_MODEL_TABLE+'\r\n'],seen)
    out=run(setup);assert out['coverage']['passed'] and len(seen)==1
    plan,root,campaign,policy,target=setup;p=B.prepared_from(read(record(next(root.glob('batch-*/prepared.json')))))
    assert p.policy.reasoning_effort is None and set(json.loads(seen[0]))=={'model','messages'}
    assert seen[0]==json.dumps({'model':policy.model,'messages':[{'role':m['role'],'content':m['content']} for m in read(p.audit_context.attempt)['messages']]}).encode()
    assert Path(E.inspect(p,campaign).call.parsed['path']).read_bytes().startswith(b'\r\n')
    before={str(p):p.read_bytes() for p in target.rglob('*') if p.is_file()}
    assert run(setup)==out and len(seen)==1
    assert before=={str(p):p.read_bytes() for p in target.rglob('*') if p.is_file()}


def test_semantic_second_attempt_preserves_original_four_messages_and_ordinal(setup,monkeypatch):
    seen=[];bad=GOOD_MODEL_TABLE.replace('R0002','R9999');transport(monkeypatch,[bad,GOOD_MODEL_TABLE],seen)
    out=run(setup);assert out['coverage']['passed'] and len(seen)==2
    messages=json.loads(seen[1])['messages'];assert [m['role'] for m in messages]==['system','user','assistant','user']
    assert messages[2]['content']==bad
    p=B.prepared_from(read(record(sorted(setup[1].glob('batch-*/prepared.json'))[-1])))
    assert p.ordinal==2 and p.limit==2 and len(p.audit_context.previous)==1
    assert run(setup)==out and len(seen)==2


@pytest.mark.parametrize('failure',['unknown','partial','hard_exit'])
def test_unknown_never_reenters_auditor_or_advances_semantic_attempt(setup,monkeypatch,failure):
    count=[]
    def opened(*args,**kwargs):
        count.append(1)
        if failure=='unknown':raise urllib.error.URLError('opaque synthetic delivery state')
        if failure=='hard_exit':raise SystemExit(91)
        class Partial(Response):
            def read(self,*a):raise http.client.IncompleteRead(b'{"partial":',100)
        return Partial(b'')
    monkeypatch.setattr(E,'_open',opened)
    with pytest.raises((B.AuditTransportStop,SystemExit)):run(setup)
    paths=list(setup[4].glob('.audit-attempts/*/batch-*/*.request.json'));assert len(paths)==1 and paths[0].name=='01.request.json'
    with pytest.raises(B.AuditTransportStop,match='reentry'):run(setup)
    assert len(count)==1 and len(list(setup[4].glob('.audit-attempts/*/batch-*/*.request.json')))==1
    if failure=='partial':assert any(p.read_bytes()==b'{"partial":' for p in setup[2].root.rglob('*.body.raw'))


@pytest.mark.parametrize('change',['messages','scope','history','inherited','inventory','explicit_effort','ordinal','original'])
def test_changed_context_and_omitted_budget_rejected(setup,monkeypatch,change):
    seen=[];transport(monkeypatch,[GOOD_MODEL_TABLE.replace('R0002','R9999'),GOOD_MODEL_TABLE],seen);run(setup)
    p=B.prepared_from(read(record(sorted(setup[1].glob('batch-*/prepared.json'))[-1])))
    ctx=p.audit_context;request=read(p.request);policy=p.policy
    if change=='messages':request['messages'][3]['content']='Arbitrary context'
    elif change=='scope':
        value=read(ctx.attempt);value['scope']['generator_version']='other';bound=write(setup[1]/'changed.json',value);ctx=dataclasses.replace(ctx,attempt=bound)
    elif change=='history':ctx=dataclasses.replace(ctx,previous=())
    elif change=='inherited':
        value=read(ctx.root_plan);value['inherited_budget']={'path':'missing','sha256':'a'*64};ctx=dataclasses.replace(ctx,root_plan=write(setup[1]/'changed.json',value))
    elif change=='inventory':
        value=read(ctx.inventory);value['variables'][0]['label']='Changed';ctx=dataclasses.replace(ctx,inventory=write(setup[1]/'changed.json',value))
    elif change=='explicit_effort':policy=dataclasses.replace(policy,reasoning_effort='high')
    else:
        changed=dataclasses.replace(p,scope=dataclasses.replace(p.scope,original_sha256='0'*64)) if change=='original' else dataclasses.replace(p,ordinal=1)
        with pytest.raises(E.ExecutorError):E.prepared_plan(changed,setup[2])
        return
    with pytest.raises((ValueError,KeyError)):P.validate(ctx,request,policy)


def test_inherited_call_is_counted_without_fabricating_local_ordinal(setup,monkeypatch):
    plan,root,campaign,policy,target=setup;value=read(plan)
    artifact=Path(read(value['baseline'])['bindings']['baseline_inputs']['path']);seg=artifact.parent/'seg'
    inv=audit.build_inventory('0001',artifact,seg);header,rows=audit._split_chunks(inv)[0]
    old_request=write(root.parent/'historical/request.json',dict(endpoint=audit.PROD_ENDPOINT,model=policy.model,messages=audit._base_messages(header,rows,1,1)))
    old_response=write(root.parent/'historical/response.json',dict(content='retained actual failed response',finish_reason='stop'))
    inherited=write(root.parent/'inherited.json',dict(schema_version='audit-inherited-budget/v1',workbook='0001',attempts=[dict(request=old_request,response=old_response)]))
    value['inherited_budget']=inherited;value['batch_consumed']={1:1};plan=write(Path(plan['path']),value)
    fresh=(plan,root,campaign,policy,target);seen=[];transport(monkeypatch,[GOOD_MODEL_TABLE],seen);run(fresh)
    prepared=B.prepared_from(read(record(next(root.glob('batch-*/prepared.json')))))
    assert prepared.ordinal==prepared.limit==1
    assert P.validate(prepared.audit_context,read(prepared.request),prepared.policy)['inherited']==1
    assert prepared.audit_context.previous==() and len(seen)==1
    # Omitting the inherited receipt in either independently bound record fails.
    changed=read(prepared.audit_context.policy_snapshot);changed['inherited_budget']=None
    context=dataclasses.replace(prepared.audit_context,policy_snapshot=write(root/'changed-policy.json',changed))
    with pytest.raises(ValueError,match='inherited'):P.validate(context,read(prepared.request),prepared.policy)


def test_default_executor_plan_does_not_change_historical_protocol_shape(tmp_path):
    request=write(tmp_path/'review.json',dict(model='openai/gpt-5.6-sol',reasoning_effort='high',messages=[dict(role='system',content='Review independently.'),dict(role='user',content='Synthetic claims.')]))
    budget=write(tmp_path/'budget.json',dict(attempt=1))
    p=E.PreparedCall(request,Policy('fixture-project'),E.SemanticScope('0001','a'*64,'normalization','original'),1,2,str(uuid.uuid4()),(budget,))
    plan,*_=E.prepared_plan(p,E.Campaign(tmp_path/'calls',tmp_path/'slots'))
    assert 'audit_context' not in plan
    changed=read(request);changed['messages']+= [dict(role='assistant',content='Prior answer'),dict(role='user',content='Retry')]
    p=dataclasses.replace(p,request=write(tmp_path/'changed-review.json',changed))
    with pytest.raises(E.ExecutorError,match='stateless'):E.prepared_plan(p,E.Campaign(tmp_path/'calls',tmp_path/'slots'))

import dataclasses
import io
import json
import multiprocessing
import os
from pathlib import Path
import sys
import time
import urllib.error
import uuid

import pytest

from scripts.batch.harbor_admission import record
from scripts.batch.harbor_model_review_provenance import Policy, validate_call

from scripts.batch import harbor_model_executor as E

TOKEN='fixture-credential-not-secret'


class Response(io.BytesIO):
    def __enter__(self):return self
    def __exit__(self,*args):self.close()


def response_bytes(policy,content=None,**changes):
    response={'id':str(uuid.uuid4()),'object':'chat.completion','model':policy.model,'created':time.time_ns()//10**9,
              'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(content if content is not None else {'passed':False,'finding':'Synthetic rejection remains rejected.'})}}]}
    response.update(changes)
    return json.dumps(response).encode()


@pytest.fixture
def setup(tmp_path,monkeypatch):
    root=tmp_path.resolve();campaign=E.Campaign(root/'journal',root/'slots')
    monkeypatch.setenv('HARBOR_MODEL_SLOT_ROOT',str(campaign.slot_root));monkeypatch.setenv('HARBOR_MODEL_SLOTS','2')
    def forbidden(*args,**kwargs):raise AssertionError('No actual transport in fixture')
    monkeypatch.setattr(E,'_open',forbidden)
    def prepare(scope='0001/normalization',ordinal=1,limit=2):
        own=root/('prepared-'+str(uuid.uuid4()));own.mkdir()
        policy=Policy('fixture-project')
        request={'model':policy.model,'reasoning_effort':'high','messages':[{'role':'system','content':'Independently review the supplied synthetic claim.'},{'role':'user','content':'{"claim":"fixture"}'}]}
        (own/'request.json').write_bytes(json.dumps(request,indent=3).encode()+b'\n')
        (own/'budget.json').write_text(json.dumps({'fixture':'owning-lane-state','scope':scope,'ordinal':ordinal,'limit':limit}))
        workbook,lane=scope.split('/',1)
        identity=E.SemanticScope(workbook,'a'*64,lane,'fixture-partition-01')
        prepared=E.PreparedCall(record(own/'request.json'),policy,identity,ordinal,limit,str(uuid.uuid4()),(record(own/'budget.json'),))
        return prepared
    return campaign,prepare


def test_exact_request_response_and_completed_call_without_semantic_pass(setup,monkeypatch):
    campaign,prepare=setup;p=prepare();raw=response_bytes(p.policy);seen=[]
    def opened(request,timeout):
        seen.append((request.full_url,request.data,timeout,request.get_header('X-labelbox-context')))
        return Response(raw)
    monkeypatch.setattr(E,'_open',opened)
    result=E.execute(p,campaign,credential=TOKEN)
    assert result.status=='completed' and result.call is not None and result.reused is False
    root=E.location(p,campaign)
    assert (root/'request.json').read_bytes()==Path(p.request['path']).read_bytes()
    assert (root/'response.raw.json').read_bytes()==raw
    assert (root/'transport-00.body.raw').read_bytes()==raw
    checked=validate_call(result.call,json.loads(Path(p.request['path']).read_bytes()),policy=p.policy)
    assert checked['review']['passed'] is False
    assert len(seen)==1 and seen[0][0]==p.policy.endpoint and seen[0][1]==Path(p.request['path']).read_bytes()
    assert json.loads(seen[0][3])=={'project_id':p.policy.project_id}
    assert E.inspect(p,campaign).status=='completed' and E.inspect(p,campaign).reused is True
    assert E.execute(p,campaign,credential=TOKEN).status=='completed' and len(seen)==1
    assert all(TOKEN.encode() not in f.read_bytes() for f in root.iterdir() if f.is_file())


@pytest.mark.parametrize('change',['endpoint','model','reasoning','request_field','project','budget_missing','ordinal','limit','hash','budget_hash'])
def test_invalid_preparation_does_not_dispatch(setup,change):
    campaign,prepare=setup;p=prepare()
    if change=='endpoint':p=dataclasses.replace(p,policy=dataclasses.replace(p.policy,endpoint='https://other.invalid'))
    elif change=='model':p=dataclasses.replace(p,policy=dataclasses.replace(p.policy,model='other'))
    elif change=='reasoning':p=dataclasses.replace(p,policy=dataclasses.replace(p.policy,reasoning_effort='low'))
    elif change=='project':p=dataclasses.replace(p,policy=dataclasses.replace(p.policy,project_id=''))
    elif change=='budget_missing':p=dataclasses.replace(p,budget_evidence=())
    elif change=='ordinal':p=dataclasses.replace(p,ordinal=0)
    elif change=='limit':p=dataclasses.replace(p,limit=3)
    elif change=='hash':Path(p.request['path']).write_bytes(b'{}')
    elif change=='budget_hash':Path(p.budget_evidence[0]['path']).write_bytes(b'{}')
    else:
        path=Path(p.request['path']);data=json.loads(path.read_bytes());data['previous_response_id']='forbidden';path.write_text(json.dumps(data));p=dataclasses.replace(p,request=record(path))
    with pytest.raises(Exception):E.execute(p,campaign,credential=TOKEN)
    assert not campaign.root.exists()


@pytest.mark.parametrize('exception',[urllib.error.URLError('not logged'),ConnectionError('not logged')])
def test_uncertain_transport_never_replayed(setup,monkeypatch,exception):
    campaign,prepare=setup;p=prepare();calls=[]
    def opened(*args):calls.append(1);raise exception
    monkeypatch.setattr(E,'_open',opened)
    first=E.execute(p,campaign,credential=TOKEN);assert first.status=='incomplete' and first.call is None
    assert E.execute(p,campaign,credential=TOKEN).status=='incomplete' and len(calls)==1
    with pytest.raises(E.ExecutorError,match='unresolved prior'):E.execute(prepare(ordinal=2),campaign,credential=TOKEN)


def test_partial_bytes_survive_read_failure_and_no_replay(setup,monkeypatch):
    campaign,prepare=setup;p=prepare();partial=b'{"id":"partially-observed"'
    class Partial(Response):
        def read(self,size):
            if self.tell():raise TimeoutError('No full response')
            return super().read(size)
    monkeypatch.setattr(E,'_open',lambda *a:Partial(partial))
    assert E.execute(p,campaign,credential=TOKEN).status=='incomplete'
    assert (E.location(p,campaign)/'transport-00.body.raw').read_bytes()==partial
    assert E.inspect(p,campaign).call is None


@pytest.mark.parametrize('point',['after_submitted','after_dispatch_started','after_response_retained','before_outcome'])
def test_interruption_preserves_identity_without_resubmission(setup,monkeypatch,point):
    campaign,prepare=setup;p=prepare();calls=[]
    def opened(*args):calls.append(1);return Response(response_bytes(p.policy))
    monkeypatch.setattr(E,'_open',opened)
    def interrupt(name):
        if name==point:raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):E.execute(p,campaign,credential=TOKEN,_failpoint=interrupt)
    before=len(calls);assert E.execute(p,campaign,credential=TOKEN).status=='incomplete';assert len(calls)==before
    different=dataclasses.replace(p,attempt_id=str(uuid.uuid4()))
    with pytest.raises(E.ExecutorError,match='identity'):E.execute(different,campaign,credential=TOKEN)


def test_hard_process_exit_after_dispatch_start_never_replays(setup,monkeypatch):
    campaign,prepare=setup;p=prepare()
    def die(name):
        if name=='after_dispatch_started':os._exit(73)
    proc=multiprocessing.get_context('fork').Process(target=lambda:E.execute(p,campaign,credential=TOKEN,_failpoint=die))
    proc.start();proc.join(5)
    assert proc.exitcode==73
    result=E.inspect(p,campaign);assert result.status=='incomplete'
    assert result.outcome['details']['automatic_replay'] is False
    assert E.execute(p,campaign,credential=TOKEN).status=='incomplete'


@pytest.mark.parametrize('status',[408,429,500,502,503,504])
def test_at_most_three_explicit_http_retries_with_journal(setup,monkeypatch,status):
    campaign,prepare=setup;p=prepare();calls=[];delays=[]
    def opened(*args):
        calls.append(1)
        raise urllib.error.HTTPError(p.policy.endpoint,status,'fixture error',{'Retry-After':'0.25'},io.BytesIO(b'{"error":"fixture"}'))
    monkeypatch.setattr(E,'_open',opened);monkeypatch.setattr(E.time,'sleep',delays.append)
    result=E.execute(p,campaign,credential=TOKEN)
    assert result.status=='http_rejected' and len(calls)==4 and delays==[.25]*3
    root=E.location(p,campaign)
    assert len(list(root.glob('transport-*.http-error.json')))==4
    assert all(f.read_bytes()==b'{"error":"fixture"}' for f in root.glob('*.body.raw'))
    assert E.execute(p,campaign,credential=TOKEN).status=='http_rejected' and len(calls)==4


def test_retry_then_success_remains_one_semantic_identity(setup,monkeypatch):
    campaign,prepare=setup;p=prepare();calls=[]
    def opened(*args):
        calls.append(1)
        if len(calls)==1:raise urllib.error.HTTPError(p.policy.endpoint,429,'fixture',{'Retry-After':'0'},io.BytesIO(b'limited'))
        return Response(response_bytes(p.policy))
    monkeypatch.setattr(E,'_open',opened)
    result=E.execute(p,campaign,credential=TOKEN);assert result.status=='completed' and len(calls)==2
    ids={json.loads(f.read_bytes())['attempt_id'] for f in E.location(p,campaign).glob('transport-*.started.json')}
    assert ids=={p.attempt_id}


@pytest.mark.parametrize('status',[301,302,307,308,400,401,403])
def test_nonretryable_errors_are_retained_once(setup,monkeypatch,status):
    campaign,prepare=setup;p=prepare();calls=[]
    def opened(*args):calls.append(1);raise urllib.error.HTTPError(p.policy.endpoint,status,'fixture',{},io.BytesIO(b'error'))
    monkeypatch.setattr(E,'_open',opened)
    assert E.execute(p,campaign,credential=TOKEN).status=='http_rejected' and len(calls)==1
    assert E.NoRedirect().redirect_request(None,None,status,'',{},'https://other.invalid') is None


@pytest.mark.parametrize('fault',['wrong_model','finish_length','refusal','invalid_json','wrong_object','nonobject_content','missing_choice'])
def test_complete_but_unusable_response_retained_without_pass(setup,monkeypatch,fault):
    campaign,prepare=setup;p=prepare();value=json.loads(response_bytes(p.policy))
    if fault=='wrong_model':value['model']='other'
    elif fault=='finish_length':value['choices'][0]['finish_reason']='length'
    elif fault=='refusal':value['choices'][0]['message']['refusal']='declined'
    elif fault=='wrong_object':value['object']='other'
    elif fault=='nonobject_content':value['choices'][0]['message']['content']='[]'
    elif fault=='missing_choice':value['choices']=[]
    raw=b'not-json' if fault=='invalid_json' else json.dumps(value).encode()
    monkeypatch.setattr(E,'_open',lambda *args:Response(raw))
    result=E.execute(p,campaign,credential=TOKEN)
    assert result.status=='response_rejected' and result.call is None
    assert (E.location(p,campaign)/'response.raw.json').read_bytes()==raw


@pytest.mark.parametrize('name',['plan.json','request.json','submitted.json','response.raw.json','completed.json','parsed.json','content.txt','transport-00.body.raw','budget-00.snapshot'])
def test_retained_evidence_tampering_blocks_reuse(setup,monkeypatch,name):
    campaign,prepare=setup;p=prepare();monkeypatch.setattr(E,'_open',lambda *a:Response(response_bytes(p.policy)))
    E.execute(p,campaign,credential=TOKEN);path=E.location(p,campaign)/name;path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(E.ExecutorError):E.inspect(p,campaign)
    with pytest.raises(E.ExecutorError):E.execute(p,campaign,credential=TOKEN)


def test_second_ordinal_requires_prior_and_preserves_limits(setup,monkeypatch):
    campaign,prepare=setup;p=prepare(ordinal=2)
    with pytest.raises(E.ExecutorError,match='prior semantic attempt missing'):E.execute(p,campaign,credential=TOKEN)
    first=prepare();monkeypatch.setattr(E,'_open',lambda *a:Response(response_bytes(first.policy)))
    assert E.execute(first,campaign,credential=TOKEN).status=='completed'
    assert E.execute(p,campaign,credential=TOKEN).status=='completed'
    assert json.loads((E.location(p,campaign)/'prior-attempts.json').read_bytes())
    changed=dataclasses.replace(first,limit=1)
    with pytest.raises(E.ExecutorError,match='identity'):E.execute(changed,campaign,credential=TOKEN)


def test_slot_configuration_must_match_shared_cap(setup,monkeypatch):
    campaign,prepare=setup;p=prepare()
    monkeypatch.setenv('HARBOR_MODEL_SLOTS','3')
    with pytest.raises(E.ExecutorError):E.execute(p,campaign,credential=TOKEN)
    monkeypatch.setenv('HARBOR_MODEL_SLOTS','2');monkeypatch.setenv('HARBOR_MODEL_SLOT_ROOT',str(campaign.slot_root/'other'))
    with pytest.raises(E.ExecutorError):E.execute(p,campaign,credential=TOKEN)


def test_three_processes_share_two_real_slots_and_release_on_exit(setup,monkeypatch):
    campaign,prepare=setup;prepared=[prepare('000%d/review'%n) for n in range(1,4)]
    ctx=multiprocessing.get_context('fork');entered=ctx.Queue();release=ctx.Event()
    def opened(*args):
        entered.put(os.getpid())
        assert release.wait(8)
        return Response(response_bytes(prepared[0].policy))
    monkeypatch.setattr(E,'_open',opened)
    def run(p):assert E.execute(p,campaign,credential=TOKEN).status=='completed'
    children=[ctx.Process(target=run,args=(p,)) for p in prepared]
    try:
        for child in children:child.start()
        entered.get(timeout=5);entered.get(timeout=5)
        import queue
        with pytest.raises(queue.Empty):entered.get(timeout=.3)
        release.set();entered.get(timeout=5)
        for child in children:child.join(5);assert child.exitcode==0
    finally:
        release.set()
        for child in children:
            if child.is_alive():child.terminate()
            child.join(3)
    assert all(E.inspect(p,campaign).status=='completed' for p in prepared)


def test_retry_after_http_date_invalid_and_bounded_values():
    assert E.retry_delay(0,'9999')==60
    assert E.retry_delay(0,'-10')==0
    assert E.retry_delay(1,'nan')==4
    assert E.retry_delay(2,'invalid')==8
    assert E.retry_delay(0,'Thu, 01 Jan 1970 00:00:00 GMT')==0


def test_incomplete_read_exception_retains_its_partial_bytes(setup,monkeypatch):
    campaign,prepare=setup;p=prepare()
    class Partial(Response):
        def read(self,size):raise E.http.client.IncompleteRead(b'observed-partial',100)
    monkeypatch.setattr(E,'_open',lambda *a:Partial(b''))
    assert E.execute(p,campaign,credential=TOKEN).status=='incomplete'
    assert (E.location(p,campaign)/'transport-00.body.raw').read_bytes()==b'observed-partial'


def test_attempt_uuid_cannot_be_reused_in_different_semantic_scope(setup,monkeypatch):
    campaign,prepare=setup;first=prepare();calls=[]
    def opened(*a):calls.append(1);return Response(response_bytes(first.policy))
    monkeypatch.setattr(E,'_open',opened);assert E.execute(first,campaign,credential=TOKEN).status=='completed'
    second=dataclasses.replace(prepare('0002/review'),attempt_id=first.attempt_id)
    with pytest.raises(E.ExecutorError,match='UUID already reserved'):E.execute(second,campaign,credential=TOKEN)
    assert len(calls)==1


def test_complete_chat_body_under_error_status_is_uncertain_not_retried(setup,monkeypatch):
    campaign,prepare=setup;p=prepare();raw=response_bytes(p.policy);calls=[]
    def opened(*a):calls.append(1);raise urllib.error.HTTPError(p.policy.endpoint,500,'fixture',{},io.BytesIO(raw))
    monkeypatch.setattr(E,'_open',opened)
    assert E.execute(p,campaign,credential=TOKEN).status=='incomplete' and len(calls)==1
    assert (E.location(p,campaign)/'transport-00.body.raw').read_bytes()==raw


def test_attempt_registry_tamper_blocks_reuse(setup,monkeypatch):
    campaign,prepare=setup;p=prepare();monkeypatch.setattr(E,'_open',lambda *a:Response(response_bytes(p.policy)))
    E.execute(p,campaign,credential=TOKEN)
    (campaign.root/'attempt-identities'/(p.attempt_id+'.json')).write_text('{}')
    with pytest.raises(Exception):E.inspect(p,campaign)


def test_same_bytes_symlink_in_journal_is_rejected(setup,monkeypatch,tmp_path):
    campaign,prepare=setup;p=prepare();monkeypatch.setattr(E,'_open',lambda *a:Response(response_bytes(p.policy)))
    E.execute(p,campaign,credential=TOKEN)
    path=E.location(p,campaign)/'transport-00.body.raw';copy=tmp_path/'same';copy.write_bytes(path.read_bytes());path.unlink();path.symlink_to(copy)
    with pytest.raises(E.ExecutorError,match='symlinks'):E.inspect(p,campaign)


def test_uncertain_scope_ordinal_cannot_be_bypassed_by_new_uuid_request_or_output(setup,monkeypatch):
    campaign,prepare=setup;first=prepare();calls=[]
    def opened(*a):calls.append(1);raise urllib.error.URLError('opaque delivery state')
    monkeypatch.setattr(E,'_open',opened)
    assert E.execute(first,campaign,credential=TOKEN).status=='incomplete'
    replacement=prepare()  # Different prepared path/UUID; identical logical scope/ordinal.
    with pytest.raises(E.ExecutorError,match='identity'):E.execute(replacement,campaign,credential=TOKEN)
    assert E.location(first,campaign)==E.location(replacement,campaign) and len(calls)==1


@pytest.mark.parametrize('field,value',[('workbook_id',''),('original_sha256','unbound'),('lane','/tmp/output'),('budget_id','../attempt2')])
def test_scope_requires_original_workbook_and_logical_budget_not_output_path(setup,field,value):
    campaign,prepare=setup;p=prepare();p=dataclasses.replace(p,scope=dataclasses.replace(p.scope,**{field:value}))
    with pytest.raises(E.ExecutorError):E.execute(p,campaign,credential=TOKEN)
    assert not campaign.root.exists()


@pytest.mark.parametrize('text',['# Senior\n\nUse the supplied worksheet.\n','\ufeff# Senior\r\n\r\nKeep the exact \u00a3 unit.\r\n'])
def test_root_selected_text_mode_keeps_exact_markdown_bytes(setup,monkeypatch,text):
    campaign,prepare=setup;p=dataclasses.replace(prepare(),response_mode='text')
    value=json.loads(response_bytes(p.policy));value['choices'][0]['message']['content']=text
    raw=json.dumps(value).encode();monkeypatch.setattr(E,'_open',lambda *a:Response(raw))
    result=E.execute(p,campaign,credential=TOKEN)
    assert result.status=='completed' and result.call is not None
    assert Path(result.call.parsed['path']).read_bytes()==text.encode('utf-8')
    assert Path(result.call.response['path']).read_bytes()==raw
    assert not (E.location(p,campaign)/'parsed.json').exists()
    expected=json.loads(Path(p.request['path']).read_bytes())
    assert validate_call(result.call,expected,policy=p.policy,json_response=False)['review']==text
    with pytest.raises(ValueError):validate_call(result.call,expected,policy=p.policy)
    assert E.inspect(p,campaign).status=='completed'


def test_response_mode_is_bound_to_plan_submission_and_completion(setup,monkeypatch):
    campaign,prepare=setup;p=dataclasses.replace(prepare(),response_mode='text')
    monkeypatch.setattr(E,'_open',lambda *a:Response(response_bytes(p.policy)))
    E.execute(p,campaign,credential=TOKEN)
    with pytest.raises(E.ExecutorError,match='identity'):E.execute(dataclasses.replace(p,response_mode='json_object'),campaign,credential=TOKEN)
    root=E.location(p,campaign)
    for filename in ['plan.json','submitted.json','completed.json']:
        assert json.loads((root/filename).read_bytes())['response_mode']=='text'


def test_plain_markdown_does_not_silently_switch_json_review_mode(setup,monkeypatch):
    campaign,prepare=setup;p=prepare();value=json.loads(response_bytes(p.policy));value['choices'][0]['message']['content']='# Markdown is not a JSON review'
    monkeypatch.setattr(E,'_open',lambda *a:Response(json.dumps(value).encode()))
    assert E.execute(p,campaign,credential=TOKEN).status=='response_rejected'
    with pytest.raises(E.ExecutorError,match='identity'):E.execute(dataclasses.replace(p,response_mode='text'),campaign,credential=TOKEN)


def test_interrupted_call_mode_cannot_change_on_resume(setup,monkeypatch):
    campaign,prepare=setup;p=prepare()
    def die(name):raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):E.execute(p,campaign,credential=TOKEN,_failpoint=die)
    with pytest.raises(E.ExecutorError,match='identity'):E.execute(dataclasses.replace(p,response_mode='text'),campaign,credential=TOKEN)


def test_root_clamped_single_model_slot_is_supported_and_bound(setup,monkeypatch):
    campaign,prepare=setup;campaign=dataclasses.replace(campaign,model_slots=1);p=prepare()
    monkeypatch.setenv('HARBOR_MODEL_SLOTS','1');monkeypatch.setattr(E,'_open',lambda *a:Response(response_bytes(p.policy)))
    assert E.execute(p,campaign,credential=TOKEN).status=='completed'
    assert (campaign.slot_root/'capacity').read_bytes()==b'1\n'
    assert json.loads((E.location(p,campaign)/'plan.json').read_bytes())['model_slots']==1
    monkeypatch.setenv('HARBOR_MODEL_SLOTS','2')
    with pytest.raises(E.ExecutorError,match='identity'):E.execute(p,dataclasses.replace(campaign,model_slots=2),credential=TOKEN)


def test_absent_call_inspection_is_read_only(setup):
    campaign,prepare=setup;p=prepare()
    with pytest.raises(E.ExecutorError,match='has not been submitted'):E.inspect(p,campaign)
    assert not campaign.root.exists() and not campaign.slot_root.exists()


def test_inspection_does_not_recreate_missing_lock(setup,monkeypatch):
    campaign,prepare=setup;p=prepare();monkeypatch.setattr(E,'_open',lambda *a:Response(response_bytes(p.policy)))
    E.execute(p,campaign,credential=TOKEN);lock=E.location(p,campaign).parent/'scope.lock';lock.unlink()
    with pytest.raises(E.ExecutorError,match='existing logical scope lock'):E.inspect(p,campaign)
    assert not lock.exists()


def test_retry_ordinal_cannot_change_json_review_protocol_to_text(setup,monkeypatch):
    campaign,prepare=setup;first=prepare();monkeypatch.setattr(E,'_open',lambda *a:Response(response_bytes(first.policy)))
    assert E.execute(first,campaign,credential=TOKEN).status=='completed'
    second=dataclasses.replace(prepare(ordinal=2),response_mode='text')
    with pytest.raises(E.ExecutorError,match='prior semantic scope/limit differs'):E.execute(second,campaign,credential=TOKEN)

import copy,json,uuid
from pathlib import Path
import pytest
from scripts.batch.harbor_admission import record
from scripts.batch.harbor_model_review_provenance import Call,Policy,validate_call,require_independent


def fixture(tmp_path,style):
    def write(name,value):
        p=tmp_path/name;p.write_text(json.dumps(value));return record(p)
    policy=Policy('fixture-project')
    request={'model':policy.model,'reasoning_effort':'high','messages':[{'role':'system','content':'Review each exact claim independently.'},{'role':'user','content':'{"claims":["synthetic-claim"]}'}]}
    q=write('request.json',request);parsed={'passed':True,'claims':[{'id':'synthetic-claim','entailed':True}]};j=write('review.json',parsed)
    response={'id':'response-fixture','model':policy.model,'object':'chat.completion','created':101,'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(parsed)}}]}
    r=write('response.raw.json',response)
    submitted={'attempt_id':str(uuid.uuid4()),'submitted_at_ns':100*10**9,'endpoint':policy.endpoint,'project_id':policy.project_id,'model':policy.model,'reasoning_effort':'high','semantic_budget_reset':False}
    completed={'completed_at_ns':102*10**9}
    if style=='normalization':submitted['request_sha256']=q['sha256'];completed.update(response_sha256=r['sha256'],review_sha256=j['sha256'])
    else:submitted['request']=q;completed['response']=r
    return Call(q,write('submitted.json',submitted),r,write('completed.json',completed),j),policy,request


@pytest.mark.parametrize('style',['normalization','naturalization'])
def test_actual_retained_journal_shapes_validate_without_network(tmp_path,style):
    call,policy,request=fixture(tmp_path,style)
    result=validate_call(call,request,policy=policy)
    assert result['review']['claims'][0]['id']=='synthetic-claim'
    assert require_independent(result,[{**result,'response_id':'different','attempt_id':str(uuid.uuid4())}])


@pytest.mark.parametrize('change',['request_context','hash','parsed','model','endpoint','budget','response_model','finish','refusal','choices','created','time','response_binding','same_actor','prior_review'])
def test_forged_or_inapplicable_review_journal_rejected(tmp_path,change):
    call,policy,expected=fixture(tmp_path,'naturalization')
    if change=='request_context':expected=copy.deepcopy(expected);expected['messages'][1]['content']='different current claims'
    elif change in ('same_actor','prior_review'):
        result=validate_call(call,expected,policy=policy)
        with pytest.raises(ValueError):require_independent(result,[result])
        return
    elif change=='hash':Path(call.response['path']).write_text('{}')
    else:
        field='parsed' if change=='parsed' else 'submitted' if change in ('model','endpoint','budget','time') else 'completed' if change=='response_binding' else 'response'
        binding=getattr(call,field);path=Path(binding['path']);value=json.loads(path.read_text())
        if change=='parsed':value={'passed':True,'claims':[]}
        elif change=='model':value['model']='other'
        elif change=='endpoint':value['endpoint']='https://other.example/chat/completions'
        elif change=='budget':value['semantic_budget_reset']=True
        elif change=='time':value['submitted_at_ns']=104*10**9
        elif change=='response_model':value['model']='other'
        elif change=='finish':value['choices'][0]['finish_reason']='length'
        elif change=='refusal':value['choices'][0]['message']['refusal']='declined'
        elif change=='choices':value['choices']*=2
        elif change=='created':value['created']=500
        elif change=='response_binding':value['response']['sha256']='0'*64
        path.write_text(json.dumps(value));object.__setattr__(call,field,record(path))
        if field=='response':
            completed=json.loads(Path(call.completed['path']).read_text());completed['response']=call.response
            Path(call.completed['path']).write_text(json.dumps(completed));object.__setattr__(call,'completed',record(Path(call.completed['path'])))
    with pytest.raises(ValueError):validate_call(call,expected,policy=policy)

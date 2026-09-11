"""Every review shard has a real journal and exact global coverage."""
import copy,json
from dataclasses import asdict,replace
from pathlib import Path
import pytest
from scripts.batch import harbor_disclosure_partition_review as R,harbor_disclosure_partition_evidence as E
from scripts.batch import harbor_semantic_readiness as S
from scripts.batch.harbor_admission import record
from scripts.batch.harbor_complete_readiness import SemanticSelection
from scripts.batch import harbor_transition_codec as K
from test_harbor_semantic_readiness import disclosure_fixture,bind_json,set_call,ROOT
from test_harbor_model_review_provenance import fixture as journal

@pytest.fixture
def reviewed(tmp_path):
    _,_,args,policy,_,_=disclosure_fixture(tmp_path)
    value=S.read(args['disclosure']);value['method_assessments']=[]
    args['disclosure']=bind_json(Path(args['disclosure']['path']),value)
    manifest=bind_json(tmp_path/'full-manifest.json',S.disclosure_manifest(**args,root=ROOT))
    body=R.full_body(manifest,**args,root=ROOT)
    projection=E.plan(body,args['golden']['path'],args['delivered']['path'],root=ROOT,max_sentences=2)
    run=tmp_path/'worker';run.mkdir();initial=bind_json(run/'initial.json',{'fixture':'original initial'})
    p=dict(schema_version=R.PLAN,full_body=bind_json(tmp_path/'full-body.json',body),projection=bind_json(tmp_path/'projection.json',projection),manifest=manifest,
        **args,workbook='fixture',original_source=args['golden'],policy=asdict(policy),worker_run=str(run),initial=initial)
    plan=bind_json(tmp_path/'plan.json',p)
    permit=bind_json(run/'permit.json',dict(schema_version=R.CONTINUATION,review_plan=plan,global_round=1,global_limit=1,initial=initial,
        worker_run=str(run),workbook=p['workbook'],original_source=p['original_source']))
    bind_json(run/'disclosure-partition-continuation.json',dict(schema_version=R.CONTINUATION,permit=permit))
    calls=[]
    for index,batch in enumerate(projection['batches']):
        out=tmp_path/f'call{index}';out.mkdir();call,_,_=journal(out,'naturalization')
        request=R.payload(p,projection,index,policy)
        review=dict(schema_version='harbor-disclosure-faithfulness/v1',model='gpt-5.6-sol-high',input_hashes=batch['manifest']['input_hashes'],manifest_sha256=batch['manifest_sha256'],
          sentences=[{k:v for k,v in row.items() if k!='order'}|dict(golden_evidence=['independent source formula'],delivered_evidence=['independent delivered cell'],verdict='pass',findings=[]) for row in batch['manifest']['sentences']],
          blocking_findings=[],non_blocking_flags=[],passed=True)
        set_call(call,request,review)
        submitted=S.read(call.submitted);submitted.update(scope=asdict(R.scope(p,index)),semantic_ordinal=1,semantic_limit=1,budget_evidence=[plan])
        object.__setattr__(call,'submitted',bind_json(Path(call.submitted['path']),submitted))
        response=S.read(call.response);response['id']=f'actual-response-{index}'
        object.__setattr__(call,'response',bind_json(Path(call.response['path']),response))
        complete=S.read(call.completed);complete['response']=call.response
        object.__setattr__(call,'completed',bind_json(Path(call.completed['path']),complete))
        calls.append(call)
    return plan,p,projection,args,policy,calls


def test_actual_distinct_journals_reconcile_full_coverage(reviewed,tmp_path):
    plan,p,projection,args,policy,calls=reviewed
    value=R.aggregate(plan,calls,policy,ROOT);binding=bind_json(tmp_path/'aggregate.json',value)
    result=R.validate_aggregate(binding,p['manifest'],**args,policy=policy,root=ROOT)
    assert result['sentences']==len(projection['full_manifest']['sentences'])
    assert result['actual_review_calls']==len(calls)>1
    assert 'provenance' not in result  # There is no invented aggregate Call.

@pytest.mark.parametrize('fault',['missing','duplicate','order','scope','budget','coverage','evidence','response','body','reservation'])
def test_incomplete_or_substituted_reviews_never_form_aggregate(reviewed,fault):
    plan,p,projection,args,policy,calls=reviewed
    if fault=='missing':calls.pop()
    elif fault=='duplicate':calls[1]=calls[0]
    elif fault=='order':calls.reverse()
    elif fault=='reservation':
        path=Path(p['worker_run'])/'disclosure-partition-continuation.json';path.write_text('{}')
    else:
        call=calls[0]
        if fault in ('scope','budget'):
            submitted=S.read(call.submitted)
            if fault=='scope':submitted['scope']['budget_id']='foreign'
            else:submitted['semantic_ordinal']=2
            object.__setattr__(call,'submitted',bind_json(Path(call.submitted['path']),submitted))
        elif fault=='response':Path(call.response['path']).write_text('{}')
        else:
            request=S.read(call.request);review=S.read(call.parsed)
            if fault=='coverage':review['sentences'].pop()
            elif fault=='evidence':review['sentences'][0]['golden_evidence']=[]
            else:request['messages'][1]['content']='{}'
            set_call(call,request,review)
    with pytest.raises(ValueError):R.aggregate(plan,calls,policy,ROOT)


def test_byte_identical_publication_relocation_is_supported(reviewed,tmp_path):
    plan,p,_,args,policy,calls=reviewed
    binding=bind_json(tmp_path/'aggregate.json',R.aggregate(plan,calls,policy,ROOT))
    relocated=dict(args)
    for name in ('golden','delivered','disclosure'):
        path=tmp_path/('relocated-'+name);path.write_bytes(Path(args[name]['path']).read_bytes());relocated[name]=record(path)
    R.validate_aggregate(binding,p['manifest'],**relocated,policy=policy,root=ROOT)
    Path(relocated['delivered']['path']).write_bytes(b'changed workbook')
    with pytest.raises(ValueError):R.validate_aggregate(binding,p['manifest'],**relocated,policy=policy,root=ROOT)


def test_optional_aggregate_codec_preserves_historical_encoding():
    old=SemanticSelection(None,{}, {},None,None,())
    encoded=K.encode(old)
    assert 'disclosure_partition_aggregate' not in encoded['value'] and K.decode(encoded)==old
    current=replace(old,disclosure_partition_aggregate={'path':'fixture','sha256':'0'*64})
    assert K.decode(K.encode(current))==current


def test_multiline_disclosure_is_one_complete_review_unit(tmp_path):
    _,_,args,policy,_,_=disclosure_fixture(tmp_path)
    value=S.read(args['disclosure']);value['method_assessments']=[]
    value['records'][0]['fields']['label']='"Operating costs\n  excluding depreciation"'
    renderer=S.skill_module(ROOT,'task-disclosure','disclose');value['agent_records']=renderer.agent_records(value['records'])
    args['disclosure']=bind_json(Path(args['disclosure']['path']),value)
    Path(args['instruction']['path']).write_text('Original task context\n'+renderer.render_section(value['agent_records'])+'\nOriginal output instructions')
    args['instruction']=record(args['instruction']['path'])
    manifest=bind_json(tmp_path/'multiline-manifest.json',S.disclosure_manifest(**args,root=ROOT))
    body=R.full_body(manifest,**args,root=ROOT)
    projected=E.plan(body,args['golden']['path'],args['delivered']['path'],root=ROOT,max_sentences=1)
    first=projected['batches'][0]
    assert '\n  excluding depreciation' in first['manifest']['sentences'][0]['text']
    assert first['manifest']['sentences'][0]['text'] in first['instruction']
    assert first['manifest']['sentences'][0]['text'] not in projected['batches'][1]['instruction']
    assert all('Original task context' in b['instruction'] and 'Original output instructions' in b['instruction'] for b in projected['batches'])

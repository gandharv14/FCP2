import copy,hashlib,json
import pytest
from scripts.batch import harbor_disclosure_manifest_revision as m


def fixture():
    units=[{'order':i,'text':text,'sentence_id':'id'+str(i),'record_ids':['record'+str(i)]} for i,text in enumerate(['A','B prefix','C'])]
    old=dict(schema_version='harbor-disclosure-sentence-manifest/v1',input_hashes={'same':'bytes'},agent_record_ids=['record0','record1','record2'],sentences=units)
    new=copy.deepcopy(old);new['sentences'][1].update(text='B prefix\ncomplete negative sign.',sentence_id='corrected-id1')
    rows=[dict(sentence_id=u['sentence_id'],text=u['text'],record_ids=u['record_ids'],golden_evidence=['direct formula'],delivered_evidence=['direct cell'],verdict='blocking' if i==1 else 'pass',findings=['truncated'] if i==1 else []) for i,u in enumerate(units)]
    review=dict(schema_version='harbor-disclosure-faithfulness/v1',model='gpt-5.6-sol-high',input_hashes=old['input_hashes'],manifest_sha256='old-sha',sentences=rows,blocking_findings=['truncated'],non_blocking_flags=[],passed=False)
    return old,new,review


def correction(scope):
    return dict(schema_version='harbor-disclosure-faithfulness/v1',model='gpt-5.6-sol-high',input_hashes=scope['input_hashes'],manifest_sha256='scope-sha',sentences=[dict(sentence_id=u['sentence_id'],text=u['text'],record_ids=u['record_ids'],golden_evidence=['independent formula'],delivered_evidence=['independent cell'],verdict='pass',findings=[]) for u in scope['sentences']],blocking_findings=[],non_blocking_flags=[],passed=True)


def test_disjoint_composition_retains_original_review_byte_equivalent():
    old,new,review=fixture();before=copy.deepcopy(review)
    scope,delta=m.partition(old,new,review,'old-sha')
    got=m.compose(old,new,review,'old-sha',scope,correction(scope),'scope-sha')
    assert got['sentences']==3 and got['retained_sentences']==2 and got['newly_reviewed_sentences']==1
    assert delta['changed_indices']==[1] and review==before and review['passed'] is False


@pytest.mark.parametrize('mutation',[
 lambda o,n,r:n['sentences'][0].update(text='rewritten passing clause'),
 lambda o,n,r:n['sentences'][1].update(text='different financial meaning'),
 lambda o,n,r:n['sentences'][1].update(record_ids=['other']),
 lambda o,n,r:n['input_hashes'].update(same='changed'),
 lambda o,n,r:r['sentences'][0].update(verdict='blocking',findings=['other substantive issue']),
 lambda o,n,r:r['sentences'][0].update(verdict='unknown'),
 lambda o,n,r:r['sentences'][0].update(golden_evidence=[]),
 lambda o,n,r:r['sentences'][0].update(text='substituted'),
 lambda o,n,r:r['sentences'].pop(),
 lambda o,n,r:n['sentences'].append(copy.deepcopy(n['sentences'][1])),
])
def test_wrong_delta_or_original_evidence_rejected(mutation):
    old,new,review=fixture();mutation(old,new,review)
    with pytest.raises(ValueError):m.partition(old,new,review,'old-sha')


@pytest.mark.parametrize('mutation',[
 lambda s,r:r['sentences'][0].update(verdict='blocking',findings=['still wrong']),
 lambda s,r:r['sentences'][0].update(delivered_evidence=[]),
 lambda s,r:r['sentences'][0].update(record_ids=['record0']),
 lambda s,r:r['sentences'].append(copy.deepcopy(r['sentences'][0])),
 lambda s,r:s['sentences'].append({'order':0,'text':'A','sentence_id':'id0','record_ids':['record0']}),
 lambda s,r:r.update(manifest_sha256='old-sha'),
])
def test_new_review_must_cover_exact_missing_partition(mutation):
    old,new,review=fixture();scope,_=m.partition(old,new,review,'old-sha');fresh=correction(scope);mutation(scope,fresh)
    with pytest.raises(ValueError):m.compose(old,new,review,'old-sha',scope,fresh,'scope-sha')


def test_final_gate_combines_two_actual_calls_and_rechecks_workbooks(tmp_path,monkeypatch):
    """Receipt ownership is separate; both Calls and financial context are real."""
    from pathlib import Path
    from scripts.batch import harbor_semantic_readiness as r,harbor_disclosure_revision as dispatch
    from scripts.batch.harbor_model_review_provenance import validate_call
    from test_harbor_semantic_readiness import disclosure_fixture,bind_json,set_call,ROOT
    from test_harbor_model_review_provenance import fixture as journal
    call,manifest,args,policy,request,review=disclosure_fixture(tmp_path)
    renderer=r.skill_module(ROOT,'task-disclosure','disclose')
    payload=r.read(args['disclosure']);payload['records'][0]['fields']['label']='"Operating costs\n     excluding depreciation"'
    payload['agent_records']=renderer.agent_records(payload['records']);args['disclosure']=bind_json(Path(args['disclosure']['path']),payload)
    Path(args['instruction']['path']).write_text('Build this model.\n\n'+renderer.render_section(payload['records']))
    args['instruction']=r.record(args['instruction']['path']);full=r.disclosure_manifest(**args,root=ROOT)
    old=copy.deepcopy(full)
    for unit in old['sentences']:
        unit['text']=unit['text'].split('\n',1)[0]
        unit['sentence_id']='sentence-'+r._hash(r._canonical({k:v for k,v in unit.items() if k!='sentence_id'}))[:24]
    old_binding=bind_json(tmp_path/'original-manifest.json',old)
    review.update(input_hashes=old['input_hashes'],manifest_sha256=old_binding['sha256'],passed=False,blocking_findings=['truncated'])
    for i,(row,unit) in enumerate(zip(review['sentences'],old['sentences'])):
        row.update({k:unit[k] for k in ('sentence_id','text','record_ids')});row.update(verdict='blocking' if i==0 else 'pass',findings=['truncated'] if i==0 else [])
    body=dict(manifest=old,manifest_sha256=old_binding['sha256'],instruction=Path(args['instruction']['path']).read_text(),disclosure=payload,workbooks={k:r.workbook_evidence(args[k]) for k in ('golden','delivered')})
    request['messages'][1]['content']=json.dumps(body);set_call(call,request,review)
    old_result=validate_call(call,request,policy=policy)
    scope,_=m.partition(old,full,review,old_binding['sha256']);scope_binding=bind_json(tmp_path/'scoped.json',scope)
    newdir=tmp_path/'new';newdir.mkdir();newcall,_,newrequest=journal(newdir,'naturalization')
    new_review=correction(scope);new_review['manifest_sha256']=scope_binding['sha256']
    newrequest['messages'][1]['content']=json.dumps({**body,'manifest':scope,'manifest_sha256':scope_binding['sha256']})
    set_call(newcall,newrequest,new_review)
    response=r.read(newcall.response);response['id']='independent-corrective-response';object.__setattr__(newcall,'response',bind_json(Path(newcall.response['path']),response))
    completed=r.read(newcall.completed);completed['response']=newcall.response;object.__setattr__(newcall,'completed',bind_json(Path(newcall.completed['path']),completed))
    permit=dict(schema_version=m.SCHEMA,full_manifest=bind_json(tmp_path/'full.json',full),manifest=scope_binding,old_manifest=old_binding)
    binding=bind_json(tmp_path/'permit.json',permit)
    monkeypatch.setattr(m,'owned',lambda *a:None)
    monkeypatch.setattr(m,'original',lambda *a:(call,validate_call(call,request,policy=policy),body))
    monkeypatch.setattr(dispatch,'validate_review_history',lambda *a:{})
    result=r.disclosure_review(newcall,scope_binding,**args,policy=policy,revision=binding,root=ROOT)
    assert result['retained_sentences']==2 and result['newly_reviewed_sentences']==1 and result['records']==3
    assert result['retained_provenance']['review']['passed'] is False
    assert result['provenance']['attempt_id']!=result['retained_provenance']['attempt_id']
    body2=json.loads(newrequest['messages'][1]['content']);body2['workbooks']['golden']['sheets'][0]['cells'].pop()
    newrequest['messages'][1]['content']=json.dumps(body2);set_call(newcall,newrequest,new_review)
    with pytest.raises(ValueError,match='workbook snapshot'):r.disclosure_review(newcall,scope_binding,**args,policy=policy,revision=binding,root=ROOT)

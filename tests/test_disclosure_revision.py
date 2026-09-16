"""Corrective version is a new reviewed budget, never an erased rejection."""
import copy
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.batch import harbor_disclosure_revision as R
from scripts.batch import harbor_instruction_producer as P
from scripts.batch import harbor_model_executor as E
from scripts.batch.harbor_admission import record


def manifest():
    return dict(schema_version='harbor-disclosure-sentence-manifest/v1',input_hashes={},agent_record_ids=['a','b'],sentences=[
        dict(sentence_id='s1',order=0,text='Growth copies EBITDA.',record_ids=['a']),
        dict(sentence_id='s2',order=1,text='Keep other scope.',record_ids=['b'])])


def review():
    m=manifest()
    return dict(schema_version='harbor-disclosure-faithfulness/v1',model='gpt-5.6-sol-high',input_hashes={},manifest_sha256='m',passed=False,blocking_findings=['Incorrect target label'],non_blocking_flags=[],sentences=[
        {k:x[k] for k in ('sentence_id','text','record_ids')} | dict(golden_evidence=['row label'],delivered_evidence=['actual cell'],findings=['Incorrect target label'] if i==0 else [],verdict='blocking' if i==0 else 'pass') for i,x in enumerate(m['sentences'])])


def disclosure():
    row=dict(label='Growth copies source',note='Growth copies source',fields={'label':'"Growth"','ingredient':'Source!J13'},cells=['Target!J13'],entry='source_selection')
    return dict(records=[row],agent_records=[row],method_assessments=[{'scope':['Target!J13'],'role_context':['Growth'],'formula':'=Source!J13'}],golden='immutable')


def correction():
    a=disclosure();b=copy.deepcopy(a)
    for seq in ('records','agent_records'):
        b[seq][0].update(label='EBITDA copies source',note='EBITDA copies source');b[seq][0]['fields']['label']='"EBITDA"'
    b['method_assessments'][0]['role_context']=['EBITDA']
    old=manifest();new=copy.deepcopy(old);new['sentences'][0]['text']='EBITDA copies EBITDA.'
    return a,b,old,new


def test_complete_rejection_retains_blocking_findings():
    value=review();prior=copy.deepcopy(value)
    assert R.rejected(value,manifest(),'m')==[0]
    assert value==prior and value['passed'] is False


@pytest.mark.parametrize('change',[lambda x:x['sentences'].pop(),lambda x:x['sentences'][0].update(verdict='uncertain'),lambda x:x.update(passed=True),lambda x:x['sentences'][0].update(golden_evidence=[])])
def test_incomplete_uncertain_or_successful_review_is_not_revision_authority(change):
    value=review();change(value)
    with pytest.raises(ValueError):R.rejected(value,manifest(),'m')


def test_only_rejected_labels_change_all_sources_and_passing_sentences_preserved():
    assert R.label_changes(*correction(),[0])['sentence_indices']==[0]


@pytest.mark.parametrize('kind',['source','formula','passing','missing','metadata'])
def test_correction_cannot_change_scope_or_omit_records(kind):
    a,b,old,new=correction()
    if kind=='source':b['records'][0]['fields']['ingredient']='Different!J13'
    if kind=='formula':b['method_assessments'][0]['formula']='=Source!K13'
    if kind=='passing':new['sentences'][1]['text']='Changed other scope'
    if kind=='missing':b['records']=[]
    if kind=='metadata':b['golden']='other'
    with pytest.raises(ValueError):R.label_changes(a,b,old,new,[0])


def put(p,value):p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(E.canonical(value));return record(p)


def test_evaluator_delta_is_not_a_disclosure_revision(tmp_path):
    old=tmp_path/'old';new=tmp_path/'new';name='xl_seg/evaluate.py'
    for root,value in ((old,'original'),(new,'changed')):(root/'xl_seg').mkdir(parents=True);(root/name).write_text(value)
    binding=put(tmp_path/'snapshot.json',dict(root=str(old),files=[dict(relative=name,sha256=record(old/name)['sha256'])]))
    with pytest.raises(ValueError,match='evaluator'):R.changes(binding,new)


def test_new_review_scope_carries_original_history_and_never_reuses_uuid(tmp_path,monkeypatch):
    tracker=put(tmp_path/'tracker.json',{'tracker':'real retained identity'})
    receipt=put(tmp_path/'permit.json',{'revision':2,'limit':2,'original_attempt_id':'old','physical_count':1})
    t=SimpleNamespace(run=tmp_path/'producer',tracker=tracker,policy=SimpleNamespace(model='m',reasoning_effort='high'),stage_tree={},disclosure_revision=receipt,dialogue_pre_review_repair=None)
    identity=dict(workbook='0056',original={'sha256':'a'*64})
    monkeypatch.setattr(P,'lane_context',lambda *a:({},identity));monkeypatch.setattr(P,'code_evidence',lambda t:[]);monkeypatch.setattr(R,'validate_task',lambda t:read_receipt(t))
    req=P._request(t,'disclosure-review',P.DISCLOSURE,1,1,{},'Review every sentence',[])
    assert req.prepared.scope.budget_id==R.BUDGET and req.prepared.ordinal==req.prepared.limit==1
    assert req.prepared.attempt_id!='old'
    context=P.read(req.context);assert context['disclosure_revision']==receipt
    assert any(E.checked_bytes(b)==E.checked_bytes(receipt) for b in req.prepared.budget_evidence)
    assert not (t.run/'requests/disclosure-review').exists()


def read_receipt(t):return P.read(t.disclosure_revision)


def test_semantic_selection_old_codec_defaults_no_revision():
    from scripts.batch.harbor_complete_readiness import SemanticSelection
    from scripts.batch import harbor_transition_codec as C
    value=SemanticSelection(None,None,None,None,None,())
    encoded=C.encode(value);del encoded['value']['disclosure_revision']
    assert C.decode(encoded)==value
    revised=__import__('dataclasses').replace(value,disclosure_revision={'path':'/bound','sha256':'b'*64})
    assert C.decode(C.encode(revised))==revised


def test_suppressed_records_and_unrelated_assessments_remain_original():
    old=disclosure();hidden=copy.deepcopy(old['records'][0]);hidden.update(cells=['Target!Q13'],entry='hold_growth')
    old['records'].append(hidden)
    detected=copy.deepcopy(old);detected['records'][1]['fields']['ingredient']='new metadata'
    detected['method_assessments'][0]['role_context']=['different context']
    result=R.corrected_payload(old,detected)
    assert result['records'][1]==hidden
    assert result['method_assessments']==old['method_assessments']


def test_second_namespace_cannot_reopen_global_revision(tmp_path,monkeypatch):
    from scripts.batch import harbor_instruction_worker as W
    run=tmp_path/'run';run.mkdir();(run/'worker.lock').touch()
    put(run/'disclosure-revision.json',{'directory':'retained interrupted first preparation'})
    worker=SimpleNamespace(run=run,disclosure_repair={'path':'old'},disclosure_revision=None,lease_renewal=None)
    monkeypatch.setattr(W,'active',lambda *a:pytest.fail('second revision must fail before active mutation'))
    with pytest.raises(ValueError,match='already used'):
        R.authorize(worker,None,None,old_snapshot=None,directory=tmp_path/'other-namespace')
    assert not (tmp_path/'other-namespace').exists()


@pytest.fixture
def task_authority(tmp_path,monkeypatch):
    from scripts.batch import harbor_instruction_worker as W
    run=tmp_path/'worker';stage=tmp_path/'stage';stage.mkdir()
    instruction=put(stage/'instruction.md',{'exact':'instruction'});disclosure=put(stage/'tests/disclosure.json',{'records':[]})
    directory=tmp_path/'revision';permit=put(directory/'permit.json',dict(schema_version=R.SCHEMA,revision=2,limit=2,budget_id=R.BUDGET,semantic_budget_reset=False,worker_run=str(run),selection={},current_code={'code':'bound'},instruction=instruction,disclosure=disclosure))
    put(run/'disclosure-revision.json',{'directory':str(directory)})
    put(run/'disclosure-revision-complete.json',permit)
    t=SimpleNamespace(disclosure_revision=permit,run=run/'producer',stage=stage)
    monkeypatch.setattr(R.codec,'decode',lambda _:SimpleNamespace(content=SimpleNamespace(stage=stage)))
    monkeypatch.setattr(W,'code',lambda _: {'code':'bound'})
    monkeypatch.setattr(R,'permit_history',lambda p:p)
    return t


def test_task_accepts_exact_completed_reservation(task_authority):
    assert R.validate_task(task_authority)['revision']==2


@pytest.mark.parametrize('kind',['permit','stage','instruction','disclosure','namespace'])
def test_forged_revision_binding_or_content_cannot_prepare_review(task_authority,kind,tmp_path):
    t=task_authority
    if kind=='permit':t.disclosure_revision=put(tmp_path/'forged.json',P.read(t.disclosure_revision))
    if kind=='stage':t.stage=tmp_path/'other'
    if kind=='instruction':(t.stage/'instruction.md').write_text('changed')
    if kind=='disclosure':(t.stage/'tests/disclosure.json').write_text('{}')
    if kind=='namespace':t.run=tmp_path/'fresh-budget'
    with pytest.raises(ValueError):R.validate_task(t)


def test_old_snapshot_must_be_actual_failed_worker_root(tmp_path):
    old=tmp_path/'actual';foreign=tmp_path/'foreign';name='scripts/batch/harbor_instruction_worker.py'
    for root in (old,foreign):(root/name).parent.mkdir(parents=True);(root/name).write_text('identical bytes')
    prior={'current_code':{str(old/name):record(old/name)['sha256']}}
    wrong=put(tmp_path/'wrong.json',dict(root=str(foreign),files=[dict(relative=name,sha256=record(foreign/name)['sha256'])]))
    with pytest.raises(ValueError,match='actual physical'):R.historical_code(wrong,prior)
    right=put(tmp_path/'right.json',dict(root=str(old),files=[dict(relative=name,sha256=record(old/name)['sha256'])]))
    assert R.historical_code(right,prior)['root']==str(old)


def test_exact_section_replacement_preserves_all_outside_bytes():
    renderer=SimpleNamespace(render_section=lambda records:records[0])
    old='Old disclosure\n';new='New disclosure\n'
    original='Before\n\n\n'+old+'\n\n## Output\nUntouched\n'
    result=R.exact_instruction(renderer,original,{'agent_records':[old]},{'agent_records':[new]})
    assert result=='Before\n\n\n'+new+'\n\n## Output\nUntouched\n'
    with pytest.raises(ValueError,match='ambiguous'):R.exact_instruction(renderer,original+old,{'agent_records':[old]},{'agent_records':[new]})


@pytest.mark.parametrize('existing',['permit','request','journal','steps','completion'])
def test_preparation_recovery_cannot_reopen_any_prepared_or_dispatched_scope(tmp_path,existing):
    run=tmp_path/'worker';directory=tmp_path/'revision';campaign=SimpleNamespace(root=tmp_path/'campaign')
    worker=SimpleNamespace(run=run,campaign=campaign)
    prepared=SimpleNamespace(scope=E.SemanticScope('0056','a'*64,'harbor-disclosure-writer','disclosure-review'))
    if existing=='permit':put(directory/'permit.json',{})
    if existing=='completion':put(run/'disclosure-revision-complete.json',{})
    if existing=='request':(run/'producer/requests'/R.BUDGET).mkdir(parents=True)
    if existing=='steps':(directory/'steps').mkdir(parents=True)
    if existing=='journal':
        scope=E.SemanticScope('0056','a'*64,'harbor-disclosure-writer',R.BUDGET)
        (campaign.root/E.sha(E.canonical(__import__('dataclasses').asdict(scope)))).mkdir(parents=True)
    with pytest.raises(ValueError):R.no_corrective_request(worker,directory,prepared)


def test_additional_renewal_must_extend_exact_first_renewal(tmp_path,monkeypatch):
    from scripts import harbor_campaign as C
    from scripts.batch.harbor_workbook_bootstrap import Lease
    from scripts.batch import harbor_nat_retry as N
    before_binding=put(tmp_path/'before.json',{'revision':10})
    after_binding=put(tmp_path/'after.json',{'revision':11})
    old=Lease('owner','token',10,before_binding['sha256']);new=Lease('owner','token',11,after_binding['sha256'])
    original={'lease':C.encode(old)}
    value=dict(previous=C.encode(old),lease=C.encode(new),before=before_binding,tracker=after_binding,result={'revision':11,'sha256':new.sha256})
    calls=[];monkeypatch.setattr(N,'_renewal_chain',lambda *args:calls.append(args))
    binding=put(tmp_path/'renewal.json',value)
    R.validate_additional_renewal(original,binding,{'revision':11},new)
    assert len(calls)==1 and calls[0][0]==before_binding
    with pytest.raises(ValueError,match='explicit additional'):R.validate_additional_renewal(original,None,{'revision':11},new)
    forged=copy.deepcopy(value);forged['previous']=C.encode(Lease('other','token',10,old.sha256))
    with pytest.raises(ValueError,match='exact retained'):R.validate_additional_renewal(original,put(tmp_path/'forged.json',forged),{'revision':11},new)
    assert len(calls)==1


def test_production_campaign_paths_are_json_serializable_in_permit(tmp_path):
    campaign=E.Campaign(tmp_path/'model-campaign',tmp_path/'model-slots',1)
    payload={'schema_version':R.SCHEMA,'campaign':R.campaign_record(campaign),'revision':2,'limit':2,
        'selection':R.codec.encode(__import__('scripts.batch.harbor_complete_readiness',fromlist=['SemanticSelection']).SemanticSelection(None,None,None,None,None,())),
        'old_call':R.codec.encode(__import__('scripts.batch.harbor_model_review_provenance',fromlist=['Call']).Call(*([{'path':'/retained','sha256':'a'*64}]*4)))}
    encoded=E.canonical(payload)
    actual=__import__('json').loads(encoded)
    assert actual['campaign']=={'root':str(campaign.root),'slot_root':str(campaign.slot_root),'model_slots':1}


def test_full_request_validation_handles_binary_workbook_budget_before_receipt(tmp_path,monkeypatch):
    from scripts.harbor_instruction_code_applicability import validate
    from scripts import harbor_instruction_code_applicability as A
    from scripts.batch.harbor_model_review_provenance import Policy
    original={'sha256':'a'*64};revision=put(tmp_path/'revision.json',{'revision':2})
    binary=tmp_path/'golden.xlsx';binary.write_bytes(b'PK\x03\x04\xe5\xff\x00');receipt_snapshot=tmp_path/'receipt.snapshot';receipt_snapshot.write_bytes(E.checked_bytes(revision))
    payload=put(tmp_path/'request.json',{})
    context=put(tmp_path/'context.json',dict(kind='disclosure-review',stage_tree={},workbook='0056',original=original,
        disclosure_revision=revision,evidence=[revision],budget_evidence=[record(binary),record(receipt_snapshot)],request=payload,ordinal=1,limit=1,response_mode='json_object',code=[]))
    t=SimpleNamespace(disclosure_revision=revision,run=tmp_path/'producer',stage_tree={},code_repair=None,dialogue_pre_review_repair=None)
    prepared=E.PreparedCall(payload,Policy('project'),E.SemanticScope('0056','a'*64,P.DISCLOSURE,R.BUDGET),1,1,'00000000-0000-4000-8000-000000000001',(record(binary),record(receipt_snapshot)))
    request=P.Request(prepared,context)
    monkeypatch.setattr(P,'task_context',lambda t:({},dict(workbook='0056',original=original)))
    monkeypatch.setattr(R,'validate_task',lambda t:{})
    monkeypatch.setattr(P,'code_evidence',lambda t:[])
    assert P.validate_request(t,request,'disclosure-review')['disclosure_revision']==revision
    binary.write_bytes(b'changed binary')
    with pytest.raises(ValueError):P.validate_request(t,request,'disclosure-review')

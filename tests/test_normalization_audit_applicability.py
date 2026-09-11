"""Root-selected exact request/context and independent current proof fixtures."""
import copy
from dataclasses import asdict
import json
from pathlib import Path
import pytest

import xl_audit_equivalence as eq
import xl_audit_validator_upgrade as upgrade
from scripts.batch import harbor_normalization_audit_applicability as app
from scripts.batch.harbor_admission import record
from scripts.batch.harbor_review_context import json_value,normalization_review
from test_audit_validator_upgrade import equivalence_case,save
from test_harbor_model_review_provenance import fixture as journal_fixture
from test_harbor_review_context import set_normalization_response


@pytest.fixture
def continuity(equivalence_case,tmp_path):
    (old,new,output,calls),original,audit_upgrade=equivalence_case
    current=eq.propose_audit_equivalence(old,new,validator_upgrade=audit_upgrade,original_auditor=original)
    current_record=save(tmp_path/'current-locator.json',current)
    original_code=tmp_path/'original-locator-code.py';original_code.write_text('raise RuntimeError("never execute historical code")\n')
    previous=copy.deepcopy(current);previous.pop('validator_upgrade');previous.pop('original_auditor')
    previous['adapter_sha256']=upgrade.digest(original_code);previous['auditor_sha256']=upgrade.ORIGINAL_SHA256
    proof=json.loads(Path(audit_upgrade['path']).read_text())
    files=previous['checked_files']
    for path in [audit_upgrade['path'],proof['identity']['original_generator']['path'],proof['identity']['adapter']['path'],proof['identity']['inventory_lookup_code']['path']]:files.pop(path)
    files.pop(str(Path(eq.__file__).resolve()))
    files[str(Path(eq.audit.__file__).resolve().parent/'xl_audit_equivalence.py')]=previous['adapter_sha256']
    files[str(Path(eq.audit.__file__).resolve())]=upgrade.ORIGINAL_SHA256
    previous_record=save(tmp_path/'original-locator.json',previous)
    bindings={**new,**{k:old[k] for k in upgrade.AUDIT_KEYS},'audit_equivalence':current_record,
        'audit_validator_upgrade':audit_upgrade,'audit_upgrade_original_code':original}
    return bindings,previous_record,record(original_code)


def test_exact_proof_only_continuity_current_checks(continuity):
    bindings,previous,code=continuity
    result=app.locator_continuity(previous,bindings['audit_equivalence'],bindings=bindings,original_locator_code=code)
    assert result['historical_code_executed'] is False and len(result['added_upgrade_evidence'])==4


@pytest.mark.parametrize('change',['source','targets','inventory','budget','drop_file','change_file','unknown_field','old_code','old_auditor'])
def test_no_financial_history_or_missing_evidence_exemptions(continuity,change):
    bindings,previous,code=continuity;p=Path(previous['path']);value=json.loads(p.read_text())
    if change=='source':value['current_bindings']['source_file']['sha256']='0'*64
    elif change=='targets':value['source_builder_equivalence']={'changed':True}
    elif change=='inventory':value['current_inventory_sha256']='0'*64
    elif change=='budget':value['retained_consumption']['submitted_counts']['1']=0
    elif change in ['drop_file','change_file']:
        key=next(k for k in value['checked_files'] if not k.endswith('.py'))
        if change=='drop_file':value['checked_files'].pop(key)
        else:value['checked_files'][key]='0'*64
    elif change=='unknown_field':value['extra']=True
    elif change=='old_code':value['adapter_sha256']='0'*64
    else:value['auditor_sha256']='0'*64
    previous=save(p,value)
    with pytest.raises(ValueError):app.locator_continuity(previous,bindings['audit_equivalence'],bindings=bindings,original_locator_code=code)


def normalization_fixture(tmp_path,continuity):
    bindings,previous,code=continuity
    draft=save(tmp_path/'draft.json',{'rows':[]})
    source=bindings['source_file'];baseline=bindings['baseline_inputs']
    hashes=dict(source_sha256=source['sha256'],baseline_sha256=baseline['sha256'],draft_sha256=draft['sha256'])
    rows=[dict(clean=True,reference='Inputs!B1',cells=[('Inputs','B1',0.25)],duplicate_refs=[],evidence=hashes)]
    body=dict(source=source,baseline=baseline,draft=draft,**hashes,audit=bindings['variable_source_audit'],
        audit_equivalence=previous,current_source_generation=bindings['source_generation'],current_segmentation_generation=bindings['segmentation_generation'],
        candidates=json_value(rows),context={},candidate_primitive_checks=[dict(reference='Inputs!B1',source_data_type='n',baseline_data_type='n',source_equals_baseline=True,number_format='General',duplicate_refs=[])])
    evidence=save(tmp_path/'evidence.json',body);analysis=save(tmp_path/'analysis.json',json_value(rows))
    call,policy,request=journal_fixture(tmp_path,'normalization')
    request['messages'][1]['content']=Path(evidence['path']).read_text()
    object.__setattr__(call,'request',save(Path(call.request['path']),request))
    submitted=json.loads(Path(call.submitted['path']).read_text());submitted['request_sha256']=call.request['sha256'];object.__setattr__(call,'submitted',save(Path(call.submitted['path']),submitted))
    review=dict(schema_version='normalization-review/v1',reviewer='Independent fixture',**hashes,cells={'Inputs!B1':dict(eligible=True,reason='Supplied assumption',evidence_refs=['Inputs!B1'],entity='Fixture',period='2025',scenario='Base',basis='Workbook units')})
    set_normalization_response(call,review)
    prepared=save(tmp_path/'prepared.json',dict(request=call.request,evidence=evidence,analysis=analysis))
    args=dict(source=source,baseline=baseline,draft=draft,rows=rows,bindings=bindings,policy=policy,original_locator_code=code)
    return prepared,call,args


def test_full_current_audit_and_exact_model_request_applicability(continuity,tmp_path):
    prepared,call,args=normalization_fixture(tmp_path,continuity)
    old_files={Path(r['path']):Path(r['path']).read_bytes() for r in [prepared,*asdict(call).values()] if r}
    with pytest.raises(ValueError,match='audit applicability changed'):
        normalization_review(call,prepared,**{k:v for k,v in args.items() if k!='original_locator_code'})
    proposal=app.propose_applicability(prepared,call,**args)
    assert proposal['semantic_attempts_added']==0 and proposal['lane_approval'] is False
    receipt=save(tmp_path/'applicability.json',proposal)
    result=app.validate_applicability(receipt,prepared,call,**args)
    assert result['review']['cells']['Inputs!B1']['eligible'] is True
    assert all(p.read_bytes()==value for p,value in old_files.items())


@pytest.mark.parametrize('change',['context','candidate','generation','draft','request','parsed','current_proof'])
def test_no_context_prompt_response_or_current_proof_reinterpretation(continuity,tmp_path,monkeypatch,change):
    prepared,call,args=normalization_fixture(tmp_path,continuity)
    if change=='context':
        manifest=json.loads(Path(prepared['path']).read_text());p=Path(manifest['evidence']['path']);body=json.loads(p.read_text());body['context']={'Inputs':[dict(reference='Inputs!B1',value=0.5,data_type='n',number_format='General')]};p.write_text(json.dumps(body))
    elif change=='candidate':args['rows'][0]['cells'][0]=('Inputs','B1',0.5)
    elif change=='generation':args['bindings']['source_generation']['id']='0'*64
    elif change=='draft':Path(args['draft']['path']).write_text('{"changed":true}')
    elif change=='request':Path(call.request['path']).write_text('{}')
    elif change=='parsed':Path(call.parsed['path']).write_text('{}')
    else:
        monkeypatch.setattr(eq,'_context',lambda *a,**k:(_ for _ in ()).throw(ValueError('current proof failed')))
    with pytest.raises(ValueError):app.propose_applicability(prepared,call,**args)


@pytest.mark.parametrize('change',[None,'current_row','old_hash','old_bytes','extra_path','old_analysis'])
def test_exact_draft_copy_reanalysis_only_path_difference(tmp_path,monkeypatch,change):
    import gen_normalizer as gen
    from test_harbor_normalization_producer import setup
    from scripts.batch import harbor_normalization_producer as producer
    factory=setup.__wrapped__(tmp_path,monkeypatch);selection,*_=factory('0001')
    prepared=producer.prepare(selection);manifest=producer.read(prepared);body=producer.read(manifest['evidence'])
    current=tmp_path/'new-draft-run'/'0001-variable-sources'/'draft.json';current.parent.mkdir(parents=True);current.write_bytes(Path(body['draft']['path']).read_bytes());draft=record(current)
    _,rows=gen.analyse('0001',selection.bindings['source_file']['path'],selection.bindings['source_file']['sha256'],runs_root=current.parent.parent,inputs_root=Path(selection.bindings['baseline_inputs']['path']).parent)
    if change=='current_row':rows[0]['reference']='Inputs!ZZ9'
    elif change=='old_hash':body['draft']['sha256']='0'*64
    elif change=='old_bytes':Path(body['draft']['path']).write_text('{}')
    elif change=='extra_path':rows[0]['evidence_paths']['source_sha256']='wrong'
    elif change=='old_analysis':Path(manifest['analysis']['path']).write_text('[]')
    kwargs=dict(source=selection.bindings['source_file'],baseline=selection.bindings['baseline_inputs'],draft=draft,rows=rows)
    if change is None:
        actual,proof=app.draft_relocation(body,manifest,**kwargs)
        assert proof['same_bytes'] and proof['changed_field']=='rows[*].evidence_paths.draft_sha256'
        assert json_value(actual)==producer.read(manifest['analysis'])
    else:
        with pytest.raises((ValueError,SystemExit)):app.draft_relocation(body,manifest,**kwargs)

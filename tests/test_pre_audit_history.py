import json
from pathlib import Path
import pytest
from scripts.batch import harbor_pre_audit_history as P

@pytest.fixture
def case(tmp_path,monkeypatch):
    runs=tmp_path/'runs';tracks=runs/'batch'/'workbooks';tracks.mkdir(parents=True)
    source=tmp_path/'0150.xlsx';source.write_bytes(b'original workbook')
    c=tmp_path/'curation.toml';c.write_text('preserved=true')
    diag=runs/'0150-source-health.json';diag.write_text('{}')
    driver=runs/'batch'/'driver.py';driver.write_text('historical bytes are never executed')
    monkeypatch.setattr(P,'DRIVER_SHA',P.binding(driver)['sha256'])
    d={'workbook_id':'0150','source_candidate':str(source),'source_health':{'source_sha256':P.binding(source)['sha256']},'status':'blocked','mode':None,'staged_path':None,'promotion_attempted':False,'first_blocker':{'gate':'segmentation_verification'},'gates':{'source_health':'pass','segmentation_verification':'fail'},'curation':{'preserved':True,'recurate_used':False,'sha256_before':P.binding(c)['sha256'],'sha256_after':P.binding(c)['sha256']},'diagnostics':[str(diag)]}
    tracker=tracks/'0150.json';tracker.write_text(json.dumps(d))
    args=dict(workbook='0150',original=P.binding(source),tracker=P.binding(tracker),curations=[P.binding(c)],driver=P.binding(driver),history_roots=[runs],known_ids={'0150','0200'})
    return args,d,tmp_path

def save(tmp,value):
    path=tmp/'proof.json';path.write_text(json.dumps(value));return P.binding(path)

def test_exact_rederivation_retains_attempts_without_grant(case):
    a,d,tmp=case;v=P.project(**a)
    assert v['historical_tracker_value']==d
    assert 'without_reset' in v['local_attempt_history']
    assert P.validate(save(tmp,v),**{k:a[k] for k in ['workbook','original','history_roots','known_ids']})==v

@pytest.mark.parametrize('mutation',['late_gate','unknown_field','promoted','wrong_source','curation_change','missing_diagnostic','extra_model_file','extra_metadata','symlink'])
def test_unsafe_histories_rejected(case,mutation):
    a,d,tmp=case
    if mutation=='late_gate':d['gates']['audit']='pass'
    elif mutation=='unknown_field':d['api_calls']=1
    elif mutation=='promoted':d['staged_path']='/old/task'
    elif mutation=='wrong_source':d['source_health']['source_sha256']='0'*64
    elif mutation=='curation_change':Path(a['curations'][0]['path']).write_text('changed')
    elif mutation=='missing_diagnostic':Path(d['diagnostics'][0]).unlink()
    elif mutation=='extra_model_file':(a['history_roots'][0]/'0150-request.json').write_text('{}')
    elif mutation=='extra_metadata':(a['history_roots'][0]/'0150-extra.json').write_text('{"api_calls":1}')
    elif mutation=='symlink':(a['history_roots'][0]/'0150-link').symlink_to(tmp)
    Path(a['tracker']['path']).write_text(json.dumps(d));a['tracker']=P.binding(a['tracker']['path'])
    with pytest.raises(ValueError):P.project(**a)

def test_proof_is_not_a_permission_boolean(case):
    a,d,tmp=case;v=P.project(**a);v['model_history_route']='force_new_budget'
    with pytest.raises(ValueError,match='rederivation'):P.validate(save(tmp,v),**{k:a[k] for k in ['workbook','original','history_roots','known_ids']})

def test_namespace_addition_after_proof_rejected(case):
    a,d,tmp=case;proof=save(tmp,P.project(**a));(a['history_roots'][0]/'0150-extra.log').write_text('extra local history')
    with pytest.raises(ValueError,match='rederivation'):P.validate(proof,**{k:a[k] for k in ['workbook','original','history_roots','known_ids']})

def test_narrowed_namespace_not_accepted(case):
    a,d,tmp=case;proof=save(tmp,P.project(**a));args={k:a[k] for k in ['workbook','original','history_roots','known_ids']};args['history_roots']=[a['history_roots'][0]/'batch']
    with pytest.raises(ValueError,match='namespace'):P.validate(proof,**args)

def test_batch_range_name_does_not_attribute_other_workbooks(case):
    a,d,tmp=case;directory=a['history_roots'][0]/'assigned-0150-0200';directory.mkdir()
    (directory/'0200-variable-source-audit.log').write_text('other workbook')
    assert P.project(**a)['workbook']=='0150'
    (directory/'0150-variable-source-audit.log').write_text('this workbook')
    with pytest.raises(ValueError,match='post-segmentation'):P.project(**a)

def test_owning_admission_requires_and_revalidates_migration(case,monkeypatch):
    from scripts.batch import harbor_admission as A
    from types import SimpleNamespace
    a,d,tmp=case;proof=save(tmp,P.project(**a))
    inv=tmp/'inventory.json';inv.write_text('{}');ib=P.binding(inv)
    inventory={'sources':[]}
    policy=SimpleNamespace(batch='test',requested_ids=('0150',),tracker_root=tmp/'new',mode='new',campaign_evidence=(),original_inventory=ib,dependency={},dependencies=None,history_roots=a['history_roots'])
    obj=A.DispatcherAdmission.__new__(A.DispatcherAdmission);obj.policy=policy;obj.identity=('test',('0150',),str(tmp/'new'),'new');obj.inventory=inventory;obj.sources={'0150':dict(a['original']),'0200':{}}
    monkeypatch.setattr(A,'read_inventory',lambda p:inventory)
    monkeypatch.setattr(A,'validate_dependency',lambda *args:True)
    monkeypatch.setattr(obj,'_source',lambda *args:(a['original'],a['original'],{},[a['original']]))
    # Stub only unrelated dependency/source computation. Actual namespace scan,
    # migration proof rederivation, and admission history fence run unchanged.
    with pytest.raises(ValueError,match='lossless resume'):obj._check('0150')
    policy.campaign_evidence=(proof,)
    request,history=obj._check('0150')
    assert a['tracker'] in request['intake_evidence'] and proof in request['intake_evidence']
    assert history==[a['tracker']]
    Path(d['diagnostics'][0]).write_text('{"changed":true}')
    with pytest.raises(ValueError,match='rederivation'):obj._check('0150')

def test_exact_numeric_cohort_directory_scopes_children(case):
    a,d,tmp=case;directory=a['history_roots'][0]/'0150-0200';directory.mkdir()
    (directory/'0200-response.json').write_text('{"messages": []}')
    P.project(**a)
    (directory/'0150-response.json').write_text('{"messages": []}')
    with pytest.raises(ValueError):P.project(**a)

@pytest.mark.parametrize('payload',[{'workbook_id':'0150','api_calls':1},{'messages':[],'model':'x'}])
def test_unmarked_json_consumption_detected(case,payload):
    a,d,tmp=case;(a['history_roots'][0]/'opaque.json').write_text(json.dumps(payload))
    with pytest.raises(ValueError):P.project(**a)

def test_parent_symlink_rejected(case):
    a,d,tmp=case;link=tmp/'alias';link.symlink_to(a['history_roots'][0],target_is_directory=True)
    a['tracker']={'path':str(link/'batch/workbooks/0150.json'),'sha256':a['tracker']['sha256']}
    with pytest.raises(ValueError):P.project(**a)

def test_hardlinked_curation_paths_retained_explicitly(case):
    import os
    a,d,tmp=case;alias=tmp/'curation-alias.toml';os.link(a['curations'][0]['path'],alias);a['curations'].append(P.binding(alias))
    proof=P.project(**a)
    assert sorted([a['curations'][0]['path'],str(alias)]) in proof['physical_alias_groups']
    assert len(proof['curations'])==2

def test_explicit_namespace_alias_preserves_lexical_and_canonical_evidence(case):
    a,d,tmp=case
    target=tmp/'seg_out';target.mkdir();real=target/'curation.toml';real.write_bytes(Path(a['curations'][0]['path']).read_bytes())
    repo=tmp/'synthetic-data-pipeline';repo.mkdir();alias=repo/'seg_out';alias.symlink_to(target,target_is_directory=True)
    lexical=alias/'curation.toml';a['curations']=[P.binding(real),{'path':str(lexical),'sha256':P.binding(real)['sha256']}]
    a['historical_path_aliases']=[{'path':str(alias),'target':str(alias.readlink()),'canonical_root':str(target)}]
    d['diagnostics'].append(str(lexical));Path(a['tracker']['path']).write_text(json.dumps(d));a['tracker']=P.binding(a['tracker']['path'])
    v=P.project(**a)
    assert v['curations'][1]['path']==str(lexical)
    assert v['canonical_curations'][1]==P.binding(real)
    proof=save(tmp,v)
    P.validate(proof,**{k:a[k] for k in ['workbook','original','history_roots','known_ids']})
    alias.unlink();alias.symlink_to(tmp,target_is_directory=True)
    with pytest.raises(ValueError,match='alias changed'):P.validate(proof,**{k:a[k] for k in ['workbook','original','history_roots','known_ids']})

def test_arbitrary_alias_recipe_cannot_authorize_a_parent_symlink(case):
    a,d,tmp=case;alias=tmp/'unapproved';alias.symlink_to(tmp,target_is_directory=True)
    a['historical_path_aliases']=[{'path':str(alias),'target':str(tmp),'canonical_root':str(tmp)}]
    with pytest.raises(ValueError,match='unapproved'):P.project(**a)

def test_multibook_records_do_not_share_model_budgets(case):
    a,d,tmp=case;(a['history_roots'][0]/'multi.json').write_text(json.dumps([
        {'workbook':'0150','local_reconstruction':'failed'},
        {'workbook':'0200','api_calls':1}]))
    assert P.project(**a)['workbook']=='0150'

def test_unrelated_known_id_cannot_cover_unscoped_call(case):
    a,d,tmp=case;(a['history_roots'][0]/'multi.json').write_text(json.dumps([
        {'workbook':'0200','local_reconstruction':'failed'},
        {'messages':[],'model':'test'}]))
    with pytest.raises(ValueError,match='unscoped'):P.project(**a)

def test_nested_enclosing_workbook_owns_consumption(case):
    a,d,tmp=case;(a['history_roots'][0]/'multi.json').write_text(json.dumps(
        {'workbook':'0150','request':{'messages':[],'model':'test'}}))
    with pytest.raises(ValueError,match='consumption'):P.project(**a)

def test_closed_identity_request_is_local_not_model(case):
    from xl_source_publication import _binding_hash
    a,d,tmp=case;directory=a['history_roots'][0]/'0150-source';directory.mkdir();p=directory/'request.json'
    v={'mode':'no_recalculation_required','policy_version':'source-recalc-policy/v3','schema_version':'source-identity-request/v2','source_sha256':a['original']['sha256'],'request_id':'identity-'+a['original']['sha256'][:24]}
    v['request_sha256']=_binding_hash(v);p.write_text(json.dumps(v))
    assert P.project(**a)['workbook']=='0150'
    for key,val in [('messages',[]),('request_sha256','0'*64),('source_sha256','1'*64),('mode','model')]:
        bad=dict(v);bad[key]=val;p.write_text(json.dumps(bad))
        with pytest.raises(ValueError):P.project(**a)

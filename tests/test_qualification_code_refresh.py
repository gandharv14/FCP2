"""Historical immutable releases require current reconstruction proof for yield."""
import json
from pathlib import Path
import pytest
from scripts import corpus_qualification as candidate

def promoted(tmp_path, monkeypatch, module):
    wb = '0001'
    source = tmp_path/'original.xlsx'
    source.write_bytes(b'original')
    record = {'workbook_id':wb,'path':str(source),'sha256':module.digest(source)}
    root = tmp_path/'harbor'
    trackers = root/'workbooks'
    trackers.mkdir(parents=True)
    notes = trackers/'notes.md'
    notes.write_text('retained evidence')
    ids = {'source':'s','segmentation':'g','task':'t'}
    tracker = {'workbook_id':wb,'execution_state':'PROMOTED',
        'raw_source':{'sha256':record['sha256']},
        'agent_notes':{'path':str(notes),'sha256':module.digest(notes)},
        'promotion_failure':None,'current_confidence':{'level':'high'},
        'lane_state':{k:{'state':'passed','current_confidence':'high'} for k in module.REQUIRED_LANES},
        'bindings':{'source_publication_root':{'path':str(tmp_path/'source')},
                    'segmentation_workbook_root':{'path':str(tmp_path/'seg')}},
        'promotion_ids':{'release_id':'release','task_generation_id':'t'},'generation_ids':ids}
    source_dir = tmp_path/'source/generations/s';source_dir.mkdir(parents=True)
    (source_dir/'generation-manifest.json').write_text(json.dumps(
        {'bindings': {'source_sha256': record['sha256']}}))
    monkeypatch.setattr(module, 'validate_generation_lineage', lambda *a, **k: {'mode': 'original'})
    monkeypatch.setattr(module, '_validate_audit_proof', lambda *a: None)
    path = trackers/(wb+'.json')
    path.write_text(json.dumps(tracker))
    handoff = root/'handoff.json'
    handoff.write_text(json.dumps({'workbook_id':wb,'verdict':'PUBLICATION_READY',
        'promotion_failure':None,'docker_smoke':True,'grader_smoke_score':1.0,
        'curation_preserved':True,'solve_rollouts':0,'staged_tree_sha256':'tree'}))
    (root/'0001-publication-evidence.json').write_text(json.dumps({str(handoff):module.digest(handoff)}))
    manifest = {'release_id':'release', **{k+'_generation':{'generation_id':v} for k,v in ids.items()}}
    monkeypatch.setattr(module.release,'resolve_current_release_integrity',lambda *a,**k:(root/'release',manifest))
    monkeypatch.setattr(module.release,'validate_task_generation',lambda *a,**k:None)
    monkeypatch.setattr(module.release,'validate_release',lambda *a,**k:manifest)
    monkeypatch.setattr(module.release,'_tree_hash',lambda *a,**k:('output',{'generation-manifest.json':{},'task':{}}))
    monkeypatch.setattr(module.release,'canonical_hash',lambda *a,**k:'tree')
    return {'inventory_sha256':'frozen700','sources':[record]}, path, tracker




    # This reproduces the early rejection only. Accepting the tracker would
    # additionally require verified immutable transformation lineage, which
    # this synthetic fixture deliberately does not claim to provide.


def test_stale_retains_prior_promotion_but_not_current_numerator(tmp_path, monkeypatch):
    manifest, _, _ = promoted(tmp_path, monkeypatch, candidate)
    def stale(*a): raise candidate.RevalidationRequired('verifier_code changed')
    monkeypatch.setattr(candidate,'_validate_current_proof',stale)
    # Retain all originals in the denominator, including inputs without trackers.
    manifest['sources'] += [{'workbook_id':f'{i:04d}','path':f'missing/{i:04d}.xlsx','sha256':'x'} for i in range(2,701)]
    before = {str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    report = candidate.build_report(manifest,tmp_path)
    assert report['raw_denominator'] == 700
    assert report['verified_promoted'] == 0 and report['prior_promotions'] == 1
    assert report['revalidation_required'] == 1 and report['states']['STALE'] == 1
    assert report['workbooks'][0]['release_id'] == 'release'
    assert before == {str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}


def test_current_proof_pass_counts(tmp_path, monkeypatch):
    manifest, _, _ = promoted(tmp_path, monkeypatch, candidate)
    monkeypatch.setattr(candidate,'_validate_current_proof',lambda *a:None)
    report = candidate.build_report(manifest,tmp_path)
    assert report['verified_promoted'] == report['prior_promotions'] == 1
    assert report['revalidation_required'] == 0


@pytest.mark.parametrize('registered,corrupt_task', [(True, False), (False, False), (True, True)])
def test_changed_checkout_verifier_requires_refresh_without_hiding_artifact_corruption(
        tmp_path, monkeypatch, registered, corrupt_task):
    manifest, _, _ = promoted(tmp_path, monkeypatch, candidate)
    monkeypatch.setattr(candidate, '_validate_current_proof', lambda *a: None)
    code = tmp_path / 'evaluate.py'
    code.write_text('old verifier')
    evidence = tmp_path / 'harbor/0001-publication-evidence.json'
    records = json.loads(evidence.read_text())
    records[str(code)] = candidate.digest(code)
    evidence.write_text(json.dumps(records))
    monkeypatch.setattr(candidate.segmentation_publication, 'verifier_paths',
                        lambda: (code,) if registered else ())
    code.write_text('new verifier')
    if corrupt_task:
        (tmp_path / 'harbor/handoff.json').write_text('{}')
    report = candidate.build_report(manifest, tmp_path)
    assert report['verified_promoted'] == 0
    if registered and not corrupt_task:
        assert report['states'] == {'STALE': 1}
        assert report['prior_promotions'] == report['revalidation_required'] == 1
    else:
        assert report['states'] == {'EVIDENCE_INVALID': 1}


def test_explicit_ledger_binding_preserves_prior_promotion_history(tmp_path, monkeypatch):
    manifest, _, _ = promoted(tmp_path, monkeypatch, candidate)
    def stale(*a): raise candidate.RevalidationRequired('AST builder changed')
    monkeypatch.setattr(candidate,'_validate_current_proof',stale)
    directory=tmp_path/'workbooks/0001';directory.mkdir(parents=True)
    prior={'state':'PROMOTED','verified_promoted':True,'release_id':'release'}
    ledger=directory/'ledger.json'
    ledger.write_text(json.dumps({'source':manifest['sources'][0],'harbor':prior,'promoted':True}))
    report=candidate.build_report(manifest,tmp_path,bind_ledgers=True)
    bound=json.loads(ledger.read_text())
    assert report['states']=={'STALE':1}
    assert bound['harbor_history']==[prior]
    assert bound['harbor']['prior_promotion'] is True
    assert bound['harbor']['revalidation_required'] is True
    assert bound['promoted'] is False


@pytest.mark.parametrize('registered,corrupt_task', [(True,False),(False,False),(True,True)])
def test_changed_actual_auditor_requires_audit_revalidation_not_source_invalidity(tmp_path,monkeypatch,registered,corrupt_task):
    import xl_variable_source_audit as auditor
    manifest,_,_=promoted(tmp_path,monkeypatch,candidate)
    code=tmp_path/'xl_variable_source_audit.py';code.write_text('old audit')
    evidence=tmp_path/'harbor/0001-publication-evidence.json'
    records=json.loads(evidence.read_text());records[str(code)]=candidate.digest(code)
    evidence.write_text(json.dumps(records));code.write_text('new audit validation')
    if registered:monkeypatch.setattr(auditor,'__file__',str(code))
    if corrupt_task:(tmp_path/'harbor/handoff.json').write_text('{}')
    result=candidate.build_report(manifest,tmp_path)
    assert result['verified_promoted']==0
    if registered and not corrupt_task:
        assert result['states']=={'STALE':1}
        assert 'restart audit evidence validation' in result['workbooks'][0]['error']
        assert result['prior_promotions']==result['revalidation_required']==1
    else:assert result['states']=={'EVIDENCE_INVALID':1}


def test_requalification_in_progress_clears_current_ledger_promotion(tmp_path, monkeypatch):
    manifest, tracker_path, tracker = promoted(tmp_path, monkeypatch, candidate)
    tracker['execution_state'] = 'running'
    tracker_path.write_text(json.dumps(tracker))
    directory = tmp_path / 'workbooks/0001'
    directory.mkdir(parents=True)
    prior = {'state': 'PROMOTED', 'verified_promoted': True, 'release_id': 'release'}
    ledger = directory / 'ledger.json'
    ledger.write_text(json.dumps({'source': manifest['sources'][0], 'harbor': prior,
                                 'promoted': True}))
    report = candidate.build_report(manifest, tmp_path, bind_ledgers=True)
    bound = json.loads(ledger.read_text())
    assert report['states'] == {'RUNNING': 1}
    assert bound['harbor_history'] == [prior]
    assert bound['harbor'] == {'state': 'RUNNING'}
    assert bound['promoted'] is False


@pytest.mark.parametrize('confidence',[None,'high',[],42,{}])
def test_malformed_confidence_fails_only_its_row(tmp_path, monkeypatch, confidence):
    manifest,path,tracker = promoted(tmp_path,monkeypatch,candidate)
    tracker['current_confidence'] = confidence
    path.write_text(json.dumps(tracker))
    manifest['sources'].append({'workbook_id':'0002','path':'missing','sha256':'x'})
    report = candidate.build_report(manifest,tmp_path)
    assert report['raw_denominator'] == 2 and report['verified_promoted'] == 0
    assert report['states'] == {'EVIDENCE_INVALID':1,'NOT_RUN':1}


def test_ast_builder_comparison_ignores_relocation_but_rejects_changed_bytes(tmp_path,monkeypatch):
    builder=tmp_path/'xl_ast_graph.py';builder.write_text('stable parser')
    recorded=[{'name':builder.name,'path':'/archived/snapshot/xl_ast_graph.py',
               'sha256':candidate.digest(builder),'size_bytes':builder.stat().st_size}]
    sm={'ast_provenance':{'builder':{'code':recorded}},
        'layout':{'source_workbook':'source/0001.xlsx','ast_directory':'ast/0001'}}
    source_dir=tmp_path/'source/generations/s';source_dir.mkdir(parents=True)
    (source_dir/'generation-manifest.json').write_text(json.dumps(sm))
    monkeypatch.setattr(candidate.source_publication,'validate_source_generation',lambda *a,**k:sm)
    monkeypatch.setattr(candidate.source_publication,'_default_builder_paths',lambda:(builder,))
    calls=[]
    monkeypatch.setattr(candidate.segmentation_publication,'resolve_generation_by_id',lambda *a,**k:calls.append(k))
    t={'generation_ids':{'source':'s','segmentation':'g'},'bindings':{'segmentation_workbook_root':{'path':str(tmp_path/'seg')}}}
    candidate._validate_current_proof(t,tmp_path/'source')
    assert calls[0]['validate_live_evidence'] is True and calls[0]['require_pass'] is True
    builder.write_text('new parser')
    with pytest.raises(candidate.RevalidationRequired,match='AST builder code changed'):
        candidate._validate_current_proof(t,tmp_path/'source')


def test_strict_live_failure_classified_for_revalidation(tmp_path,monkeypatch):
    builder=tmp_path/'xl_ast_graph.py';builder.write_text('same')
    sm={'ast_provenance':{'builder':{'code':[{'name':builder.name,'sha256':candidate.digest(builder),'size_bytes':4}]}},
        'layout':{'source_workbook':'source/0001.xlsx','ast_directory':'ast/0001'}}
    source_dir=tmp_path/'source/generations/s';source_dir.mkdir(parents=True)
    (source_dir/'generation-manifest.json').write_text(json.dumps(sm))
    monkeypatch.setattr(candidate.source_publication,'validate_source_generation',lambda *a,**k:sm)
    monkeypatch.setattr(candidate.source_publication,'_default_builder_paths',lambda:(builder,))
    def stale(*a,**k): raise ValueError('verifier_code mismatch')
    monkeypatch.setattr(candidate.segmentation_publication,'resolve_generation_by_id',stale)
    t={'generation_ids':{'source':'s','segmentation':'g'},'bindings':{'segmentation_workbook_root':{'path':str(tmp_path/'seg')}}}
    with pytest.raises(candidate.RevalidationRequired,match='strict segmentation live proof'):
        candidate._validate_current_proof(t,tmp_path/'source')


def test_integrity_and_current_proof_do_not_bypass_full_release_validation(tmp_path, monkeypatch):
    manifest, _, _ = promoted(tmp_path, monkeypatch, candidate)
    monkeypatch.setattr(candidate, '_validate_current_proof', lambda *a: None)
    def reject(*a, **k):
        raise ValueError('current release policy rejection')
    monkeypatch.setattr(candidate.release, 'validate_release', reject)
    report = candidate.build_report(manifest, tmp_path)
    assert report['verified_promoted'] == 0
    assert report['states'] == {'EVIDENCE_INVALID': 1}


@pytest.mark.parametrize('other_verdict,expected_count', [(None,1),('PREPARED',1),('PUBLICATION_READY',0)])
def test_only_qualification_handoffs_participate_in_publication_uniqueness(tmp_path,monkeypatch,other_verdict,expected_count):
    manifest,_,_=promoted(tmp_path,monkeypatch,candidate)
    monkeypatch.setattr(candidate,'_validate_current_proof',lambda *a:None)
    other=tmp_path/'harbor/audit-preparation/handoff.json';other.parent.mkdir()
    other.write_text(json.dumps({'workbook_id':'0001','verdict':other_verdict}))
    evidence=tmp_path/'harbor/0001-publication-evidence.json';records=json.loads(evidence.read_text())
    records[str(other)]=candidate.digest(other);evidence.write_text(json.dumps(records))
    report=candidate.build_report(manifest,tmp_path)
    assert report['verified_promoted']==expected_count
    if not expected_count: assert report['states']=={'EVIDENCE_INVALID':1}
    other.write_text('{"changed":true}')
    assert candidate.build_report(manifest,tmp_path)['states']=={'EVIDENCE_INVALID':1}

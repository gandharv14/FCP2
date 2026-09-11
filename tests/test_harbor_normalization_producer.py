"""Hermetic tiny workbook pipeline tests; no network/private workbook execution.

Only expensive upstream source/segmentation resolvers are fixture doubles.
Complete audit coverage, inventory/sidecar, review_context, real executor,
normalizer/projection, plain rules and golden-value validators run unchanged.
"""
import copy
import dataclasses
import io
import json
from pathlib import Path
import time
import uuid
from unittest.mock import Mock

import pytest
import openpyxl
import xl_variable_source_audit as audit
from grader.tests.test_variable_source_audit import GOOD_MODEL_TABLE,write_two_variable_fixture
from scripts import qualified_publication as publication
from xl_seg.publication import write_inputs_sidecar
from scripts.batch.harbor_admission import record
from scripts.batch import harbor_normalization_producer as N
from scripts.batch import harbor_model_executor as E


@pytest.fixture
def setup(tmp_path,monkeypatch):
    def create(wb='9472'):
        home=tmp_path/wb;home.mkdir()
        artifact,oldseg=write_two_variable_fixture(home)
        target=home/(wb+'-inputs.xlsx');artifact.rename(target);artifact=target
        source_dir=home/'sources'/wb/'generations'/('a'*64);seg_dir=home/'segments'/wb/'generations'/('b'*64)
        source_dir.mkdir(parents=True);seg_dir.mkdir(parents=True)
        for p in (source_dir,seg_dir):(p/'generation-manifest.json').write_text('{"synthetic_fixture":true}')
        (seg_dir/'segments.json').write_bytes((oldseg/'segments.json').read_bytes())
        (seg_dir/'curation.toml').write_text('outputs=["Inputs!B1"]\n')
        source=source_dir/(wb+'.xlsx');source.write_bytes(artifact.read_bytes())
        bindings=dict(source_generation_id=source_dir.name,segmentation_generation_id=seg_dir.name,
            source_manifest_sha256=N.executor.sha((source_dir/'generation-manifest.json').read_bytes()),
            segmentation_manifest_sha256=N.executor.sha((seg_dir/'generation-manifest.json').read_bytes()),
            inventory_approval_sha256=None,recalc_signals_sha256=None,source_health_sha256='c'*64,
            restriction_evidence_sha256=None,release_id=None,task_generation_id=None)
        context=dict(source_dir=str(source_dir),source_path=str(source),source_sha256=record(source)['sha256'],
                     segmentation_dir=str(seg_dir),ast_dir=str(source_dir/'ast'),bindings=bindings,
                     source_manifest={'ast_provenance':{'builder':{'code':[dict(name=p.name,sha256=record(p)['sha256'],size_bytes=p.stat().st_size) for p in publication.source_publication._default_builder_paths()]}}})
        from xl_seg import publication as segpub
        resolver=Mock(return_value=context);live=Mock(return_value=(seg_dir,{}))
        monkeypatch.setattr(publication.release,'resolve_build_context',resolver);monkeypatch.setattr(N,'resolve_build_context',resolver)
        monkeypatch.setattr(segpub,'resolve_generation_by_id',live)
        sidecar=write_inputs_sidecar(artifact,seg_dir,{'generation_id':seg_dir.name},pipeline_bindings=bindings)
        monkeypatch.setattr(audit,'_call_model',lambda *a,**k:(GOOD_MODEL_TABLE,'stop'))
        segment_meta={k:bindings[v] for k,v in [('generation_id','segmentation_generation_id'),('manifest_sha256','segmentation_manifest_sha256'),('source_generation_id','source_generation_id'),('inventory_approval_sha256','inventory_approval_sha256'),('recalc_signals_sha256','recalc_signals_sha256')]}
        output=home/'audit';audit.generate_audit(wb,str(artifact),seg_dir,output,'fixture-unused',segmentation=segment_meta)
        b={key:record(output/(wb+'-inputs-variable-sources'+suffix))for key,suffix in [('variable_source_metadata','.metadata.json'),('variable_source_inventory','.inventory.json'),('variable_source_audit','.md')]}
        b.update(source_generation=dict(path=str(source_dir),id=source_dir.name,manifest_sha256=bindings['source_manifest_sha256']),
            segmentation_generation=dict(path=str(seg_dir),id=seg_dir.name,manifest_sha256=bindings['segmentation_manifest_sha256']),
            source_file=record(source),original_raw_source=record(source),baseline_inputs=record(artifact),baseline_segmentation=record(sidecar),curation=record(seg_dir/'curation.toml'))
        budget=home/'original-budget.json';budget.write_text('{"synthetic_root_review_budget":true,"attempts_used":0,"limit":2}')
        selection=N.Selection(wb,b,home/'custom-runs'/(wb+'-variable-sources'),N.Policy('fixture-project'),N.Budget(1,2,str(uuid.uuid4()),(record(budget),)))
        campaign=E.Campaign(home/'model-journal',home/'model-slots')
        monkeypatch.setenv('HARBOR_MODEL_SLOT_ROOT',str(campaign.slot_root));monkeypatch.setenv('HARBOR_MODEL_SLOTS','2')
        return selection,campaign,resolver,live
    return create


def mock_review(monkeypatch,selection,*,change=None):
    def opened(request,timeout):
        body=json.loads(json.loads(request.data)['messages'][1]['content'])
        review={k:body[k] for k in ['draft_sha256','source_sha256','baseline_sha256']}
        review.update(schema_version='normalization-review/v1',reviewer='Independent synthetic reviewer',cells={})
        for row in body['candidates']:
            review['cells'][row['reference']]=dict(eligible=True,reason='Supplied historical primitive fixture.',evidence_refs=[row['reference'],row['row']['draft_id']],entity='Synthetic entity',period='Supplied observation period',scenario='Supplied base case',basis='Workbook native units')
        if change:change(review)
        response=dict(id=str(uuid.uuid4()),object='chat.completion',model=selection.policy.model,created=time.time_ns()//10**9,
            choices=[dict(finish_reason='stop',message=dict(role='assistant',content=json.dumps(review)))])
        return io.BytesIO(json.dumps(response).encode())
    monkeypatch.setattr(E,'_open',opened)


@pytest.mark.parametrize('wb',['9472','0001'])
def test_arbitrary_workbook_fresh_audit_full_review_and_phase1(setup,monkeypatch,wb):
    selection,campaign,resolver,live=setup(wb);original={key:Path(selection.bindings[key]['path']).read_bytes() for key in ['source_file','curation','variable_source_audit']}
    prepared=N.prepare(selection);data=N.read(prepared)
    assert data['candidate_count']==4 and 'audit_equivalence' not in selection.bindings
    assert N.prepare(selection)==prepared
    mock_review(monkeypatch,selection);execution=N.dispatch(selection,prepared,campaign,credential='fixture-unused')
    assert execution.status=='completed'
    result=N.read(N.emit_phase1(selection,prepared,execution.call))
    assert result['status']=='phase1_evidence_ready' and result['lane_approval'] is False
    spec=N.read(result['phase1']);assert len(spec['variables'])==4
    assert all(v['status']=='Supplied workbook input' for v in spec['variables'])
    assert N.emit_phase1(selection,prepared,execution.call)==record(selection.run/'phase1-result.json')
    assert live.call_args.kwargs['require_pass'] is True and live.call_args.kwargs['validate_live_evidence'] is True
    assert all(Path(selection.bindings[k]['path']).read_bytes()==v for k,v in original.items())
    assert len(list(selection.run.glob('normalize_*.py')))==1


@pytest.mark.parametrize('field',['source_file','baseline_inputs','curation','variable_source_audit','variable_source_inventory','variable_source_metadata'])
def test_changed_input_blocks_before_normalization_artifacts(setup,field):
    selection,*_=setup();path=Path(selection.bindings[field]['path']);path.write_bytes(path.read_bytes()+b'changed')
    with pytest.raises(Exception):N.prepare(selection)
    assert not selection.run.exists()


def test_current_proof_failure_not_bypassed_by_valid_saved_audit(setup):
    selection,_,resolver,_=setup();resolver.side_effect=ValueError('current source proof stale')
    with pytest.raises(ValueError,match='stale'):N.prepare(selection)
    assert not selection.run.exists()


@pytest.mark.parametrize('field',['eligible','reason','evidence_refs','entity','drop','extra','hash'])
def test_incomplete_or_invalid_atomic_review_does_not_emit(setup,monkeypatch,field):
    selection,campaign,*_=setup();prepared=N.prepare(selection)
    def change(review):
        ref=next(iter(review['cells']));decision=review['cells'][ref]
        if field=='drop':review['cells'].pop(ref)
        elif field=='extra':review['cells']['Inputs!ZZ100']=decision
        elif field=='hash':review['source_sha256']='0'*64
        else:decision.pop(field)
    mock_review(monkeypatch,selection,change=change);call=N.dispatch(selection,prepared,campaign,credential='fixture-unused').call
    with pytest.raises((ValueError,SystemExit)):N.emit_phase1(selection,prepared,call)
    assert not (selection.run/'normalized.json').exists()


def test_emptying_research_by_semantic_reject_still_obeys_plain_floor(setup,monkeypatch):
    selection,campaign,*_=setup();prepared=N.prepare(selection)
    def reject(review):
        for d in review['cells'].values():d.update(eligible=False,reason='Synthetic internal assumption, no historical external evidence.')
    mock_review(monkeypatch,selection,change=reject);call=N.dispatch(selection,prepared,campaign,credential='fixture-unused').call
    result=N.read(N.emit_phase1(selection,prepared,call))
    assert result['status']=='normalization_rejected'
    assert N.read(result['plain_eligibility'])['mode']=='fail'
    assert N.read(result['plain_eligibility'])['inventory_rows']==2


@pytest.mark.parametrize('artifact',['analysis.json','review-evidence.json','review-request.json','draft.json'])
def test_prepared_context_tampering_rejects_without_dispatch(setup,artifact):
    selection,campaign,*_=setup();prepared=N.prepare(selection);p=selection.run/artifact;p.write_bytes(p.read_bytes()+b' ')
    with pytest.raises(ValueError):N.prepared_call(selection,prepared)
    assert not campaign.root.exists()


def test_budget_identity_and_first_normalization_history_are_preserved(setup,monkeypatch):
    selection,campaign,*_=setup();previous=selection.run.parent.parent/'prior-first.json'
    previous.write_text(json.dumps({'schema_version':'1.0','workbook':selection.workbook,'atomic_variables':20}))
    selection=dataclasses.replace(selection,first_normalization=record(previous));prepared=N.prepare(selection)
    assert (selection.run/'first_normalization.json').read_bytes()==previous.read_bytes()
    changed=dataclasses.replace(selection,budget=dataclasses.replace(selection.budget,attempt_id=str(uuid.uuid4())))
    with pytest.raises(ValueError,match='selection/budget changed'):N.prepare(changed)
    def reject(review):
        for d in review['cells'].values():d['eligible']=False
    mock_review(monkeypatch,selection,change=reject);call=N.dispatch(selection,prepared,campaign,credential='fixture-unused').call
    result=N.read(N.emit_phase1(selection,prepared,call))
    assert 'cannot be downgraded' in N.read(result['plain_eligibility'])['reason']


def test_altered_partial_output_is_not_overwritten_on_resume(setup,monkeypatch):
    selection,campaign,*_=setup();prepared=N.prepare(selection);mock_review(monkeypatch,selection)
    call=N.dispatch(selection,prepared,campaign,credential='fixture-unused').call
    N.emit_phase1(selection,prepared,call);path=selection.run/'normalized.json';path.write_text('{"tampered":true}')
    with pytest.raises(ValueError,match='refusing to overwrite'):N.emit_phase1(selection,prepared,call)
    assert path.read_text()=='{"tampered":true}'


def test_never_consume_unreviewed_profile_or_forced_exclusion_inputs(setup,monkeypatch):
    selection,campaign,*_=setup();prepared=N.prepare(selection);mock_review(monkeypatch,selection)
    call=N.dispatch(selection,prepared,campaign,credential='fixture-unused').call
    (selection.run/'oracle_forced_exclusions.json').write_text('[]')
    with pytest.raises(ValueError,match='unreviewed later-stage'):N.emit_phase1(selection,prepared,call)


def test_cli_prepares_and_consumes_exact_call_without_dispatch(setup,monkeypatch,capsys):
    selection,campaign,*_=setup();recipe=selection.run.parent.parent/'root-selection.json'
    recipe.write_bytes(E.canonical(dict(workbook=selection.workbook,bindings=selection.bindings,run=str(selection.run),
        policy=dataclasses.asdict(selection.policy),budget=dataclasses.asdict(selection.budget),first_normalization=None)))
    capsys.readouterr()  # Discard upstream audit fixture setup status.
    args=['--selection',str(recipe),'--selection-sha256',record(recipe)['sha256']]
    assert N.main(['prepare',*args])==0
    prepared=json.loads(capsys.readouterr().out)
    mock_review(monkeypatch,selection);call=N.dispatch(selection,prepared,campaign,credential='fixture-unused').call
    binding=recipe.parent/'root-call.json';binding.write_bytes(E.canonical(dataclasses.asdict(call)))
    monkeypatch.setattr(E,'_open',lambda *a,**k:pytest.fail('CLI emission must not invoke a model'))
    assert N.main(['emit-phase1',*args,'--prepared',prepared['path'],'--prepared-sha256',prepared['sha256'],
                   '--call',str(binding),'--call-sha256',record(binding)['sha256']])==0
    assert N.read(json.loads(capsys.readouterr().out))['status']=='phase1_evidence_ready'


def test_interrupted_emission_resumes_same_review_without_new_call(setup,monkeypatch):
    selection,campaign,*_=setup();prepared=N.prepare(selection);mock_review(monkeypatch,selection)
    execution=N.dispatch(selection,prepared,campaign,credential='fixture-unused')
    # Interrupt after the original normalizer has durably emitted all three files.
    # This does not alter a production script or validator implementation.
    real_validate=N.validate_emission
    monkeypatch.setattr(N,'validate_emission',lambda *a,**k:(_ for _ in ()).throw(InterruptedError('fixture interruption')))
    with pytest.raises(InterruptedError):N.emit_phase1(selection,prepared,execution.call)
    assert (selection.run/'normalization_report.json').exists() and not (selection.run/'phase1-result.json').exists()
    first=(selection.run/'first_normalization.json').read_bytes()
    monkeypatch.setattr(N,'validate_emission',real_validate)
    monkeypatch.setattr(E,'_open',lambda *a,**k:pytest.fail('retained completed review cannot be repeated'))
    assert N.dispatch(selection,prepared,campaign,credential='fixture-unused').reused is True
    assert N.read(N.emit_phase1(selection,prepared,execution.call))['status']=='phase1_evidence_ready'
    assert (selection.run/'first_normalization.json').read_bytes()==first


def test_fresh_directory_cannot_reset_same_logical_attempt(setup,monkeypatch):
    selection,campaign,*_=setup();prepared=N.prepare(selection);mock_review(monkeypatch,selection)
    N.dispatch(selection,prepared,campaign,credential='fixture-unused')
    second=dataclasses.replace(selection,run=selection.run.parent.parent/'another-run'/selection.run.name,
        budget=dataclasses.replace(selection.budget,attempt_id=str(uuid.uuid4())))
    prepared2=N.prepare(second)
    with pytest.raises(ValueError,match='identity/configuration/budget changed'):
        N.dispatch(second,prepared2,campaign,credential='fixture-unused')


def test_second_ordinal_requires_actual_retained_prior_journal(setup,monkeypatch):
    selection,campaign,*_=setup();selection=dataclasses.replace(selection,budget=dataclasses.replace(selection.budget,ordinal=2))
    prepared=N.prepare(selection)
    monkeypatch.setattr(E,'_open',lambda *a,**k:pytest.fail('missing inherited attempt must block dispatch'))
    with pytest.raises(ValueError,match='prior semantic attempt missing'):
        N.dispatch(selection,prepared,campaign,credential='fixture-unused')


def test_second_ordinal_retains_original_budget_and_first_count(setup,monkeypatch):
    selection,campaign,*_=setup();prepared=N.prepare(selection);mock_review(monkeypatch,selection)
    first=N.dispatch(selection,prepared,campaign,credential='fixture-unused')
    N.emit_phase1(selection,prepared,first.call)
    snapshot=record(selection.run/'first_normalization.json')
    second=dataclasses.replace(selection,run=selection.run.parent.parent/'second-run'/selection.run.name,
        first_normalization=snapshot,budget=dataclasses.replace(selection.budget,ordinal=2,attempt_id=str(uuid.uuid4()),
            evidence=selection.budget.evidence+(first.outcome,)))
    prepared2=N.prepare(second);new=N.dispatch(second,prepared2,campaign,credential='fixture-unused')
    assert new.status=='completed'
    result=N.read(N.emit_phase1(second,prepared2,new.call))
    assert result['semantic_budget_reset'] is False
    assert Path(result['first_normalization']['path']).read_bytes()==N.raw(snapshot)
    submitted=N.read(new.call.submitted)
    assert submitted['semantic_ordinal']==2 and submitted['semantic_limit']==2
    assert submitted['budget_evidence'][-1]==first.outcome


def test_transport_uncertainty_cannot_use_new_ordinal(setup,monkeypatch):
    import urllib.error
    selection,campaign,*_=setup();prepared=N.prepare(selection)
    monkeypatch.setattr(E,'_open',lambda *a,**k:(_ for _ in ()).throw(urllib.error.URLError('fixture transport failure')))
    result=N.dispatch(selection,prepared,campaign,credential='fixture-unused')
    assert result.call is None
    second=dataclasses.replace(selection,run=selection.run.parent.parent/'second-run'/selection.run.name,
        budget=dataclasses.replace(selection.budget,ordinal=2,attempt_id=str(uuid.uuid4())))
    prepared2=N.prepare(second)
    monkeypatch.setattr(E,'_open',lambda *a,**k:pytest.fail('uncertainty cannot be bypassed'))
    with pytest.raises(ValueError,match='unresolved prior transport'):
        N.dispatch(second,prepared2,campaign,credential='fixture-unused')


def test_generation_changes_during_emission_cannot_seal_phase1(setup,monkeypatch):
    selection,campaign,resolver,_=setup();prepared=N.prepare(selection);mock_review(monkeypatch,selection)
    call=N.dispatch(selection,prepared,campaign,credential='fixture-unused').call
    actual=N.validate_emission
    def change(*args,**kwargs):
        result=actual(*args,**kwargs)
        resolver.side_effect=ValueError('current generation invalidated during emission')
        return result
    monkeypatch.setattr(N,'validate_emission',change)
    with pytest.raises(ValueError,match='invalidated during emission'):N.emit_phase1(selection,prepared,call)
    assert not (selection.run/'normalized.phase1.json').exists()

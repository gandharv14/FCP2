"""Real immutable release integrity plus explicit mechanical journal boundary.

No model/runtime operations. The synthetic release does not claim financial
readiness; its component validators are real. Existing mechanical tracker
fixture isolates the transition/report composition from all financial gates.
"""
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

ROOT=next(p for p in Path(__file__).resolve().parents if (p/'xl_ast_graph.py').is_file())
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
from test_release_publication import _real_release_fixture
from test_harbor_promotion_evidence import promoted as mechanical_promoted
from scripts.batch import harbor_promotion_evidence as live
from scripts.batch import harbor_ready_transitions as transitions
from scripts.batch import harbor_tracker_lifecycle as lifecycle
from scripts.batch.harbor_admission import record
from scripts import corpus_qualification as qualification
import xl_source_publication as source
import xl_release_publication as release
from xl_seg import publication as seg

P = live


def real_context(tmp_path,monkeypatch):
    builder=tmp_path/'registered-builder.py';builder.write_text('version = 1\n')
    monkeypatch.setattr(source,'_default_builder_paths',lambda:(builder,))
    case=_real_release_fixture(tmp_path,builder_code_paths=[builder])
    source_manifest=json.loads((case['source_dir']/'generation-manifest.json').read_text())
    src=case['source_dir']/source_manifest['layout']['source_workbook']
    binding={'original_raw_source':record(src),'source_file':record(src),'curation':record(case['segmentation_root']/'curation.toml')}
    for kind,key in [('source','source_dir'),('segmentation','segmentation_dir')]:
        directory=case[key];binding[kind+'_generation']={'path':str(directory),'id':directory.name,'manifest_sha256':record(directory/'generation-manifest.json')['sha256']}
    before={'workbook_id':case['workbook'],'bindings':binding,'promotion_ids':{'release_id':None}}
    promoted={'bindings':copy.deepcopy(binding),'promotion_ids':{'release_id':case['release']['release_id'],'task_generation_id':case['task_id'],'previous_release_id':None},'generation_ids':{'source':case['source_id'],'segmentation':case['segmentation_id'],'task':case['task_id'],'release':case['release']['release_id']}}
    stage=tmp_path/'real-task-stage'
    selected=SimpleNamespace(content=SimpleNamespace(workbook=case['workbook'],bindings=copy.deepcopy(binding),stage=stage,stage_sha256=release._tree_hash(stage)[0]),runtime=None)
    published=transitions.ReleaseSelection(case['release_root'],case['source_root'],case['segmentation_root'],case['release_root'],case['release']['release_id'],record(case['release_root']/'current-release.json'),record(case['release_dir']/'release-manifest.json'))
    records=[record(builder),record(case['release_root']/'current-release.json'),record(case['release_dir']/'release-manifest.json'),record(stage/'instruction.md')]
    event={'evidence_paths':P.notes.evidence_list(records)}
    return case,builder,before,promoted,selected,published,event


def test_real_intact_release_has_no_code_drift(tmp_path,monkeypatch):
    case,builder,before,promoted,selected,published,event=real_context(tmp_path,monkeypatch)
    historical=P.historical_release(before,promoted,selected,published)
    assert P.historical_evidence(event,historical,selected)==[]


def test_real_stale_builder_integrity_passes_and_drift_is_explicit(tmp_path,monkeypatch):
    case,builder,before,promoted,selected,published,event=real_context(tmp_path,monkeypatch)
    builder.write_text('version = 2\n')
    with pytest.raises(ValueError):release.resolve_current_release(case['release_root'],source_root=case['source_root'],segmentation_root=case['segmentation_root'])
    historical=P.historical_release(before,promoted,selected,published)
    changes=P.historical_evidence(event,historical,selected)
    assert {c['kind'] for c in changes}=={'registered_code','AST_builder'}


def test_verifier_drift_is_detected_without_mutating_historical_artifacts(tmp_path,monkeypatch):
    case,builder,before,promoted,selected,published,event=real_context(tmp_path,monkeypatch)
    verifier=tmp_path/'new-registered-verifier.py';verifier.write_text('# synthetic new verifier\n')
    old=seg.verifier_paths();monkeypatch.setattr(seg,'verifier_paths',lambda:(*old,verifier))
    historical=P.historical_release(before,promoted,selected,published)
    assert [c['kind'] for c in P.historical_evidence(event,historical,selected)]==['segmentation_verifier']


@pytest.mark.parametrize('damage',['source','segmentation','task','manifest','pointer','stage','selected_tuple','predecessor'])
def test_real_corruption_is_rejected_even_when_registered_code_also_changed(tmp_path,monkeypatch,damage):
    case,builder,before,promoted,selected,published,event=real_context(tmp_path,monkeypatch)
    builder.write_text('version = 2\n')
    if damage in {'source','segmentation','task'}:
        directory=Path(case['release'][damage+'_generation']['generation_path']);manifest=json.loads((directory/'generation-manifest.json').read_text());path=directory/next(iter(manifest['artifacts']));path.chmod(0o600);path.write_bytes(path.read_bytes()+b'changed')
    elif damage=='manifest':
        path=case['source_dir']/'generation-manifest.json';path.chmod(0o600);path.write_bytes(path.read_bytes()+b' ')
    elif damage=='pointer':(case['release_root']/'current-release.json').write_text('{}')
    elif damage=='stage':(selected.content.stage/'instruction.md').write_text('changed')
    elif damage=='selected_tuple':selected.content.bindings['source_generation']['manifest_sha256']='f'*64
    else:promoted['promotion_ids']['previous_release_id']='f'*64
    with pytest.raises((ValueError,KeyError)):P.historical_release(before,promoted,selected,published)


@pytest.mark.parametrize('change',['noncode','same_basename','missing','symlink'])
def test_only_exact_registered_code_can_be_stale_not_general_evidence(tmp_path,monkeypatch,change):
    case,builder,before,promoted,selected,published,event=real_context(tmp_path,monkeypatch)
    artifact=tmp_path/('xl_variable_source_audit.py' if change=='same_basename' else 'retained-review.json');artifact.write_text('retained\n')
    event['evidence_paths']=P.notes.evidence_list(event['evidence_paths']+[record(artifact)])
    builder.write_text('version = 2\n')
    if change=='missing':artifact.unlink()
    elif change=='symlink':
        other=tmp_path/'same-bytes.json';other.write_bytes(artifact.read_bytes());artifact.unlink();artifact.symlink_to(other)
    else:artifact.write_text('changed\n')
    historical=P.historical_release(before,promoted,selected,published)
    with pytest.raises((ValueError,OSError)):P.historical_evidence(event,historical,selected)


def test_observed_live_bug_drops_prior_promotion_on_explicit_revalidation(tmp_path,monkeypatch):
    path,state,src,selected,proof=mechanical_promoted(tmp_path,monkeypatch)
    before=path.read_bytes()
    def stale(*args):raise qualification.RevalidationRequired('explicit current verifier drift fixture')
    monkeypatch.setattr(transitions,'_proof',stale)
    report=qualification.build_report({'sources':[src],'inventory_sha256':'a'*64},tmp_path)
    assert report['states']=={'EVIDENCE_INVALID':1} and report['prior_promotions']==0
    assert path.read_bytes()==before


def test_strict_report_stale_after_historical_integrity_and_preserves_denominator(tmp_path,monkeypatch):
    path,state,src,selected,proof=mechanical_promoted(tmp_path,monkeypatch)
    # Real historical release tests above cover this fixed owning boundary.
    seen=[]
    monkeypatch.setattr(P,'historical_release',lambda *args:seen.append('integrity') or {'release_path':'synthetic-immutable-release'})
    monkeypatch.setattr(P,'historical_evidence',lambda *args:seen.append('evidence') or [{'kind':'segmentation_verifier'}])
    def must_not_reprove(*args):raise AssertionError('stale evidence never reaches current qualification')
    monkeypatch.setattr(transitions,'_proof',must_not_reprove);monkeypatch.setattr(live,'validate',P.validate)
    raw=path.read_bytes();inventory={'sources':[src,dict(workbook_id='0002',path=str(tmp_path/'not-run.xlsx'),sha256='f'*64)],'inventory_sha256':'a'*64}
    report=qualification.build_report(inventory,tmp_path)
    assert report['states']=={'STALE':1,'NOT_RUN':1} and report['prior_promotions']==1 and report['verified_promoted']==0 and report['raw_denominator']==2
    assert report['revalidation_required']==1 and seen==['integrity','evidence'] and path.read_bytes()==raw


def test_unrecognized_gate_failure_is_not_blanket_stale(tmp_path,monkeypatch):
    path,state,src,selected,proof=mechanical_promoted(tmp_path,monkeypatch)
    monkeypatch.setattr(P,'historical_release',lambda *args:{'release_path':'synthetic-immutable-release'})
    monkeypatch.setattr(P,'historical_evidence',lambda *args:[])
    def bad(*args):raise ValueError('unrecognized owning defect, not code drift')
    monkeypatch.setattr(transitions,'_proof',bad);monkeypatch.setattr(live,'validate',P.validate)
    report=qualification.build_report({'sources':[src],'inventory_sha256':'a'*64},tmp_path)
    assert report['states']=={'EVIDENCE_INVALID':1} and report['verified_promoted']==0


def test_current_strict_promotion_still_replays_all_owning_checks(tmp_path,monkeypatch):
    path,state,src,selected,proof=mechanical_promoted(tmp_path,monkeypatch)
    monkeypatch.setattr(P,'historical_release',lambda *args:{'release_path':'synthetic-immutable-release'})
    monkeypatch.setattr(P,'historical_evidence',lambda *args:[]);monkeypatch.setattr(live,'validate',P.validate)
    result=qualification.validate_terminal(src,tmp_path)
    assert result['verified_promoted'] and result['prior_promotion']


@pytest.mark.parametrize('change',['selection','published','before','partial_seal','prepared_at'])
def test_descriptor_tampering_rejected_before_stale_branch(tmp_path,monkeypatch,change):
    path,state,src,selected,proof=mechanical_promoted(tmp_path,monkeypatch)
    journal=Path(state['journal']['path']);descriptor=P.read(record(journal))
    if change=='selection':
        altered=copy.deepcopy(selected);object.__setattr__(altered.content,'stage_sha256','f'*64)
        descriptor['selection']=transitions.encode(altered)
    elif change=='published':
        altered=transitions.decode(descriptor['published']);object.__setattr__(altered,'release_id','f'*64);descriptor['published']=transitions.encode(altered)
    elif change=='before':
        before=P.read(descriptor['before']);before['artifact_hashes']['invented']='a'*64;Path(descriptor['before']['path']).write_bytes(lifecycle.canonical(before));descriptor['before']=record(Path(descriptor['before']['path']))
    elif change=='prepared_at':descriptor['prepared_at']+=1
    else:
        after=P.read(descriptor['after']);after['handoffs'][-1].pop('selection_sha256')
        notes=P.notes.render_notes(after);after['agent_notes']['sha256']=P.schema.sha(notes)
        Path(descriptor['after']['path']).write_bytes(lifecycle.canonical(after));Path(descriptor['notes']['path']).write_bytes(notes)
        Path(after['agent_notes']['path']).write_bytes(notes);path.write_bytes(lifecycle.canonical(after))
        descriptor['after']=record(Path(descriptor['after']['path']));descriptor['notes']=record(Path(descriptor['notes']['path']))
        journal.with_name('complete.json').write_bytes(lifecycle.canonical({'tracker_sha256':descriptor['after']['sha256'],'notes_sha256':descriptor['notes']['sha256']}))
    journal.write_bytes(lifecycle.canonical(descriptor))
    def forbidden(*args):raise AssertionError('corrupt journal reached historical release validation')
    monkeypatch.setattr(P,'historical_release',forbidden)
    with pytest.raises(ValueError):P.validate(src,path,trusted_public_key=selected.content.trusted_public_key)


def unseal(path,state):
    journal=Path(state['journal']['path']);descriptor=P.read(record(journal));after=P.read(descriptor['after'])
    for key in ('prior_tracker_sha256','selection_sha256','published_selection_sha256'):after['handoffs'][-1].pop(key)
    raw=P.notes.render_notes(after);after['agent_notes']['sha256']=P.schema.sha(raw)
    Path(descriptor['after']['path']).write_bytes(lifecycle.canonical(after));Path(descriptor['notes']['path']).write_bytes(raw)
    Path(after['agent_notes']['path']).write_bytes(raw);path.write_bytes(lifecycle.canonical(after))
    descriptor['after']=record(Path(descriptor['after']['path']));descriptor['notes']=record(Path(descriptor['notes']['path']))
    journal.write_bytes(lifecycle.canonical(descriptor));journal.with_name('complete.json').write_bytes(lifecycle.canonical({'tracker_sha256':descriptor['after']['sha256'],'notes_sha256':descriptor['notes']['sha256']}))


@pytest.mark.parametrize('changed',[False,True])
def test_unsealed_transaction_requires_complete_current_replay(tmp_path,monkeypatch,changed):
    path,state,src,selected,proof=mechanical_promoted(tmp_path,monkeypatch);unseal(path,state)
    monkeypatch.setattr(P,'historical_release',lambda *args:{'release_path':'synthetic-immutable-release'})
    monkeypatch.setattr(P,'historical_evidence',lambda *args:[{'kind':'AST_builder'}] if changed else [])
    if changed:
        with pytest.raises(ValueError,match='unsealed'):P.validate(src,path,trusted_public_key=selected.content.trusted_public_key)
    else:assert P.validate(src,path,trusted_public_key=selected.content.trusted_public_key)['verified_promoted']


def test_exact_registered_auditor_change_has_explicit_stage(tmp_path,monkeypatch):
    import xl_variable_source_audit as auditor
    case,builder,before,promoted,selected,published,event=real_context(tmp_path,monkeypatch)
    code=tmp_path/'registered-auditor.py';code.write_text('# initial auditor fixture\n')
    monkeypatch.setattr(auditor,'__file__',str(code))
    event['evidence_paths']=P.notes.evidence_list(event['evidence_paths']+[record(code)])
    code.write_text('# changed auditor fixture\n')
    historical=P.historical_release(before,promoted,selected,published)
    changed=P.historical_evidence(event,historical,selected)
    assert len(changed)==1 and changed[0]['kind']=='registered_code' and changed[0]['path']==str(code)

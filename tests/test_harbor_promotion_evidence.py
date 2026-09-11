"""Journal/report integration. Financial proof is an explicit test boundary;
real gate and release fixtures live in the owning readiness test modules.
"""
import copy
from pathlib import Path
import pytest

from scripts.batch import harbor_promotion_evidence as evidence
from scripts.batch import harbor_ready_transitions as ready
from scripts.batch import harbor_tracker_lifecycle as lifecycle
from scripts.batch.harbor_admission import record
from scripts import corpus_qualification as qualification
from test_harbor_ready_transitions import fixture, kw
from test_harbor_tracker_lifecycle import authority


def promoted(tmp_path, monkeypatch):
    # Mechanical journal fixture; real release integrity is tested separately.
    monkeypatch.setattr(evidence, 'historical_release', lambda *args: {'release_path': 'synthetic-immutable-release'})
    monkeypatch.setattr(evidence, 'historical_evidence', lambda *args: [])
    root = tmp_path / 'harbor/workbooks'; root.mkdir(parents=True)
    facade, path, start, selection, proof = fixture(root, monkeypatch)
    queued = facade.transition('0245', 'enqueue', selection, **kw(start))
    leased = lifecycle.lease_operation(path, authority=authority(), operation='acquire',
        lease_kind='publication', owner='publisher', scope='publication:0245',
        expected_revision=queued['revision'], expected_sha256=queued['sha256'],
        duration_ns=80, clock=lambda:31)
    pub = tmp_path / 'published'; pub.mkdir()
    for name in ('current-release.json','release-manifest.json'):
        (pub/name).write_text('{}')
    selected = ready.ReleaseSelection(pub, pub, pub, pub, 'a'*64,
        record(pub/'current-release.json'), record(pub/'release-manifest.json'))
    ids = {'task_generation_id':'b'*64, 'release_id':'a'*64, 'previous_release_id':None}
    def release(before, chosen, published):
        assert chosen == selection and published == selected
        return ids, [selected.pointer, selected.manifest]
    monkeypatch.setattr(ready, '_release', release)
    monkeypatch.setattr(ready.time, 'time_ns', lambda:32)
    state = facade.transition('0245','promoted',selection,
        expected_revision=leased['revision'], expected_sha256=leased['sha256'],
        owner='publisher',token=leased['tracker']['leases']['publication']['token'],
        published=selected)
    source = dict(workbook_id='0245',**state['tracker']['bindings']['original_raw_source'])
    return path, state, source, selection, proof


def test_current_report_replays_completed_transaction_and_exact_release_chain(tmp_path, monkeypatch):
    path, state, source, selection, proof = promoted(tmp_path, monkeypatch)
    for kind in ('publication','task_agent'):
        old = state['tracker']['leases'][kind]
        state = lifecycle.lease_operation(path,authority=authority(),operation='release',
            lease_kind=kind,owner=old['owner'],scope=old['scope'],token=old['token'],
            expected_revision=state['revision'],expected_sha256=state['sha256'],clock=lambda:33)
    before = path.read_bytes()
    result = qualification.validate_terminal(source,tmp_path)
    assert result['verified_promoted'] is True and result['release_id']=='a'*64
    assert path.read_bytes()==before
    # An old PROMOTED flag and journal cannot override a newly failing gate.
    monkeypatch.setattr(ready,'_proof',lambda *args:(_ for _ in ()).throw(ValueError('current owning failure')))
    with pytest.raises(ValueError,match='current owning failure'):
        qualification.validate_terminal(source,tmp_path)
    assert path.read_bytes()==before


@pytest.mark.parametrize('change',['missing_complete','bad_complete','source','journal_after','lease','selection','tracker_field'])
def test_changed_promotion_evidence_never_counts(tmp_path,monkeypatch,change):
    path,state,source,selection,proof=promoted(tmp_path,monkeypatch)
    journal=Path(state['journal']['path']);d=evidence.read(record(journal))
    if change=='missing_complete':journal.with_name('complete.json').unlink()
    elif change=='bad_complete':journal.with_name('complete.json').write_text('{}')
    elif change=='source':source['sha256']='0'*64
    elif change=='journal_after':Path(d['after']['path']).write_text('{}')
    elif change=='lease':d['lease']['token']='foreign';journal.write_bytes(lifecycle.canonical(d))
    elif change=='selection':d['selection']={'type':'execute_python','value':'anything'};journal.write_bytes(lifecycle.canonical(d))
    else:
        t=copy.deepcopy(state['tracker']);t['artifact_hashes']['invented']='c'*64;path.write_bytes(lifecycle.canonical(t))
    before=path.read_bytes()
    with pytest.raises((ValueError,KeyError,OSError)):
        qualification.validate_terminal(source,tmp_path)
    assert path.read_bytes()==before


def test_incomplete_real_content_cannot_pass_via_valid_mechanical_journal(tmp_path,monkeypatch):
    actual = ready._proof
    path,state,source,selection,proof=promoted(tmp_path,monkeypatch)
    monkeypatch.setattr(ready,'_proof',actual)
    with pytest.raises((ValueError,KeyError,OSError,TypeError,AttributeError)):
        qualification.validate_terminal(source,tmp_path)

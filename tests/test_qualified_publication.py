from pathlib import Path
from unittest.mock import Mock
import json

import pytest

from scripts import qualified_publication as publication


def setup(monkeypatch, current):
    monkeypatch.setattr(publication.release, '_current_pointer',
                        lambda *a, **k: {'release_id': current['release_id']} if current else None)
    monkeypatch.setattr(publication.release, 'resolve_current_release',
                        lambda *a, **k: (Path('/release'), current))
    monkeypatch.setattr(publication.release, 'resolve_current_release_integrity',
                        lambda *a, **k: (Path('/release'), current))
    freeze = Mock(return_value=(Path('/snapshot'), {'snapshot_hash': 'snapshot'}))
    publish = Mock(return_value=(Path('/new'), {'release_id': 'new'}))
    monkeypatch.setattr(publication.release, 'freeze_absent_legacy_snapshot', freeze)
    monkeypatch.setattr(publication.release, 'publish_release', publish)
    return freeze, publish


def invoke(previous, *, resuming=False):
    return publication.publish_or_resume('/publication', '0001',
        source_root='/sources', source_generation_id='source',
        segmentation_root='/segments', segmentation_generation_id='seg',
        task_generation_id='task', expected_current_release_id=previous,
        output_path='/output', resuming=resuming)


def test_first_publication_binds_absent_snapshot(monkeypatch):
    freeze, publish = setup(monkeypatch, None)
    invoke(None)
    freeze.assert_called_once()
    assert publish.call_args.kwargs['expected_current_release_id'] is None
    assert publish.call_args.kwargs['legacy_snapshot_hash'] == 'snapshot'


@pytest.mark.parametrize('resuming', [False, True])
def test_requalification_cas_uses_prior_release_even_before_interrupted_commit(monkeypatch, resuming):
    freeze, publish = setup(monkeypatch, {'release_id': 'prior'})
    invoke('prior', resuming=resuming)
    freeze.assert_not_called()
    assert publish.call_args.kwargs['expected_current_release_id'] == 'prior'
    assert 'legacy_snapshot_hash' not in publish.call_args.kwargs


def completed():
    return {'release_id': 'new', 'prior_release_id': 'prior',
        'source_generation': {'generation_id': 'source'},
        'segmentation_generation': {'generation_id': 'seg'},
        'task_generation': {'generation_id': 'task'}}


def test_resume_after_release_cas_returns_exact_qualified_tuple(monkeypatch):
    current = completed()
    freeze, publish = setup(monkeypatch, current)
    assert invoke('prior', resuming=True) == (Path('/release'), current)
    freeze.assert_not_called()
    publish.assert_not_called()


@pytest.mark.parametrize('field', ['prior_release_id', 'source_generation', 'segmentation_generation', 'task_generation'])
def test_resume_cannot_absorb_conflicting_publication(monkeypatch, field):
    current = completed()
    current[field] = 'different' if field == 'prior_release_id' else {'generation_id': 'different'}
    freeze, publish = setup(monkeypatch, current)
    with pytest.raises(publication.release.ReleasePublicationError, match='prior release changed'):
        invoke('prior', resuming=True)
    freeze.assert_not_called()
    publish.assert_not_called()


def test_unexpected_existing_release_is_not_treated_as_own_resume(monkeypatch):
    _, publish = setup(monkeypatch, completed())
    with pytest.raises(publication.release.ReleasePublicationError, match='prior release changed'):
        invoke('prior')
    publish.assert_not_called()


def test_disappeared_previous_release_fails_without_creating_snapshot(monkeypatch):
    freeze, publish = setup(monkeypatch, None)
    with pytest.raises(publication.release.ReleasePublicationError, match='prior release changed'):
        invoke('prior', resuming=True)
    freeze.assert_not_called()
    publish.assert_not_called()


def test_prior_publication_evidence_is_retained_and_tampering_fails(tmp_path):
    path = tmp_path / '0001-publication-evidence.json'
    assert publication.archive_publication_evidence(path) is None
    path.write_bytes(b'{"prior": "proof"}\n')
    record = publication.archive_publication_evidence(path)
    assert Path(record['path']).read_bytes() == path.read_bytes()
    assert publication.archive_publication_evidence(path) == record
    path.write_bytes(b'{"fresh": "proof"}\n')
    assert publication.archive_publication_evidence(path) != record
    path.write_bytes(b'{"prior": "proof"}\n')
    Path(record['path']).write_bytes(b'altered')
    with pytest.raises(ValueError, match='archived publication evidence changed'):
        publication.archive_publication_evidence(path)


def test_refresh_adds_candidate_to_prior_path_bound_source_root(tmp_path, monkeypatch):
    root, stable, candidate = (tmp_path / name for name in ('releases', 'old-source', 'fresh-source'))
    prior_dir = root / 'releases/prior'
    prior_dir.mkdir(parents=True)
    (stable / 'generations/old').mkdir(parents=True)
    fresh = candidate / 'generations/new'
    fresh.mkdir(parents=True)
    (fresh / 'proof').write_text('validated proof')
    prior = {'source_generation': {'generation_path': str(stable / 'generations/old')}}
    (prior_dir / 'release-manifest.json').write_text(json.dumps(prior))
    pointer = {'release_path': 'releases/prior', 'release_id': 'prior', 'manifest_sha256': 'bound'}
    monkeypatch.setattr(publication.release, '_current_pointer', lambda *a, **k: pointer)
    validated = Mock()
    monkeypatch.setattr(publication.release, 'resolve_current_release_integrity', validated)
    monkeypatch.setattr(publication.release, 'resolve_current_release', validated)
    def source_check(directory, **kwargs):
        assert (Path(directory) / 'proof').read_text() == 'validated proof'
        return {'generation_id': kwargs['expected_generation_id']}
    monkeypatch.setattr(publication.source_publication, 'validate_source_generation', source_check)
    result = publication.stable_source_root(root, candidate, 'new',
        segmentation_root=tmp_path / 'segments', expected_current_release_id='prior')
    assert result == stable
    assert validated.call_args.kwargs['source_root'] == stable
    assert validated.call_args.kwargs['expected_release_id'] == 'prior'
    assert (stable / 'generations/new/proof').read_bytes() == (fresh / 'proof').read_bytes()
    pointer['release_id'] = 'conflict'
    with pytest.raises(publication.release.ReleasePublicationError, match='prior release changed'):
        publication.stable_source_root(root, candidate, 'new',
            segmentation_root=tmp_path / 'segments', expected_current_release_id='prior')

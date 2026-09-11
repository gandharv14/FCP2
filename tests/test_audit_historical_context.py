"""A real retained generation is history, never a substitute for live proof."""
import json
from pathlib import Path

import pytest

import xl_audit_equivalence as equivalence
import xl_release_publication as release
from test_corpus_source_checkpoint import completed
from test_corpus_native_source_reuse import _verified_build


def pair(completed, tmp_path):
    path = _verified_build(completed, tmp_path)
    build = json.loads(path.read_text())['build']
    root = Path(completed[0].run_root)
    source = root / 'sources/0001/generations' / build['source_generation_id']
    segment = root / 'segments/0001/generations' / build['segmentation_generation_id']
    return source, segment


def live(source, segment):
    return release.resolve_build_context('0001', source_root=source.parent.parent,
        source_generation_id=source.name, segmentation_root=segment.parent.parent,
        segmentation_generation_id=segment.name)


def test_historical_envelope_survives_verifier_change_but_current_pair_does_not(
        completed, tmp_path, monkeypatch):
    source, segment = pair(completed, tmp_path)
    current = live(source, segment)
    before = equivalence._historical_context(source, segment)
    assert before == {key: current[key] for key in before}
    changed = tmp_path / 'changed-verifier.py'
    changed.write_text('new verification implementation')
    monkeypatch.setattr('xl_seg.publication.verifier_paths', lambda: (changed,))
    assert equivalence._historical_context(source, segment) == before
    with pytest.raises(ValueError, match='current evidence does not match'):
        live(source, segment)


@pytest.mark.parametrize('artifact', ['verification', 'segments.json', 'curation.toml'])
def test_corrupted_historical_segmentation_is_never_accepted(completed, tmp_path, artifact):
    source, segment = pair(completed, tmp_path)
    equivalence._historical_context(source, segment)
    path = segment / ('segments.json' if artifact == 'verification' else artifact)
    if artifact == 'verification':
        document = json.loads(path.read_text())
        document['verification']['passed'] = False
        path.write_text(json.dumps(document))
    else:
        path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(ValueError):
        equivalence._historical_context(source, segment)

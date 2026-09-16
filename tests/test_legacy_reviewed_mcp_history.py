from pathlib import Path
import copy
import json
from unittest.mock import patch

import pytest

from scripts.batch import harbor_legacy_staged_history as S
from scripts.batch import harbor_legacy_research_history as L

ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / 'diagnostics/vm-fleet-20260912/control/source-pool-staged-pipeline-002/histories-011/0276.json'


def test_actual_full_history_preserves_original_consumption_and_fresh_gate():
    p = L.read(L.binding(HISTORY))
    result = S.validate(L.binding(HISTORY), at_ns=p['created_at_ns'])
    assert result['staged_history']['new_authoring_authorized'] is False
    assert result['staged_history']['dialogue_rounds_upper_bound'] == 1
    assert result['staged_history']['naturalization_reservations_upper_bound'] == 2
    assert result['new_source_reconstruction_ordinal'] == 2
    assert result['audit_files'] == L.inventory(p['audit_directory'])


@pytest.mark.parametrize('defect', [
    'manifest-hash', 'review-failed', 'sentence-text', 'sentence-missing',
    'sentence-duplicate', 'sentence-finding', 'input-hash', 'validation',
    'summary-count', 'summary-review-round', 'summary-verdict', 'summary-path',
    'summary-extra', 'summary-smoke', 'summary-copy', 'application',
    'new-authority', 'reset-rounds', 'unknown-custody', 'missing-log',
])
def test_full_history_rejects_changed_evidence_or_authority(tmp_path, defect):
    p = L.read(L.binding(HISTORY))
    if defect == 'new-authority':
        p['staged_history']['new_authoring_authorized'] = True
    if defect == 'reset-rounds':
        p['staged_history']['dialogue_rounds_upper_bound'] = 0
    if defect == 'unknown-custody':
        p['staged_history']['disclosure_custody_kind'] = 'arbitrary'
    if defect == 'missing-log':
        del p['audit_files']['oracle-process.log']
    destination = tmp_path / 'history.json'
    destination.write_text(json.dumps(p))
    real = L.read

    def read(row):
        value = copy.deepcopy(real(row))
        name = Path(row['path']).name
        if name == 'disclosure-faithfulness.json':
            if defect == 'manifest-hash': value['manifest_sha256'] = '0' * 64
            if defect == 'review-failed': value['passed'] = False
            if defect == 'sentence-text': value['sentences'][0]['text'] += ' changed'
            if defect == 'sentence-missing': value['sentences'].pop()
            if defect == 'sentence-finding': value['sentences'][0]['findings'] = ['unresolved']
            if defect == 'input-hash': value['input_hashes'] = {}
        if name == 'disclosure-sentence-manifest.json' and defect == 'sentence-duplicate':
            value['sentences'][1]['sentence_id'] = value['sentences'][0]['sentence_id']
        if name == 'disclosure-faithfulness-validation.json' and defect == 'validation':
            value['valid'] = False
        if row == p['tracker']:
            aa = value['additional_assumptions']
            if defect == 'summary-count': aa['claims'] = True
            if defect == 'summary-review-round': aa['review_round'] = 2
            if defect == 'summary-verdict': aa['review_verdict'] = 'fail'
            if defect == 'summary-path': aa['apply'] = '/foreign/apply.json'
            if defect == 'summary-extra': aa['unknown'] = True
            if defect == 'summary-smoke': aa['main_image_smoke'] = 'fail'
            if defect == 'summary-copy': aa['docker_copy'] = 'COPY /foreign /app'
        if name == 'apply.json' and defect == 'application':
            value['applied'] = False
        return value

    with patch.object(L, 'read', side_effect=read), pytest.raises(ValueError):
        S.validate(L.binding(destination), at_ns=p['created_at_ns'])


def test_old_empty_reader_does_not_silently_admit_nonempty_review():
    p = L.read(L.binding(HISTORY))
    with pytest.raises(ValueError, match='empty historical disclosure'):
        S.terminal_empty_disclosure_evidence(p['audit_files'])


def test_partial_logs_are_rejected_even_with_complete_review():
    p = L.read(L.binding(HISTORY))
    rows = dict(p['audit_files'])
    del rows['oracle-process.log']
    with pytest.raises(ValueError, match='diagnostic inventory'):
        S.terminal_reviewed_mcp_evidence(rows)

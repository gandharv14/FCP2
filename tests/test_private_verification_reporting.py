"""Explicit report selection routing; isolated financial authority is mocked."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

from scripts import corpus_qualification as report
from scripts.batch import harbor_promotion_evidence as promotion
from scripts.batch.harbor_admission import record


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


@pytest.fixture
def selected(tmp_path, monkeypatch):
    run = tmp_path / 'run'
    tracker = write(run / 'harbor/workbooks/0001.json', {
        'workbook_id': '0001', 'execution_state': 'PROMOTED',
        'handoffs': [{'schema_version': 'harbor-tracker-created/v1'}]})
    source = {'workbook_id': '0001', 'path': str(tmp_path / 'raw/0001.xlsx'), 'sha256': 'a' * 64}
    inventory = {'inventory_sha256': 'b' * 64, 'sources': [source]}
    entry = {'workbook_id': '0001', 'original_sha256': source['sha256'],
             'harbor_root': str(run / 'harbor'), 'tracker': record(tracker),
             'bundle': str(tmp_path / 'private-bundle'), 'manifest_sha256': 'c' * 64,
             'verifier_image': 'sha256:' + 'd' * 64}
    document = {'schema_version': report.PRIVATE_SELECTION_SCHEMA,
                'inventory_sha256': inventory['inventory_sha256'], 'workbooks': [entry]}
    selection_path = write(tmp_path / 'private-selection.json', document)
    calls = []
    def validate(source, tracker_path, *, trusted_public_key, private_verification=None):
        calls.append((source, tracker_path, trusted_public_key, private_verification))
        if private_verification is None:
            raise FileNotFoundError('native scratch evidence was cleaned after custody transfer')
        assert type(private_verification) is promotion.PrivateVerificationSelection
        return {'state': 'PROMOTED', 'verified_promoted': True, 'prior_promotion': True,
                'revalidation_required': False, 'release_id': 'fixture-release',
                'private_verification': {'manifest_sha256': private_verification.manifest_sha256,
                                         'fixture_authority_boundary': True}}
    monkeypatch.setattr(promotion, 'validate', validate)
    return {'run': run, 'tracker': tracker, 'source': source, 'inventory': inventory,
            'entry': entry, 'document': document, 'path': selection_path, 'calls': calls}


def selected_report(f, **kwargs):
    return report.build_report(f['inventory'], f['run'], private_verifications=record(f['path']), **kwargs)


def test_explicit_retained_selection_recovers_numerator_after_scratch_cleanup(selected):
    f = selected
    default = report.build_report(f['inventory'], f['run'])
    assert default['verified_promoted'] == 0 and default['states'] == {'EVIDENCE_INVALID': 1}
    assert f['calls'][-1][-1] is None
    result = selected_report(f, trusted_public_key='/explicit/root/key.pem')
    assert result['verified_promoted'] == result['private_verified_promotions'] == 1
    assert result['harbor_yield'] == 1 and result['raw_denominator'] == 1
    assert result['private_verifications_selection'] == record(f['path'])
    source, tracker, key, choice = f['calls'][-1]
    assert source == f['source'] and tracker == f['tracker'] and key == '/explicit/root/key.pem'
    assert choice == promotion.PrivateVerificationSelection(Path(f['entry']['bundle']),
        f['entry']['manifest_sha256'], f['entry']['verifier_image'])


def test_all_700_originals_remain_denominator(selected):
    f = selected
    f['inventory']['sources'].extend({'workbook_id': f'{number:04}',
        'path': str(f['path'].parent / f'raw/{number:04}.xlsx'), 'sha256': 'e' * 64}
        for number in range(2, 701))
    result = selected_report(f)
    assert result['raw_denominator'] == len(result['workbooks']) == 700
    assert result['verified_promoted'] == result['private_verified_promotions'] == 1
    assert result['harbor_yield'] == 1 / 700
    assert result['states'] == {'PROMOTED': 1, 'NOT_RUN': 699}


def test_private_failure_is_evidence_invalid_without_native_fallback(selected, monkeypatch):
    f = selected
    calls = []
    def rejected(*args, **kwargs):
        calls.append(kwargs)
        raise ValueError('isolated owning proof rejected')
    monkeypatch.setattr(promotion, 'validate', rejected)
    result = selected_report(f)
    assert result['states'] == {'EVIDENCE_INVALID': 1}
    assert result['verified_promoted'] == 0
    assert len(calls) == 1 and calls[0]['private_verification'] is not None


@pytest.mark.parametrize('change', ['duplicate', 'foreign', 'source_hash', 'inventory_hash', 'extra_field',
                                  'tracker_path', 'bad_image', 'bad_manifest_hash', 'relative_bundle', 'unknown_namespace'])
def test_invalid_or_ambiguous_selection_fails_before_validation(selected, change):
    f = selected
    document = deepcopy(f['document']); entry = document['workbooks'][0]
    if change == 'duplicate': document['workbooks'].append(deepcopy(entry))
    elif change == 'foreign': entry['workbook_id'] = '0701'
    elif change == 'source_hash': entry['original_sha256'] = 'f' * 64
    elif change == 'inventory_hash': document['inventory_sha256'] = 'f' * 64
    elif change == 'extra_field': entry['passed'] = True
    elif change == 'tracker_path': entry['tracker']['path'] = str(f['tracker'].with_name('0002.json'))
    elif change == 'bad_image': entry['verifier_image'] = 'latest'
    elif change == 'bad_manifest_hash': entry['manifest_sha256'] = 'not-a-digest'
    elif change == 'relative_bundle': entry['bundle'] = 'relative'
    elif change == 'unknown_namespace': entry['harbor_root'] = str(f['run'] / 'harbor/unselected')
    write(f['path'], document)
    with pytest.raises(ValueError): selected_report(f)
    assert not f['calls']


def test_changed_selection_file_hash_rejects(selected):
    f = selected
    pinned = record(f['path'])
    f['path'].write_text('{}')
    with pytest.raises(ValueError, match='selection file changed'):
        report.build_report(f['inventory'], f['run'], private_verifications=pinned)
    assert not f['calls']


def test_selection_change_during_validation_cannot_return_report(selected, monkeypatch):
    f = selected
    original = promotion.validate
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        f['path'].write_text('{}')
        return result
    monkeypatch.setattr(promotion, 'validate', changed)
    with pytest.raises(ValueError, match='selection file changed'):
        selected_report(f)


@pytest.mark.parametrize('change', ['missing', 'changed'])
def test_missing_or_stale_selected_tracker_cannot_count(selected, change):
    f = selected
    if change == 'missing': f['tracker'].unlink()
    else: write(f['tracker'], {'workbook_id': '0001', 'execution_state': 'RUNNING'})
    result = selected_report(f)
    assert result['states'] == {'EVIDENCE_INVALID': 1}
    assert not f['calls'] and result['raw_denominator'] == 1


@pytest.mark.parametrize('state', ['STOPPED', 'RUNNING'])
def test_private_proof_requires_strict_promoted_tracker(selected, state):
    f = selected
    tracker = json.loads(f['tracker'].read_text()); tracker['execution_state'] = state
    write(f['tracker'], tracker)
    document = deepcopy(f['document']); document['workbooks'][0]['tracker'] = record(f['tracker'])
    write(f['path'], document)
    result = selected_report(f)
    assert result['states'] == {'EVIDENCE_INVALID': 1} and not f['calls']


def test_historical_and_private_selection_cannot_overlap(selected, monkeypatch):
    f = selected
    from scripts import harbor_retirement_reporting
    monkeypatch.setattr(harbor_retirement_reporting, 'load', lambda *args: {'0001': object()})
    with pytest.raises(ValueError, match='selections overlap'):
        selected_report(f, historical_retirements={'fixture': True})
    assert not f['calls']


def test_additional_namespace_must_be_explicit_and_unique(selected):
    f = selected
    nested = f['run'] / 'harbor/campaign'
    target = nested / 'workbooks/0001.json'
    target.parent.mkdir(parents=True)
    f['tracker'].rename(target)
    document = deepcopy(f['document']); entry = document['workbooks'][0]
    entry['harbor_root'] = str(nested); entry['tracker'] = record(target)
    write(f['path'], document)
    with pytest.raises(ValueError, match='namespace was not explicitly selected'):
        selected_report(f)
    result = selected_report(f, harbor_roots=[nested])
    assert result['verified_promoted'] == 1 and f['calls'][-1][1] == target
    write(f['tracker'], json.loads(target.read_text()))
    result = selected_report(f, harbor_roots=[nested])
    assert result['states'] == {'EVIDENCE_INVALID': 1}
    assert result['workbooks'][0]['failure_class'] == 'ambiguous_original_tracker'


@pytest.mark.parametrize('argument', ['--private-verifications', '--private-verifications-sha256'])
def test_cli_private_selection_requires_path_hash_pair(selected, monkeypatch, argument):
    f = selected
    monkeypatch.setattr(sys, 'argv', ['corpus_qualification', '--inventory', 'unused',
        '--run-root', str(f['run']), argument, str(f['path']) if argument == '--private-verifications' else 'a' * 64])
    with pytest.raises(SystemExit) as error:
        report.main()
    assert error.value.code == 2 and not f['calls']


def test_cli_passes_explicit_selection_and_writes_full_denominator(selected, monkeypatch):
    f = selected
    monkeypatch.setattr(report, 'read_inventory', lambda _: f['inventory'])
    monkeypatch.setattr(sys, 'argv', ['corpus_qualification', '--inventory', 'fixture',
        '--run-root', str(f['run']), '--private-verifications', str(f['path']),
        '--private-verifications-sha256', record(f['path'])['sha256']])
    assert report.main() == 0
    result = json.loads((f['run'] / 'qualified-yield-report.json').read_text())
    assert result['raw_denominator'] == 1 and result['verified_promoted'] == 1


def test_explicit_current_private_proof_can_bind_original_ledger(selected):
    f = selected
    path = write(f['run'] / 'workbooks/0001/ledger.json', {'source': f['source'], 'promoted': False})
    result = selected_report(f, bind_ledgers=True)
    ledger = json.loads(path.read_text())
    assert result['verified_promoted'] == 1 and ledger['promoted'] is True
    assert ledger['source'] == f['source']
    assert ledger['harbor']['private_verification']['manifest_sha256'] == f['entry']['manifest_sha256']


def test_untyped_direct_private_selection_is_never_an_authority_flag(selected):
    f = selected
    with pytest.raises(ValueError, match='typed private verification'):
        report.validate_terminal(f['source'], f['run'], private_verification={'passed': True})
    assert not f['calls']

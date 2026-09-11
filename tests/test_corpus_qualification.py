import json
from pathlib import Path

from scripts.corpus import digest
from scripts.corpus_qualification import build_report
from xl_inventory_approval import object_hash


def fixture(tmp_path, state):
    source = tmp_path / '0001.xlsx'
    source.write_bytes(b'original workbook identity')
    record = {'workbook_id': '0001', 'path': str(source), 'sha256': digest(source)}
    directory = tmp_path / 'harbor/workbooks'
    directory.mkdir(parents=True)
    notes = directory / '0001-agent-notes.md'
    notes.write_text('Classified terminal result with retained evidence.')
    tracker = {'workbook_id': '0001', 'execution_state': state,
               'raw_source': {'sha256': record['sha256']},
               'agent_notes': {'path': str(notes), 'sha256': digest(notes)},
               'promotion_failure': None}
    path = directory / '0001.json'
    path.write_text(json.dumps(tracker))
    return {'inventory_sha256': 'frozen', 'sources': [record]}, tracker, path


def test_promotion_flag_without_qualification_is_never_counted(tmp_path):
    manifest, _, _ = fixture(tmp_path, 'PROMOTED')
    report = build_report(manifest, tmp_path)
    assert report['raw_denominator'] == 1
    assert report['verified_promoted'] == 0
    assert report['states'] == {'EVIDENCE_INVALID': 1}
    assert report['measurement_state'] == 'in_progress'


def test_stopped_result_requires_notes_and_unaltered_evidence(tmp_path):
    manifest, tracker, path = fixture(tmp_path, 'STOPPED')
    evidence = tmp_path / 'review.json'
    evidence.write_text('{"passed": false, "reason": "naturalness"}')
    records = [{'path': str(evidence), 'sha256': digest(evidence)}]
    tracker['promotion_failure'] = {'subclass': 'review_budget_exhausted',
        'diagnostic_signature': 'signature', 'evidence_paths': records}
    tracker['agent_notes'].update(diagnostic_signature='signature',
        evidence_set_sha256=object_hash(records))
    path.write_text(json.dumps(tracker))
    directory = tmp_path / 'workbooks/0001'
    directory.mkdir(parents=True)
    ledger = directory / 'ledger.json'
    ledger.write_text(json.dumps({'source': manifest['sources'][0], 'history': ['retained']}))
    report = build_report(manifest, tmp_path, bind_ledgers=True)
    assert report['states'] == {'STOPPED': 1}
    bound = json.loads(ledger.read_text())
    assert bound['history'] == ['retained'] and bound['harbor']['state'] == 'STOPPED'
    build_report(manifest, tmp_path, bind_ledgers=True)
    assert json.loads(ledger.read_text()) == bound
    ledger.write_text('{interrupted')
    report = build_report(manifest, tmp_path, bind_ledgers=True)
    assert report['states'] == {'EVIDENCE_INVALID': 1}
    ledger.write_text(json.dumps(bound))
    evidence.write_text('{"passed": true}')
    report = build_report(manifest, tmp_path, bind_ledgers=True)
    assert report['states'] == {'EVIDENCE_INVALID': 1}
    assert json.loads(ledger.read_text()) == bound


def test_stopped_candidate_retains_original_denominator_and_bound_evidence(tmp_path):
    manifest, tracker, path = fixture(tmp_path, 'STOPPED')
    source = manifest['sources'][0]
    candidate = tmp_path / 'candidate.xlsx'
    candidate.write_bytes(b'immutable candidate; failed downstream quality gate')
    evidence = tmp_path / 'failure.json'
    evidence.write_text('{"passed":false}')
    records = [{'path': str(evidence), 'sha256': digest(evidence)}]
    tracker['promotion_failure'] = {'subclass': 'review_budget_exhausted',
        'diagnostic_signature': 'signature', 'evidence_paths': records}
    tracker['agent_notes'].update(diagnostic_signature='signature',
        evidence_set_sha256=object_hash(records))
    tracker['bindings'] = {'original_raw_source': {
        'path': source['path'], 'sha256': source['sha256']}}
    tracker['raw_source'] = {'path': str(candidate), 'sha256': digest(candidate)}
    path.write_text(json.dumps(tracker))
    report = build_report(manifest, tmp_path)
    assert report['states'] == {'STOPPED': 1}
    assert report['raw_denominator'] == 1 and report['verified_promoted'] == 0
    assert report['workbooks'][0]['original_sha256'] == source['sha256']
    candidate.write_bytes(b'changed candidate')
    assert build_report(manifest, tmp_path)['states'] == {'EVIDENCE_INVALID': 1}
    candidate.write_bytes(b'immutable candidate; failed downstream quality gate')
    tracker['bindings']['original_raw_source']['sha256'] = digest(candidate)
    path.write_text(json.dumps(tracker))
    assert build_report(manifest, tmp_path)['states'] == {'EVIDENCE_INVALID': 1}
    tracker['bindings'] = {}
    path.write_text(json.dumps(tracker))
    assert build_report(manifest, tmp_path)['states'] == {'EVIDENCE_INVALID': 1}

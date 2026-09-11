"""Historical repair provenance remains mandatory under a newer dialogue policy."""
from types import SimpleNamespace
import json
import pytest
from scripts.batch import harbor_instruction_repair as repair
from scripts.batch import harbor_transition_codec as codec
from scripts.batch.harbor_admission import record
from scripts.batch.harbor_readiness import Selection
from scripts.batch.harbor_complete_readiness import ReadinessSelection


@pytest.fixture
def history(tmp_path):
    def put(name, value):
        path = tmp_path/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return record(path)
    original = put('original.json', {'source': 'unchanged'})
    before = {'workbook_id': '0035', 'revision': 12,
              'bindings': {'original_raw_source': original}}
    initial = put('run/initial.json', {'original': original})
    code = put('historical/code.py', {'code': 'old bytes, not executed'})
    old = put('snapshot.json', {'root': str(tmp_path/'historical'),
        'files': [{'relative': 'code.py', 'sha256': code['sha256']}]})
    witness = put('retained.blob', {'proof': 'retained'})
    selection = ReadinessSelection(Selection('0035', tmp_path/'stage', 'a'*64,
        before['bindings'], tmp_path/'normalization', tmp_path/'naturalization', None, None),
        None, None, None)
    value = {'schema_version': repair.SCHEMA, 'phase': 'dialogue-init',
        'worker_run': str(tmp_path/'run'), 'initial': initial, 'original': original,
        'current_code': {code['path']: code['sha256']},
        'failure': put('failure.json', {'failed': True}),
        'predecessor': put('predecessor.json', {'completed': True}),
        'tracker': put('tracker.before.json', before), 'old_snapshot': old,
        'retained_prefix': [{'original': witness, 'retained': witness}],
        'selection': codec.encode(selection)}
    worker = SimpleNamespace(run=tmp_path/'run', code_repair=put('permit.json', value))
    return worker, before, value, put


def test_historical_repair_is_read_only(history):
    worker, before, expected, _ = history
    value, records = repair.validate_historical_integrity(worker, before)
    assert value == expected and worker.code_repair in records
    assert before['revision'] == 12


@pytest.mark.parametrize('what', ['code', 'prefix', 'original', 'revision'])
def test_changed_historical_evidence_is_rejected(history, what):
    worker, before, value, put = history
    if what == 'code':
        put('historical/code.py', {'changed': True})
    elif what == 'prefix':
        put('retained.blob', {'changed': True})
    elif what == 'original':
        before['bindings']['original_raw_source'] = put('other.json', {})
    else:
        before['revision'] = 1
    with pytest.raises(Exception):
        repair.validate_historical_integrity(worker, before)

from pathlib import Path
import hashlib
import json
import pytest
from scripts.fleet import operations as O


def save(path, value):
    path.write_text(json.dumps(value))
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


@pytest.fixture
def selected(tmp_path, monkeypatch):
    runner = save(tmp_path / 'runner.py', {'fixture': True})
    monkeypatch.setattr(O, 'RUNNER_SHA', runner['sha256'])
    workers = [dict(workbook=w, plan=save(tmp_path / (w + '.json'), dict(workbook=w)))
               for w in ('0017', '0200')]
    manifest = save(tmp_path / 'manifest.json', dict(schema_version=O.SCHEMA, runner=runner, workers=workers))
    return manifest, workers, tmp_path


def test_tampered_plan_rejected_before_any_call(selected):
    manifest, workers, _ = selected
    Path(workers[1]['plan']['path']).write_text('{}')
    with pytest.raises(ValueError, match='changed'):
        O.run(manifest, 'status', caller=lambda *a: pytest.fail('called before full validation'))


def test_absent_skipped_running_interrupted_once(selected):
    manifest, _, root = selected
    calls = []
    def caller(runner, worker, action):
        calls.append((worker['workbook'], action))
        if action == 'interrupt':
            assert (root/'stop/0200.signal-intent.json').exists()
            return {'fixture_signal_recorded': True}
        return dict(process=dict(state='ABSENT' if worker['workbook']=='0017' else 'RUNNING'),
                    interrupt_intent_exists=False)
    result = O.run(manifest, 'interrupt', root/'stop', caller=caller)
    assert calls == [('0017','status'), ('0200','status'), ('0200','interrupt')]
    assert result[0]['state'] == 'ABSENT'
    assert json.loads((root/'stop/complete.json').read_text())['claims_released'] is False
    with pytest.raises(FileExistsError):
        O.run(manifest, 'interrupt', root/'stop', caller=lambda *a: pytest.fail('replayed'))


def test_uncertain_signal_preserved_without_next_worker_or_replay(selected):
    manifest, _, root = selected
    calls = []
    def caller(runner, worker, action):
        calls.append((worker['workbook'],action))
        if action == 'interrupt':
            raise TimeoutError('fixture response lost')
        return dict(process=dict(state='RUNNING'), interrupt_intent_exists=False)
    with pytest.raises(TimeoutError):
        O.run(manifest, 'interrupt', root/'stop', caller=caller)
    assert calls == [('0017','status'), ('0017','interrupt')]
    assert (root/'stop/0017.signal-intent.json').exists()
    assert json.loads((root/'stop/0017.uncertain.json').read_text())['automatic_retry'] is False
    assert not (root/'stop/complete.json').exists()


@pytest.mark.parametrize('state,prior', [('IDENTITY_MISMATCH',False), ('RUNNING',True)])
def test_changed_identity_or_prior_signal_never_signalled(selected,state,prior):
    manifest, _, root = selected
    def caller(runner, worker, action):
        assert action == 'status'
        return dict(process=dict(state=state),interrupt_intent_exists=prior)
    with pytest.raises(ValueError):
        O.run(manifest, 'interrupt', root/'stop',caller=caller)
    assert not (root/'stop/0017.signal-intent.json').exists()


def test_pathlike_worker_id_rejected(selected):
    manifest, workers, root = selected
    bad = dict(workbook='../escape',plan=workers[0]['plan'])
    value=json.loads(Path(manifest['path']).read_text());value['workers']=[bad]
    manifest=save(root/'bad.json',value)
    with pytest.raises(ValueError,match='selection'):
        O.run(manifest,'status',caller=lambda *a:pytest.fail('called'))

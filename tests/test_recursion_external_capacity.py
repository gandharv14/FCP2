"""External reservation accounting must never widen campaign result acceptance."""
import copy
import json
import sys
from pathlib import Path
from unittest.mock import patch
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import recursion_hardening_revisions as r


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def review(work):
    folder = work / 'content-amendments/0002'
    configs = []
    evidence = {}
    records = {}
    for n in range(5):
        cid = 'external-' + str(n)
        value = {'id': cid, 'lockedAt': 'locked', 'verifiedAt': 'verified', 'config': {
            'args': '--model claude-opus-5 --effort max',
            'envVars': {'ANTHROPIC_MODEL': 'claude-opus-5', 'CLAUDE_CODE_EFFORT_LEVEL': 'max'},
            'harnessImageUrl': 'us-central1-docker.pkg.dev/lb-ml-dev/fde-runners/runner-fcp-claude-code-opus-5@sha256:' + str(n)*64,
        }}
        path = folder / 'capacity-configs' / (cid + '.json')
        save(path, value); evidence[str(path)] = r.sha(path)
        configs.append({'runConfigVersionId': cid, 'model': 'opus', 'configPath': str(path), 'configSha256': r.sha(path)})
        records[cid] = value
    path = folder / 'reviewed-capacity-configs.json'
    save(path, {'schemaVersion': 1, 'status': 'reviewed', 'scope': 'capacity_reservations_only',
                'configs': configs, 'evidenceHashes': evidence})
    return path, records


def capacity(work, records, *, drift=None, unknown=False):
    solvers = [{'runConfigVersionId': cid, 'jobStatus': 'running', 'metadata': {'concurrency': n}}
               for cid, n in [('campaign-opus', 50), ('campaign-opus', 50), ('campaign-gemini', 50),
                              ('campaign-gemini', 50), *zip(records, [75, 10, 10, 10, 10])]]
    if unknown:
        solvers.append({'runConfigVersionId': 'not-reviewed', 'jobStatus': 'running', 'metadata': {'concurrency': 10}})
    mapping = {'campaign-opus': 'opus', 'campaign-gemini': 'gemini'}
    calls = []
    def cli(*args):
        calls.append(args)
        if args[:2] == ('evaluations', 'list'):
            return {'items': [{'id': 'active', 'status': 'running'}]}
        if args[:2] == ('evaluations', 'get'):
            return {'solvers': solvers}
        assert args[:2] == ('run-config-versions', 'get')
        value = copy.deepcopy(records[args[-1]])
        if drift: drift(value)
        return value
    with patch.object(r, 'known_configs', return_value=mapping), patch.object(r.c, 'cli', side_effect=cli), patch.object(r.c, 'CONFIG', {'concurrencyPerModel': 150}):
        result = r.fresh_capacity(work)
    assert mapping == {'campaign-opus': 'opus', 'campaign-gemini': 'gemini'}
    assert all(c[:2] in [('evaluations', 'list'), ('evaluations', 'get'), ('run-config-versions', 'get')] for c in calls)
    return result


def test_reviewed_external_overcommit_keeps_gemini_capacity(tmp_path):
    path, records = review(tmp_path)
    result = capacity(tmp_path, records)
    assert result['reservedConcurrencyByModel'] == {'opus': 215, 'gemini': 100}
    assert result['availableConcurrencyByModel'] == {'opus': 0, 'gemini': 50}
    assert result['overReservationByModel'] == {'opus': 65, 'gemini': 0}
    assert result['externalCapacityReview']['sha256'] == r.sha(path)
    assert result['activeReviewedExternalConfigIds'] == sorted(records)
    # External identities still fail actual campaign result binding.
    with pytest.raises(ValueError, match='Unknown run configuration'):
        r.validate_run_binding('task', 'version', next(iter(records)), context={
            'knownConfigs': {'campaign-opus': 'opus'}, 'bindings': {'task': {'version': {'campaign-opus'}}}})


@pytest.mark.parametrize('mutate', [
    lambda v: v['config'].update(args='--model claude-sonnet --effort max'),
    lambda v: v.update(lockedAt=None),
    lambda v: v.update(id='wrong-id'),
    lambda v: v['config'].update(timeoutSeconds=99),
])
def test_live_config_drift_fails_closed(tmp_path, mutate):
    _, records = review(tmp_path)
    with pytest.raises(ValueError, match='drifted'):
        capacity(tmp_path, records, drift=mutate)


def test_unreviewed_active_config_fails_closed(tmp_path):
    _, records = review(tmp_path)
    with pytest.raises(ValueError, match='Unknown active solver'):
        capacity(tmp_path, records, unknown=True)


def test_changed_private_receipt_bytes_rejected(tmp_path):
    _, records = review(tmp_path)
    p = tmp_path / 'content-amendments/0002/capacity-configs/external-0.json'
    p.write_text(p.read_text() + '\n')
    with pytest.raises(ValueError, match='evidence changed'):
        capacity(tmp_path, records)


def test_rehashed_wrong_model_evidence_still_rejected(tmp_path):
    p, records = review(tmp_path); receipt = json.loads(p.read_text())
    source = Path(receipt['configs'][0]['configPath']); value = json.loads(source.read_text())
    value['config']['envVars']['ANTHROPIC_MODEL'] = 'claude-other'
    save(source, value); receipt['evidenceHashes'][str(source)] = r.sha(source)
    receipt['configs'][0]['configSha256'] = r.sha(source); save(p, receipt)
    with pytest.raises(ValueError, match='model identity differs'):
        capacity(tmp_path, records)

"""Closed reader for 0006's second stopped environment guard.

This reads the completed normalization Call and imported research checkpoint.
It grants neither a semantic retry nor an extra model ordinal. The unchanged
owning lease implementation still checks the token, history, locks and third
and final replacement budget.
"""
from pathlib import Path
import hashlib
import json

SCHEMA = '0006-retained-normalization-timeout-profile/v1'
SOURCE = '81953973e0bbde9508ba0c7ae53b5b725ffb988cf8cc072367e5d5bd0c4ec960'
VERSION = '1857e0ce39d62f949149c1ad9d4b7a8e941153f6e7ec8c8b4a2ffe660b54b96a'
GUARD = '53929796e0eac7b925a7d5e2af1d42e3e402a517c4eae4e8adaa2acff1cf4714'
TRACKER = '88f6f6cd0e9ee9aa1f161faf4e0adc7ecaa23eb47e4607f07eb1fd6f0f129232'
PROOF = '0336830208403aecc662a00ab7b16341a2769894c490d30f31e543d8bb4c83e9'
FIELDS = {'schema_version', 'workbook', 'source_sha256', 'version', 'custody',
          'original_profile', 'guard', 'guard_intent', 'guard_outcome', 'checkpoint',
          'descriptor', 'tracker', 'prior_takeover', 'config', 'normalization_proof',
          'runtime_adapter', 'semantic_budget_reset', 'expired_replacement_limit'}


def require(value, message):
    if not value:
        raise ValueError(message)


def binding(path):
    path = Path(path).absolute()
    require(not any(p.is_symlink() for p in (path, *path.parents)), 'symlink timeout evidence')
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def read(row):
    require(type(row) is dict and set(row) == {'path', 'sha256'} and binding(row['path']) == row,
            'normalization timeout evidence changed')
    return json.loads(Path(row['path']).read_bytes())


def profile(row):
    p = read(row)
    require(set(p) == FIELDS and p['schema_version'] == SCHEMA and p['workbook'] == '0006'
            and p['source_sha256'] == SOURCE and p['version'] == VERSION
            and p['guard']['sha256'] == GUARD and p['tracker']['sha256'] == TRACKER
            and p['normalization_proof']['sha256'] == PROOF
            and p['semantic_budget_reset'] is False and p['expired_replacement_limit'] == 3,
            'only the selected 0006 normalization timeout qualifies')
    custody = read(p['custody'])
    captured = {x['native']['path']: x for x in custody['files']}
    for key in ('guard', 'guard_intent', 'guard_outcome', 'checkpoint', 'descriptor', 'tracker', 'runtime_adapter'):
        entry = captured.get(p[key]['path'])
        require(entry and entry['native'] == p[key] and binding(entry['copy']['path']) == entry['copy']
                and entry['copy']['sha256'] == p[key]['sha256'], 'stopped capture binding differs')
    old = read(p['original_profile'])
    require(old['workbook'] == '0006' and old['version'] == VERSION
            and old['source_sha256'] == SOURCE and old['config'] == p['config'],
            'original source/configuration differs')
    guard = read(p['guard'])
    require(set(guard) == {'intent', 'log', 'metrics', 'returncode', 'failure'}
            and guard['returncode'] is None and guard['intent'] == p['guard_intent']
            and guard['failure'] == {'type': 'ConversionTimeout', 'message_sha256':
                '4a71c32674908a31e9df52d0938f3a00707c49e75029d252df1d601e1970ada3'}
            and guard['metrics'] == {'limit_reason': 'wall_time', 'peak_rss_bytes': 864346112},
            'exact original wall-time guard required')
    require(binding(guard['log']['path']) == guard['log'], 'stopped log changed')
    intent = read(p['guard_intent'])
    require(intent['schema_version'] == 'harbor-coordinator-step/v1' and intent['action'] == 'step'
            and intent['timeout_seconds'] == 3300 and intent['memory_limit_bytes'] == 8 * 1024**3
            and intent['dispatch'] is True and intent['arguments'] == {
                'captures': None, 'checkpoint': p['checkpoint'], 'descriptor': p['descriptor']},
            'original environment step/limits differ')
    outcome = read(p['guard_outcome'])
    require(outcome['status'] == 'RECONCILIATION_REQUIRED' and outcome['resource'] == p['guard']
            and outcome['automatic_retry'] is False and outcome.get('result') is None,
            'timed-out operation must remain unresolved')
    proof = read(p['normalization_proof'])
    require(proof['status'] == 'ACTUAL_NORMALIZATION_PHASE1_AFTER_TIMEOUT_VALIDATED'
            and proof['tracker'] == p['tracker'] and proof['candidate_count'] == 185
            and proof['original_ordinal'] == 1 and proof['original_limit'] == 2
            and proof['models_dispatched'] == proof['owning_operations'] == 0
            and proof['phase1_lane_committed'] is False and proof['artifact_bytes_unchanged'] is True,
            'actual retained normalization proof differs')
    for key in ('prepared', 'phase1', 'review', 'outcome'):
        require(binding(proof[key]['path']) == proof[key], 'completed normalization artifact changed')
    normalized_witness(proof)
    for value in proof['call'].values():
        require(binding(value['path']) == value, 'completed normalization Call changed')
    require(read(proof['outcome'])['status'] == 'completed', 'normalization Call remains uncertain')
    # The tracker is checked against its captured immutable bytes here; the
    # owning T._recipe separately authenticates live or historical tracker state.
    tracker = read(captured[p['tracker']['path']]['copy'])
    require(tracker['revision'] == 6 and tracker['bindings']['original_raw_source']['sha256'] == SOURCE
            and tracker['lane_state']['harbor-research-auditor']['state'] == 'passed',
            'retained research completion differs')
    return p


def normalized_witness(proof):
    """Read the producer's exact phase1 copy after later normalized.json updates.

    The original proof and its normalized.json hash are retained unchanged.
    This is an explicit byte-identical historical witness, not a new hash for
    that mutable path. Subsequent owning gates still validate actual phase2.
    """
    value = read(proof['phase1'])
    phase1 = value['phase1']
    require(value['status'] == 'phase1_evidence_ready'
            and Path(phase1['path']) == Path(proof['normalized']['path']).with_name('normalized.phase1.json')
            and phase1['sha256'] == proof['normalized']['sha256']
            and binding(phase1['path']) == phase1,
            'exact original normalized phase1 witness differs')
    return phase1


def allowed(selection, guard, selected_profile):
    p = profile(selected_profile)
    require(selection.workbook == '0006' and selection.worker_kind == 'environment'
            and selection.guard == p['guard'] and selection.tracker == p['tracker']
            and selection.checkpoint == p['checkpoint'] and selection.config == p['config']
            and selection.prior_completion == p['prior_takeover']
            and selection.duration_ns == 7200 * 10**9 and guard == read(p['guard']),
            'exact third replacement selection required')
    return True


def external_environment(selection, intent, run, tracker_path, selected_profile):
    """Authenticate one real environment coordinator outside the campaign home."""
    p = profile(selected_profile)
    allowed(selection, read(p['guard']), selected_profile)
    require(intent == read(p['guard_intent']), 'external environment intent differs')
    from scripts import harbor_campaign as C
    descriptor = read(p['descriptor'])
    require(set(descriptor) == {'schema_version', 'worker', 'controls', 'origin', 'code', 'initial_lease'}
            and descriptor['schema_version'] == 'harbor-environment-coordinator/v1', 'owning environment descriptor required')
    worker, initial, controls = C.selected(p['descriptor'])
    require(worker.bootstrap.workbook == '0006' and worker.run == run
            and worker.bootstrap.policy.tracker_root == tracker_path.parent,
            'external guard belongs to another environment')
    require(descriptor['controls'] == read(p['config'])['controls'] == intent['controls']
            and descriptor['code'] == intent['code'] == binding(C.__file__),
            'external guard producer or configured controls differ')
    prior = read(p['checkpoint'])
    require(prior['status'] == 'IMPORTED' and prior['descriptor'] == p['descriptor'],
            'exact actual research import required')
    C._prior(p['checkpoint'], p['descriptor'])
    evidence = [p['descriptor'], p['runtime_adapter'], p['normalization_proof'],
                *descriptor['origin'], *prior['evidence']]
    for item in evidence:
        require(binding(item['path']) == item, 'external environment ancestry changed')
    return evidence

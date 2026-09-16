"""Fenced continuation of an unsent, oversized normalization on its owning code.

Standalone orchestration only: immutable partition/refresh/authoring APIs own
all evidence and gates. No action is replayed; every stage retains its guard.
"""
from __future__ import annotations
import argparse
from dataclasses import replace
import hashlib
import importlib.util
import json
import os
import shutil
from pathlib import Path
import sys

SCHEMA = 'harbor-fleet-normalization-continuation/v1'
ERROR_SHA = 'd047209d6de55067a0a42557e2db791bbef1eb5f1838414475fb566a3138c4c2'
MEMORY = 8 * 1024**3
SECONDS = 3300
TERMINAL_CHANGES = frozenset({'scripts/batch/harbor_normalization_partitioned.py',
    'scripts/batch/harbor_normalization_routing.py',
    'scripts/batch/harbor_normalization_terminal_repair.py'})


def require(ok, message):
    if not ok: raise ValueError(message)


def binding(path):
    path = Path(path).absolute()
    require(path.is_file() and not path.is_symlink(), 'regular evidence file required')
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def checked(row):
    require(set(row) == {'path', 'sha256'} and binding(row['path']) == row, 'evidence changed')
    return json.loads(Path(row['path']).read_bytes())


def retain(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()
    with path.open('xb') as f: f.write(raw); f.flush(); os.fsync(f.fileno())
    return binding(path)


def successor(plan, original_job):
    """Select a declared producer successor without altering prior evidence."""
    value = plan['terminal_rejection']
    require(set(value) == {'permit', 'successor'}, 'closed terminal continuation required')
    p = checked(value['successor'])
    require(set(p) == {'schema_version', 'original_snapshot', 'snapshot', 'original_config',
        'config', 'job', 'changed_files'} and p['schema_version'] == 'normalization-terminal-code-successor/v1',
        'closed terminal code successor required')
    old_config = checked(p['original_config']); config = checked(p['config'])
    require(original_job['config'] == p['original_config'] and old_config['code'] == p['original_snapshot']
        and config == {**old_config, 'code': p['snapshot']}, 'only the current code selection may change')
    old = checked(p['original_snapshot']); new = checked(p['snapshot'])
    require(old['root'] != new['root'] == plan['code_root'], 'separate selected code root required')
    inventories = []
    for snapshot in (old, new):
        require(set(snapshot) == {'root', 'files'}, 'closed full code inventory required')
        files = {r['relative']: r['sha256'] for r in snapshot['files']}
        require(len(files) == len(snapshot['files']), 'duplicate code inventory entry')
        root = Path(snapshot['root'])
        actual = {}
        for path in root.rglob('*'):
            if any(x in {'.git', '__pycache__', '.pytest_cache'} for x in path.relative_to(root).parts): continue
            require(not path.is_symlink(), 'symlink in selected code')
            if path.is_file(): actual[str(path.relative_to(root))] = binding(path)['sha256']
        require(files == actual, 'full code inventory differs')
        inventories.append(files)
    a, b = inventories
    changed = {name for name in a.keys() | b.keys() if a.get(name) != b.get(name)}
    # The canonical inventory is itself data; its content must describe every
    # selected implementation file, rather than becoming an arbitrary delta.
    manifest = '.pipeline-code.json'
    if manifest in changed:
        mv = json.loads((Path(new['root']) / manifest).read_bytes())
        require(mv == {k: v for k, v in b.items() if k != manifest}, 'canonical selected inventory differs')
        changed.remove(manifest)
    require(changed == set(p['changed_files']) and changed <= TERMINAL_CHANGES
        and 'scripts/batch/harbor_normalization_terminal_repair.py' in changed,
        'unreviewed terminal producer code delta')
    job = checked(p['job'])
    driver = Path(new['root']) / Path(original_job['driver']['path']).relative_to(old['root'])
    require(binding(driver)['sha256'] == original_job['driver']['sha256']
        and job == {**original_job, 'driver': binding(driver), 'config': p['config']},
        'current dispatcher or original authoring controls changed')
    return job


def historical_worker(base, descriptor, plan):
    """Run the unchanged descriptor reader with one same-byte code locator.

    Its worker, original intake, source, controls, and origin checks remain the
    original function. Only this historical read sees the prior coordinator
    binding; subsequent refreshes and guarded writes use the selected root.
    """
    from types import FunctionType
    p = checked(plan['terminal_rejection']['successor'])
    old = checked(p['original_snapshot']); new = checked(p['snapshot'])
    current = Path(base.__file__).absolute()
    prior = Path(old['root']) / current.relative_to(new['root'])
    original = binding(prior); selected = binding(current)
    require(original['sha256'] == selected['sha256'] and checked(descriptor)['code'] == original,
        'historical coordinator is not the identical selected implementation')
    function = base.selected
    require(function.__globals__ is base.__dict__ and function.__closure__ is None,
        'plain original coordinator reader required')
    record = base.record
    def historical_record(path):
        if Path(path).absolute() == current:
            require(binding(prior) == original and binding(current) == selected, 'coordinator changed during historical read')
            return original
        return record(path)
    namespace = {**function.__globals__, 'record': historical_record}
    return FunctionType(function.__code__, namespace, function.__name__, function.__defaults__)(descriptor)


def initial_lease(plan, base):
    old = base.decode(checked(plan['unresolved'])['lease'])
    if 'lease_continuation' not in plan: return old
    require('terminal_rejection' in plan, 'explicit terminal continuation required for replacement custody')
    p = plan['lease_continuation']
    require(set(p) == {'takeover', 'completion'}, 'closed completed takeover custody required')
    require(binding(p['takeover']['path']) == p['takeover'], 'takeover reader changed')
    spec = importlib.util.spec_from_file_location('terminal_completed_takeover', p['takeover']['path'])
    T = importlib.util.module_from_spec(spec); sys.modules[spec.name] = T; spec.loader.exec_module(T)
    before, after, lease = T.validate_completion(p['completion'])
    stop = checked(checked(p['completion'])['stopped_owner_proof'])
    successor_proof = checked(plan['terminal_rejection']['successor'])
    require(stop['selection']['checkpoint'] == plan['unresolved']
        and stop['selection']['config'] == successor_proof['original_config']
        and stop['selection']['worker_kind'] == 'environment'
        and stop['selection']['tracker']['path'] == plan['tracker']['path']
        and stop['selection']['workbook'] == plan['workbook']
        and (old.owner, old.token) == (before['leases']['task_agent']['owner'], before['leases']['task_agent']['token'])
        and (old.revision, old.sha256) == (before['revision'], stop['before']['sha256'])
        and plan['tracker']['sha256'] == checked(p['completion'])['after']['sha256'],
        'completed replacement detached from original unresolved lease')
    require(isinstance(lease, base.bootstrap.Lease), 'actual typed replacement lease required')
    return lease


def load(plan):
    fields = {'schema_version', 'workbook', 'code_root', 'home', 'original_job',
              'tracker', 'unresolved', 'research', 'normalization_plan', 'stopped_proof'}
    require(set(plan) - {'prepared_continuation', 'terminal_rejection', 'lease_continuation'} == fields and plan['schema_version'] == SCHEMA, 'closed continuation plan required')
    require('lease_continuation' not in plan or 'terminal_rejection' in plan, 'replacement custody requires terminal continuation')
    require(not ('prepared_continuation' in plan and 'terminal_rejection' in plan), 'distinct continuation authorities cannot be combined')
    job = checked(plan['original_job']); checked(plan['stopped_proof'])
    if 'terminal_rejection' in plan: job = successor(plan, job)
    root = Path(plan['code_root']); require(root.is_absolute() and root.is_dir(), 'owning code root missing')
    require(not Path(plan['home']).is_relative_to(root), 'continuation must be outside immutable code')
    if 'terminal_rejection' in plan:
        helper = root / 'scripts/fleet/stdlib_imports.py'
        require(binding(helper)['sha256'] == '5bb59bfa3010bc1ba156b20ba12ae602881b590877c39e63fd98b6a6dda588a8',
            'reviewed cold standard-library bootstrap required')
        spec = importlib.util.spec_from_file_location('normalization_stdlib', helper)
        bootstrap = importlib.util.module_from_spec(spec); spec.loader.exec_module(bootstrap)
        bootstrap.preload_inspect()
    sys.path.insert(0, str(root)); sys.path.insert(1, str(Path(job['driver']['path']).parent))
    require(binding(job['driver']['path']) == job['driver'], 'dispatcher changed')
    spec = importlib.util.spec_from_file_location('selected_continuation_dispatcher', job['driver']['path'])
    dispatcher = importlib.util.module_from_spec(spec); spec.loader.exec_module(dispatcher)
    env = dispatcher.setup(job); base, _, _, _, _, config, actual_root, controls = env
    require(actual_root == root and config['workbook'] == plan['workbook'], 'owning identity changed')
    require(base.SECONDS == SECONDS and base.MEMORY == MEMORY and job['publish'] is False, 'original guards/publication scope changed')
    from scripts.batch import harbor_normalization_partitioned as P
    from scripts.batch.harbor_normalization_recovery import selection_from
    old = checked(plan['normalization_plan'])
    selected = selection_from({k: old[k] for k in ('workbook', 'bindings', 'run', 'policy', 'budget', 'first_normalization')})
    unresolved = checked(plan['unresolved']); research = checked(plan['research'])
    worker, _, _ = (historical_worker(base, unresolved['descriptor'], plan)
        if 'terminal_rejection' in plan else base.selected(unresolved['descriptor']))
    require(worker.normalization_budget == selected.budget and selected.budget.ordinal == 1 and selected.budget.limit == 2,
            'original normalization budget must remain first of two')
    require(selected.workbook == plan['workbook'] == worker.bootstrap.workbook, 'workbook changed')
    require(unresolved['status'] == 'UNRESOLVED' and unresolved['step'] == 'normalization_phase1', 'exact unresolved boundary required')
    reasons = [checked(x) for x in unresolved['evidence'] if Path(x['path']).name.startswith('unresolved-')]
    if 'terminal_rejection' in plan:
        from scripts.batch import harbor_normalization_terminal_repair as T
        permit, _ = T.validate(plan['terminal_rejection']['permit'], selected, worker.campaign)
        require(permit['previous_selection'] == plan['normalization_plan']
            and len(reasons) == 1 and reasons[0]['error_type'] == 'AuditTransportStop'
            and reasons[0]['automatic_retry'] is False, 'exact terminal normalization stop required')
    else:
        require(len(reasons) == 1 and reasons[0]['error_sha256'] == ERROR_SHA, 'only proven unsent size guard supported')
    require(research['status'] == 'STEP_COMPLETE' and research['step'] == 'research', 'successful research checkpoint required')
    require(research['descriptor'] == unresolved['descriptor'], 'research descriptor changed')
    return dispatcher, env, P, selected, worker


def active(workbook):
    from scripts.fleet.coordinator import Client
    keys = {'owner': 'OWNER', 'token': 'CLAIM_TOKEN', 'fence': 'FENCE'}
    require(os.environ.get('HARBOR_FLEET_WORKBOOK') == workbook, 'live fleet workbook claim required')
    auth = {k: os.environ['HARBOR_FLEET_' + suffix] for k, suffix in keys.items()}
    auth.update(workbook=workbook, fence=int(auth['fence']))
    return Client(os.environ['HARBOR_FLEET_URL'], os.environ['HARBOR_FLEET_TOKEN_FILE']).call('heartbeat', **auth, lease_seconds=180)


def configure_campaign(base, campaign):
    require(type(campaign.model_slots) is int and campaign.model_slots == 1, 'fleet model limit must remain one')
    base._campaign(campaign)
    require(os.environ.get('HARBOR_MODEL_SLOTS') == '1' and
            os.environ.get('HARBOR_MODEL_SLOT_ROOT') == str(campaign.slot_root), 'canonical local model slot configuration failed')


def retained_preparation(plan, P, base, worker):
    proof = plan['prepared_continuation']
    require(set(proof) == {'prepared', 'prepare_result', 'failed_resource', 'previous_plan', 'previous_result'}, 'closed prepared continuation required')
    previous = checked(proof['previous_plan']); prior_result = checked(proof['previous_result'])
    require(previous['schema_version'] == SCHEMA and 'prepared_continuation' not in previous, 'only original setup failure can use this continuation')
    require(prior_result['status'] == 'RECONCILIATION_REQUIRED' and prior_result['plan'] == proof['previous_plan'], 'original failed composition changed')
    require(all(previous[k] == plan[k] for k in ('workbook','code_root','original_job','unresolved','research','normalization_plan')), 'prepared continuation changed owning source context')
    before = Path(previous['home']); home = Path(plan['home'])
    require(home != before and not home.is_relative_to(before) and not before.is_relative_to(home), 'new continuation must be a disjoint sibling')
    prepared = checked(proof['prepare_result']); preparation = Path(proof['prepare_result']['path']).parent
    require(proof['prepare_result']['path'] == str(before/'stages/prepare/result.json') and prepared['value'] == proof['prepared'], 'foreign retained preparation')
    guard = checked(binding(preparation/'resource.json')); intent = checked(guard['intent'])
    require(guard['returncode'] == 0 and guard['failure_type'] is None and guard['log'] == binding(preparation/'step.log') and
            intent['action'] == 'prepare' and intent['plan'] == proof['previous_plan'], 'preparation guard did not complete')
    failed = checked(proof['failed_resource']); failed_dir = Path(proof['failed_resource']['path']).parent
    failure_intent = checked(failed['intent'])
    require(failed_dir == before/'stages/partition-0000-round-1' and failed['returncode'] == 1 and failed['failure_type'] is None,
            'only first unsubmitted configuration failure is supported')
    require(failed['log'] == binding(failed_dir/'step.log') and failed['log']['sha256'] ==
            '9fb4f89a08bd6ce8dbf1e2fef8378cccef0eed564a7b90e777e38ec3114b9389', 'exact executor configuration failure log required')
    require(failure_intent['driver']['sha256'] == '6a8354c667a041e3f2bde4fa59e8b4656292b6ff4e9ec7c3de48e8f14ee4bcd6' and
            failure_intent['plan'] == proof['previous_plan'] and failure_intent['action'] == 'dispatch' and
            failure_intent['arguments'] == dict(prepared=proof['prepared'],index=0,ordinal=1) and
            failure_intent['lease'] == prepared['lease'], 'failed dispatch identity changed')
    require(not (failed_dir/'lease-renewal.json').exists() and not (failed_dir/'result.json').exists(), 'failed stage has later state requiring reconciliation')
    lease = base.decode(prepared['lease']); require(lease.sha256 == plan['tracker']['sha256'], 'current tracker differs from retained prepared lease')
    base.worker._active(worker, lease)
    manifest, selected, campaign, draft, rows = P._current(proof['prepared'])
    require(campaign == worker.campaign and selected.budget == worker.normalization_budget and
            manifest['previous']['prior_plan'] == plan['normalization_plan'], 'retained partition source/budget differs')
    for index in range(len(manifest['partitions'])):
        for ordinal in (1,2):
            call = P._prepared_call(proof['prepared'],index,ordinal)
            require(not P.E.location(call,campaign).exists() and not (campaign.root/'attempt-identities'/(call.attempt_id+'.json')).exists(),
                    'a partition was submitted or uncertain; this continuation cannot replay it')
    return proof['prepared'], prepared['lease'], manifest, rows


def probe(plan_binding):
    plan = checked(plan_binding); dispatcher, env, P, selected, worker = load(plan)
    base = env[0]; unresolved = checked(plan['unresolved']); lease = initial_lease(plan, base)
    require(binding(plan['tracker']['path']) == plan['tracker'], 'selected tracker changed; reconcile first')
    require(not Path(plan['home']).exists(), 'continuation namespace already consumed')
    current = P.N.selection_data(selected)[0]; previous = checked(plan['normalization_plan'])
    require(current == ({**previous, 'code': current['code']} if 'terminal_rejection' in plan else previous),
        'current source or original preparation differs')
    if 'prepared_continuation' in plan:
        prepared, lease, manifest, rows = retained_preparation(plan, P, base, worker)
        return dict(status='SUPPORTED_RETAINED_UNSENT_PARTITIONS', plan=plan_binding, prepared=prepared,
            candidate_count=len(manifest['full_candidate_references']), atomic_rows=len(rows), partitions=len(manifest['partitions']),
            request_bytes=[Path(p['request']['path']).stat().st_size for p in manifest['partitions']],
            original_budget=manifest['original_budget'], code_changed=False, semantic_budget_reset=False, models_dispatched=0)
    base.worker._active(worker, lease)
    if 'terminal_rejection' in plan:
        from scripts.batch import harbor_normalization_terminal_repair as T
        permit, _ = T.validate(plan['terminal_rejection']['permit'], selected, worker.campaign, at=__import__('time').time_ns())
        P.unused(selected, worker.campaign, terminal_rejection=plan['terminal_rejection']['permit'])
        body = P.read(binding(selected.run / 'review-evidence.json')); rows = P.read(binding(selected.run / 'analysis.json'))
        parts = P.K.partitions(body, lambda compact, refs: P.request(selected, compact, refs), max_references=P.K.MAX_REFERENCES)
        refs = [ref for group, _ in parts for ref in group]
        require(len(parts) == permit['partitions'] and len(refs) == len(set(refs))
            and set(refs) == {r['reference'] for r in rows if r['clean']}, 'terminal partition coverage or allowance differs')
        return dict(status='SUPPORTED_TERMINAL_PARTITION_CONTINUATION', plan=plan_binding,
            candidate_count=len(refs), atomic_rows=len(rows), partitions=len(parts),
            request_bytes=[P.K.encoded_size(payload) for _, payload in parts],
            original_consumed_physical_calls=1, maximum_additional_physical_calls=permit['maximum_additional_physical_calls'],
            semantic_budget_reset=False, prior_response_reused=False, models_dispatched=0)
    P.unused(selected, worker.campaign)
    for name in ('prepared.json', 'review-request.json', 'normalization-review.json', 'phase1-result.json'):
        require(not (selected.run / name).exists(), 'original request/preparation already consumed')
    body = P.read(binding(selected.run / 'review-evidence.json'))
    rows = P.read(binding(selected.run / 'analysis.json'))
    parts = P.K.partitions(body, lambda compact, refs: P.request(selected, compact, refs), max_references=P.K.MAX_REFERENCES)
    refs = [ref for group, _ in parts for ref in group]
    require(len(refs) == len(set(refs)) and set(refs) == {r['reference'] for r in rows if r['clean']}, 'partition coverage differs')
    return dict(status='SUPPORTED_UNSENT_PARTITION_CONTINUATION', plan=plan_binding,
        candidate_count=len(refs), atomic_rows=len(rows), partitions=len(parts),
        request_bytes=[P.K.encoded_size(payload) for _, payload in parts], original_budget=checked(plan['normalization_plan'])['budget'],
        code_changed=False, semantic_budget_reset=False, models_dispatched=0)


def child(intent_binding):
    intent = checked(intent_binding); plan = checked(intent['plan']); dispatcher, env, P, selected, worker = load(plan)
    require(intent['driver'] == binding(__file__) and intent['seconds'] == SECONDS and intent['memory'] == MEMORY, 'guarded helper changed')
    active(plan['workbook']); base = env[0]; configure_campaign(base, worker.campaign); directory = Path(intent_binding['path']).parent
    lease = base.decode(intent['lease']); lease = base._lease(worker, lease, directory)
    args = intent['arguments']; action = intent['action']
    if action == 'prepare':
        selected = replace(selected, run=Path(plan['home']) / 'normalization' / selected.run.name)
        kwargs = {'terminal_rejection': plan['terminal_rejection']['permit']} if 'terminal_rejection' in plan else {}
        value = P.prepare(selected, worker.campaign, Path(plan['home']) / 'partitions', prior_plan=plan['normalization_plan'], **kwargs)
    elif action == 'dispatch':
        from xl_task_build import read_env_key
        credential = read_env_key(env[-1].env_file); require(bool(credential), 'configured credential unavailable')
        out = P.dispatch(args['prepared'], args['index'], args['ordinal'], credential=credential)
        # Inspection, not replay, establishes whether this actual response can
        # enter the remaining semantic round. Unknown transport raises here.
        m, s, campaign, draft, rows = P._current(args['prepared'])
        review = P._review(args['prepared'], args['index'], args['ordinal'], campaign, rows)
        value = retain(directory / 'review.json', review)
    elif action == 'emit': value = P.emit_phase1(args['prepared'])
    else: raise ValueError('unsupported guarded partition action')
    return retain(directory / 'result.json', dict(value=value, lease=base.encode(lease), automatic_retry=False))


def guarded(plan_binding, action, arguments, lease, name, env):
    plan = checked(plan_binding); directory = Path(plan['home']) / 'stages' / name
    require(not directory.exists(), 'stage already attempted; reconcile retained evidence')
    require(shutil.disk_usage(plan['code_root']).free >= MEMORY, '8-GiB free-space reserve required before stage')
    directory.mkdir(parents=True)
    intent = retain(directory / 'intent.json', dict(plan=plan_binding, driver=binding(__file__), action=action,
        arguments=arguments, lease=lease, seconds=SECONDS, memory=MEMORY))
    from scripts.batch.hardened_runner import _run_in_process_group
    metrics = {}; rc = None; failure = None
    try:
        with (directory / 'step.log').open('xb') as log:
            rc = _run_in_process_group([sys.executable, '-I', '-B', str(Path(__file__).resolve()), 'child',
                '--plan', intent['path'], '--plan-sha256', intent['sha256']], stdout=log,
                timeout_seconds=SECONDS, grace_seconds=3, memory_limit_bytes=MEMORY,
                metrics=metrics, cwd=Path(plan['code_root']), env={**os.environ, 'PYTHONDONTWRITEBYTECODE':'1'})
    except Exception as exc: failure = type(exc).__name__
    retain(directory / 'resource.json', dict(intent=intent, returncode=rc, failure_type=failure,
        metrics=metrics, log=binding(directory/'step.log'), automatic_retry=False))
    require(rc == 0 and failure is None, 'partition stage stopped; reconcile, never replay')
    result = checked(binding(directory/'result.json')); return result['value'], result['lease']


def run(plan_binding):
    plan = checked(plan_binding); diagnostic = probe(plan_binding)
    dispatcher, env, P, selected, worker = load(plan); active(plan['workbook'])
    home = Path(plan['home']); home.mkdir(parents=True, mode=0o700)
    retain(home/'intent.json', dict(plan=plan_binding, driver=binding(__file__), probe=diagnostic, automatic_retry=False))
    base = env[0]; controls = env[-1]; original_job = checked(plan['original_job'])
    if 'terminal_rejection' in plan: original_job = successor(plan, original_job)
    lease = base.encode(initial_lease(plan, base)) if 'lease_continuation' in plan else checked(plan['unresolved'])['lease']
    try:
        if 'prepared_continuation' in plan:
            prepared, lease, _, _ = retained_preparation(plan, P, base, worker)
            retain(home/'prepared-custody.json', dict(prepared=prepared, predecessor=plan['prepared_continuation'], emission_replayed=False, preparation_replayed=False))
        else:
            prepared, lease = guarded(plan_binding, 'prepare', {}, lease, 'prepare', env)
        manifest = checked(prepared); first = []
        for index in range(len(manifest['partitions'])):
            review, lease = guarded(plan_binding, 'dispatch', dict(prepared=prepared,index=index,ordinal=1), lease, f'partition-{index:04d}-round-1', env)
            first.append(checked(review))
        for index, review in enumerate(first):
            if not review['valid']:
                reviewed, lease = guarded(plan_binding, 'dispatch', dict(prepared=prepared,index=index,ordinal=2), lease, f'partition-{index:04d}-round-2', env)
                require(checked(reviewed)['valid'], 'partition semantic budget exhausted')
        emitted_binding, lease = guarded(plan_binding, 'emit', dict(prepared=prepared), lease, 'emit', env)
        emitted = checked(emitted_binding); require(emitted['status'] == 'phase1_evidence_ready', 'normalization did not qualify')
        from scripts.batch.harbor_transition_codec import decode, encode
        old = decode(checked(plan['research'])['selection']); content = old.content; r = P.selection(prepared).run
        bindings = {**content.bindings, 'normalization_partitioned_review':emitted['normalization_partitioned_review'],
            'normalization_draft':binding(r/'draft.json'), 'normalizer':binding(r/f'normalize_{plan["workbook"]}.py'),
            'normalization_prepared_manifest':prepared, 'normalized_phase1':emitted['phase1'],
            'normalized_final':binding(r/'normalized.json'), 'normalization_report':binding(r/'normalization_report.json'),
            'normalization_exclusions':binding(r/'exclusions.json')}
        current = replace(old, content=replace(content, bindings=bindings, normalization_run=r, normalization_prepared=prepared, normalization_call=None))
        from dataclasses import asdict
        legacy = retain(home/'legacy.json', dict(worker=base.worker.bootstrap.serial(asdict(worker)), lease=lease))
        arguments = dict(legacy=legacy, checkpoint=plan['research'], current=retain(home/'current.json', encode(current)), lease=retain(home/'lease.json', lease))
        active(plan['workbook']); outcome = base.run('refresh', arguments, controls, home/'refresh')
        result = checked(outcome); require(result['status'] == 'COMPLETED_OPERATION', 'prefix refresh requires reconciliation')
        checkpoint = result['result']; refreshed = checked(checkpoint)
        require(refreshed['status'] == 'IMPORTED', 'prefix refresh did not import')
        outcome = base.run('step', dict(descriptor=refreshed['descriptor'], checkpoint=checkpoint, captures=None), controls, home/'bridge-step', dispatch=True)
        dispatcher.successful(outcome, env)
        job = {**original_job, 'outcome':outcome, 'operations':str(Path(env[5]['run_root'])/'dispatcher-v2'/home.name)}
        job_binding = retain(home/'authoring-job.json', job); dispatcher.run(job_binding)
        held = checked(binding(Path(job['operations'])/'hold.json'))
        checkpoint, result = dispatcher.successful(held['outcome'], env)
        require(result['status'] == 'PUBLICATION_READY', 'remaining authoring stopped at owning hold')
        return retain(home/'result.json', dict(status='PUBLICATION_READY', workbook=plan['workbook'], plan=plan_binding,
            job=job_binding, config=job['config'], outcome=held['outcome'], checkpoint=checkpoint,
            publication_performed=False, automatic_retry=False))
    except Exception as exc:
        retain(home/'result.json', dict(status='RECONCILIATION_REQUIRED', workbook=plan['workbook'], plan=plan_binding,
            failure_type=type(exc).__name__, publication_performed=False, automatic_retry=False))
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('action', choices=('probe','run','child'))
    p.add_argument('--plan',required=True); p.add_argument('--plan-sha256',required=True)
    a = p.parse_args(); row=dict(path=str(Path(a.plan).absolute()),sha256=a.plan_sha256)
    value = {'probe':probe,'run':run,'child':child}[a.action](row)
    print(json.dumps(value), flush=True)

if __name__ == '__main__': main()

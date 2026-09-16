"""0073 expired-custody continuation, derived from the reviewed 0270 helper.

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

SCHEMA = 'harbor-fleet-0073-normalization-continuation/v1'
ERROR_SHA = 'd047209d6de55067a0a42557e2db791bbef1eb5f1838414475fb566a3138c4c2'
MEMORY = 8 * 1024**3
SECONDS = 3300


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


def load(plan):
    fields = {'schema_version', 'workbook', 'code_root', 'home', 'original_job',
              'tracker', 'unresolved', 'research', 'normalization_plan', 'stopped_proof'}
    require(set(plan) == fields | {'snapshot', 'original_config'} and plan['schema_version'] == SCHEMA and plan['workbook']=='0073', 'closed 0073 continuation plan required')
    checked(plan['snapshot']); checked(plan['original_config'])
    job = checked(plan['original_job']); handoff=checked(plan['stopped_proof'])
    require(handoff['schema_version']=='local-to-vm-custody-handoff/v1' and handoff['status']=='LOCAL_DISPATCH_PAUSED_HANDOFF_FOR_SUPPORTED_VM_ADMISSION', 'explicit local custody handoff required')
    require(checked(handoff['pause'])['new_dispatch_allowed'] is False, 'local dispatch not paused')
    checked(handoff['process_check']);checked(handoff['lock_check'])
    rows=[r for r in handoff['workbooks'] if r['workbook_id']=='0073']
    require(len(rows)==1 and rows[0]['status']=='normalization_context_size_hold' and rows[0]['original_source']['sha256']=='cde07456bdefc8b3ae66dcd98040c86c3cf16c9896e9790064380932ed116e39','original held custody differs')
    root = Path(plan['code_root']); require(root.is_absolute() and root.is_dir(), 'owning code root missing')
    require(not Path(plan['home']).is_relative_to(root), 'continuation must be outside immutable code')
    sys.path.insert(0, str(root)); sys.path.insert(1, str(Path(job['driver']['path']).parent))
    require(binding(job['driver']['path']) == job['driver'], 'dispatcher changed')
    spec = importlib.util.spec_from_file_location('selected_continuation_dispatcher', job['driver']['path'])
    dispatcher = importlib.util.module_from_spec(spec); spec.loader.exec_module(dispatcher)
    env = dispatcher.setup(job); base, _, _, _, _, config, actual_root, controls = env
    require(actual_root == root and config['workbook'] == plan['workbook'], 'owning identity changed')
    require(base.SECONDS == SECONDS and base.MEMORY == MEMORY and job['publish'] is False, 'original guards/publication scope changed')
    from scripts.fleet import legacy_0073 as L
    L.fleet_profile()
    require(config['code']==plan['snapshot'], 'root-selected exact successor snapshot required')
    old_config=checked(plan['original_config']);expected=dict(old_config);expected['code']=plan['snapshot'];expected['controls']=dict(type='Controls',value={**old_config['controls']['value'],'env_file':{'type':'path','value':'/home/gmahajan_labelbox_com/.config/harbor-fleet/model.env'}})
    require(config==expected, 'only reviewed code and native credential locator may change')
    from scripts.batch import harbor_normalization_partitioned as P
    from scripts.batch.harbor_normalization_recovery import selection_from
    old = checked(plan['normalization_plan'])
    selected = selection_from({k: old[k] for k in ('workbook', 'bindings', 'run', 'policy', 'budget', 'first_normalization')})
    unresolved = checked(plan['unresolved']); research = checked(plan['research'])
    descriptor=checked(unresolved['descriptor']); worker=base.decode(descriptor['worker'])
    require(descriptor['code']['sha256']==binding(Path(plan['code_root'])/'scripts/harbor_campaign.py')['sha256'], 'owning coordinator implementation changed')
    require(worker.normalization_budget == selected.budget and selected.budget.ordinal == 1 and selected.budget.limit == 2,
            'original normalization budget must remain first of two')
    require(selected.workbook == plan['workbook'] == worker.bootstrap.workbook, 'workbook changed')
    require(unresolved['status'] == 'UNRESOLVED' and unresolved['step'] == 'normalization_phase1', 'exact unresolved boundary required')
    reasons = [checked(x) for x in unresolved['evidence'] if Path(x['path']).name.startswith('unresolved-')]
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


def takeover_selection(plan, owner):
    from scripts.batch import harbor_lease_takeover as T
    original=checked(plan['original_config']); home=Path(original['run_root'])
    return T.Selection(campaign_root=home,workbook='0073',tracker=plan['tracker'],worker_kind='environment',
        guard=binding(home/'dispatcher-v2/first/003/resource.json'),checkpoint=plan['research'],owner=owner,config=plan['original_config'])


def takeover_directory(plan):
    return Path(checked(plan['original_config'])['run_root'])/'lease-takeovers'/'fleet-normalization-0073-001'


def probe(plan_binding):
    plan=checked(plan_binding);dispatcher,env,P,selected,worker=load(plan)
    require(binding(plan['tracker']['path'])==plan['tracker'],'tracker changed; reconcile')
    require(not Path(plan['home']).exists(),'continuation namespace already consumed')
    require(selected.budget.attempt_id=='3973add1-8769-4a3c-b1d4-bcbf3564b79f','original normalization UUID changed')
    from scripts.batch import harbor_lease_takeover as T
    before,old,run,evidence=T._recipe(takeover_selection(plan,'fleet-0073-readonly-probe'))
    base=env[0];current=base.content_codec.decode(checked(plan['research'])['selection'])
    check=base.worker.lanes.evaluate_completed_gate(before,base.worker.lanes.CompletionEvidence('research',current),base.worker._context(worker))
    require(check['passed'],'owning retained research prefix differs')
    P.unused(selected,worker.campaign)
    require(not any((selected.run/name).exists() for name in ('prepared.json','review-request.json','normalization-review.json','phase1-result.json')),'prior normalization attempted')
    body=P.read(binding(selected.run/'review-evidence.json'));rows=P.read(binding(selected.run/'analysis.json'))
    parts=P.K.partitions(body,lambda compact,refs:P.request(selected,compact,refs));refs=[r for group,_ in parts for r in group]
    require(len(refs)==len(set(refs))==242 and set(refs)=={r['reference'] for r in rows if r['clean']},'complete candidate coverage differs')
    return dict(status='SUPPORTED_EXPIRED_UNSENT_PARTITIONS',plan=plan_binding,partitions=len(parts),request_bytes=[P.K.encoded_size(x) for _,x in parts],original_budget=checked(plan['normalization_plan'])['budget'],retained_research=check,snapshot=plan['snapshot'],models_dispatched=0,tracker_modified=False,semantic_budget_reset=False)


def child(intent_binding):
    intent = checked(intent_binding); plan = checked(intent['plan']); dispatcher, env, P, selected, worker = load(plan)
    require(intent['driver'] == binding(__file__) and intent['seconds'] == SECONDS and intent['memory'] == MEMORY, 'guarded helper changed')
    active(plan['workbook']); base = env[0]; configure_campaign(base, worker.campaign); directory = Path(intent_binding['path']).parent
    lease = base.decode(intent['lease']); lease = base._lease(worker, lease, directory)
    args = intent['arguments']; action = intent['action']
    if action == 'prepare':
        selected = replace(selected, run=Path(plan['home']) / 'normalization' / selected.run.name)
        value = P.prepare(selected, worker.campaign, Path(plan['home']) / 'partitions', prior_plan=plan['normalization_plan'])
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
    lease = checked(plan['unresolved'])['lease']
    try:
        from scripts.batch import harbor_lease_takeover as T
        configure_campaign(base,worker.campaign);base._controls(controls);base._dependency(worker,home)
        active(plan['workbook'])
        replaced,receipt=T.replace_expired(takeover_selection(plan,os.environ['HARBOR_FLEET_OWNER']),takeover_directory(plan))
        lease=base.encode(replaced)
        retain(home/'custody.json',dict(prior_tracker=plan['tracker'],takeover=receipt,lease=lease,semantic_budget_reset=False))
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

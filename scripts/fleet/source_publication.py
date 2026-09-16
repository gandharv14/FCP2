"""Typed publication history for completed source-preserving first authoring.

This reader grants neither first admission nor another authoring attempt. The
unchanged owning publisher and independent verifier still decide promotion.
"""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import sys
import time

AUTHOR_HELPER = {
    'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/authoring-resume-20260910/runner/author_workbook.py',
    'sha256': '414a278fe2bc52858788e1253af18d4989f0275869e86fbab6b071a13b241666'}


def preload(plan, *, checked, binding, load, require):
    selected = checked(plan['source_authoring'])
    require(binding(selected['preparer']['path']) == selected['preparer'],
            'source authoring preparer changed')
    require(binding(AUTHOR_HELPER['path']) == AUTHOR_HELPER,
            'source publication author helper changed')
    previous = sys.modules.get('author_workbook')
    require(previous is None or binding(previous.__file__) == AUTHOR_HELPER,
            'foreign cached source publication author helper')
    # Preload exact bytes in the real publication interpreter before the
    # dispatcher executes its normal author_workbook import.
    sys.modules['author_workbook'] = load('author_workbook', AUTHOR_HELPER)
    return load('source_publication_selected_preparer', selected['preparer'])



# This is a data-only reader for the one already-selected cold renewal runtime.
# It does not import its executable controller or permit another renewal.
COLD_ROOT = Path('/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/control/0200-cold-continuation-001')
COLD_PLAN = {'path': str(COLD_ROOT/'plan.json'), 'sha256': 'b81a07ec45012ce50df1d187519099aa8a6db09a2c2b226b088b44cd01d35a05'}
COLD_SOURCE_SHA = 'bf807aa63628a337e6a333764fdac8216e201f2980b42b57a177406051ce23c8'
COLD_PYTHON = '/home/gmahajan_labelbox_com/synthetic-data-fleet/releases/5d3b09ac15928bcb163405f8fd950a84d7ff3d5f56b5c01eb2a47e8f810e9057/venv/bin/python'


def cold_runtime(ready, source_binding, *, checked, binding, require):
    selected = source_binding.get('sha256') == COLD_SOURCE_SHA
    declared = 'runtime_continuation' in ready or 'runtime_sidecars' in ready
    require(selected == declared, 'missing or unrelated source cold runtime')
    if not selected:
        return []
    require(ready['workbook'] == '0200' and ready['runtime_continuation'] == COLD_PLAN,
            'unknown source cold runtime')
    p = checked(COLD_PLAN)
    require(p['source_plan'] == source_binding and ready['job'] == p['authoring_job'],
            'cold runtime detached from selected source or authoring job')
    job = checked(p['authoring_job'])
    require(job['operations'] == p['operations'] and job['config'] == ready['config'],
            'cold runtime changed owning namespace/config')
    for key in ('bootstrap', 'stdlib', 'runtime'):
        require(binding(p[key]['path']) == p[key], 'cold runtime dependency changed')
    operations = Path(p['operations'])
    sidecars = [binding(q) for q in sorted(operations.glob('*/runtime-bootstrap.json'))]
    require(bool(sidecars) and ready['runtime_sidecars'] == sidecars,
            'cold runtime sidecar inventory differs')
    renewals = []
    for q in sorted(operations.glob('*/intent.json')):
        value = checked(binding(q))
        if set(value) == {'job', 'checkpoint', 'seconds', 'driver'}:
            require(value['job'] == p['authoring_job'] and value['driver'] == job['driver'],
                    'cold renewal intent belongs to another job')
            renewals.append(binding(q.parent/'runtime-bootstrap.json'))
    require(renewals == sidecars, 'cold renewal intent/sidecar coverage differs')
    for row in sidecars:
        v = checked(row);directory = Path(row['path']).parent
        intent = binding(directory/'intent.json');value = checked(intent)
        seconds = value['seconds']
        require(type(seconds) is int and 60 <= seconds <= 900,
                'cold renewal guard differs')
        expected = dict(schema_version='fleet-0200-cold-renewal-runtime/v1',
            plan=COLD_PLAN, job=p['authoring_job'], intent=intent, driver=job['driver'],
            bootstrap=p['bootstrap'], stdlib=p['stdlib'],
            original_command=[COLD_PYTHON,'-B',job['driver']['path'],'renew-child',intent['path'],intent['sha256']],
            actual_command=[COLD_PYTHON,'-I','-B',p['bootstrap']['path'],'renew-child',intent['path'],intent['sha256']],
            timeout_seconds=seconds, memory_limit_bytes=8*1024**3,
            task_code_changed=False, model_budget_reset=False, automatic_retry=False)
        require(v == expected, 'cold renewal declared execution differs')
        outcome = checked(binding(directory/'outcome.json'))
        resource_binding = binding(directory/'resource.json')
        result_binding = binding(directory/'result.json')
        require(outcome == dict(status='COMPLETED_OPERATION',resource=resource_binding,
            result=result_binding,automatic_retry=False), 'cold renewal did not complete')
        resource = checked(resource_binding);result = checked(result_binding)
        require(resource['intent'] == intent and resource['returncode'] == 0
                and resource['failure'] is None and resource['log'] == binding(directory/'step.log'),
                'cold renewal guard evidence differs')
        require(result['status'] == 'RETRY_READY', 'cold renewal owning result differs')
    return sidecars


INITIAL_ROOT = Path('/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/control/0036-expired-initial-001')
INITIAL_PLAN = {'path': str(INITIAL_ROOT/'remaining/selected-001/plan.json'), 'sha256': 'def5c1b62be795508783888677e5adb2ba6da1be317d8ed87425159f3b3be001'}
INITIAL_SOURCE_SHA = '61777ebbd945ddeee0ccc903f14651e8085f7f6e0448725126925dd20239f4db'
INITIAL_BOOTSTRAP = {'path': str(INITIAL_ROOT/'remaining/bootstrap.py'), 'sha256': '4e204cd9df26f1eea4eed6702ee021a198a45f0aa33ee384c05677bdf63aed11'}


def initial_runtime(ready, source_binding, *, checked, binding, require):
    """Read the sealed initial takeover/start and declared later cold renewals.

    No continuation code is imported or run. The selected actual takeover was
    validated by the original protocol; all its immutable linked bytes remain
    required here, while the live tracker may advance through ordinary work.
    """
    selected = source_binding.get('sha256') == INITIAL_SOURCE_SHA
    declared = any(k in ready for k in ('initial_continuation', 'initial_runtime_sidecars', 'custody'))
    require(selected == declared, 'missing or unrelated initial custody history')
    if not selected:
        return []
    require(ready['workbook'] == '0036' and ready['initial_continuation'] == INITIAL_PLAN,
            'unknown initial continuation')
    p = checked(INITIAL_PLAN)
    require(p['source_plan'] == source_binding and ready['custody'] == p['custody_completion'],
            'initial custody detached from selected source')
    for key in ('helper', 'runtime', 'stdlib'):
        require(binding(p[key]['path']) == p[key], 'initial continuation dependency changed')
    require(binding(INITIAL_BOOTSTRAP['path']) == INITIAL_BOOTSTRAP,
            'initial cold bootstrap changed')
    custody = checked(p['custody_completion']);takeover = checked(custody['takeover'])
    stopped = checked(takeover['stopped_owner_proof']);before = checked(stopped['before']);after = checked(takeover['after'])
    require(custody['status'] == 'INITIAL_ENVIRONMENT_CUSTODY_REPLACED'
            and custody['plan'] == p['custody_plan'] and custody['replacement_count'] == 1
            and custody['max_replacements'] == 3 and custody['duration_ns'] == 7200*10**9
            and custody['models_dispatched'] == 0 and custody['review_budget_reset'] is False,
            'initial replacement history differs')
    for evidence in stopped['evidence']:
        require(binding(evidence['path']) == evidence, 'initial stopped evidence changed')
    original = checked(p['original_authoring_job'])
    require(stopped['selection']['worker_kind'] == 'environment'
            and stopped['selection']['checkpoint'] == p['environment_checkpoint']
            and stopped['selection']['config'] == original['config']
            and before['revision'] == 19 and after['revision'] == 20
            and len(after['handoffs']) == len(before['handoffs']) + 1,
            'initial replacement detached from original environment')
    event = after['handoffs'][-1]
    expected_after = {**before, 'revision': 20, 'updated_at': event['at'],
        'handoffs': [*before['handoffs'], event],
        'leases': {**before['leases'], 'task_agent': event['after']}}
    require(after == expected_after and event['operation'] == 'replace_expired'
            and event['before'] == before['leases']['task_agent']
            and event['stopped_owner_proof'] == takeover['stopped_owner_proof']
            and event['after']['owner'] == 'hhu-server-3:slot-1:0036-initial-expired-continuation-001'
            and event['after']['expires_at'] - event['at'] == 7200*10**9,
            'initial replacement changed nonlease history or duration')
    require(set(takeover['lease']) == {'owner','token','revision','sha256'}, 'initial lease fields differ')
    encoded_lease = dict(type='Lease', value={k:dict(type='scalar',value=v) for k,v in takeover['lease'].items()})
    home = Path(p['home']);operations = Path(p['operations'])
    environment = checked(p['environment_checkpoint'])
    projected_binding = binding(home/'renewed-environment.json')
    require(checked(projected_binding) == {**environment, 'lease': encoded_lease,
        'evidence': [*environment['evidence'], p['custody_completion'], custody['takeover'], p['initial_checkpoint']],
        'initial_custody_projection': INITIAL_PLAN}, 'initial environment lease projection differs')
    old_initial = checked(p['initial_checkpoint'])
    require(old_initial['status'] == 'INSTRUCTION_READY' and old_initial['checkpoint'] is None,
            'initial continuation consumed prior instruction work')
    start = home/'instruction-start';intent = binding(start/'intent.json');value = checked(intent)
    require(value['action'] == 'start' and value['arguments'] == dict(
        environment_descriptor=environment['descriptor'], checkpoint=projected_binding,
        worker_run=str(Path(checked(original['config'])['run_root'])/'instruction'))
        and value['models'] is value['commands'] is value['containers'] is False
        and value['timeout_seconds'] == 3300 and value['memory_limit_bytes'] == 8*1024**3,
        'initial original start arguments or guard differ')
    for key in ('code', 'base_code', 'executable'):
        require(binding(value[key]['path']) == value[key], 'original start implementation changed')
    resource_binding = binding(start/'resource.json');result_binding = binding(start/'result.json')
    outcome_binding = binding(start/'outcome.json');outcome = checked(outcome_binding)
    resource = checked(resource_binding);result = checked(result_binding)
    require(outcome == dict(status='COMPLETED_OPERATION',result=result_binding,
        resource=resource_binding,publication_performed=False)
        and resource['intent'] == intent and resource['returncode'] == 0
        and resource['failure'] is None and resource['log'] == binding(start/'step.log')
        and result['status'] == 'INSTRUCTION_READY' and result['checkpoint'] is None
        and result['selection'] == environment['selection'] and result['lease'] == encoded_lease,
        'original initial start did not complete exactly')
    job = checked(ready['job'])
    require(job == {**original, 'outcome': outcome_binding, 'operations': str(operations)}
            and ready['config'] == original['config'], 'initial authoring job differs from original remaining work')
    sidecars = [binding(q) for q in sorted(operations.glob('*/runtime-bootstrap.json'))]
    require(ready['initial_runtime_sidecars'] == sidecars, 'initial cold sidecar inventory differs')
    renewals = []
    for q in sorted(operations.glob('*/intent.json')):
        v = checked(binding(q))
        if set(v) == {'job', 'checkpoint', 'seconds', 'driver'}:
            require(v['job'] == ready['job'] and v['driver'] == job['driver'], 'foreign initial cold renewal intent')
            renewals.append(binding(q.parent/'runtime-bootstrap.json'))
    require(renewals == sidecars, 'initial cold intent/sidecar coverage differs')
    for row in sidecars:
        directory = Path(row['path']).parent;intent = binding(directory/'intent.json');v = checked(intent);seconds = v['seconds']
        require(type(seconds) is int and 60 <= seconds <= 900, 'initial cold guard differs')
        expected = dict(schema_version='fleet-0036-cold-renewal-runtime/v1',plan=INITIAL_PLAN,
            job=ready['job'],intent=intent,driver=job['driver'],bootstrap=INITIAL_BOOTSTRAP,stdlib=p['stdlib'],
            original_command=[COLD_PYTHON,'-B',job['driver']['path'],'renew-child',intent['path'],intent['sha256']],
            actual_command=[COLD_PYTHON,'-I','-B',INITIAL_BOOTSTRAP['path'],'renew-child',intent['path'],intent['sha256']],
            timeout_seconds=seconds,memory_limit_bytes=8*1024**3,task_code_changed=False,model_budget_reset=False,automatic_retry=False)
        require(checked(row) == expected, 'initial cold declared execution differs')
        res = binding(directory/'resource.json');out = binding(directory/'result.json')
        require(checked(binding(directory/'outcome.json')) == dict(status='COMPLETED_OPERATION',resource=res,result=out,automatic_retry=False),
                'initial cold renewal did not complete')
        resource = checked(res)
        require(resource['intent'] == intent and resource['returncode'] == 0 and resource['failure'] is None
                and resource['log'] == binding(directory/'step.log') and checked(out)['status'] == 'RETRY_READY',
                'initial cold renewal result differs')
    return [p['custody_completion'], custody['takeover'], projected_binding, outcome_binding, *sidecars]


def history_binding(plan, *, checked, binding, require):
    require('history' not in plan and 'continuation_history' not in plan
            and 'source_continuation' not in plan, 'mixed publication histories')
    row = plan['source_authoring']
    selected = checked(row)
    require(selected['schema_version'] == 'fleet-source-to-first-authoring-plan/v1'
            and selected['workbook'] == plan['workbook']
            and plan['workbook'] in {'0200', '0013', '0036', '0064'},
            'selected source authoring identity differs')
    snapshot = checked(selected['snapshot'])
    root = Path(plan['code_root'])
    require(root == Path(snapshot['root']) and root.name == snapshot['fingerprint'],
            'source authoring code root differs')
    manifest = binding(root/'.pipeline-code.json')
    require('.pipeline-code.json' not in snapshot['files']
            and checked(manifest) == snapshot['files']
            and hashlib.sha256(json.dumps(snapshot['files'], sort_keys=True,
                separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
                == snapshot['fingerprint'], 'canonical source manifest differs')
    inventory = {**snapshot['files'], '.pipeline-code.json': manifest['sha256']}
    actual = {str(p.relative_to(root)): binding(p)['sha256']
              for p in root.rglob('*') if p.is_file()}
    require(actual == inventory, 'source authoring code inventory changed')
    certificate = checked(selected['history'])
    require(certificate['workbook'] == plan['workbook']
            and certificate['schema_version'] in {
                'fleet-source-to-first-authoring-history/v1',
                'fleet-source-to-first-authoring-history/v2'},
            'source authoring history differs')
    ready = checked(plan['ready'])
    require(ready['status'] == 'PUBLICATION_READY'
            and ready['publication_performed'] is False
            and ready['automatic_retry'] is False
            and ready['workbook'] == plan['workbook']
            and ready['plan'] == row
            and ready['source_history'] == selected['history'],
            'completed source authoring boundary required')
    job = checked(ready['job'])
    config = checked(ready['config'])
    owning_code = {'root': snapshot['root'], 'files': [
        {'relative': name, 'sha256': inventory[name]} for name in sorted(inventory)]}
    require(job['config'] == ready['config'] and job['driver'] == selected['dispatcher']
            and checked(config['code']) == owning_code,
            'owning configuration detached from source authoring')
    cold_runtime(ready, row, checked=checked, binding=binding, require=require)
    initial_runtime(ready, row, checked=checked, binding=binding, require=require)
    return selected['history']



CAPACITY_POLICY = None  # The builder replaces this with the exact frozen copy.
CAPACITY_POLICY_SHA = '17d52d922dc4141f33e9627ece86762e8ebb664233278956a9485b8a2b52b3a4'


def capacity_policy():
    row = CAPACITY_POLICY or dict(path=str(Path(__file__).with_name('capacity_policy.py')),
                                  sha256=CAPACITY_POLICY_SHA)
    raw = Path(row['path']).read_bytes()
    if row['sha256'] != CAPACITY_POLICY_SHA or hashlib.sha256(raw).hexdigest() != row['sha256']:
        raise ValueError('source publication concurrency policy changed')
    spec = importlib.util.spec_from_file_location('source_publication_capacity', row['path'])
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def validate_status(status, auth, plan_binding, plan, source_sha256, *, now, require):
    policy = capacity_policy()
    limit = policy.validate_limits(status)
    policy.validate_occupancy(status)
    require(abs(status['at']-now) <= 60, 'publication observation differs')
    rows = [w for w in status['workbooks'] if w['workbook'] == plan['workbook']]
    require(len(rows) == 1, 'unique publication workbook required')
    w = rows[0]
    require(w['state'] == 'active' and w['owner'] == auth['owner']
            and w['fence'] == auth['fence'] and w['expires'] > now
            and w['source_sha256'] == source_sha256
            and w['version'] == Path(plan['code_root']).name,
            'publication source/version/owner/fence/expiry differs')
    active = []
    for op in status['operations']:
        require(op['state'] in {'active','hold','confirmed','abandoned'}
                and op['kind'] in {'model','publication'},
                'unknown or uncertain global operation')
        if op['state'] == 'hold':
            owners = [x for x in status['workbooks'] if x['workbook'] == op['workbook']]
            require(op['kind'] == 'model' and op['workbook'] != plan['workbook'] and len(owners) == 1
                    and owners[0]['state'] == 'hold' and owners[0]['owner'] == op.get('owner')
                    and owners[0]['fence'] == op.get('fence'), 'uncertain publication, target or unknown owner')
        if op['state'] == 'active': active.append(op)
    # Publication and model limits are distinct. A known other active model
    # may coexist, but another publication or this task's model cannot.
    publications = [op for op in active if op['kind'] == 'publication']
    models = [op for op in active if op['kind'] == 'model']
    require(len(publications) == 1 and (limit is None or len(models) <= limit),
            'global operation limits differ')
    for op in models:
        owners = [x for x in status['workbooks'] if x['workbook'] == op['workbook']]
        require(op['workbook'] != plan['workbook'] and len(owners) == 1
                and owners[0]['state'] == 'active' and owners[0]['owner'] == op['owner']
                and owners[0]['fence'] == op['fence'] and owners[0]['expires'] > now
                and op['expires'] > now, 'unknown or stale concurrent model owner')
    op = publications[0]
    identity = hashlib.sha256(json.dumps(dict(workbook=plan['workbook'],
        request_sha256=plan_binding['sha256']), sort_keys=True,
        separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    require(op['workbook'] == plan['workbook'] and op['owner'] == auth['owner']
            and op['fence'] == auth['fence'] and op['expires'] > now
            and op['identity'] == identity, 'publication operation detached from selected plan')
    return w


def live(plan_binding, plan, *, checked, require):
    require(os.environ.get('HARBOR_FLEET_PUBLICATION_ONLY') == '1'
            and not os.environ.get('HARBOR_FLEET_MODEL_JOURNAL')
            and not os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY'),
            'live publication-only environment required')
    require(os.environ['HARBOR_FLEET_WORKBOOK'] == plan['workbook'], 'publication workbook differs')
    auth = dict(workbook=plan['workbook'], owner=os.environ['HARBOR_FLEET_OWNER'],
                token=os.environ['HARBOR_FLEET_CLAIM_TOKEN'], fence=int(os.environ['HARBOR_FLEET_FENCE']))
    from scripts.fleet.coordinator import Client
    client = Client(os.environ['HARBOR_FLEET_URL'], os.environ['HARBOR_FLEET_TOKEN_FILE'])
    source = checked(checked(plan['source_authoring'])['history'])['source']['sha256']
    validate_status(client.call('status'), auth, plan_binding, plan, source,
                    now=time.time(), require=require)
    # The unchanged owning source-history survey subsequently authenticates
    # this same claim by heartbeat. No model context/journal is constructed.


def patch(source):
    """Apply closed, reviewable additions to a selected immutable publisher."""
    def replace(old, new, count=1):
        nonlocal source
        if source.count(old) != count:
            raise ValueError('publication source anchor differs')
        source = source.replace(old, new)
    replace('    """Publication reuses the exact central survey selected for fresh authoring."""',
            '    """Publication reuses the exact central survey selected for fresh authoring."""\n'
            '    if "source_authoring" in plan:\n'
            '        return source_publication.history_binding(plan, checked=checked, binding=binding, require=require)')
    old = "prefix = 'HARBOR_FLEET_CONTINUATION_HISTORY_' if 'continuation_history' in plan else 'HARBOR_FLEET_HISTORY_'"
    replace(old, "prefix = ('HARBOR_FLEET_SOURCE_HISTORY_' if 'source_authoring' in plan else "
            "'HARBOR_FLEET_CONTINUATION_HISTORY_' if 'continuation_history' in plan else 'HARBOR_FLEET_HISTORY_')", 2)
    replace("    if 'continuation_history' in plan:\n        from scripts.fleet.continuation_history import projected_survey",
            "    if 'source_authoring' in plan:\n        source_publication.live(plan_binding, plan, checked=checked, require=require)\n        from scripts.fleet.source_authoring_history import survey as projected_survey\n"
            "    elif 'continuation_history' in plan:\n        from scripts.fleet.continuation_history import projected_survey")
    replace("    projected_survey(tuple(map(Path, certificate['roots'])), set(certificate['known_ids']))",
            "    projected_survey(tuple(map(Path, certificate['history_roots'] if 'source_authoring' in plan else certificate['roots'])), set(certificate['known_ids']))")
    replace('    driver = load("fleet_publication_dispatcher", job["driver"])',
            '    if "source_authoring" in plan:\n'
            '        source_publication.preload(plan, checked=checked, binding=binding, load=load, require=require)\n'
            '    driver = load("fleet_publication_dispatcher", job["driver"])')
    return source

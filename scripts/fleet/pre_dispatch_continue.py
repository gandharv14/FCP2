"""Continue an admitted task after a proved failure before model dispatch.

The failed guard stays immutable. The original dispatcher gets the original
completed admission and a distinct operation directory; no budget is reset.
"""
import argparse
import ast
import hashlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import sys

SCHEMA = 'fleet-pre-dispatch-continuation/v1'
FIELDS = {'schema_version', 'helper', 'workbook', 'source', 'original_worker_job',
          'original_job', 'outer', 'failed', 'tracker', 'custody', 'history_reader',
          'owner', 'fence', 'home', 'operations'}


def require(ok, message):
    if not ok: raise ValueError(message)


def binding(path):
    p = Path(path)
    require(p.is_absolute() and p.is_file() and not any(x.is_symlink() for x in (p, *p.parents)), 'regular bound file required')
    return dict(path=str(p), sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def read(row):
    require(set(row) == {'path', 'sha256'} and binding(row['path']) == row, 'retained pre-dispatch evidence changed')
    return json.loads(Path(row['path']).read_bytes())


def module(row, name):
    require(binding(row['path']) == row, 'selected implementation changed')
    spec = importlib.util.spec_from_file_location(name, row['path'])
    m = importlib.util.module_from_spec(spec); sys.modules[name] = m; spec.loader.exec_module(m)
    return m


def failed_guard(p, job, checkpoint, value, base):
    failed = read(p['failed']); guard = read(failed['resource']); intent = read(guard['intent'])
    root = Path(job['operations']); directory = root / '000'
    require(p['failed'] == binding(directory / 'outcome.json')
            and failed == dict(status='RECONCILIATION_REQUIRED', resource=failed['resource'],
                              automatic_retry=False, terminal_verdict=None, source_invalidity_claimed=False)
            and guard['returncode'] == 1 and guard['failure'] is None
            and guard['log'] == binding(directory / 'step.log')
            and intent['action'] == 'step'
            and intent['arguments'] == dict(descriptor=value['descriptor'], checkpoint=checkpoint, captures=None)
            and intent['code'] == binding(base.__file__) and intent['dispatch'] is True,
            'different or uncertain original pre-dispatch guard')
    log = Path(guard['log']['path']).read_text()
    require(log.rstrip().endswith('scripts.batch.harbor_admission.AdmissionError: configured model credential unavailable')
            and "credential=read_env_key(controls.env_file)" in log,
            'failure did not stop at the original credential read')
    require({x.name for x in directory.iterdir()} == {'intent.json', 'dependency.json', 'step.log', 'resource.json', 'outcome.json'}
            and {x.name for x in root.iterdir()} == {'job.json', '000', 'hold.json'}
            and read(binding(root / 'job.json')) == job
            and read(binding(root / 'hold.json'))['outcome'] == p['failed'],
            'failed dispatcher advanced or acquired extra effects')


def context(row, *, probe=False):
    p = read(row)
    if 'setup_dependency' in p:return setup_dependency_context(row, probe=probe)
    if p.get('schema_version')=='fleet-completed-pre-dispatch-history/v1':return completed_history_context(row)
    if 'renewal_setup' in p:return renewal_setup_context(row)
    require(set(p) == FIELDS and p['schema_version'] == SCHEMA and p['helper'] == binding(__file__), 'closed pre-dispatch continuation required')
    old = read(p['original_worker_job']); job = read(p['original_job']); outer = read(p['outer']); intent = read(outer['intent'])
    fresh = read(old['inputs'][0]); root = Path(fresh['code_root']); sys.path.insert(0, str(root))
    require(old['workbook'] == fresh['workbook'] == p['workbook'] and old['source'] == p['source']
            and intent['job'] == p['original_worker_job'] and outer['status'] == 'RECONCILIATION_REQUIRED'
            and outer['returncode'] == 1 and outer['failure_type'] is None
            and outer['automatic_retry'] is False and outer['publication_performed'] is False
            and p['original_job'] == binding(Path(fresh['home']) / 'authoring-job.json'), 'original stopped author differs')
    H = module(p['history_reader'], 'pre_dispatch_original_history')
    H.select_history(p, old, probe=probe)
    D = module(job['driver'], 'pre_dispatch_original_dispatcher'); env = D.setup(job); base = env[0]
    checkpoint, value = D.successful(job['outcome'], env)
    require(value['status'] == 'ADMITTED' and job['publish'] is False
            and all(job[k] is True for k in ('models', 'commands', 'containers')), 'original admission and capabilities required')
    failed_guard(p, job, checkpoint, value, base)
    worker, lease, controls = base.selected(value['descriptor'])
    base._prior(checkpoint, value['descriptor']); base.worker._active(worker, lease)
    require(p['tracker'] == binding(worker.bootstrap.policy.tracker_root / (p['workbook'] + '.json'))
            and p['tracker']['sha256'] == lease.sha256, 'task changed after admitted pre-dispatch failure')
    require({x.name for x in worker.run.iterdir()} == {'selection.json'}
            and not worker.campaign.root.exists(), 'task or model operation exists; reconcile separately')
    custody = read(p['custody']); require(custody['live'] == [], 'stopped custody contains active authors')
    rows = {x['path']: x for x in custody['tasks'][0]['files']}
    for r in (p['outer'], outer['intent'], p['original_job'], job['config'], p['failed'], p['tracker']):
        require(r['path'] in rows and rows[r['path']]['sha256'] == r['sha256'], 'missing original stopped custody')
    from xl_task_build import read_env_key
    require(bool(read_env_key(controls.env_file)), 'credential reader still unavailable')
    require(not Path(p['home']).exists() and Path(p['home']).parent == Path(env[5]['run_root']) / 'continuations', 'continuation output consumed or outside original task')
    D.operation_namespace(env[5], p['operations'])
    return p, D, env, job, checkpoint


def dependency_dispatcher_source(original, dependency):
    """Supply one declared pure inventory helper without changing task code."""
    require(binding(original['path']) == original and binding(dependency['path']) == dependency,
            'dispatcher dependency bytes changed')
    source = Path(original['path']).read_text()
    anchor = '    from scripts.fleet.snapshot_inventory import equivalent\n'
    require(source.count(anchor) == 1, 'original inventory import boundary differs')
    selected = (
        '    dependency = ' + repr(dependency) + '\n'
        "    require(binding(dependency['path']) == dependency, 'dispatcher inventory dependency changed')\n"
        "    spec = importlib.util.spec_from_file_location('scripts.fleet.snapshot_inventory', dependency['path'])\n"
        "    inventory_module = importlib.util.module_from_spec(spec)\n"
        "    spec.loader.exec_module(inventory_module)\n"
        "    sys.modules['scripts.fleet.snapshot_inventory'] = inventory_module\n"
    )
    return source.replace(anchor, selected + anchor)


def setup_dependency_guard(p, old, job, outer, fresh):
    """Prove setup failed after admission and before any dispatcher operation."""
    result = read(p['failed'])
    require(outer['result'] == p['failed']
            and p['failed'] == binding(Path(fresh['home']) / 'result.json')
            and result == dict(status='RECONCILIATION_REQUIRED', workbook=p['workbook'],
                              plan=old['inputs'][0], failure_type='ModuleNotFoundError',
                              publication_performed=False, automatic_retry=False),
            'different or uncertain initial setup result')
    log = Path(outer['log']['path'])
    require(binding(log) == outer['log']
            and log.read_text().rstrip().endswith("ModuleNotFoundError: No module named 'scripts.fleet.snapshot_inventory'")
            and 'dispatcher.successful(outcome, dispatcher.setup(job)); dispatcher.run(job_binding)' in log.read_text()
            and 'from scripts.fleet.snapshot_inventory import equivalent' in log.read_text()
            and not Path(job['operations']).exists(),
            'setup advanced or failure differs from the missing inventory dependency')


def setup_dependency_context(row, *, probe=False):
    p = read(row)
    require(set(p) == FIELDS | {'setup_dependency'} and p['schema_version'] == SCHEMA
            and p['helper'] == binding(__file__)
            and set(p['setup_dependency']) == {'dispatcher', 'dependency'},
            'closed initial dependency continuation required')
    old = read(p['original_worker_job']); job = read(p['original_job'])
    outer = read(p['outer']); intent = read(outer['intent']); fresh = read(old['inputs'][0])
    root = Path(fresh['code_root']); sys.path.insert(0, str(root))
    require(old['workbook'] == fresh['workbook'] == p['workbook'] and old['source'] == p['source']
            and old['version'] == root.name and job['driver'] == fresh['dispatcher']
            and intent['job'] == p['original_worker_job'] and p['outer'] == binding(Path(old['run']) / 'outcome.json')
            and outer['status'] == 'RECONCILIATION_REQUIRED' and outer['returncode'] == 1
            and outer['failure_type'] is None and outer['automatic_retry'] is False
            and outer['publication_performed'] is False
            and p['original_job'] == binding(Path(fresh['home']) / 'authoring-job.json'),
            'different stopped original author')
    claim = read(binding(Path(old['run']) / 'claim.json'))
    require(claim['claim_id'] == intent['claim_id'] and claim['owner'] == intent['owner']
            and claim['workbook'] == p['workbook'] and claim['source_sha256'] == p['source']['sha256']
            and claim['version'] == old['version'] and p['fence'] == claim['fence'] + 1
            and p['owner'] != claim['owner'], 'continuation detached from original claim')
    setup_dependency_guard(p, old, job, outer, fresh)
    H = module(p['history_reader'], 'initial_dependency_original_history'); H.select_history(p, old, probe=probe)
    selected = p['setup_dependency']; require(binding(selected['dispatcher']['path']) == selected['dispatcher'],
                                            'selected dispatcher changed')
    expected = dependency_dispatcher_source(job['driver'], selected['dependency'])
    require(ast.dump(ast.parse(expected)) == ast.dump(ast.parse(Path(selected['dispatcher']['path']).read_text())),
            'dispatcher changed beyond declared dependency import')
    D = module(selected['dispatcher'], 'initial_dependency_dispatcher')
    current = dict(job, driver=selected['dispatcher'], operations=p['operations'])
    env = D.setup(current); base = env[0]; checkpoint, value = D.successful(job['outcome'], env)
    require(value['status'] == 'ADMITTED' and job['publish'] is False
            and all(job[k] is True for k in ('models', 'commands', 'containers')), 'original admission capabilities changed')
    worker, lease, controls = base.selected(value['descriptor']); base._prior(checkpoint, value['descriptor'])
    base.worker._active(worker, lease)
    require(p['tracker'] == binding(worker.bootstrap.policy.tracker_root / (p['workbook'] + '.json'))
            and p['tracker']['sha256'] == lease.sha256, 'admitted task custody changed')
    require({x.name for x in worker.run.iterdir()} == {'selection.json'} and not worker.campaign.root.exists(),
            'task or model effects exist; reconcile separately')
    custody = read(p['custody']); require(custody['processes'] == [], 'original author is still active')
    rows = {x['native']['path']: x['native'] for x in custody['files']}
    for item in (p['outer'], outer['intent'], outer['log'], p['original_job'], job['config'], p['failed'], p['tracker']):
        require(rows.get(item['path']) == item, 'missing stopped native custody evidence')
    from xl_task_build import read_env_key
    require(bool(read_env_key(controls.env_file)), 'original credential reader unavailable')
    require(not Path(p['home']).exists() and Path(p['home']).parent == Path(env[5]['run_root']) / 'continuations',
            'continuation output consumed or outside task')
    D.operation_namespace(env[5], p['operations'])
    return p, D, env, current, checkpoint


def renewal_dispatcher(original, selected):
    """The sole code change selects the authenticated root before its import."""
    require(binding(original['path'])==original and binding(selected['path'])==selected,
            'renewal dispatcher bytes changed')
    source=Path(original['path']).read_text()
    old="    from scripts.fleet.snapshot_inventory import equivalent\n    snapshot=checked(config['code']);root=Path(snapshot['root']);require(equivalent(code_snapshot(root),snapshot),'frozen code changed')"
    new="    snapshot=checked(config['code']);root=Path(snapshot['root']);sys.path.insert(0,str(root))\n    from scripts.fleet.snapshot_inventory import equivalent\n    require(equivalent(code_snapshot(root),snapshot),'frozen code changed')"
    require(source.count(old)==1 and ast.dump(ast.parse(source.replace(old,new)))==
            ast.dump(ast.parse(Path(selected['path']).read_text())),
            'dispatcher changed beyond the isolated setup import order')


def renewal_setup_guard(p, job, D, env):
    """Retain every completed predecessor and the exact unused renewal child."""
    root=Path(job['operations']);dirs=sorted(x for x in root.iterdir() if x.is_dir())
    require(1<len(dirs)<=101 and [x.name for x in dirs]==['%03d'%i for i in range(len(dirs))]
            and {x.name for x in root.iterdir()}=={'job.json','hold.json',*[x.name for x in dirs]}
            and read(binding(root/'job.json'))==job
            and read(binding(root/'hold.json'))['outcome']==p['failed'],
            'original completed dispatcher prefix changed')
    require(p['failed']['path']==str(dirs[-1]/'outcome.json'),
            'later dispatcher child requires reconciliation')
    previous,_=D.successful(job['outcome'],env)
    for directory in dirs[:-1]:
        outcome=binding(directory/'outcome.json');cp,value=D.successful(outcome,env)
        intent=read(read(read(outcome)['resource'])['intent'])
        if intent.get('action')=='step':
            require(intent['arguments']['checkpoint']==previous,'completed prefix skipped its predecessor')
        previous=cp
    require(outcome==p['renewal_setup']['completed'] and value['status']=='LEASE_RENEWAL_REQUIRED',
            'actual last renewal-required checkpoint is missing')
    failed=read(p['failed']);guard=read(failed['resource']);intent=read(guard['intent']);directory=dirs[-1]
    require(p['failed']==binding(directory/'outcome.json')
            and failed==dict(status='RECONCILIATION_REQUIRED',resource=failed['resource'],result=None,automatic_retry=False)
            and guard['returncode']==1 and guard['failure'] is None
            and guard['intent']==binding(directory/'intent.json') and guard['log']==binding(directory/'step.log')
            and intent==dict(job=p['original_job'],checkpoint=previous,seconds=intent.get('seconds'),driver=job['driver'])
            and type(intent['seconds']) is int and 60<=intent['seconds']<=900
            and {x.name for x in directory.iterdir()}=={'intent.json','step.log','resource.json','outcome.json'},
            'renewal child has different failure or possible effects')
    log=Path(guard['log']['path']).read_text()
    require(log.rstrip().endswith("ModuleNotFoundError: No module named 'scripts'")
            and 'in renewal_child' in log and 'in setup' in log
            and 'from scripts.fleet.snapshot_inventory import equivalent' in log,
            'different pre-renewal failure requires reconciliation')
    return previous,value


def renewal_setup_context(row):
    p=read(row)
    require(set(p)==(FIELDS-{'history_reader'})|{'renewal_setup'} and p['schema_version']==SCHEMA
            and p['helper']==binding(__file__) and set(p['renewal_setup'])=={'dispatcher','completed'},
            'closed pre-renewal setup continuation required')
    old=read(p['original_worker_job']);job=read(p['original_job']);outer=read(p['outer']);intent=read(outer['intent'])
    fresh=read(old['inputs'][0]);root=Path(fresh['code_root'])
    require(old['workbook']==fresh['workbook']==p['workbook'] and old['source']==p['source']
            and old['version']==root.name and job['driver']==fresh['dispatcher']
            and intent['job']==p['original_worker_job'] and p['outer']==binding(Path(old['run'])/'outcome.json')
            and outer['status']=='RECONCILIATION_REQUIRED' and outer['returncode']==1 and outer['failure_type'] is None
            and outer['automatic_retry'] is False and outer['publication_performed'] is False
            and p['original_job']==binding(Path(fresh['home'])/'authoring-job.json'),
            'different stopped original author')
    claim=read(binding(Path(old['run'])/'claim.json'))
    require(claim['claim_id']==intent['claim_id'] and claim['owner']==intent['owner']
            and claim['workbook']==p['workbook'] and claim['source_sha256']==p['source']['sha256']
            and claim['version']==old['version'] and p['fence']==claim['fence']+1 and p['owner']!=claim['owner'],
            'new continuation claim detached from retained original')
    renewal_dispatcher(job['driver'],p['renewal_setup']['dispatcher'])
    D=module(p['renewal_setup']['dispatcher'],'pre_renewal_selected_dispatcher')
    selected=dict(job,driver=p['renewal_setup']['dispatcher'],outcome=p['renewal_setup']['completed'],operations=p['operations'])
    env=D.setup(selected);require(env[6]==root and job['publish'] is False
            and all(job[k] is True for k in ('models','commands','containers')),'original code and capabilities changed')
    checkpoint,value=renewal_setup_guard(p,job,D,env)
    held,worker,recipe,selection,lease=D.renewal_context(env,checkpoint)
    require(held==value and p['tracker']==binding(worker.context.tracker_root/(p['workbook']+'.json'))
            and p['tracker']['sha256']==lease.sha256,'current renewal custody changed')
    custody=read(p['custody']);require(custody['processes']==[],'original author is not stopped')
    rows={x['native']['path']:x['native'] for x in custody['files']}
    for item in (p['outer'],outer['intent'],p['original_job'],p['failed'],p['renewal_setup']['completed']):
        require(rows.get(item['path'])==item,'stopped custody omitted original evidence')
    require(not Path(p['home']).exists() and Path(p['home']).parent==Path(env[5]['run_root'])/'continuations',
            'continuation home consumed or outside task')
    D.operation_namespace(env[5],p['operations'])
    return p,D,env,selected,checkpoint


HISTORY_FIELDS={'schema_version','helper','workbook','source','previous','original_worker_job',
    'original_job','outer','failed','completed','last_step','dispatcher'}

def completed_history_context(row):
    """Read the source and every completed step after a credential-read repair.

    Native custody may have expired. Only a separate authenticated takeover may
    authorize another step; this reader neither replaces a lease nor runs work.
    """
    p=read(row);require(set(p)==HISTORY_FIELDS and p['helper']==binding(__file__)
        and p['schema_version']=='fleet-completed-pre-dispatch-history/v1','closed completed pre-dispatch history required')
    q=read(p['previous']);require(set(q)==FIELDS and q['schema_version']==SCHEMA
        and q['workbook']==p['workbook']and q['source']==p['source'],'original credential continuation required')
    old=read(q['original_worker_job']);fresh=read(old['inputs'][0]);original=read(q['original_job'])
    root=Path(fresh['code_root']);sys.path.insert(0,str(root))
    H=module(q['history_reader'],'completed_pre_dispatch_source_history');H.select_history(q,old,probe=True)
    require(binding(q['helper']['path'])==q['helper'],'original credential continuation code changed')
    prior=module(q['helper'],'completed_pre_dispatch_original')
    # Retain the original credential-read failure independently of later work.
    D0=module(original['driver'],'completed_pre_dispatch_original_dispatcher');original_env=D0.setup(original)
    admitted,admission=D0.successful(original['outcome'],original_env)
    require(admission['status']=='ADMITTED','original task was not admitted')
    prior.failed_guard(q,original,admitted,admission,original_env[0])
    require(read(binding(Path(q['home'])/'intent.json'))==dict(plan=p['previous'],failed=q['failed'],retained_admission=admitted,automatic_retry=False),
        'original credential continuation intent changed')
    job=read(p['original_job']);require(p['original_job']==binding(Path(q['home'])/'authoring-job.json')
        and job==dict(original,operations=q['operations']),'completed author changed original job')
    outer=read(p['outer']);wi=read(outer['intent']);wj=read(p['original_worker_job']);claim=read(binding(Path(wj['run'])/'claim.json'))
    require(wi['job']==p['original_worker_job']and wj['driver']==q['helper']and wj['inputs'][0]==p['previous']
        and wj['source']==p['source']and wj['workbook']==p['workbook']and wj['version']==root.name
        and p['outer']==binding(Path(wj['run'])/'outcome.json')
        and wi['owner']==claim['owner']==q['owner']and wi['claim_id']==claim['claim_id']and claim['fence']==q['fence']
        and claim['source_sha256']==p['source']['sha256']and claim['workbook']==p['workbook']and claim['version']==root.name
        and outer['status']=='RECONCILIATION_REQUIRED'and outer['returncode']==1 and outer['failure_type']is None
        and outer.get('result')is None and outer['automatic_retry']is False and outer['publication_performed']is False,
        'completed pre-dispatch worker identity changed')
    renewal_dispatcher(job['driver'],p['dispatcher']);D=module(p['dispatcher'],'completed_pre_dispatch_selected_dispatcher')
    env=D.setup(dict(job,driver=p['dispatcher']))
    require(env[5:]==original_env[5:],'cold setup changed original configuration or controls')
    held,value=renewal_setup_guard(dict(p,renewal_setup={'completed':p['completed']}),job,D,env)
    cp,result=D.successful(p['last_step'],env)
    require(value['evidence']==[cp]and result['status']=='STEP_COMPLETE'
        and all(value[k]==result[k]for k in ('checkpoint','selection','lease','descriptor','next_step')),
        'renewal hold detached from the actual completed instruction')
    return p,D,env,dict(job,driver=p['dispatcher']),cp


def run(row):
    p, D, env, job, checkpoint = context(row)
    require(p['schema_version']==SCHEMA,'completed history cannot run without an explicit current handoff')
    from scripts.fleet.coordinator import Client
    require(os.environ['HARBOR_FLEET_OWNER'] == p['owner'] and os.environ['HARBOR_FLEET_WORKBOOK'] == p['workbook']
            and int(os.environ['HARBOR_FLEET_FENCE']) == p['fence'], 'exclusive continuation claim required')
    current = Client(os.environ['HARBOR_FLEET_URL'], os.environ['HARBOR_FLEET_TOKEN_FILE']).call('heartbeat',
        workbook=p['workbook'], owner=p['owner'], fence=p['fence'], token=os.environ['HARBOR_FLEET_CLAIM_TOKEN'], lease_seconds=180)
    require(current['source_sha256'] == p['source']['sha256'], 'current source differs')
    from scripts.fleet.migration import retain
    home = Path(p['home']); home.mkdir(parents=True)
    retain(home / 'intent.json', dict(plan=row, failed=p['failed'], retained_admission=checkpoint, automatic_retry=False))
    selected = retain(home / 'authoring-job.json', dict(job, operations=p['operations']))
    D.run(selected)
    hold = read(binding(Path(p['operations']) / 'hold.json')); cp, value = D.successful(hold['outcome'], env)
    return retain(home / 'result.json', dict(status=value['status'], workbook=p['workbook'], plan=row, job=selected,
        config=job['config'], outcome=hold['outcome'], checkpoint=cp, publication_performed=False, automatic_retry=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('action', choices=['run', 'probe'])
    parser.add_argument('--plan', required=True); parser.add_argument('--plan-sha256', '--sha256', dest='plan_sha256', required=True)
    args = parser.parse_args(); row = dict(path=args.plan, sha256=args.plan_sha256)
    if args.action == 'run': result = run(row)
    else:
        p, D, env, job, cp = context(row, probe=True)
        result = dict(status='PASS', models_dispatched=0, result=dict(plan=row, workbook=p['workbook'], checkpoint=cp))
    print(json.dumps(result))

"""Declared cold child and original dispatcher runner for 0200 source cards.

This module is external to the retained task snapshot. Each child checks the
root-selected execution plan, immutable adapter inventory and both command
vectors before invoking the unchanged owning campaign child.
"""
from pathlib import Path
from types import FunctionType, SimpleNamespace
import argparse
import hashlib
import importlib.util
import json
import os
import sys

SCHEMA = 'fleet-0200-source-card-execution/v1'


def require(value, message):
    if not value: raise ValueError(message)


def binding(path):
    p = Path(path)
    require(p.is_absolute() and not any(x.is_symlink() for x in (p, *p.parents)), 'exact execution path required')
    return {'path': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}


def checked(row):
    require(type(row) is dict and set(row) == {'path', 'sha256'} and binding(row['path']) == row,
            'source-card execution evidence changed')
    return json.loads(Path(row['path']).read_bytes())


def load(plan_binding, *, probe):
    p = checked(plan_binding)
    require(set(p) == {'schema_version', 'runtime', 'entry', 'runner', 'owner', 'authorization_directory',
                      'operations', 'home'} and p['schema_version'] == SCHEMA,
            'closed source-card execution plan required')
    require(binding(__file__) == p['entry'], 'unselected source-card entry')
    runtime = checked(p['runtime']); manifest = checked(runtime['implementation_manifest'])
    row = manifest['modules']['source_card_bootstrap']
    require(binding(row['path']) == row, 'cold source-card loader changed')
    spec = importlib.util.spec_from_file_location('source_card_execution_bootstrap', row['path'])
    loader = importlib.util.module_from_spec(spec); spec.loader.exec_module(loader)
    runtime, reader, selected, driver, env = loader.load(p['runtime'], probe=probe)
    require(p['owner'] == 'hhu-server-4:slot-1:0200-source-card-001', 'source-card owner differs')
    home = Path(p['home']); operations = Path(p['operations']); authorization = Path(p['authorization_directory'])
    require(home.is_absolute() and home.parent == Path(env[5]['run_root'])/'dispatcher-v2' and
            home.name == 'source-card-authoring-001' and authorization == home/'authorization' and
            operations == home.parent/'source-card-ordinary-001', 'source-card execution namespace differs')
    # Original v8 accepts only a direct child of dispatcher-v2. The actual
    # ordinary operations use their own such child; never nested home/ordinary.
    require(operations.parent == Path(env[5]['run_root'])/'dispatcher-v2',
            'ordinary operations must satisfy original dispatcher namespace')
    for row in (p['entry'], p['runner']):
        require(row in reader.dependencies() and binding(row['path']) == row,
                'undeclared execution dependency')
    spec = importlib.util.spec_from_file_location('source_card_declared_campaign_runner', p['runner']['path'])
    runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
    return p, runtime, reader, selected, driver, env, runner


def live(p, reader):
    from scripts.fleet import model_gate
    client, owner, journal = model_gate.context()
    require(owner['workbook'] == '0200' and owner['owner'] == p['owner'], 'source-card fleet custody differs')
    observed = client.call('heartbeat', **owner)
    require(observed['workbook'] == '0200' and observed['owner'] == p['owner'] and
            observed['fence'] == owner['fence'] and observed['source_sha256'] == reader.plan['source']['sha256'] and
            observed['version'] == '3ea7e135b1b4dc2acaa88b5bff985ccf49f98a3aa3c46f3c886ce06b22ba1c11',
            'source-card heartbeat source/version/fence differs')
    return observed


def dispatcher(original, selected, runner, *, plan_binding, entry, reader):
    """Keep original dispatcher bytecode; declare both guarded child launches."""
    base = selected['campaign'].base
    evidence = reader.dependencies()
    campaign = selected['campaign']
    run = runner.select_run(campaign, base=base, kind='instruction', reader_plan=plan_binding,
                            child_wrapper=entry, producer_code=binding(campaign.__file__), reader_evidence=evidence)
    campaign.__dict__['run'] = run
    if hasattr(campaign, 'declared_namespace'): campaign.declared_namespace['run'] = run
    namespace = dict(original.__dict__)
    for name, value in tuple(namespace.items()):
        if isinstance(value, FunctionType) and value.__globals__ is original.__dict__:
            clone = FunctionType(value.__code__, namespace, value.__name__, value.__defaults__, value.__closure__)
            clone.__kwdefaults__ = value.__kwdefaults__; namespace[name] = clone
    facade = SimpleNamespace(**namespace)
    renew = runner.select_run(facade, base=base, kind='renewal', reader_plan=plan_binding,
                              child_wrapper=entry, producer_code=binding(original.__file__), reader_evidence=evidence)
    # v8 renew obtains base from its env argument, so a global base proxy is
    # insufficient. Bind the single original guard call explicitly instead.
    import inspect
    before = inspect.getsource(original.renew)
    old = 'rc=base._run_in_process_group('
    require(before.count(old) == 1, 'unreviewed ordinary renewal launch')
    after = before.replace(old, 'rc=_source_card_guard(')
    namespace['_source_card_guard'] = renew.declared_runner
    exec(compile(after, str(Path(__file__).absolute())+'::renew', 'exec'), namespace)
    namespace['_source_card_renewal_witness'] = dict(
        before_sha256=hashlib.sha256(before.encode()).hexdigest(),
        after_sha256=hashlib.sha256(after.encode()).hexdigest(), before=before, after=after)
    facade.__dict__.update(namespace)
    facade.declared_namespace = namespace
    return facade


def owning_child(plan_binding, kind, intent, *, probe=False):
    p, runtime, reader, selected, original, env, runner = load(plan_binding, probe=probe)
    require(not probe, 'diagnostic cannot enter owning campaign child')
    require(kind in {'instruction', 'renewal'}, 'unsupported source-card child')
    producer = selected['campaign'] if kind == 'instruction' else original
    runner.validate_child(producer, base=env[0], kind=kind, reader_plan=plan_binding,
                          child_wrapper=p['entry'], intent=intent, reader_evidence=reader.dependencies())
    live(p, reader)
    if kind == 'instruction':
        value = checked(intent)
        require(value['action'] == 'step', 'source-card runtime cannot perform fresh start/import')
        selected['campaign'].child(intent)
    else:
        original.renewal_child(intent)


def authorization_intent(p, plan_binding):
    return dict(schema_version='fleet-0200-source-card-authorization-guard/v1',
        plan=plan_binding, entry=p['entry'], seconds=900, memory_bytes=8*1024**3,
        models=False, claims=False, lease_mutation=False, automatic_retry=False)


def takeover_lease(reader, value):
    from dataclasses import asdict
    _,_,lease=reader.takeover.validate_completion(reader.plan['takeover'])
    require(asdict(lease)==value['lease'], 'authorization lease differs from actual owning takeover')
    return lease


def authorize_child(plan_binding, intent):
    from dataclasses import asdict, replace
    p, runtime, reader, selected, driver, env, runner = load(plan_binding, probe=False)
    require(checked(intent) == authorization_intent(p, plan_binding) and
            Path(intent['path']) == Path(p['authorization_directory'])/'intent.json',
            'source-card authorization intent differs')
    expected=[p['entry']['path'],'authorize-child','--plan',plan_binding['path'],
        '--plan-sha256',plan_binding['sha256'],'--intent',intent['path'],'--intent-sha256',intent['sha256']]
    require(sys.argv == expected, 'actual source-card authorization argv differs')
    from scripts.batch import harbor_model_executor as E, harbor_tracker_lifecycle as L, harbor_lease_takeover as T
    from scripts.fleet import source_card_authorize as A
    def forbidden(*a, **k): raise ValueError('source-card authorization has no model, lease or publication capability')
    E.execute=E._open=forbidden; L.lease_operation=forbidden; T.replace_expired=forbidden
    env[2].run=env[2].execute=forbidden
    authority=A.authorize(reader, live_custody=lambda:live(p,reader))
    reader.authority=authority
    value=checked(authority); C=selected['campaign']; base=env[0]
    old=checked(reader.plan['old_descriptor']); worker=C.worker_decode(old['worker'])
    current=replace(worker, dialogue_pre_review_repair=authority)
    selection=C.codec.decode(value['selection'])
    lease=takeover_lease(reader,value)
    directory=Path(p['authorization_directory'])
    descriptor=driver.retain(directory/'descriptor.json', dict(schema_version=C.SCHEMA,
        worker=C.worker_encode(current), controls=old['controls'], dependencies=old['dependencies'],
        origin=[reader.plan['old_descriptor'], reader.plan['blocked'], reader.plan['takeover'], authority,
                plan_binding, *reader.dependencies()], code=binding(C.__file__),
        initial=dict(selection=C.codec.encode(selection), lease=base.encode(lease))))
    result=driver.retain(directory/'result.json',C._result('RETRY_READY',descriptor,selection,lease,
        checkpoint=authority,next_step='dialogue-writer',evidence=[reader.plan['blocked'],authority,plan_binding]))
    C.selected(descriptor); C._prior(result,descriptor)


def authorization_outcome(outcome, *, p, plan_binding, reader, env):
    directory=Path(p['authorization_directory'])
    require(Path(outcome['path'])==directory/'outcome.json', 'foreign source-card authorization outcome')
    value=checked(outcome)
    require(set(value)=={'status','resource','result','automatic_retry'} and
        value['status']=='COMPLETED_OPERATION' and value['automatic_retry'] is False and
        value['resource']==binding(directory/'resource.json') and value['result']==binding(directory/'result.json'),
        'source-card authorization guard did not complete')
    resource=checked(value['resource'])
    require(set(resource)=={'intent','returncode','failure','metrics','log','command','seconds','memory_bytes'} and
        resource['returncode']==0 and resource['failure'] is None and resource['seconds']==900 and
        resource['memory_bytes']==8*1024**3 and resource['intent']==binding(directory/'intent.json') and
        resource['log']==binding(directory/'step.log'), 'source-card authorization resource differs')
    require(checked(resource['intent'])==authorization_intent(p,plan_binding), 'authorization plan changed')
    expected=[sys.executable,'-I','-B',p['entry']['path'],'authorize-child','--plan',plan_binding['path'],
        '--plan-sha256',plan_binding['sha256'],'--intent',resource['intent']['path'],
        '--intent-sha256',resource['intent']['sha256']]
    require(resource['command']==expected,'authorization command differs')
    result=checked(value['result']); C=env[1]
    require(result['schema_version']==C.SCHEMA and result['status']=='RETRY_READY' and
        result['next_step']=='dialogue-writer' and result['checkpoint']==reader.authority and
        result['descriptor']==binding(directory/'descriptor.json') and
        result['evidence']==[reader.plan['blocked'],reader.authority,plan_binding] and
        result['publication_performed'] is False, 'authorization did not produce the exact remaining writer checkpoint')
    descriptor=checked(result['descriptor'])
    require(descriptor['origin']==[reader.plan['old_descriptor'],reader.plan['blocked'],reader.plan['takeover'],
        reader.authority,plan_binding,*reader.dependencies()], 'authorization descriptor omitted lineage')
    C.selected(result['descriptor']); C._prior(value['result'],result['descriptor'])
    return value['result'],result


def run(plan_binding):
    p, runtime, reader, selected, original, env, runner=load(plan_binding,probe=False)
    live(p,reader)
    require(not Path(p['home']).exists() and not Path(p['authorization_directory']).exists(),
            'source-card execution already attempted; reconcile without replay')
    original.operation_namespace(env[5],p['operations'])
    # Do not consume the newly selected native lease on an unusable start.
    prior,after,lease=reader.takeover.validate_completion(reader.plan['takeover'])
    import time, fcntl
    require(after['leases']['task_agent']['expires_at']-time.time_ns() > (900+3330+60)*10**9,
            'insufficient native lease for authorization and first owning step')
    Path(p['home']).mkdir()
    base=env[0]; directory=Path(p['authorization_directory'])
    with original.fleet_slot('0200') as fleet_fds:
        fd=os.open(base._controls(env[7]),os.O_RDWR|os.O_NOFOLLOW)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            directory.mkdir()
            intent=original.retain(directory/'intent.json',authorization_intent(p,plan_binding))
            command=[sys.executable,'-I','-B',p['entry']['path'],'authorize-child','--plan',plan_binding['path'],
                '--plan-sha256',plan_binding['sha256'],'--intent',intent['path'],'--intent-sha256',intent['sha256']]
            rc=None; failure=None; metrics={}
            try:
                with (directory/'step.log').open('xb') as log:
                    rc=base._run_in_process_group(command,stdout=log,timeout_seconds=900,
                        memory_limit_bytes=8*1024**3,grace_seconds=3,metrics=metrics,cwd=env[6],
                        env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},guardian_lock_fds=(fd,*fleet_fds))
            except Exception as exc:
                failure=dict(type=type(exc).__name__,message_sha256=hashlib.sha256(str(exc).encode()).hexdigest())
            resource=original.retain(directory/'resource.json',dict(intent=intent,returncode=rc,failure=failure,
                metrics=metrics,log=binding(directory/'step.log'),command=command,seconds=900,memory_bytes=8*1024**3))
            result=binding(directory/'result.json') if rc==0 and failure is None and (directory/'result.json').exists() else None
            outcome=original.retain(directory/'outcome.json',dict(status='COMPLETED_OPERATION' if result else 'RECONCILIATION_REQUIRED',
                resource=resource,result=result,automatic_retry=False))
        finally:os.close(fd)
    require(result is not None,'source-card authorization failed; retain partial work without replay')
    reader.authority=binding(Path(reader.plan['directory'])/'permit.json')
    adapted=dispatcher(original,selected,runner,plan_binding=plan_binding,entry=p['entry'],reader=reader)
    original_successful=adapted.successful
    current_env=(env[0],selected['campaign'],env[2],selected['worker'],selected['renewal'],*env[5:])
    def successful(bound,observed=None):
        if Path(bound['path'])==directory/'outcome.json':
            return authorization_outcome(bound,p=p,plan_binding=plan_binding,reader=reader,env=current_env)
        return original_successful(bound,observed)
    adapted.declared_namespace['successful']=successful;adapted.successful=successful
    authorization_outcome(outcome,p=p,plan_binding=plan_binding,reader=reader,env=current_env)
    old=checked(runtime['original_authoring_job'])
    require(old['publish'] is False and all(old[name] is True for name in ('models','commands','containers')),
            'original remaining capabilities differ')
    job=original.retain(Path(p['home'])/'authoring-job.json',{**old,'outcome':outcome,'operations':p['operations']})
    adapted.run(job)
    held=checked(binding(Path(p['operations'])/'hold.json'))
    checkpoint,value=successful(held['outcome'],current_env)
    ready=value['status']=='PUBLICATION_READY'
    result=original.retain(Path(p['home'])/'result.json',dict(workbook='0200',status='PUBLICATION_READY' if ready else 'HELD',
        PUBLICATION_READY=ready,publication_performed=False,plan=plan_binding,config=old['config'],job=job,
        outcome=held['outcome'],checkpoint=checkpoint,automatic_retry=False))
    require(ready,'owning source-card continuation is held; no automatic repeat')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['owning-child','authorize-child','run'])
    parser.add_argument('--plan', required=True); parser.add_argument('--plan-sha256', required=True)
    parser.add_argument('--kind', choices=['instruction', 'renewal'])
    parser.add_argument('--intent'); parser.add_argument('--intent-sha256')
    args = parser.parse_args()
    bound={'path':args.plan,'sha256':args.plan_sha256}
    if args.action=='run':
        require(args.kind is args.intent is args.intent_sha256 is None,'run accepts only selected plan')
        run(bound)
    else:
        require(args.intent is not None and args.intent_sha256 is not None,'child intent required')
        intent={'path':args.intent,'sha256':args.intent_sha256}
        if args.action=='authorize-child':
            require(args.kind is None,'authorization has no instruction capability')
            authorize_child(bound,intent)
        else:owning_child(bound,args.kind,intent)


if __name__ == '__main__': main()

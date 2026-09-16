"""Preserve an environment gate failure and continue its original owning worker.

Original failure snapshots, fixed downstream invalidation and final complete
readiness remain the only state transitions. A repair never supplies a gate PASS.
"""
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import ast,copy,inspect,json,textwrap,time

SCHEMA='harbor-environment-gate-repair/v1'


def require(ok,message):
    if not ok:raise ValueError(message)


def snapshot(C,path,binding):
    C.executor.checked_bytes(binding)
    C.executor.retain(Path(path),Path(binding['path']).read_bytes())
    result=C.record(path);require(result['sha256']==binding['sha256'],'tracker snapshot bytes changed')
    return result


def failure(C,checkpoint,tracker,*,permit_expired=False):
    value=C.read(checkpoint);before=C.read(tracker)
    require(value['status']=='OWNING_GATE_FAILED' and value['step']=='normalization_phase1'
        and value['lease']==C.encode(C.bootstrap.Lease(before['leases']['task_agent']['owner'],
            before['leases']['task_agent']['token'],before['revision'],tracker['sha256'])),
        'exact settled normalization gate failure and custody required')
    require(before['promotion_failure']['gate']=='normalization_phase1' and before['execution_state']=='running'
        and before['promotion_failure']['fixability']=='fixable',
        'different or terminal failure requires its own repair')
    worker,_,_=C.selected(value['descriptor']);selection=C.content_codec.decode(value['selection'])
    require(before['workbook_id']==worker.bootstrap.workbook==selection.content.workbook
        and before['bindings']['original_raw_source']==selection.content.bindings['original_raw_source'],
        'repair source identity changed')
    if permit_expired and before['leases']['task_agent']['expires_at']<=time.time_ns():
        require(C.record(worker.bootstrap.policy.tracker_root/(worker.bootstrap.workbook+'.json'))==tracker,
            'expired failed tracker is no longer current')
        facade=C.worker.HarborLaneLifecycle(C.worker._context(worker));facade.require_synchronized(worker.bootstrap.workbook)
    else:facade,_=C.worker._active(worker,C.decode(value['lease']))
    return value,before,worker,selection,facade


def prepare(C,checkpoint,tracker,directory,*,custody,takeover=None):
    from scripts.batch import harbor_completed_lanes as L
    value,before,worker,selection,facade=failure(C,checkpoint,tracker,permit_expired=takeover is not None)
    gate=L.check_completed_gate(before,L.CompletionEvidence(value['step'],selection),C.worker._context(worker))
    require(gate['passed'] is True,'corrected original environment gate still rejects')
    directory=Path(directory);require(not directory.exists(),'repair already attempted; reconcile its actual transactions')
    custody();directory.mkdir(parents=True)
    C.keep(directory/'intent.json',dict(schema_version=SCHEMA,code=C.record(__file__),checkpoint=checkpoint,tracker=tracker,automatic_retry=False))
    snapshot(C,directory/'original-failed-tracker.json',tracker)
    lease=C.decode(value['lease']);replacement=None
    if before['leases']['task_agent']['expires_at']<=time.time_ns():
        require(takeover is not None,'expired gate requires an explicit stopped-owner takeover policy')
        T,chosen,target,policy=takeover
        require(chosen.checkpoint==checkpoint and chosen.tracker==tracker and T.life.validate_lifecycle(before)==0,
            'only the selected first expired replacement may run')
        custody();lease,replacement=T.replace_expired(chosen,target)
    else:lease=C._lease(worker,lease,directory)
    renewed=C.record(worker.bootstrap.policy.tracker_root/(worker.bootstrap.workbook+'.json'))
    snapshot(C,directory/'before-snapshot.json',renewed);custody()
    captured=facade.snapshot_failure(worker.bootstrap.workbook,expected_revision=lease.revision,
        expected_sha256=lease.sha256,owner=lease.owner,token=lease.token)
    lease=replace(lease,revision=captured['revision'],sha256=captured['sha256']);custody()
    repaired=facade.begin_repair(worker.bootstrap.workbook,expected_revision=lease.revision,
        expected_sha256=lease.sha256,owner=lease.owner,token=lease.token)
    lease=replace(lease,revision=repaired['revision'],sha256=repaired['sha256'])
    after=snapshot(C,directory/'after-repair.json',C.record(worker.bootstrap.policy.tracker_root/(worker.bootstrap.workbook+'.json')))
    result=C.keep(directory/'receipt.json',dict(schema_version=SCHEMA,code=C.record(__file__),checkpoint=checkpoint,
        failed_tracker=C.record(directory/'original-failed-tracker.json'),before_snapshot=C.record(directory/'before-snapshot.json'),
        renewal=C.record(directory/'lease-renewal.json')if(directory/'lease-renewal.json').exists()else None,
        takeover=replacement,
        snapshot_transaction=captured['journal'],after_repair=after,descriptor=value['descriptor'],
        environment_run=str(worker.run),instruction_run=str(worker.run.parent/'instruction'),
        source=before['bindings']['original_raw_source'],workbook=worker.bootstrap.workbook,
        lease=C.encode(lease),selection=value['selection'],models_dispatched=0,semantic_budget_reset=False))
    validate(C,result)
    return result


def validate(C,receipt):
    from scripts.batch import harbor_failure_snapshot as F,harbor_lane_notes as notes,harbor_preserved_gate_repair as K,harbor_lease_takeover as T
    v=C.read(receipt);require(set(v)=={'schema_version','code','checkpoint','failed_tracker','before_snapshot','renewal','takeover',
        'snapshot_transaction','after_repair','descriptor','environment_run','instruction_run','source','workbook','lease',
        'selection','models_dispatched','semantic_budget_reset'} and v['schema_version']==SCHEMA and v['code']==C.record(__file__)
        and v['models_dispatched']==0 and v['semantic_budget_reset'] is False,'closed original environment repair required')
    old=C.read(v['checkpoint']);failed=C.read(v['failed_tracker']);before=C.read(v['before_snapshot']);tx=C.read(v['snapshot_transaction'])
    require(old['status']=='OWNING_GATE_FAILED' and old['step']=='normalization_phase1' and old['descriptor']==v['descriptor']
        and old['selection']==v['selection'] and failed['promotion_failure']['gate']=='normalization_phase1'
        and old['lease']==C.encode(C.bootstrap.Lease(failed['leases']['task_agent']['owner'],
            failed['leases']['task_agent']['token'],failed['revision'],v['failed_tracker']['sha256']))
        and failed['bindings']['original_raw_source']==v['source'] and failed['workbook_id']==v['workbook'],
        'environment repair lost actual failed predecessor')
    worker,_,_=C.selected(v['descriptor'])
    require(str(worker.run)==v['environment_run'] and str(worker.run.parent/'instruction')==v['instruction_run'],
        'environment repair run changed')
    if v['takeover'] is not None:
        previous,current,new=T.validate_completion(v['takeover']);stop=T.read(T.read(v['takeover'])['stopped_owner_proof'])
        require(v['renewal'] is None and previous==failed and current==before and T.life.validate_lifecycle(previous)==0
            and stop['selection']['checkpoint']==v['checkpoint'] and stop['selection']['workbook']==v['workbook']
            and stop['selection']['worker_kind']=='environment','takeover lost exact failed predecessor or consumed custody')
    elif v['renewal'] is None:require(before==failed,'unrecorded environment custody change')
    else:
        renewal=C.read(v['renewal']);previous=C.decode(renewal['previous']);new=C.decode(renewal['lease'])
        require(renewal['previous']==old['lease'] and before['revision']==failed['revision']+1
            and new.revision==before['revision'] and renewal['tracker']['sha256']==v['before_snapshot']['sha256']
            and (previous.owner,previous.token)==(new.owner,new.token), 'environment renewal detached from old custody')
        C.lifecycle.validate_lifecycle(before)
        require(before['handoffs']==failed['handoffs']+[dict(schema_version=C.lifecycle.EVENT_SCHEMA,
            operation='renew',lease_kind='task_agent',revision=before['revision'],root_actor='harbor-orchestrator',
            at=before['updated_at'],before=failed['leases']['task_agent'],after=before['leases']['task_agent'],
            prior_tracker_sha256=v['failed_tracker']['sha256'])], 'renewal lost exact original lease event')
        changed=copy.deepcopy(before);changed['revision']=failed['revision'];changed['updated_at']=failed['updated_at']
        changed['leases']['task_agent']=failed['leases']['task_agent']
        changed['handoffs']=failed['handoffs']
        require(changed==failed,'renewal changed non-custody evidence')
    require(tx['schema_version']==F.SCHEMA and C.read(tx['before'])==before,'snapshot lost live renewed predecessor')
    first=C.read(tx['after']);rebuilt,rendered=F.after_snapshot(before,tx['snapshot'],first['updated_at'],Path(first['agent_notes']['path']))
    require(first==rebuilt and Path(tx['notes']['path']).read_bytes()==rendered
        and C.read(C.record(Path(v['snapshot_transaction']['path']).parent/'complete.json'))==
            dict(tracker_sha256=tx['after']['sha256'],notes_sha256=tx['notes']['sha256']), 'original snapshot pair changed')
    context=notes.RootContext(worker.bootstrap.policy.tracker_root,worker.bootstrap.policy.trusted_public_key)
    after=K._repair_after(F,notes,tx['after'],v['after_repair'],tx['notes'],context)
    lease=C.decode(v['lease'])
    require(lease.revision==after['revision'] and lease.sha256==v['after_repair']['sha256']
        and (lease.owner,lease.token)==(after['leases']['task_agent']['owner'],after['leases']['task_agent']['token'])
        and after['promotion_failure']==first['promotion_failure'],'repair changed failure or resulting custody')
    return v,after


@contextmanager
def scope(C,I,W,receipt):
    """Keep the failure visible until the original final clear transition."""
    v,after=validate(C,receipt);old_prior=C._prior;old_active=W.active;old_operation=W._operation
    def prior(checkpoint,descriptor):
        if checkpoint!=receipt:return old_prior(checkpoint,descriptor)
        current,_=validate(C,receipt);require(descriptor==current['descriptor'],'repair descriptor changed')
        value=C.read(current['checkpoint']);lease=C.decode(current['lease'])
        worker,_,_=C.selected(descriptor);C.worker._active(worker,lease)
        return value,lease
    tree=ast.parse(textwrap.dedent(inspect.getsource(old_active)));target=ast.parse(
        "require(before['promotion_failure'] is None, 'existing failure requires root-owned snapshot/repair before continuing')").body[0];count=0
    class Replace(ast.NodeTransformer):
        def visit_Expr(self,node):
            nonlocal count
            if ast.dump(node,include_attributes=False)==ast.dump(target,include_attributes=False):
                count+=1;return ast.copy_location(ast.parse("require(_environment_repair_failure(worker,before), 'different failure requires original repair')").body[0],node)
            return self.generic_visit(node)
    tree=Replace().visit(tree);require(count==1,'original instruction active predicate changed')
    def allowed(worker,before):
        _,base=validate(C,receipt)
        require(str(worker.run)==v['instruction_run'] and before['bindings']['original_raw_source']==v['source']
            and before['failure_history'][:len(base['failure_history'])]==base['failure_history']
            and before['handoffs'][:len(base['handoffs'])]==base['handoffs'],'instruction lost original environment repair')
        return before['promotion_failure'] is None or before['promotion_failure']==base['promotion_failure']
    namespace={**old_active.__globals__,'_environment_repair_failure':allowed}
    exec(compile(ast.fix_missing_locations(tree),old_active.__code__.co_filename,'exec'),namespace)
    def operation(worker,selection,lease,phase,path,**options):
        if phase=='enqueue' and str(worker.run)==v['instruction_run']:
            facade,before,_=W.active(worker,selection,lease)
            if before['promotion_failure'] is not None:
                validate(C,receipt)
                result=facade.transition(v['workbook'],'clear',selection,expected_revision=lease.revision,
                    expected_sha256=lease.sha256,owner=lease.owner,token=lease.token)
                lease=replace(lease,revision=result['revision'],sha256=result['sha256'])
                W.keep(path/'environment-failure-cleared.json',dict(repair=receipt,result=result,original_complete_readiness=True))
        return old_operation(worker,selection,lease,phase,path,**options)
    C._prior=prior;W.active=namespace[old_active.__name__];W._operation=operation
    try:yield
    finally:C._prior=old_prior;W.active=old_active;W._operation=old_operation

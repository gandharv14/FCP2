"""Root-owned lease renewal at a completed instruction boundary; no work replay."""
from dataclasses import asdict, replace
from pathlib import Path
import fcntl
import time
from scripts.batch.harbor_admission import read, record, require
from scripts.batch import harbor_transition_codec as codec
from scripts.batch import harbor_tracker_lifecycle as lifecycle
from scripts.batch.harbor_nat_retry import _renewal_chain, _archive

SCHEMA='harbor-instruction-lease-renewal/v1'
ALLOWED={'scripts/batch/harbor_instruction_lease_renewal.py',
         'scripts/batch/harbor_instruction_worker.py','scripts/harbor_instruction_campaign.py',
         'scripts/batch/harbor_disclosure_review_resume.py',
         'scripts/batch/harbor_nat_retry.py','scripts/batch/harbor_dialogue_semantic_retry.py',
         'scripts/batch/harbor_continuation_allowance.py', 'scripts/batch/harbor_tracker_lifecycle.py',
         'scripts/batch/harbor_lease_takeover.py'}
CONTINUATIONS=('retry','dialogue_retry','boundary_recovery','dialogue_semantic_retry',
               'pretransport_recovery','code_repair','warning_requalification','disclosure_repair','lease_renewal','disclosure_revision','dialogue_pre_review_repair')

def continuations(worker):
    return {k:getattr(worker,k) for k in CONTINUATIONS if getattr(worker,k,None) is not None}

def changes(binding,root):
    old=read(binding); expected={r['relative']:r['sha256'] for r in old['files']}
    for name,digest in expected.items():require(record(Path(old['root'])/name)['sha256']==digest,'historical snapshot changed')
    actual={str(p.relative_to(root)):record(p)['sha256'] for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    require(set(expected)<=set(actual),'renewal successor removed code')
    delta=sorted(k for k in actual if actual[k]!=expected.get(k))
    require(set(delta)<=ALLOWED,'unrelated renewal code successor')
    return delta


def command_repair_history(worker,snapshot,checkpoint):
    """Authenticate the exact two-root code selection of a completed repair."""
    from scripts.batch import harbor_disclosure_command_repair as K
    require(K.selected(worker),'unsupported mixed-root renewal')
    selected=read(read(worker.code_repair)['profile'])
    permit=K.validate(worker,read(record(worker.context.tracker_root/(selected['workbook']+'.json'))))
    profile=read(permit['profile']);require(snapshot==profile['snapshot'],'renewal snapshot differs from selected command repair')
    inventories={}
    for row in (profile['old_snapshot'],profile['snapshot']):
        value=read(row);root=Path(value['root']).resolve();expected={x['relative']:x['sha256']for x in value['files']}
        require(len(expected)==len(value['files']),'duplicate retained code inventory')
        actual={str(p.relative_to(root)):record(p)['sha256']for p in root.rglob('*')if p.is_file()and '__pycache__'not in p.parts}
        require(actual==expected,'retained command code inventory changed');inventories[root]=expected
    require(len(inventories)==2,'distinct retained and selected code roots required')
    code=read(read(checkpoint)['intent'])['code'];require(code and code==permit['current_code'],'completed repair code differs from permit')
    for path,digest in code.items():
        source=Path(path).resolve();roots=[r for r in inventories if source.is_relative_to(r)]
        require(len(roots)==1 and inventories[roots[0]].get(str(source.relative_to(roots[0])))==digest and record(source)['sha256']==digest,'completed repair code outside exact inventories')


def historical_code(snapshot,checkpoint,worker=None):
    """The nominated old root must be the actual last completed producer root."""
    if worker is not None and worker.code_repair is not None:return command_repair_history(worker,snapshot,checkpoint)
    old=read(snapshot);root=Path(old['root']).resolve()
    expected={row['relative']:row['sha256'] for row in old['files']}
    code=read(read(checkpoint)['intent'])['code']
    require(bool(code),'completed intent has no implementation proof')
    for path,digest in code.items():
        source=Path(path).resolve()
        require(source.is_relative_to(root),'historical snapshot is not the completed intent root')
        relative=str(source.relative_to(root))
        require(expected.get(relative)==digest and record(source)['sha256']==digest,'completed intent code differs from historical snapshot')

def _checkpoint_history(worker,selection,lease,checkpoint,*,historical):
    from scripts.batch import harbor_instruction_worker as w
    completed=[]
    for phase in w.STEPS:
        if worker.dialogue_pre_review_repair is not None and worker.dialogue_semantic_retry is None and phase=='dialogue-paraphraser':
            from scripts.batch.harbor_dialogue_pre_review_repair import validate
            validate(worker,read(record(worker.context.tracker_root/(selection.content.workbook+'.json'))))
            completed.append((phase,worker.dialogue_pre_review_repair))
            if historical and completed[-1][1]==checkpoint:break
            continue
        path=w.step_directory(worker,phase)/'complete.json'
        if not path.exists():break
        value=read(record(path));require(value['status']=='completed' and value['step']==phase,'noncompleted renewal boundary')
        completed.append((phase,record(path)))
        if historical and completed[-1][1]==checkpoint:break
    require(completed and completed[-1][1]==checkpoint,'renewal requires exact latest successful checkpoint')
    require(len(completed)<len(w.STEPS),'published chain cannot renew')
    phase=w.STEPS[len(completed)]
    if not historical:
        require(not w.step_directory(worker,phase).exists(),'pending or uncertain next step cannot renew')
    saved=read(checkpoint)
    require(saved['selection']==codec.encode(selection) and saved['lease']==asdict(lease),'renewal selection or lease changed')
    records=[checkpoint]
    for _,binding in completed:
        if binding==worker.dialogue_pre_review_repair:
            records.append(binding);continue
        value=read(binding);intent=read(value['intent'])
        require(intent['initial']==record(worker.run/'initial.json'),'foreign completed checkpoint')
        records.extend([binding,value['intent']])
        for path,digest in intent['code'].items():require(record(path)['sha256']==digest,'completed implementation changed')
        request=Path(binding['path']).parent/'request.json'
        if request.exists():
            req=w.request_saved(request);result=w.executor.inspect(req.prepared,worker.campaign)
            require(result.status=='completed' and result.call is not None,'completed checkpoint lacks completed call')
            from scripts.batch.harbor_complete_readiness import evidence_records
            records.extend([record(request),req.context,*evidence_records(result.call),result.outcome])
    return phase,records


def completed_history(worker,selection,lease,checkpoint):
    """Validate the completed prefix through an exact historical checkpoint.

    Later pending, failed or completed steps do not invalidate that prefix.
    This performs no current-custody or renewal-eligibility check and grants no
    permission to replay a request, renew a lease, or resume an uncertain step.
    Callers must independently validate their current continuation authority.
    """
    return _checkpoint_history(worker,selection,lease,checkpoint,historical=True)


def _boundary(worker,selection,lease,checkpoint):
    """Require the latest completed checkpoint and an unused next operation."""
    return _checkpoint_history(worker,selection,lease,checkpoint,historical=False)

def _prefix(before,selection,worker,phase):
    from scripts.batch.harbor_instruction_repair import prefix
    from scripts.batch import harbor_instruction_worker as w
    index=w.STEPS.index(phase)
    stop='disclosure' if index<2 else 'naturalization' if index<6 else 'verification' if index<8 else 'dialogue'
    results=prefix(before,selection,worker.context,stop=stop)
    if index>=14:
        result=w.complete_readiness(selection,root=worker.root)
        require(result['publication_ready'],'completed dialogue readiness changed')
        results.append({'kind':'complete','result':result})
    if index in (11,12,13):
        _accepted_authors(worker,selection,index)
    if index==5:
        p=w.producer;task=w.task(worker,selection,record(worker.context.tracker_root/(selection.content.workbook+'.json')))
        r,n,state=p._nat(task,worker.root)
        r._verified_budget(n,state);r._verify_attempt_evidence(n,state)
        require(state['status']=='validated','completed NAT writer is not mechanically valid')
        validator=p.skill_module(worker.root,'naturalize-finance-task-instruction','validate_instruction_rewrite')
        validator.validate(r._verified_snapshot(n,state),(n/state['final_candidate']).read_bytes(),r._verified_answer_key(n,state))
    if index==13:
        from scripts.batch.harbor_complete_readiness import check_dialogue
        check_dialogue(selection,root=worker.root)
    return results

def _accepted_authors(worker,selection,index):
    from scripts.batch import harbor_instruction_worker as w
    p=w.producer;t=w.task(worker,selection,record(worker.context.tracker_root/(selection.content.workbook+'.json')))
    writer=w.request_saved(w.step_directory(worker,'dialogue-writer')/'request.json')
    round=p._author_round(writer);directory,claims,template,slots=p._dialogue(t,round,worker.root)
    call,checked,filled,proof=p._accepted_author(t,directory,round,'writer',claims,template,slots,root=worker.root)
    require(call==p.observed(writer,worker.campaign),'renewal writer differs from accepted output')
    if index>=12:
        para=w.request_saved(w.step_directory(worker,'dialogue-paraphraser')/'request.json')
        call,_,_,_=p._accepted_author(t,directory,round,'paraphraser',claims,template,slots,incoming=filled,root=worker.root)
        require(call==p.observed(para,worker.campaign),'renewal paraphraser differs from accepted output')
        v=p.skill_module(worker.root,'additional-assumptions-dialogue','validate_dialogue')
        require(v.check_draft(p.text(record(directory/('round-%02d'%round)/'draft.md')),claims,t.stage,source_workbook=p.dialogue_source_workbook(t))['passed'],'renewal draft failed mechanical validation')

def authorize(worker,selection,lease,checkpoint,old_snapshot,*,duration_ns=3600*10**9,allowance=None,clock=time.time_ns):
    """Renew once under owning CAS. Interrupted CAS is retained, never repeated."""
    from scripts.batch import harbor_instruction_worker as w
    from scripts import harbor_campaign as base
    lock=worker.run/'worker.lock'
    with lock.open('a+b') as handle:
        fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        _,before,tracker=w.active(worker,selection,lease)
        phase,history=_boundary(worker,selection,lease,checkpoint)
        require(before['leases']['task_agent']['expires_at']>clock(),'expired lease requires takeover')
        maximum=base.DURATION
        if allowance is not None:
            from scripts.batch.harbor_continuation_allowance import lease_limits
            _,maximum=lease_limits(allowance,before,at=clock())
        require(type(duration_ns) is int and 0<duration_ns<=maximum,'invalid renewal duration')
        historical_code(old_snapshot,checkpoint,worker)
        delta=changes(old_snapshot,worker.root)
        if worker.lease_renewal is not None:validate(worker,before)
        import importlib
        for field,module in (('retry','harbor_nat_retry'),('dialogue_semantic_retry','harbor_dialogue_semantic_retry'),('dialogue_retry','harbor_dialogue_retry'),('boundary_recovery','harbor_nat_boundary_resume')):
            if getattr(worker,field) is not None:importlib.import_module('scripts.batch.'+module).validate(worker,before)
        require(worker.pretransport_recovery is None,'specialized pretransport repair requires its own reviewed renewal integration')
        if worker.code_repair is not None:
            from scripts.batch import harbor_disclosure_command_repair as command_repair
            require(command_repair.selected(worker),'unsupported code repair renewal')
            command_repair.validate(worker,before)
        if worker.warning_requalification is not None:
            from scripts.batch.harbor_dialogue_warning_requalification import validate as validate_warning
            require(read(worker.warning_requalification).get('prior_continuations',{}).get('disclosure_revision')==worker.disclosure_revision
                    and (worker.disclosure_revision is not None or read(worker.warning_requalification).get('ordinary_first_round') is not None),'warning renewal requires authenticated first-round history')
            validate_warning(worker,before)
        if worker.dialogue_pre_review_repair is not None:
            if worker.dialogue_semantic_retry is not None:
                from scripts.batch.harbor_dialogue_semantic_retry import historical_pre_review
                historical_pre_review(worker,before)
            else:
                from scripts.batch.harbor_dialogue_pre_review_repair import validate as validate_pre_review
                validate_pre_review(worker,before)
        if worker.disclosure_revision is not None:
            if worker.dialogue_pre_review_repair is not None:
                from scripts.batch.harbor_dialogue_pre_review_repair import historical_manifest
                historical_manifest(worker,before)
            else:
                from scripts.batch.harbor_disclosure_revision import validate as validate_revision
                validate_revision(worker,before)
        if worker.disclosure_repair is not None:
            from scripts.batch.harbor_disclosure_review_resume import validate_historical_integrity
            _,records=validate_historical_integrity(worker,before);history.extend(records)
        proof=_prefix(before,selection,worker,phase)
        directory=worker.run/'lease-renewals'/checkpoint['sha256']
        require(not directory.exists(),'renewal boundary already reserved; inspect retained transaction')
        directory.mkdir(parents=True)
        retained=_archive(directory/'history',history)
        prefix_records=[binding for item in proof for binding in item['result'].get('evidence',[])]
        prefix_retained=_archive(directory/'prefix',prefix_records)
        previous=w.snapshot(directory/'before-tracker.json',tracker)
        permit=w.keep(directory/'permit.json',dict(schema_version=SCHEMA,worker_run=str(worker.run),
            initial=record(worker.run/'initial.json'),checkpoint=checkpoint,phase=phase,
            selection=codec.encode(selection),lease=asdict(lease),tracker=previous,
            old_snapshot=old_snapshot,code_changes=delta,current_code=w.code(worker),
            prior_continuations=continuations(worker),history=history,retained=retained,
            prefix=proof,prefix_retained=prefix_retained,duration_ns=duration_ns,semantic_budget_reset=False,
            **({'allowance':allowance} if allowance is not None else {})))
        # Recheck the exact bytes after the potentially expensive read-only gates.
        require(record(tracker['path'])==tracker,'tracker changed during renewal validation')
        authority=base._authorize(Path(tracker['path']),lease.owner,lease.revision,lease.token)
        if allowance is not None:authority=replace(authority,max_duration_ns=maximum,allowance=allowance)
        result=lifecycle.lease_operation(Path(tracker['path']),authority=authority,
            operation='renew',lease_kind='task_agent',owner=lease.owner,scope='workbook:'+before['workbook_id'],
            token=lease.token,duration_ns=duration_ns,expected_revision=lease.revision,expected_sha256=lease.sha256,clock=clock)
        renewed=replace(lease,revision=result['revision'],sha256=result['sha256'])
        after=w.snapshot(directory/'after-tracker.json',record(tracker['path']))
        _renewal_chain(previous,asdict(lease),read(after),renewed)
        return w.keep(directory/'complete.json',dict(schema_version=SCHEMA,permit=permit,
            selection=codec.encode(selection),lease=asdict(renewed),tracker=after))

def _historical_inventory(worker,old_snapshot):
    """Authenticate old implementation bytes without importing or running them."""
    value=read(old_snapshot);root=Path(value['root']).resolve()
    require(worker.root.resolve()==root,'historical renewal root differs from selected snapshot')
    expected={item['relative']:item['sha256'] for item in value['files']}
    require(len(expected)==len(value['files']),'duplicate historical snapshot entry')
    actual={}
    for path in root.rglob('*'):
        require(not path.is_symlink(),'historical snapshot contains symlink')
        relative=path.relative_to(root)
        if '__pycache__' in relative.parts:continue
        if path.is_file():actual[str(relative)]=record(path)['sha256']
    require(actual==expected,'historical renewal snapshot inventory changed')
    names={'scripts/batch/harbor_instruction_worker.py','scripts/batch/harbor_role_producer.py',
           'scripts/batch/harbor_instruction_producer.py','scripts/batch/hardened_runner.py',
           'scripts/batch/process_guardian.py','scripts/harbor_pretransport_recovery.py',
           'scripts/harbor_instruction_code_applicability.py'}
    names.update(str(path.relative_to(root)) for path in (root/'scripts/batch').glob('harbor_*.py'))
    require(names<=set(expected),'historical renewal implementation inventory incomplete')
    return {str(root/name):expected[name] for name in names}


def validate_historical_integrity(worker,before,old_snapshot):
    """Apply current receipt/CAS checks to an explicitly selected old worker.

    The caller's closed successor proof binds this snapshot and old worker.
    This function never executes old modules, alters a lease, or allows a
    current worker to substitute historical code in its ordinary validation.
    """
    expected=_historical_inventory(worker,old_snapshot)
    return _validate(worker,before,set(),historical_code_inventory=expected)


def _retained_lease_adapter(worker,before):
    """Only an exact predecessor link may use a successor's historical proof."""
    if worker.dialogue_pre_review_repair is not None and worker.dialogue_semantic_retry is not None:
        semantic=read(worker.dialogue_semantic_retry);permit=read(semantic['permit'])
        if semantic.get('schema_version')==permit.get('schema_version')=='harbor-dialogue-semantic-retry/v1' and permit.get('prior_continuations',{}).get('dialogue_pre_review_repair')==worker.dialogue_pre_review_repair and permit['prior_continuations'].get('lease_renewal')==worker.lease_renewal:
            from scripts.batch.harbor_dialogue_semantic_retry import historical_lease
            return historical_lease(worker,before)
    if worker.dialogue_pre_review_repair is not None:
        repair=read(worker.dialogue_pre_review_repair)
        if repair.get('schema_version')=='harbor-dialogue-pre-review-repair/v1' and repair.get('prior_continuations',{}).get('lease_renewal')==worker.lease_renewal:
            from scripts.batch.harbor_dialogue_pre_review_repair import historical_lease
            return historical_lease(worker,before)
    if worker.warning_requalification is not None:
        warning=read(worker.warning_requalification)
        if warning.get('schema_version')=='harbor-dialogue-warning-requalification/v1' and warning.get('prior_continuations',{}).get('lease_renewal')==worker.lease_renewal:
            from scripts.batch.harbor_dialogue_warning_requalification import historical_lease
            return historical_lease(worker,before)
    if worker.disclosure_revision is not None:
        revision=read(worker.disclosure_revision)
        if revision.get('source_revision') is not None and revision.get('prior_continuations',{}).get('lease_renewal')==worker.lease_renewal:
            from scripts.batch.harbor_disclosure_source_revision import historical_lease
            return historical_lease(worker,before)
        if revision.get('schema_version')=='harbor-disclosure-manifest-revision/v1' and revision.get('prior_continuations',{}).get('lease_renewal')==worker.lease_renewal:
            from scripts.batch.harbor_disclosure_manifest_revision import historical_lease
            return historical_lease(worker,before)
    return None


def _validate(worker,before,seen,*,historical_code_inventory=None):
    """Authenticate immutable boundary evidence, including the exact renewal CAS."""
    from scripts.batch import harbor_instruction_worker as w
    from scripts.batch.harbor_workbook_bootstrap import Lease
    require(worker.lease_renewal['path'] not in seen and len(seen)<128,'cyclic renewal history')
    # A retained worker can itself bridge an earlier frozen implementation.
    # Its exact bound predecessor adapter still applies after full inventory validation.
    adapted=_retained_lease_adapter(worker,before)
    if adapted is not None:return adapted
    seen=set(seen)|{worker.lease_renewal['path']}
    complete=read(worker.lease_renewal);permit=read(complete['permit'])
    require(complete['schema_version']==permit['schema_version']==SCHEMA,'foreign lease renewal')
    require(permit['worker_run']==str(worker.run) and permit['initial']==record(worker.run/'initial.json'),'renewal worker changed')
    require(worker.lease_renewal==record(worker.run/'lease-renewals'/permit['checkpoint']['sha256']/'complete.json'),'foreign renewal namespace')
    expected_code=w.code(worker) if historical_code_inventory is None else historical_code_inventory
    require(permit['current_code']==expected_code and permit['code_changes']==changes(permit['old_snapshot'],worker.root),'renewal implementation changed')
    require(permit['semantic_budget_reset'] is False and complete['selection']==permit['selection'],'renewal changed selection or budget')
    require(permit['checkpoint'] in permit['history'],'renewal checkpoint detached from retained history')
    historical_code(permit['old_snapshot'],permit['checkpoint'],worker)
    checkpoint=read(permit['checkpoint'])
    require(checkpoint['status']=='completed' and checkpoint['selection']==permit['selection'] and checkpoint['lease']==permit['lease'],'renewal checkpoint changed')
    prior=read(permit['tracker']);after=read(complete['tracker'])
    _renewal_chain(permit['tracker'],permit['lease'],after,Lease(**complete['lease']))
    require(after['revision']==prior['revision']+1,'renewal must contain exactly one CAS')
    require(before['workbook_id']==after['workbook_id'] and before['bindings']['original_raw_source']==after['bindings']['original_raw_source'],'renewal source changed')
    require(before['revision']>=after['revision'] and before['handoffs'][:len(after['handoffs'])]==after['handoffs'],'renewal tracker ancestry changed')
    require(after['leases']['task_agent']['expires_at']-after['leases']['task_agent']['heartbeat_at']==permit['duration_ns'],'renewal duration changed')
    _allowance_event(permit,after)
    for binding in permit['history']:w.executor.checked_bytes(binding)
    for binding in [*permit['retained'],*permit['prefix_retained']]:
        w.executor.checked_bytes(binding['retained'])
        require(binding['original']['sha256']==binding['retained']['sha256'],'renewal archive changed')
    for binding in permit['prior_continuations'].values():w.executor.checked_bytes(binding)
    current=continuations(worker);current.pop('lease_renewal',None)
    prior=dict(permit['prior_continuations']);previous=prior.pop('lease_renewal',None)
    _later_retries(worker,before,current,prior)
    if previous is not None:
        historical=replace(worker,**{k:prior.get(k) for k in CONTINUATIONS if k!='lease_renewal'},lease_renewal=previous)
        _validate(historical,before,seen,historical_code_inventory=historical_code_inventory)
    return permit

def _allowance_event(permit,after):
    event=after['handoffs'][-1]
    require(event.get('allowance')==permit.get('allowance'),'renewal allowance detached from lifecycle event')
    if permit.get('allowance') is not None:
        from scripts.batch.harbor_continuation_allowance import lease_limits
        _,maximum=lease_limits(permit['allowance'],after,at=event['at'])
        require(permit['duration_ns']<=maximum,'renewal exceeded selected allowance')
        lifecycle.validate_lifecycle(after)


def validate(worker,before):
    return _validate(worker,before,set())

def checkpoint_predecessor(worker,binding):
    """Only a member of the explicitly retained renewal chain can be traversed."""
    from scripts.batch import harbor_instruction_worker as w
    before=read(record(worker.context.tracker_root/(read(read(read(worker.lease_renewal)['permit'])['tracker'])['workbook_id']+'.json')))
    validate(worker,before)
    current=worker.lease_renewal;seen=set()
    while current is not None:
        require(current['path'] not in seen and len(seen)<128,'cyclic renewal history')
        seen.add(current['path']);complete=read(current);permit=read(complete['permit'])
        require(complete['schema_version']==SCHEMA and permit['worker_run']==str(worker.run),'foreign renewal ancestor')
        if current==binding:return permit['checkpoint']
        current=permit['prior_continuations'].get('lease_renewal')
    require(False,'detached renewal checkpoint')

def recover(worker,permit_binding):
    """Finish evidence after one committed renewal; never issue a lifecycle call."""
    from scripts.batch import harbor_instruction_worker as w
    from scripts.batch.harbor_workbook_bootstrap import Lease
    permit=read(permit_binding);directory=worker.run/'lease-renewals'/permit['checkpoint']['sha256']
    require(permit_binding==record(directory/'permit.json'),'foreign renewal transaction')
    with (worker.run/'worker.lock').open('a+b') as handle:
        fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        tracker=record(worker.context.tracker_root/(read(permit['tracker'])['workbook_id']+'.json'))
        before=read(tracker);old=Lease(**permit['lease'])
        lease=replace(old,revision=before['revision'],sha256=tracker['sha256'])
        _renewal_chain(permit['tracker'],permit['lease'],before,lease)
        require(lease.revision==old.revision+1,'no exact committed renewal to recover')
        require(before['leases']['task_agent']['expires_at']-before['leases']['task_agent']['heartbeat_at']==permit['duration_ns'],'recovered renewal duration changed')
        _allowance_event(permit,before)
        if (directory/'complete.json').exists():
            result=record(directory/'complete.json');validate(replace(worker,lease_renewal=result),before);return result
        selection=codec.decode(permit['selection'])
        w.active(worker,selection,lease)
        require(permit['current_code']==w.code(worker) and permit['code_changes']==changes(permit['old_snapshot'],worker.root),'recovery implementation changed')
        require(permit['prior_continuations']==continuations(worker),'recovery continuation history changed')
        historical_code(permit['old_snapshot'],permit['checkpoint'],worker)
        _boundary(worker,selection,old,permit['checkpoint'])
        _prefix(before,selection,worker,permit['phase'])
        for row in permit['history']:w.executor.checked_bytes(row)
        for row in [*permit['retained'],*permit['prefix_retained']]:w.executor.checked_bytes(row['retained'])
        after=w.snapshot(directory/'after-tracker.json',tracker)
        result=w.keep(directory/'complete.json',dict(schema_version=SCHEMA,permit=permit_binding,
            selection=permit['selection'],lease=asdict(lease),tracker=after))
        validate(replace(worker,lease_renewal=result),before)
        return result


def _later_retries(worker,before,current,prior):
    """Only owning NAT/dialogue retry CAS may add a link after this renewal."""
    from scripts.batch import harbor_tracker as schema
    import importlib
    remaining=dict(current)
    for field,module_name in (('retry','harbor_nat_retry'),('dialogue_semantic_retry','harbor_dialogue_semantic_retry')):
        if current.get(field)==prior.get(field):continue
        require(prior.get(field) is None and current.get(field) is not None,'renewal retry was removed or replaced')
        complete=read(current[field]);permit=read(complete['permit']);module=importlib.import_module('scripts.batch.'+module_name)
        require(complete['schema_version']==permit['schema_version']==module.SCHEMA and permit['worker_run']==str(worker.run),'foreign later retry')
        from scripts.batch import harbor_instruction_worker as w
        require(permit['code']==w.code(worker),'later retry implementation changed')
        require(permit['prior_continuations'].get('lease_renewal')==worker.lease_renewal,'later retry detached renewal')
        old=read(permit['before']);after=read(complete['after']);expected=module._project(old,complete['permit'])
        expected['revision']+=1;expected['updated_at']=after['updated_at'];expected['leases']['task_agent']['heartbeat_at']=after['updated_at']
        require(expected==after and dict(schema_version=module.SCHEMA,permit=complete['permit']) in before['handoffs'],'later retry lacks exact owning CAS')
        require(complete['lease']==asdict(__import__('scripts.batch.harbor_workbook_bootstrap',fromlist=['Lease']).Lease(permit['lease']['owner'],permit['lease']['token'],after['revision'],schema.sha(lifecycle.canonical(after)))),'later retry lease changed')
        remaining.pop(field)
    require(remaining==prior,'renewal detached existing continuation history')


def retry_continuations(worker,field,normal):
    """An older retry keeps its original lease receipt after a later renewal."""
    binding=getattr(worker,field)
    if worker.lease_renewal is None or binding is None:return normal
    renewal=read(read(worker.lease_renewal)['permit'])
    if renewal['prior_continuations'].get(field)!=binding:return normal
    original=read(read(binding)['permit']).get('prior_continuations',{})
    result=dict(normal)
    if 'lease_renewal' in original:result['lease_renewal']=original['lease_renewal']
    else:result.pop('lease_renewal',None)
    return result


def history(worker,before):
    validate(worker,before)
    result=[];current=worker.lease_renewal
    while current is not None:
        complete=read(current);permit=read(complete['permit'])
        result.extend([current,complete['permit'],complete['tracker'],permit['tracker'],permit['checkpoint'],permit['old_snapshot']])
        result.extend(row['retained'] for row in [*permit['retained'],*permit['prefix_retained']])
        current=permit['prior_continuations'].get('lease_renewal')
    return result

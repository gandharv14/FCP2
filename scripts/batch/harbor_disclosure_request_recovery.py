"""One exact predispatch binary-budget validator repair; request stays unchanged."""
from dataclasses import asdict,replace
from pathlib import Path
import fcntl
from scripts.batch.harbor_admission import read,record,require
from scripts.batch import harbor_model_executor as E
from scripts.batch import harbor_disclosure_revision as R
from scripts.batch import harbor_transition_codec as codec

SCHEMA='harbor-disclosure-request-validator-recovery/v1'
PFILE='scripts/batch/harbor_instruction_producer.py'
RFILE='scripts/batch/harbor_disclosure_revision.py'
SELF='scripts/batch/harbor_disclosure_request_recovery.py'
EDITS=(
 ("any(text(b)==text(revision) for b in context['budget_evidence'])", "any(Path(checked_record(b)['path']).read_bytes()==Path(checked_record(revision)['path']).read_bytes() for b in context['budget_evidence'])"),
 ("        validate_task(task)\n        require(revision==task.disclosure_revision", "        authority=validate_task(task)\n        require(revision==authority.get('request_revision',task.disclosure_revision)"),
 ("    elif task.code_repair is not None and context['code']!=code_evidence(task):", "    elif context['kind']=='disclosure-review' and task.disclosure_revision is not None and read(task.disclosure_revision).get('validation_recovery') is not None:\n        from scripts.batch.harbor_disclosure_request_recovery import validate_context\n        validate_context(task,request)\n    elif task.code_repair is not None and context['code']!=code_evidence(task):"),
)


def implementation(snapshot,root,original):
    old=read(snapshot);base=Path(old['root']);R.historical_code(snapshot,original)
    files={r['relative']:r['sha256'] for r in old['files']};changed=[]
    for name,digest in files.items():
        require(record(base/name)['sha256']==digest,'original prepared implementation changed')
        if record(root/name)['sha256']!=digest:changed.append(name)
    extra={str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts}-set(files)
    require(set(changed)<={PFILE,RFILE} and PFILE in changed and extra=={SELF},'unrelated validator recovery code')
    expected=(base/PFILE).read_text()
    for before,after in EDITS:
        require(expected.count(before)==1,'validator repair anchor changed');expected=expected.replace(before,after,1)
    require((root/PFILE).read_text()==expected,'producer differs beyond exact byte comparison and proof routing')
    return sorted(changed)+sorted(extra)


def original(binding,before=None):
    p=read(binding);run=Path(p['worker_run'])
    require(p.get('validation_recovery') is None and read(record(run/'disclosure-revision-complete.json'))==binding,'original completed revision changed')
    require(p['schema_version']==R.SCHEMA and p['revision']==p['limit']==2 and p['semantic_budget_reset'] is False,'original global revision changed')
    require(p['initial']==record(run/'initial.json') and read(record(run/'disclosure-revision.json'))['directory']==str(Path(binding['path']).parent),'original reservation changed')
    if before is not None:require(p['original']==before['bindings']['original_raw_source'],'foreign original revision')
    for path,digest in p['current_code'].items():require(record(path)['sha256']==digest,'original completed code changed')
    R.permit_history(p)
    return p


def absent(request,campaign):
    require(not E.location(request.prepared,campaign).parent.exists(),'executor scope already exists; no validator replay')
    E.prepared_plan(request.prepared,campaign)


def authorize(worker,selection,lease,*,old_snapshot,failed_resource,directory):
    from scripts.batch import harbor_instruction_worker as W
    from scripts.batch.harbor_instruction_repair import prefix
    from scripts.batch.harbor_nat_retry import _renewal_chain
    with (worker.run/'worker.lock').open('a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        marker=worker.run/'disclosure-validator-recovery.json'
        require(not marker.exists(),'validator recovery already consumed')
        _,before,tracker=W.active(worker,selection,lease)
        prior=worker.disclosure_revision;old=original(prior,before)
        require(codec.decode(old['selection'])==selection,'selected prepared revision changed')
        _renewal_chain(old['tracker'],old['lease'],before,lease)
        changed=implementation(old_snapshot,worker.root,old)
        step=Path(prior['path']).parent/'steps/disclosure-review'
        request_binding=record(step/'request.json');request=W.request_saved(step/'request.json');context=read(request.context)
        require(context['disclosure_revision']==prior and context['request']==request.prepared.request
                and request.prepared.scope.budget_id==R.BUDGET and request.prepared.ordinal==request.prepared.limit==1,'prepared corrective scope changed')
        exception=record(step/'exception.json');failure=read(exception)
        require(failure['type']=='UnicodeDecodeError' and failure['automatic_replay'] is False
                and not (step/'complete.json').exists() and not (step/'blocked.json').exists(),'exact predispatch validator error required')
        resource=read(failed_resource);intent=read(resource['intent'])
        require(resource['returncode']==1 and resource.get('failure') is None,'uncompleted validator resource observation')
        require(resource['log']==record(Path(failed_resource['path']).parent/'step.log'),'validator log changed')
        log=E.checked_bytes(resource['log']);require(b'any(text(b)==text(revision)' in log and b'UnicodeDecodeError:' in log,'not binary-budget validator failure')
        E.checked_bytes(resource['intent'])
        step_intent=read(record(step/'intent.json'))
        require(step_intent['previous']==prior and step_intent['code']==old['current_code'],'failed request detached from revision/code')
        coordinator=read(intent['arguments']['checkpoint'])
        require(intent['action']=='step' and coordinator['checkpoint']==prior and intent['arguments']['descriptor']==coordinator['descriptor'],'failed coordinator detached from original revision')
        require(intent['code']==record(Path(read(old_snapshot)['root'])/'scripts/harbor_instruction_campaign.py'),'failed coordinator implementation changed')
        try:
            any(E.checked_bytes(b).decode('utf-8')==E.checked_bytes(prior).decode('utf-8') for b in context['budget_evidence'])
        except UnicodeDecodeError as exc:
            require(failure==dict(type='UnicodeDecodeError',message_sha256=E.sha(str(exc).encode()),automatic_replay=False),'binary validation exception identity changed')
        else:require(False,'retained budgets do not reproduce the sealed predispatch error')
        absent(request,worker.campaign)
        proof=prefix(before,selection,worker.context,stop='disclosure')
        directory=E.safe(directory);require(not directory.exists(),'validator recovery namespace exists')
        W.keep(marker,dict(schema_version=SCHEMA,directory=str(directory),prior_revision=prior))
        report=W.keep(directory/'recovery.json',dict(schema_version=SCHEMA,prior_revision=prior,
            old_snapshot=old_snapshot,changes=changed,request=request_binding,context=request.context,
            exception=exception,failed_resource=failed_resource,failed_intent=record(step/'intent.json'),
            current_code=W.code(worker),worker_run=str(worker.run),tracker=W.snapshot(directory/'tracker.json',tracker),
            lease=asdict(lease),proof=proof,attempt_id=request.prepared.attempt_id,ordinal=1,limit=1,global_revision=2,
            executor_scope_absent=True,semantic_budget_reset=False))
        successor=dict(old,current_code=W.code(worker),selection=codec.encode(selection),lease=asdict(lease),
            validation_recovery=report,request_revision=prior)
        permit=W.keep(directory/'permit.json',successor)
        W.keep(directory/'complete.json',permit)
        for name in ('request.json','manifest.json','instruction.snapshot.md'):W.snapshot(directory/'steps/disclosure-review'/name,record(step/name))
        newworker=replace(worker,disclosure_revision=permit)
        validate(newworker,before)
        task=W.task(newworker,selection,tracker);W.producer.validate_request(task,request,'disclosure-review')
        absent(request,worker.campaign)
        return permit


def receipt(binding,*,_historical_root=None):
    p=read(binding);q=read(p['validation_recovery']);run=Path(q['worker_run'])
    require(q['schema_version']==SCHEMA and read(record(run/'disclosure-validator-recovery.json'))==dict(schema_version=SCHEMA,directory=str(Path(binding['path']).parent),prior_revision=q['prior_revision']), 'validator recovery reservation changed')
    require(read(record(Path(binding['path']).parent/'complete.json'))==binding,'validator successor binding changed')
    old=original(q['prior_revision'])
    require(p==dict(old,current_code=q['current_code'],selection=old['selection'],lease=q['lease'],validation_recovery=p['validation_recovery'],request_revision=q['prior_revision']), 'successor changed revision beyond validator authority')
    require(q['ordinal']==q['limit']==1 and q['global_revision']==2 and q['semantic_budget_reset'] is False,'validator recovery budget changed')
    root=Path(__file__).resolve().parents[2] if _historical_root is None else Path(_historical_root)
    implementation_path=root/SELF
    require(q['current_code'].get(str(implementation_path.resolve()))==record(implementation_path)['sha256'],'validator recovery implementation differs')
    require(implementation(q['old_snapshot'],root,old)==q['changes'],'validator recovery code profile changed')
    for key in ('request','context','exception','failed_resource','failed_intent','tracker'):E.checked_bytes(q[key])
    from scripts.batch import harbor_instruction_worker as W
    request=W.request_saved(Path(q['request']['path']))
    require(request.prepared.attempt_id==q['attempt_id'] and request.context==q['context'],'prepared request or UUID changed')
    return p,q,old,request


def validate_historical_integrity(worker,before,old_snapshot):
    """Validate immutable completed validator recovery in an explicitly pinned root."""
    from scripts.batch.harbor_instruction_lease_renewal import _historical_inventory
    expected=_historical_inventory(worker,old_snapshot)
    p,q,old,request=receipt(worker.disclosure_revision,_historical_root=worker.root)
    require(p['current_code']==expected and q['worker_run']==str(worker.run)
            and p['original']==before['bindings']['original_raw_source']
            and p['predecessor']==worker.disclosure_repair,'foreign historical validator recovery')
    return p


def validate(worker,before):
    from scripts.batch import harbor_instruction_worker as W
    p,q,old,request=receipt(worker.disclosure_revision)
    require(q['worker_run']==str(worker.run) and p['original']==before['bindings']['original_raw_source']
            and p['current_code']==W.code(worker) and p['predecessor']==worker.disclosure_repair,'foreign validator continuation')
    R.validate_renewal_links(worker,before,p)
    return p


def validate_task(task):
    p,q,old,request=receipt(task.disclosure_revision)
    require(task.run==Path(p['worker_run'])/'producer' and task.stage==codec.decode(p['selection']).content.stage,'validator task scope changed')
    require(record(task.stage/'instruction.md')['sha256']==p['instruction']['sha256'] and record(task.stage/'tests/disclosure.json')['sha256']==p['disclosure']['sha256'],'prepared task content changed')
    return p


def validate_context(task,request):
    p,q,old,saved=receipt(task.disclosure_revision)
    require(request==saved,'validator recovery substituted original request')
    context=read(request.context);snapshot=read(q['old_snapshot']);root=Path(snapshot['root']);files={r['relative']:r['sha256'] for r in snapshot['files']}
    for b in context['code']:
        path=Path(b['path']);require(path.is_relative_to(root) and files.get(str(path.relative_to(root)))==b['sha256'],'prepared context not from pinned original code');E.checked_bytes(b)
    from scripts.batch import harbor_instruction_producer as P
    current=P.code_evidence(task)
    require(len(current)==len(context['code']),'validator context code inventory changed')
    for oldcode,new in zip(context['code'],current):
        relative=str(Path(oldcode['path']).relative_to(root))
        require(Path(new['path'])==task.root/relative,'validator context code role changed')
        if relative!=PFILE:require(oldcode['sha256']==new['sha256'],'non-producer context code changed')
    return context


def review_history(binding,call,manifest,instruction,disclosure):
    p,q,old,request=completed_receipt(binding)
    require(call.request['sha256']==request.prepared.request['sha256'] and read(call.submitted)['attempt_id']==request.prepared.attempt_id,'review differs from unchanged prepared request')
    result=R.validate_review_history(q['prior_revision'],call,manifest,instruction,disclosure)
    return {**result,'disclosure_revision':binding,'validator_recovery':p['validation_recovery']}


def completed_receipt(binding):
    """Completed review provenance remains tied to its actual immutable code.

    This reader does not authorize preparation or dispatch on historical code.
    The caller still validates the completed call, scope, UUID and all content.
    """
    saved=read(read(binding)['validation_recovery'])
    matches=[Path(path) for path in saved['current_code'] if path.endswith('/'+SELF)]
    require(len(matches)==1,'completed validator implementation is ambiguous')
    path=matches[0];require(record(path)['sha256']==saved['current_code'][str(path)],
                            'completed validator implementation changed')
    return receipt(binding,_historical_root=path.parents[2])

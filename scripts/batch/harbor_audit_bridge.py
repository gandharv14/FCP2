"""One-process fixed auditor transport bridge; unknown completion never advances."""
from dataclasses import asdict,replace
from pathlib import Path
import json
import threading
import uuid
import xl_variable_source_audit as audit
from scripts.batch import harbor_model_executor as executor
from scripts.batch.harbor_audit_protocol import AuditRequest,validate
from scripts.batch.harbor_admission import read,record,require
from scripts.batch.harbor_model_review_provenance import validate_call

class AuditTransportStop(RuntimeError):
    """Nonretryable worker stop, deliberately outside auditor semantic handlers."""

_BUSY=threading.Lock()


def keep(path,value):
    raw=value if isinstance(value,bytes) else executor.canonical(value)
    if path.exists():require(path.read_bytes()==raw,'audit bridge checkpoint changed')
    else:executor.retain(path,raw)
    return record(path)


def context_from(value):
    return AuditRequest(**{**value,'previous':tuple(value['previous'])})


def prepared_from(value):
    from scripts.batch.harbor_model_review_provenance import Policy
    return executor.PreparedCall(value['request'],Policy(**value['policy']),executor.SemanticScope(**value['scope']),value['ordinal'],value['limit'],value['attempt_id'],tuple(value['budget_evidence']),value['response_mode'],context_from(value['audit_context']))


def validate_completed(root,target,plan,policy,campaign):
    """All owning table/coverage/cache checks plus exact actual call projection."""
    pointer=root/'renderer-recovery.json'
    if pointer.exists():
        from xl_audit_renderer_recovery import validate_installed
        return validate_installed(record(pointer),root=root,target=target,plan=plan,policy=policy,campaign=campaign)
    selected=read(plan);wb=read(selected['prepared'])['validated']['workbook'];stem=wb+'-inputs-variable-sources'
    result=audit.validate_saved_audit_coverage(target/(stem+'.metadata.json'),target/(stem+'.inventory.json'),target/(stem+'.md'),model=policy.model,endpoint=policy.endpoint.removesuffix('/chat/completions'),project_id=policy.project_id)
    meta=read(record(target/(stem+'.metadata.json')));scope=target/'.audit-attempts'/audit._canonical_hash(meta['attempt_scope'])
    attempts=sorted(scope.glob('batch-*/*.request.json'));bindings=[]
    for path in attempts:
        saved=root/(path.parent.name+'-'+path.stem.split('.')[0])/'prepared.json';prepared=prepared_from(read(record(saved)))
        require(prepared.audit_context.attempt==record(path) and prepared.audit_context.root_plan==plan,'bridge does not bind owning attempt')
        response=read(record(path.with_name(path.name.replace('.request.','.response.'))))
        observed=executor.inspect(prepared,campaign)
        require(observed.status=='completed' and observed.call is not None,'audit transport remains unresolved')
        actual=validate_call(observed.call,read(prepared.request),policy=prepared.policy,json_response=False,audit_context=prepared.audit_context)
        require(response==dict(request_sha256=record(path)['sha256'],content=actual['review'].strip(),finish_reason='stop'),'auditor response differs from exact retained transport')
        bindings.extend([record(saved),observed.outcome])
    require(len(list(root.glob('batch-*/prepared.json')))==len(attempts),'extra or omitted auditor transport calls')
    return dict(coverage=result,calls=bindings,bindings={key:record(target/(stem+suffix)) for key,suffix in [('variable_source_metadata','.metadata.json'),('variable_source_inventory','.inventory.json'),('variable_source_audit','.md')]})


def run(plan,*,root,campaign,policy,credential):
    """Only a freshly root-revalidated bootstrap plan may enter this process.

    A retained start never creates another attempt scope. A completed audit may
    be inspected without a call; every other interrupted state requires root
    reconciliation, even if some individual responses completed successfully.
    """
    root=executor.safe(root);p=read(plan);saved=read(p['prepared']);wb=saved['validated']['workbook'];b=read(p['baseline'])['bindings'];sdir=Path(b['source_generation']['path']);seg=Path(b['segmentation_generation']['path'])
    bootstrap=Path(p['prepared']['path']).parent;target=bootstrap/'audit'/(wb+'-variable-sources')
    descriptor=dict(plan=plan,policy=asdict(policy),campaign={**asdict(campaign),'root':str(campaign.root),'slot_root':str(campaign.slot_root)},code=record(Path(__file__)))
    if root.exists():
        require(read(record(root/'intent.json'))==descriptor,'audit bridge selection changed; no reset')
        try:return validate_completed(root,target,plan,policy,campaign)
        except (ValueError,OSError,KeyError) as exc:raise AuditTransportStop('retained audit incomplete; automatic reentry prohibited') from exc
    require(not target.exists(),'existing auditor scope must not be relocated or reset')
    require(_BUSY.acquire(blocking=False),'auditor bridge already active in this process')
    original=audit._call_model
    try:
        root.mkdir(parents=True,mode=0o700);keep(root/'intent.json',descriptor)
    except BaseException:
        _BUSY.release();raise
    def transport(endpoint,api_key,model,project_id,messages):
        require((endpoint.rstrip('/')+'/chat/completions',model,project_id)==(policy.endpoint,policy.model,policy.project_id),'auditor dispatch configuration differs')
        inventory=record(target/(wb+'-inputs-variable-sources.inventory.json'));inv=read(inventory)
        scope=audit._attempt_scope_config(audit._canonical_hash(inv),endpoint,model,project_id);directory=target/'.audit-attempts'/audit._canonical_hash(scope)
        pending=[path for path in sorted(directory.glob('batch-*/*.request.json')) if not path.with_name(path.name.replace('.request.','.response.')).exists()]
        require(len(pending)==1,'exactly one persisted pending auditor request required');attempt=pending[0];value=read(record(attempt));require(value['messages']==messages,'actual auditor messages differ from persisted request')
        work=root/(attempt.parent.name+'-'+attempt.stem.split('.')[0]);work.mkdir()
        policy_snapshot=keep(work/'policy.snapshot.json',(directory/'policy.json').read_bytes())
        previous=tuple(dict(request=record(attempt.parent/f'{n:02d}.request.json'),response=record(attempt.parent/f'{n:02d}.response.json')) for n in range(1,value['attempt']))
        context=AuditRequest(record(attempt),policy_snapshot,inventory,plan,previous)
        audit_policy=replace(policy,reasoning_effort=None)
        # Preserve the auditor's json.dumps wire representation exactly.
        payload={'model':model,'messages':messages};request=keep(work/'request.json',json.dumps(payload).encode('utf-8'));checked=validate(context,payload,audit_policy)
        prepared=executor.PreparedCall(request,audit_policy,executor.SemanticScope(wb,b['original_raw_source']['sha256'],'harbor-research-auditor','audit-'+checked['scope_hash']+'-'+str(checked['batch'])),checked['ordinal'],checked['limit'],str(uuid.uuid4()),(plan,policy_snapshot,record(attempt)),response_mode='text',audit_context=context)
        keep(work/'prepared.json',asdict(prepared));result=executor.execute(prepared,campaign,credential=credential)
        keep(work/'outcome.json',dict(status=result.status,outcome=result.outcome))
        if result.status!='completed':raise AuditTransportStop('audit transport '+result.status+'; no semantic advance or automatic retry')
        proof=validate_call(result.call,payload,policy=audit_policy,json_response=False,audit_context=context)
        return proof['review'].strip(),'stop'
    try:
        audit._call_model=transport
        resolved,meta=audit.resolve_segmentation_directory(seg.parents[2],wb,segmentation_generation_id=seg.name,source_generation_root=sdir.parents[2],source_generation_id=sdir.name)
        audit.generate_audit(wb,b['baseline_inputs']['path'],resolved,target,credential,model=policy.model,endpoint=policy.endpoint.removesuffix('/chat/completions'),project_id=policy.project_id,semantic_attempt_limit=2,inherited_budget=p['inherited_budget'],segmentation=meta)
        result=validate_completed(root,target,plan,policy,campaign);keep(root/'complete.json',result);return result
    finally:
        audit._call_model=original;_BUSY.release()

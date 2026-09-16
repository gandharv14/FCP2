"""Exact owning-auditor request projection, distinct from stateless reviews."""
from dataclasses import dataclass
from pathlib import Path
import json
import xl_variable_source_audit as audit
from scripts.batch.harbor_admission import read,require
from scripts.batch.harbor_tracker_lifecycle import checked_record

@dataclass(frozen=True)
class AuditRequest:
    attempt: dict
    policy_snapshot: dict
    inventory: dict
    root_plan: dict
    previous: tuple  # Exact request/response pairs, never synthesized old calls.


def validate(context,request,policy,*,original_renderer_code=None):
    require(isinstance(context,AuditRequest),'typed owning audit context required')
    attempt=read(context.attempt);saved=read(context.policy_snapshot);inventory=read(context.inventory);plan=read(context.root_plan)
    if original_renderer_code is None:
        generator_sha=audit.hashlib.sha256(Path(audit.__file__).read_bytes()).hexdigest()
        checked_record(plan['entry'])
    else:
        from xl_audit_renderer_recovery import code_identity
        identity=code_identity(original_renderer_code);generator_sha=identity['original_generator']['sha256']
    require(plan['schema_version']=='harbor-bootstrap-audit-plan/v1' and plan['entry']['sha256']==generator_sha,'current or exactly reviewed original audit plan required')
    prepared=read(plan['prepared']);baseline=read(plan['baseline'])
    from scripts.batch import harbor_legacy_research_history as legacy
    selected=prepared.get('selection',{}).get('policy',{})
    from types import SimpleNamespace
    old=legacy.selected(SimpleNamespace(campaign_evidence=selected.get('campaign_evidence',[])),inventory['metadata']['workbook'])
    if old is not None:
        require(plan.get('legacy_research')==legacy.budget(old,plan['history'],inventory,workbook=inventory['metadata']['workbook'],inherited_budget=plan['inherited_budget']),'legacy research consumption or additional allowance differs')
    else:require('legacy_research'not in plan,'unselected legacy research budget')
    require(prepared['validated']['workbook']==inventory['metadata']['workbook'],'root audit workbook differs')
    checked_record(baseline['bindings']['original_raw_source'])
    # Root's plan uses its pretty canonical digest; the owning auditor uses its
    # existing compact inventory digest. Neither representation is rewritten.
    pretty=(json.dumps(inventory,indent=2,sort_keys=True,allow_nan=False)+'\n').encode()
    require(plan['inventory_sha256']==audit.hashlib.sha256(pretty).hexdigest(),'root inventory differs')
    require(policy.reasoning_effort is None,'auditor does not submit explicit reasoning effort')
    require(set(request)=={'model','messages'} and request['model']==policy.model,'auditor payload fields changed')
    endpoint=policy.endpoint.removesuffix('/chat/completions')
    scope=audit._attempt_scope_config(audit._canonical_hash(inventory),endpoint,policy.model,policy.project_id)
    scope['generator_sha256']=generator_sha
    require(set(attempt)=={'endpoint','model','project_id','scope','batch','attempt','messages'} and attempt['scope']==scope,'auditor scope or fields changed')
    require((attempt['endpoint'],attempt['model'],attempt['project_id'])==(endpoint,policy.model,policy.project_id),'auditor destination differs')
    require(saved['schema_version']=='audit-attempt-policy/v1' and saved['scope']==scope and type(saved['limit']) is int and saved['limit'] in (1,2),'owning audit policy differs')
    require(saved.get('inherited_budget')==plan['inherited_budget'],'inherited budget omitted or changed')
    counts=audit._inherited_batch_counts(saved['inherited_budget'],inventory)
    require({str(k):v for k,v in counts.items()}=={str(k):v for k,v in plan['batch_consumed'].items()},'inherited batch consumption changed')
    index=attempt['batch'];ordinal=attempt['attempt'];chunks=audit._split_chunks(inventory)
    require(type(index) is int and 1<=index<=len(chunks) and type(ordinal) is int and 1<=ordinal<=min(saved['limit'],2-counts[index]),'audit ordinal exceeds original budget')
    require(saved['submitted_counts'].get(str(index))==ordinal,'persisted submitted ordinal differs')
    path=Path(context.attempt['path'])
    require(path.name==f'{ordinal:02d}.request.json' and path.parent.name==f'batch-{index:04d}' and path.parent.parent.name==audit._canonical_hash(scope),'canonical audit attempt path differs')
    header,rows=chunks[index-1];messages=audit._base_messages(header,rows,index,len(chunks));by_id={json.loads(r)['row_id']:json.loads(r) for r in rows};all_ids={r['row_id'] for r in inventory['variables']}
    require(len(context.previous)==ordinal-1,'prior semantic history omitted')
    for n,pair in enumerate(context.previous,1):
        require(set(pair)=={'request','response'},'invalid prior audit pair');old=read(pair['request']);response=read(pair['response'])
        require(Path(pair['request']['path'])==path.parent/f'{n:02d}.request.json' and Path(pair['response']['path'])==path.parent/f'{n:02d}.response.json','prior audit path differs')
        require(old==dict(endpoint=endpoint,model=policy.model,project_id=policy.project_id,scope=scope,batch=index,attempt=n,messages=messages),'prior auditor context differs')
        require(response.get('request_sha256')==pair['request']['sha256'] and response.get('finish_reason')=='stop','prior response incomplete or changed')
        table=audit._clean_table(response['content']);violations=audit.validate_row_ids(table,set(by_id),all_ids)
        if not violations:violations=audit.validate_table(audit.resolve_table(table,by_id),rows,inventory['metadata'])
        require(bool(violations),'successful audit cannot consume a correction attempt')
        messages+=audit._correction_messages(response['content'],violations)
    require(attempt['messages']==messages and request['messages']==messages,'auditor base/correction messages changed')
    return dict(workbook=inventory['metadata']['workbook'],original_sha256=baseline['bindings']['original_raw_source']['sha256'],batch=index,ordinal=ordinal,limit=min(saved['limit'],2-counts[index]),scope_hash=audit._canonical_hash(scope),inherited=counts[index])

"""Read a retained successful lane CAS through its original owning implementation.

The original facade computes its gate and mutation in an isolated namespace.
Only its CAS dependency is replaced, by a comparator which cannot write files.
"""
import copy
import types
from dataclasses import asdict
from pathlib import Path

def validate(*,W,T,worker,before_binding,after_binding,completion,kind,notes_binding=None):
    before=W.read(before_binding);after=W.read(after_binding);done=W.read(completion)
    phase=kind+'-commit'
    W.require(kind in ('disclosure','naturalization','verification','dialogue'),'unsupported completed lane')
    W.require(Path(completion['path'])==W.step_directory(worker,phase)/'complete.json' and
              done['step']==phase and done['status']=='completed','exact completed owning commit required')
    intent=W.read(done['intent'])
    W.require(Path(done['intent']['path'])==Path(completion['path']).parent/'intent.json' and
              intent['step']==phase and intent['selection']==done['selection'] and
              intent['initial']==W.record(worker.run/'initial.json') and intent['code']==W.code(worker),
              'completed commit intent or owning code changed')
    for tracker in (before,after):
        T.schema.validate_tracker(tracker);T.life.validate_lifecycle(tracker)
    def lease(d,row):
        value=d['leases']['task_agent']
        return dict(owner=value['owner'],token=value['token'],revision=d['revision'],sha256=row['sha256'])
    W.require(intent['lease']==lease(before,before_binding) and done['lease']==lease(after,after_binding),
              'commit lease not bound to exact tracker snapshots')
    W.require(after['revision']==before['revision']+1 and after['handoffs']==before['handoffs'],
              'commit cannot add lease or authorization history')
    selected=W.codec.decode(done['selection'])
    facade=W.HarborLaneLifecycle(worker.context)
    # This proves a completed historical CAS. Synchronizing the live directory
    # here rejects an unrelated current transaction's own second proof. Use the
    # original synchronizer on the immutable predecessor, with explicitly bound
    # historical notes when the current notes have since advanced.
    historical_notes=before['agent_notes']['path']
    if historical_notes:
        notes_binding=notes_binding or dict(path=historical_notes,sha256=before['agent_notes']['sha256'])
        W.require(W.record(notes_binding['path'])==notes_binding and notes_binding['sha256']==before['agent_notes']['sha256'],
                  'historical commit notes differ')
    else:
        W.require(notes_binding is None,'unexpected historical commit notes')
    sync=facade.require_synchronized.__func__
    sync_namespace=dict(sync.__globals__)
    sync_namespace['Path']=lambda value:Path(notes_binding['path']) if historical_notes and str(value)==historical_notes else Path(value)
    sync_fn=types.FunctionType(sync.__code__,sync_namespace,sync.__name__,sync.__defaults__,sync.__closure__)
    sync_proxy=types.SimpleNamespace(_path=lambda wb:Path(before_binding['path']))
    def synchronized(wb):
        W.require(wb==before['workbook_id'],'foreign historical synchronization')
        return sync_fn(sync_proxy,wb)
    synchronized(before['workbook_id'])
    original=type(facade).complete
    namespace=dict(original.__globals__)
    schema=types.SimpleNamespace(**vars(namespace['schema']))
    observed=[]
    def compare(path,**kw):
        W.require(Path(path)==Path(before_binding['path']) and kw['expected_revision']==before['revision']
                  and kw['expected_sha256']==before_binding['sha256'] and
                  (kw['owner'],kw['token'],kw['scope'])==(intent['lease']['owner'],intent['lease']['token'],'workbook:'+before['workbook_id']),
                  'original commit CAS authority changed')
        result=copy.deepcopy(before);kw['mutate'](result)
        for key in before:
            W.require(result.get(key)==before.get(key) or key in kw['allowed_fields'],'unowned commit field')
        changed={k for k in before['bindings'].keys()|result['bindings'].keys() if before['bindings'].get(k)!=result['bindings'].get(k)}
        if changed:W.require(kw['evidence_validator'](copy.deepcopy(before),copy.deepcopy(result),frozenset(changed)) is True,'commit evidence validation failed')
        now=after['updated_at'];prior=before['leases']['task_agent']
        W.require(prior['heartbeat_at']<=now<prior['expires_at'],'commit outside original lease')
        result['revision']=before['revision']+1;result['updated_at']=now
        result['leases']['task_agent']['heartbeat_at']=now
        W.require(result==after and T.schema.sha(T.life.canonical(result))==after_binding['sha256'],
                  'tracker differs from exact original completed gate mutation')
        observed.append(True)
        return dict(revision=result['revision'],sha256=after_binding['sha256'],tracker=result)
    schema.cas_mutate=compare;namespace['schema']=schema
    fn=types.FunctionType(original.__code__,namespace,original.__name__,original.__defaults__,original.__closure__)
    proxy=types.SimpleNamespace(context=facade.context,_path=lambda wb:Path(before_binding['path']),
                              require_synchronized=synchronized)
    packet=W.CompletionEvidence(kind,selected)
    fn(proxy,before['workbook_id'],packet,expected_revision=before['revision'],expected_sha256=before_binding['sha256'],
       owner=intent['lease']['owner'],token=intent['lease']['token'])
    W.require(observed==[True],'exactly one read-only commit comparison required')
    return after

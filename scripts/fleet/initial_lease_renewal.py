"""Renew an unstarted instruction handoff through its original lease primitive.

This is an external continuation, not an instruction checkpoint rewrite. The
caller owns the original workbook lock and a guarded, exclusive root operation.
Failure after intent is reconciliation-only; this module never repeats a CAS.
"""
from dataclasses import asdict
from pathlib import Path
import os


def require(ok, message):
    if not ok: raise ValueError(message)


def context(base, C, W, initial, held, controls):
    first=base.read(initial); stopped=base.read(held)
    require(stopped['schema_version']==first['schema_version']==C.SCHEMA
            and stopped['status']=='LEASE_RENEWAL_REQUIRED' and first['status']=='INSTRUCTION_READY'
            and first['checkpoint'] is stopped['checkpoint'] is None
            and stopped['evidence']==[initial] and first['next_step']==stopped['next_step']=='disclosure-build'
            and all(stopped[k]==first[k] for k in ('descriptor','selection','lease')),
            'exact unstarted initial renewal boundary required')
    worker, expected, recipe=C.selected(first['descriptor'])
    require(expected==controls and not worker.run.exists(), 'initial worker already entered or controls differ')
    prior,selection,lease=C._prior(initial,first['descriptor'])
    require(prior==first, 'initial handoff changed')
    _,before,tracker=W.active(worker,selection,lease)
    require(before['revision']==lease.revision and tracker['sha256']==lease.sha256,
            'initial lease is not the exact current native revision')
    descriptor=base.read(first['descriptor'])
    environment=first['evidence'][0]
    require(environment==descriptor['origin'][0], 'original environment descriptor detached')
    source,_,source_controls=base.selected(environment)
    require(source_controls==controls and source.bootstrap.workbook==selection.content.workbook,
            'initial environment source or controls differ')
    require(source.bootstrap.policy.tracker_root==worker.context.tracker_root
            and source.campaign==worker.campaign, 'initial campaign or tracker differs')
    C.check_completed_gate(before,C.CompletionEvidence('environment',selection),worker.context)
    require(W.active(worker,selection,lease)[1]==before and not worker.run.exists(),
            'initial state changed during original environment validation')
    return first,worker,recipe,selection,lease,before,tracker,descriptor,source


def renew(base,C,W,*,initial,held,controls,directory,authority,custody):
    """Caller holds guards/locks; fresh intent precedes the original _lease CAS."""
    require(os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY')!='1','read-only cannot renew')
    directory=Path(directory)
    require(not directory.exists(), 'initial renewal already attempted; reconcile without replay')
    require(base.SECONDS==3300 and base.MEMORY==8589934592 and base.DURATION==3600*10**9,
            'original stage/lease limits differ')
    custody()
    first,w,recipe,selection,lease,before,tracker,old,source=context(base,C,W,initial,held,controls)
    require(before['leases']['task_agent']['expires_at']-base.time.time_ns()<=(base.SECONDS+30)*10**9,
            'initial handoff already has sufficient native runway')
    directory.mkdir()
    intent=base.keep(directory/'intent.json',dict(schema_version='fleet-initial-lease-continuation/v1',
        authority=authority,initial=initial,held=held,tracker=tracker,lease=base.encode(lease),
        controls=base.encode(controls),duration_ns=base.DURATION,semantic_budget_reset=False,automatic_retry=False))
    # _lease rechecks the native owner/token/revision/hash and writes its own
    # immutable lease-renewal.json after the original lifecycle CAS.
    custody(); renewed=base._lease(source,lease,directory)
    require(renewed.owner==lease.owner and renewed.token==lease.token and renewed.revision==lease.revision+1,
            'renewal changed identity or did not consume exactly one lifecycle event')
    receipt=base.record(directory/'lease-renewal.json'); saved=base.read(receipt)
    require(saved['previous']==base.encode(lease) and saved['lease']==base.encode(renewed),
            'original renewal receipt differs')
    _,after,current=W.active(w,selection,renewed)
    require(current==saved['tracker'] and not w.run.exists(), 'renewed initial state changed')
    require(after['lane_state']==before['lane_state'] and after['bindings']==before['bindings'],
            'renewal changed semantic work')
    provenance=[*old['origin'],initial,held,intent,receipt]
    descriptor=base.keep(directory/'descriptor.json',dict(old,origin=provenance,
        initial=dict(selection=C.codec.encode(selection),lease=base.encode(renewed))))
    result=base.keep(directory/'result.json',C._result('INSTRUCTION_READY',descriptor,selection,renewed,
        checkpoint=None,next_step='disclosure-build',evidence=[*first['evidence'],initial,held,intent,receipt]))
    observed,s,l=C._prior(result,descriptor)
    require(observed['checkpoint'] is None and s==selection and l==renewed, 'original initial reader rejected renewal')
    return result

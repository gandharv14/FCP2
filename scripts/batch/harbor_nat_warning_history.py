"""Authenticate completed NAT retry before a first dialogue style warning.

The old code, retry CAS, both NAT attempts and actual dialogue calls remain
immutable. This reader permits no review, retry or numerical-policy change.
"""
from dataclasses import replace
from pathlib import Path
from scripts.batch.harbor_admission import read,record,require

def eligible(worker):
    return worker.retry is not None and all(getattr(worker,k,None)is None for k in (
        'dialogue_retry','boundary_recovery','dialogue_semantic_retry','pretransport_recovery',
        'code_repair','disclosure_repair','disclosure_revision','dialogue_pre_review_repair','lease_renewal'))

def history(worker,before,snapshot):
    from scripts.batch import harbor_dialogue_first_round_warning as F
    from scripts.batch import harbor_dialogue_warning_requalification as Q
    from scripts.batch import harbor_instruction_worker as W,harbor_nat_retry as N
    from scripts.batch import harbor_dialogue_semantic_retry as S
    require(eligible(worker),'NAT warning cannot combine unrelated continuation histories')
    old=read(snapshot);root=Path(old['root']);expected={r['relative']:r['sha256']for r in old['files']}
    require(len(expected)==len(old['files'])and F.inventory(root)==expected,'original NAT snapshot changed')
    changes=Q.changes(snapshot,worker.root)
    prior=replace(worker,root=root,warning_requalification=None)
    permit=N.validate_historical_integrity(prior,before,snapshot);retained=S._history(prior,before)
    code=permit['code'];initial=record(prior.run/'initial.json')
    require(read(initial)['code']==code and permit['initial']==initial,'NAT original initial identity changed')
    completed=[]
    for phase in W.STEPS[:W.STEPS.index('dialogue-review')]:
        directory=W.step_directory(prior,phase);row=record(directory/'complete.json');v=read(row);intent=read(v['intent'])
        require(v['status']=='completed'and v['step']==phase and intent['initial']==initial
                and intent['step']==phase and intent['code']==code,'NAT completed prefix changed')
        completed.extend([row,v['intent']])
        request=directory/'request.json'
        if request.exists():
            req=W.request_saved(request);observed=W.executor.inspect(req.prepared,prior.campaign)
            require(observed.status=='completed'and observed.call is not None,'NAT completed model lacks actual Call')
            completed.append(record(request))
    directory=W.step_directory(prior,'dialogue-review');blocked=record(directory/'blocked.json');failed=read(blocked)
    require(failed['status']=='DIALOGUE_REPAIR_REQUIRED'and failed['step']=='dialogue-review'
            and not(directory/'complete.json').exists()and not(directory/'exception.json').exists(),
            'NAT first dialogue review must be settled and rejected')
    intent=read(failed['intent']);require(intent['code']==code and intent['initial']==initial,'NAT review code differs')
    for phase in W.STEPS[W.STEPS.index('dialogue-review')+1:]:
        require(not W.step_directory(prior,phase).exists(),'NAT original later phase already exists')
    return dict(schema_version='harbor-nat-first-dialogue-warning-history/v1',retry=worker.retry,
        snapshot=snapshot,code=code,initial=initial,blocked=blocked,completed=completed,
        retained=retained,code_changes=changes,original=before['bindings']['original_raw_source'])

def validate(worker,before,warning):
    require(warning.get('nat_first_round')is not None and warning['round']==1
        and warning['semantic']is None and warning.get('code_repair')is None
        and warning['model_calls']==0 and warning['semantic_budget_reset']is False
        and warning.get('prior_continuations')=={'retry':worker.retry},'NAT warning changed consumed history')
    proof=history(worker,before,warning['old_snapshot'])
    require(proof==warning['nat_first_round'],'NAT warning retained evidence differs')
    return proof

def retained_retry(worker,before):
    warning=read(worker.warning_requalification)
    validate(worker,before,warning)
    from scripts.batch import harbor_nat_retry as N
    return N.validate_historical_integrity(replace(worker,root=Path(read(warning['old_snapshot'])['root']),warning_requalification=None),before,warning['old_snapshot'])

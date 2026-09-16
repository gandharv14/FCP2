"""Read the exact renewed, rejected first-round 0190 instruction history.

The returned evidence is not a permit. Original renewal/CAS validation and
every owning completed request remain required; no old execution is enabled.
"""
from pathlib import Path
from dataclasses import replace
import hashlib

ROOT_NAME='c7ad14b03f7c0ec3c6364efbb5e388affd9036ac15d745131ea306602700808f'
RUN_SUFFIX='diagnostics/authoring-queue-extension-20260911T185027Z/authoring/0190-generated-02/instruction'
SOURCE='c6e9acb47959d5bcd2c6abac3f3111932a6e305025f59fe1881a03fe9f4292da'


def validate_retained(worker, before, snapshot):
    from scripts.batch.harbor_admission import read, record, require
    from scripts.batch import harbor_instruction_worker as W
    from scripts.batch import harbor_instruction_lease_renewal as R
    from scripts.batch import harbor_transition_codec as C
    old=read(snapshot);root=Path(old['root'])
    require(root.name==ROOT_NAME and str(worker.run).endswith('/'+RUN_SUFFIX)
            and before['workbook_id']=='0190'
            and before['bindings']['original_raw_source']['sha256']==SOURCE,
            '0190 historical warning identity differs')
    require(worker.lease_renewal is not None and all(getattr(worker,n,None) is None
            for n in R.CONTINUATIONS if n!='lease_renewal'),
            '0190 requires only the actual retained renewal')
    historical=replace(worker,root=root)
    code=R._historical_inventory(historical,snapshot)
    # This checks the original lease permit, one-CAS renewal, exact historical
    # code and checkpoint, retained archives, duration, and current ancestry.
    renewal=R.validate_historical_integrity(historical,before,snapshot)
    require(renewal['semantic_budget_reset'] is False
            and set(renewal['prior_continuations'])<={'lease_renewal'},
            '0190 renewal borrowed a specialized continuation')
    initial=record(worker.run/'initial.json');saved=read(initial)
    selected=C.decode(saved['selection'])
    require(saved['code']==code and selected.content.workbook=='0190'
            and selected.content.bindings['original_raw_source']==before['bindings']['original_raw_source'],
            '0190 initial source/code differs')
    completed=[];calls=[];steps=worker.run/'steps'
    for phase in W.STEPS[:12]:
        bound=record(steps/phase/'complete.json');item=read(bound);intent=read(item['intent'])
        require(item['status']=='completed' and item['step']==phase
                and intent['step']==phase and intent['initial']==initial and intent['code']==code,
                '0190 completed prefix/code differs')
        completed.extend([bound,item['intent']])
    blocked=record(steps/'dialogue-review/blocked.json');item=read(blocked);intent=read(item['intent'])
    require(item['status']=='DIALOGUE_REPAIR_REQUIRED' and item['step']=='dialogue-review'
            and intent['initial']==initial and intent['code']==code,
            '0190 exact rejected first review differs')
    require(not (steps/'dialogue-review/complete.json').exists()
            and not (steps/'dialogue-review/exception.json').exists(),
            '0190 rejected review has another outcome')
    for phase in W.STEPS[13:]:
        require(not (steps/phase).exists(), '0190 first-round history has a later step')
    for phase in W.STEPS[:13]:
        path=steps/phase/'request.json'
        if not path.exists():continue
        request=W.request_saved(path);observed=W.executor.inspect(request.prepared,worker.campaign)
        require(observed.status=='completed' and observed.call is not None,
                '0190 historical request lacks actual completed Call')
        if phase.startswith('dialogue-'):
            require(request.prepared.ordinal==1 and request.prepared.limit==2,
                    '0190 first dialogue budget differs')
        from scripts.batch.harbor_complete_readiness import evidence_records
        calls.append(dict(step=phase,request=record(path),ordinal=request.prepared.ordinal,
                          limit=request.prepared.limit,records=evidence_records(observed.call)))
    require({x['step'] for x in calls}>={'dialogue-writer','dialogue-paraphraser','dialogue-review'},
            '0190 actual author/reviewer Calls missing')
    records=[]
    for p in sorted(steps.rglob('*')):
        require(not p.is_symlink(),'0190 historical steps contain symlink')
        if p.is_file():records.append(record(p))
    return dict(schema_version='fleet-0190-renewed-first-round-history/v1',
                workbook='0190',snapshot=snapshot,code=code,initial=initial,
                source=before['bindings']['original_raw_source'],
                renewal=worker.lease_renewal,renewal_permit=read(worker.lease_renewal)['permit'],
                blocked=blocked,completed=completed,calls=calls,records=records,
                review_round=1,review_limit=2,models_dispatched=0,
                semantic_budget_reset=False,continuation_authorized=False)

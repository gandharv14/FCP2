"""Once-only deterministic pre-review reservation; no lease or model operation.

The selected original W.active and original R/T history authenticate custody.
Failed/uncertain reservations remain consumed and are never replayed here.
"""
from dataclasses import asdict, replace
from pathlib import Path
import fcntl, os
from scripts.fleet import criterion_pre_review as core
from scripts.fleet.source_card_profile import binding, checked, validate_lease
from scripts.fleet.criterion_pre_review_reader import PERMIT_SCHEMA


def proposal(reader, *, live_custody):
    live_custody();p=reader.plan;W=reader.W;old=reader.old_worker()
    failure=checked(p['blocked']);selection=reader.codec.decode(failure['selection'])
    from scripts.batch.harbor_workbook_bootstrap import Lease
    lease=Lease(**p['lease_boundary']['lease'])
    _,before,tracker=W.active(old,selection,lease)
    validate_lease(p,old,before,lease,renewal=reader.R,takeover=reader.T,exact_boundary=True)
    core.require(p['blocked']==binding(W.step_directory(old,'dialogue-paraphraser')/'blocked.json') and
                 failure['status']=='DIALOGUE_REPAIR_REQUIRED' and failure['step']=='dialogue-paraphraser',
                 'exact original mechanically blocked paraphraser required')
    core.unspent(old,W)
    intent=checked(failure['intent']);inventory=reader.R._historical_inventory(old,p['snapshot'])
    core.require(intent['code']==inventory and intent['selection']==failure['selection'] and
                 intent['initial']==binding(old.run/'initial.json'),'blocked original implementation differs')
    claims,draft,phrases,report=reader.reproduce();calls,authors=reader.authors()
    pair=checked(p['original_pair'])
    requests=[W.request_saved(Path(row['path'])) for row in pair['author_requests']]
    from scripts.batch.harbor_instruction_repair import prefix
    proof=prefix(before,selection,old.context,stop='dialogue')
    chosen=replace(old,dialogue_pre_review_repair=reader.authority)
    selected=replace(selection,content=replace(selection.content,dialogue_claims=p['corrected_claims']))
    directory=Path(p['directory'])
    core.require(not directory.exists() and not (old.run/'dialogue-criterion-reservation.json').exists() and
                 not (old.run/'dialogue-pre-review-repair.json').exists(),
                 'criterion reservation already attempted; reconcile without replay')
    history=[p['blocked'],failure['intent'],pair['original_claims'],pair['old_draft'],pair['old_check'],
             *pair['author_requests'],p['projection'],p['original_pair'],p['mechanical_proof']]
    for req in requests:
        context=checked(req.context);core.require(context['request']==req.prepared.request and
            context['budget_evidence']==list(req.prepared.budget_evidence),'original author context differs')
        history.extend([req.context,*context['code'],*context['evidence'],*context['budget_evidence']])
    for call in calls:history.extend(row for row in asdict(call).values() if row is not None)
    for row in history:core.require(binding(row['path'])==row,'criterion source/call history changed')
    live_custody();core.require(W.active(old,selection,lease)[1]==before,'criterion tracker changed during proof')
    permit=dict(schema_version=PERMIT_SCHEMA,correction_kind=core.SCHEMA,profile=reader.profile_binding,
        phase='dialogue-review',round=1,semantic_budget_reset=False,model_calls_added=0,
        sentence_replacements=len(phrases),worker_run=str(old.run),initial=binding(old.run/'initial.json'),
        reservation=None,old_descriptor=p['old_descriptor'],old_snapshot=p['snapshot'],blocked=p['blocked'],
        predecessor=p['blocked'],old_selection=failure['selection'],selection=reader.codec.encode(selected),
        lease=asdict(lease),tracker=tracker,prior_continuations={'lease_renewal':old.lease_renewal},
        source=p['published_source'],original=p['source'],original_claims=pair['original_claims'],
        claims=p['corrected_claims'],old_draft=pair['old_draft'],old_check=pair['old_check'],
        corrected_draft=None,sentence_proof=phrases,dialogue_directory=str(directory/'producer/dialogue'),
        author_calls=[asdict(c)for c in calls],author_requests=pair['author_requests'],
        policy=asdict(requests[0].prepared.policy),current_code=W.code(chosen),history=history,
        prefix=proof,source_proof=p['projection'])
    return permit,claims,draft,report


def authorize(reader, *, live_custody):
    core.require(os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY')!='1',
                 'read-only diagnostic cannot reserve a criterion correction')
    old=reader.old_worker();W=reader.W;p=reader.plan
    with (old.run/'worker.lock').open('a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        permit,claims,draft,report=proposal(reader,live_custody=live_custody)
        # The permanent reservation precedes all new derivative files.
        reserved=W.keep(old.run/'dialogue-criterion-reservation.json',dict(schema_version=core.SCHEMA,
            profile=reader.profile_binding,directory=p['directory'],blocked=p['blocked'],budget=p['budget']))
        directory=Path(p['directory']);directory.mkdir();target=directory/'producer/dialogue';target.mkdir(parents=True)
        composer=reader.P.skill_module(old.root,'additional-assumptions-dialogue','compose_draft')
        template,slots=composer.compose(claims,claims['writer_pack'])
        for name,value in [('claims.json',claims),('writer_pack.json',claims['writer_pack']),
                           ('draft_template.md',template.encode()),('slots.json',slots)]:W.keep(target/name,value)
        (target/'round-01').mkdir();fixed=W.keep(target/'round-01/draft.md',draft.encode())
        W.keep(target/'round-01/draft-check.json',report)
        copied=[W.snapshot(directory/'history'/('%05d.blob'%i),row)for i,row in enumerate(permit['history'])]
        permit.update(reservation=reserved,corrected_draft=fixed,history=copied,
                      tracker=W.snapshot(directory/'tracker.before.json',permit['tracker']))
        result=W.keep(directory/'permit.json',permit)
        core.require(result['path']==reader.authority['path'],'criterion authority left selected namespace')
        W.keep(old.run/'dialogue-pre-review-repair.json',result)
        return result

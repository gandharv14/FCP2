"""Current warning-policy requalification of an exact completed review.

No model dispatch, new semantic round, review rewrite, or publication. Root must
supply a current runtime/prefix and frozen previous snapshot. Original receipts
remain immutable; a separate review step applies the exact reviewed draft.
"""
from dataclasses import asdict, replace
from pathlib import Path
import copy
import fcntl

from scripts.batch.harbor_admission import read, record, require
from scripts.batch import harbor_transition_codec as codec
from scripts.batch import harbor_instruction_producer as producer
from scripts.batch import harbor_dialogue_semantic_retry as semantic
from scripts.batch import harbor_instruction_repair as repair
from scripts.batch import harbor_tracker_lifecycle as life
from scripts.batch.harbor_semantic_readiness import dialogue_review

SCHEMA='harbor-dialogue-warning-requalification/v1'
ALLOWED={'.cursor/skills/additional-assumptions-dialogue/scripts/extract_claims.py',
 '.cursor/skills/additional-assumptions-dialogue/scripts/validate_dialogue.py',
 'scripts/batch/harbor_instruction_worker.py','scripts/batch/harbor_instruction_producer.py',
 'scripts/batch/harbor_readiness.py','scripts/batch/harbor_semantic_readiness.py',
 'scripts/batch/harbor_dialogue_warning_requalification.py',
 'scripts/batch/harbor_dialogue_semantic_retry.py','scripts/batch/harbor_instruction_repair.py'}
ALLOWED.update({'scripts/batch/harbor_nat_retry.py','scripts/batch/harbor_nat_coverage_retry.py',
                'scripts/batch/harbor_lease_takeover.py','scripts/harbor_instruction_code_applicability.py',
                'scripts/harbor_instruction_campaign.py','scripts/batch/harbor_dialogue_scope_context.py',
                'scripts/batch/harbor_dialogue_scope_discovery.py','scripts/batch/harbor_scope_enqueue_requalification.py',
                'scripts/batch/harbor_tracker_lifecycle.py'})
ALLOWED.update({'scripts/batch/harbor_instruction_lease_renewal.py',
                'scripts/batch/harbor_disclosure_revision.py',
                'scripts/batch/harbor_disclosure_request_recovery.py'})
ALLOWED.update({'scripts/batch/harbor_dialogue_source_warnings.py','scripts/batch/harbor_complete_readiness.py'})
PHASES=('dialogue-review','final-runtime','dialogue-commit','enqueue')
REVISED_CONTINUATIONS=('disclosure_revision','disclosure_repair','lease_renewal')


def revised_continuations(worker):
    return {name:getattr(worker,name) for name in REVISED_CONTINUATIONS
            if getattr(worker,name,None) is not None}


def revised_history(worker,before,old_snapshot):
    """Validate the exact completed revised-disclosure history in its pinned root."""
    from scripts.batch import harbor_instruction_lease_renewal as renewal
    require(worker.disclosure_revision is not None and worker.disclosure_repair is not None
            and worker.lease_renewal is not None,'revised warning requires complete disclosure and renewal history')
    historical=replace(worker,root=Path(read(old_snapshot)['root']),warning_requalification=None)
    result=renewal.validate_historical_integrity(historical,before,old_snapshot)
    # Validate the actual global revision, physical retry, original rejected
    # call, validator recovery, and every retained byte using current readers.
    # The pinned old root is data for code identity; no old module is executed.
    from scripts.batch import harbor_disclosure_request_recovery as recovery
    recovery.validate_historical_integrity(historical,before,old_snapshot)
    return result


def historical_lease(worker,before):
    """Authenticate the retained renewal before validating its old code inventory."""
    from scripts.batch import harbor_instruction_worker as w
    p=read(worker.warning_requalification)
    require(p['schema_version']==SCHEMA and p['worker_run']==str(worker.run)
            and p['initial']==record(worker.run/'initial.json') and p['code']==w.code(worker)
            and p.get('prior_continuations')==revised_continuations(worker)
            and p['semantic'] is None and p.get('code_repair') is None,
            'warning detached revised disclosure or renewal history')
    require(p['code_changes']==changes(p['old_snapshot'],worker.root),'warning historical code transition changed')
    return revised_history(worker,before,p['old_snapshot'])



def _source_proof(worker,selection,review,claims,draft):
    from scripts.batch import harbor_dialogue_source_warnings as source_warnings
    home=Path(claims['path']).parent;round_dir=Path(draft['path']).parent
    return source_warnings.validate(review,claims,draft,record(home/'draft_template.md'),
        record(round_dir/'writer.filled.md'),record(round_dir/'paraphraser.filled.md'),
        selection.content.bindings['source_file'],task_dir=selection.content.stage,root=worker.root)


def _checked_source_proof(verified,call,review,claims,draft,*,root):
    from scripts.batch import harbor_dialogue_source_warnings as source_warnings
    proof=copy.deepcopy(verified.get('source_warning_proof'))
    require(proof is not None and proof['evidence'][0]==call.parsed
            and call.parsed['sha256']==review['sha256'],'source warning is not an authenticated selected review copy')
    proof['evidence'][0]=review
    source_warnings.revalidate(proof,read(review),read(claims),Path(draft['path']).read_text(),root=root)
    return proof


def changes(binding,root):
    old=read(binding);oldroot=Path(old['root']);expected={r['relative']:r['sha256'] for r in old['files']}
    changed=[]
    for name,digest in expected.items():
        require(record(oldroot/name)['sha256']==digest,'historical snapshot changed')
        require((root/name).is_file(),'requalification removed historical code')
        if record(root/name)['sha256']!=digest:changed.append(name)
    for path in root.rglob('*'):
        if path.is_file() and '__pycache__' not in path.parts:
            name=str(path.relative_to(root))
            if name not in expected:changed.append(name)
    require(set(changed)<=ALLOWED and '.cursor/skills/additional-assumptions-dialogue/scripts/validate_dialogue.py' in changed,
            'unrelated code change in warning requalification')
    return sorted(changed)


def authorize(worker,selection,lease,*,blocked,old_snapshot,directory,takeover=None):
    with (worker.run/'worker.lock').open('a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return _authorize(worker,selection,lease,blocked=blocked,old_snapshot=old_snapshot,
                          directory=directory,takeover=takeover)


def _authorize(worker,selection,lease,*,blocked,old_snapshot,directory,takeover):
    from scripts.batch import harbor_instruction_worker as w
    require(worker.warning_requalification is None and worker.pretransport_recovery is None
            and (worker.dialogue_semantic_retry is not None or worker.code_repair is not None
                 or worker.disclosure_revision is not None),
            'warning requalification requires exact semantic or repaired first-round history')
    _,before,tracker=w.active(worker,selection,lease)
    if worker.dialogue_semantic_retry is not None:
        history=semantic.validate_completed_integrity(worker,before);round_=2;historical_code=history['code']
    elif worker.code_repair is not None:
        history,_=repair.validate_historical_integrity(worker,before);round_=1;historical_code=history['current_code']
    else:
        history=revised_history(worker,before,old_snapshot);round_=1;historical_code=history['current_code']
    step=w.step_directory(worker,'dialogue-review')
    require(blocked==record(step/'blocked.json'),'exact completed-review blocked checkpoint required')
    old=read(blocked)
    require(old['status']=='DIALOGUE_REPAIR_REQUIRED' and old['step']=='dialogue-review'
            and not (step/'complete.json').exists() and not (step/'exception.json').exists(),
            'not a completed rejected semantic review')
    previous=codec.decode(old['selection'])
    comparison=selection
    if selection.content.dialogue_scope_receipt!=previous.content.dialogue_scope_receipt:
        from scripts.batch.harbor_dialogue_scope_context import validate_equivalence
        validate_equivalence(selection.content,root=worker.root)
        require(read(selection.content.dialogue_scope_receipt).get('replaces',read(selection.content.dialogue_scope_receipt)['previous'])==previous.content.dialogue_scope_receipt,
                'warning scope equivalence differs from reviewed scope')
        comparison=replace(selection,content=replace(selection.content,dialogue_scope_receipt=previous.content.dialogue_scope_receipt))
    require(replace(comparison,runtime=previous.runtime,observation=previous.observation)==previous,
            'selected financial content changed beyond current runtime')
    # This also verifies any same-owner lease renewal; no takeover is invented.
    from scripts.batch.harbor_lease_takeover import continuation_chain, completion_records
    context=read(w.request_saved(step/'request.json').context)
    continuation_chain(context['budget_evidence'][0],old['lease'],before,lease,
                       takeover=takeover,tracker_path=tracker['path'])
    takeover_records=completion_records(takeover) if takeover is not None else []
    changed=changes(old_snapshot,worker.root)
    require(all(Path(p).is_relative_to(Path(read(old_snapshot)['root'])) for p in historical_code),
            'previous snapshot does not own semantic code')
    for path,digest in historical_code.items():require(record(path)['sha256']==digest,'historical semantic code changed')
    proof=repair.prefix(before,selection,worker.context)
    t=w.task(worker,selection,tracker);run=t.run/('dialogue/round-%02d'%round_)
    requests={kind:w.request_saved(w.step_directory(worker,kind)/'request.json')
              for kind in ('dialogue-writer','dialogue-paraphraser','dialogue-review')}
    for kind,req in requests.items():
        p=req.prepared
        require(p.ordinal==round_ and p.limit==2 and p.scope.lane==producer.DIALOGUE and p.scope.budget_id==kind,
                'completed semantic round identity changed')
        require(p.scope.workbook_id==before['workbook_id'] and p.scope.original_sha256==before['bindings']['original_raw_source']['sha256'],
                'review original identity changed')
    calls={kind:producer.observed(req,worker.campaign) for kind,req in requests.items()}
    claims=selection.content.dialogue_claims;draft=record(run/'draft.md');review=record(run/'review.json');old_receipt=record(run/'review-result.json')
    require(read(old_receipt)['passed'] is False and read(old_receipt)['round']==round_,'original failed review receipt missing')
    validator=producer.skill_module(worker.root,'additional-assumptions-dialogue','validate_dialogue')
    # The owning producer re-derives authenticated source grouping as well as
    # claim contents; raw unscoped extraction cannot represent shared claims.
    current_directory,current_claims,_,_=producer._dialogue(t,round_,worker.root)
    require(current_directory.resolve()==run.parent.resolve() and record(current_directory/'claims.json')==claims,
            'current dialogue directory differs from exact reviewed claims')
    require(read(claims)==current_claims,'current claims differ from exact reviewed claims')
    text=Path(draft['path']).read_text();source_proof=None
    if round_==1 and not validator.review_passed(read(review),read(claims),draft_text=text):
        source_proof=_source_proof(worker,selection,review,claims,draft)
    warnings=validator.review_warnings(read(review),read(claims),draft_text=text,source_warning_proof=source_proof)
    require(bool(warnings) and validator.review_passed(read(review),read(claims),draft_text=text,source_warning_proof=source_proof),
            'substantive review rejection cannot become a warning')
    require(validator.check_draft(text,read(claims),selection.content.stage,source_workbook=selection.content.bindings['source_file'])['passed'],'current draft mechanics failed')
    directory=Path(directory);require(not directory.exists(),'warning requalification already attempted')
    directory.mkdir(parents=True)
    notes=producer.put(directory/'reviewed-notes.md',validator.strip_claim_comments(text).encode('utf-8'))
    source=selection.content.bindings['source_file'];answer=record(selection.content.stage/'tests/answer_key.json')
    verified=dialogue_review(calls['dialogue-review'],claims=claims,notes=notes,reviewed_claims=claims,
        policy=selection.content.model_policy,writer_calls=[calls[k] for k in ('dialogue-writer','dialogue-paraphraser')],
        workbook=before['workbook_id'],current_source_sha256=source['sha256'],original_sha256=before['bindings']['original_raw_source']['sha256'],
        source=source,answer_key=answer,task_dir=selection.content.stage,root=worker.root)
    require(verified['provenance']['review']==read(review),'original response and retained review differ')
    if source_proof is not None:
        require(_checked_source_proof(verified,calls['dialogue-review'],review,claims,draft,root=worker.root)==source_proof,
                'source warning is not tied to actual completed author and review Calls')
    retained=[]
    for result in proof:
        for bound in result['result']['evidence']:
            retained.append({'original':bound,'retained':w.snapshot(directory/'prefix'/('%05d.blob'%len(retained)),bound)})
    require(w.active(worker,selection,lease)[1]==before and record(tracker['path'])==tracker,
            'tracker changed during warning requalification')
    permit=producer.put(directory/'permit.json',dict(schema_version=SCHEMA,worker_run=str(worker.run),
        initial=record(worker.run/'initial.json'),semantic=worker.dialogue_semantic_retry,code_repair=worker.code_repair,blocked=blocked,
        tracker=w.snapshot(directory/'tracker.before.json',tracker),selection=codec.encode(selection),lease=asdict(lease),
        original=before['bindings']['original_raw_source'],old_snapshot=old_snapshot,code_changes=changed,code=w.code(worker),
        claims=claims,draft=draft,review=review,original_receipt=old_receipt,calls={k:asdict(v) for k,v in calls.items()},
        warnings=warnings,notes=notes,retained_prefix=retained,round=round_,semantic_budget_reset=False,model_calls=0,
        lease_takeover=takeover,takeover_records=takeover_records,
        **({'source_warning_proof':source_proof} if source_proof is not None else {}),
        **({'prior_continuations':revised_continuations(worker)}
           if worker.dialogue_semantic_retry is None and worker.code_repair is None else {})))
    return replace(worker,warning_requalification=permit),selection,lease,permit


def validate(worker,before):
    from scripts.batch import harbor_instruction_worker as w
    p=read(worker.warning_requalification)
    require(p['schema_version']==SCHEMA and p['worker_run']==str(worker.run) and p['initial']==record(worker.run/'initial.json')
            and p['semantic']==worker.dialogue_semantic_retry and p['original']==before['bindings']['original_raw_source'],
            'warning requalification history detached')
    if worker.dialogue_semantic_retry is not None:
        semantic.validate_completed_integrity(worker,before)
        require(p.get('code_repair')==worker.code_repair,'warning semantic repair history differs')
    elif worker.code_repair is not None:
        require(p.get('code_repair')==worker.code_repair,'warning repaired history differs')
        repair.validate_historical_integrity(worker,before)
    else:
        prior=p.get('prior_continuations',{}).get('lease_renewal')
        if worker.lease_renewal!=prior:
            from scripts.batch import harbor_instruction_lease_renewal as renewal
            renewal.validate(worker,before)
            current=worker.lease_renewal;seen=set()
            while current!=prior:
                require(current is not None and current['path'] not in seen and len(seen)<128,
                        'warning detached later renewal chain')
                seen.add(current['path']);item=read(read(current)['permit'])
                require(item['prior_continuations'].get('warning_requalification')==worker.warning_requalification,
                        'later renewal omitted warning authority')
                current=item['prior_continuations'].get('lease_renewal')
        historical_lease(replace(worker,lease_renewal=prior),before)
    require(p['code']==w.code(worker) and p['code_changes']==changes(p['old_snapshot'],worker.root),'warning policy code changed')
    require(p['round']==(2 if worker.dialogue_semantic_retry is not None else 1) and p['semantic_budget_reset'] is False and p['model_calls']==0,'warning requalification cannot add semantic calls')
    for key in ('blocked','tracker','claims','draft','review','original_receipt','notes'):life.checked_record(p[key])
    life.check_records(p['takeover_records'])
    for call in p['calls'].values():
        for binding in call.values():
            if binding is not None:life.checked_record(binding)
    for row in p['retained_prefix']:
        life.checked_record(row['retained']);require(row['original']['sha256']==row['retained']['sha256'],'prefix archive changed')
    v=producer.skill_module(worker.root,'additional-assumptions-dialogue','validate_dialogue');draft=Path(p['draft']['path']).read_text()
    source_proof=p.get('source_warning_proof')
    if source_proof is not None:
        from scripts.batch.harbor_model_review_provenance import Call
        selected=codec.decode(p['selection']);source=selected.content.bindings['source_file']
        require(p['round']==1 and _source_proof(worker,selected,p['review'],p['claims'],p['draft'])==source_proof,
                'source warning proof or first-round authority changed')
        calls={k:Call(**value) for k,value in p['calls'].items()}
        verified=dialogue_review(calls['dialogue-review'],claims=p['claims'],notes=p['notes'],reviewed_claims=p['claims'],
            policy=selected.content.model_policy,writer_calls=[calls[k] for k in ('dialogue-writer','dialogue-paraphraser')],
            workbook=before['workbook_id'],current_source_sha256=source['sha256'],original_sha256=before['bindings']['original_raw_source']['sha256'],
            source=source,answer_key=record(selected.content.stage/'tests/answer_key.json'),task_dir=selected.content.stage,root=worker.root)
        require(_checked_source_proof(verified,calls['dialogue-review'],p['review'],p['claims'],p['draft'],root=worker.root)==source_proof,
                'source warning lost actual completed Call provenance')
    require(v.review_warnings(read(p['review']),read(p['claims']),draft_text=draft,source_warning_proof=source_proof)==p['warnings']
            and v.review_passed(read(p['review']),read(p['claims']),draft_text=draft,source_warning_proof=source_proof),'warning policy no longer passes')
    return p


def apply_review(worker,selection,lease,directory):
    from scripts.batch import harbor_instruction_worker as w
    from scripts.batch.harbor_model_review_provenance import Call
    _,before,_=w.active(worker,selection,lease);p=validate(worker,before)
    require(p['selection']==codec.encode(selection) and p['lease']==asdict(lease),'exact requalification context required')
    v=producer.skill_module(worker.root,'additional-assumptions-dialogue','validate_dialogue')
    v.apply(selection.content.stage,Path(p['draft']['path']),read(p['claims']),read(p['review']),require_review_pass=True,skip_smoke=True,source_workbook=selection.content.bindings['source_file'],source_warning_proof=p.get('source_warning_proof'))
    producer.put(directory/'warning-review.json',{'schema_version':SCHEMA,'permit':worker.warning_requalification,
        'review':p['review'],'draft':p['draft'],'warnings':p['warnings'],'passed':True,'model_calls':0,'runtime_required':True})
    s=replace(selection.content,dialogue_review=p['review'],dialogue_reviewed_draft=p['draft'])
    semantic_=replace(selection.semantic,dialogue_call=Call(**p['calls']['dialogue-review']),dialogue_reviewed_claims=p['claims'],
        dialogue_writer_calls=tuple(Call(**p['calls'][k]) for k in ('dialogue-writer','dialogue-paraphraser')))
    return w.refreshed(replace(selection,content=s,semantic=semantic_)),lease,None

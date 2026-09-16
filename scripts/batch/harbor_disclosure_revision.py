"""One reviewed corrective disclosure version; original consumed calls stay sealed.

Only a rederived target-label correction is admitted. The rejected review must
cover every record, and the corrected version needs its own independent review.
A permanent workbook reservation bounds the global revision count at two.
"""
import copy
import fcntl
import json
import shutil
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from scripts.batch.harbor_admission import read,record,require
from scripts.batch import harbor_model_executor as E
from scripts.batch import harbor_transition_codec as codec

SCHEMA='harbor-disclosure-revision/v1'
BUDGET='disclosure-review-revision-02'
ALLOWED={'.cursor/skills/task-disclosure/scripts/disclose.py',
 'scripts/batch/harbor_disclosure_revision.py','scripts/batch/harbor_disclosure_evidence.py',
 'scripts/batch/harbor_instruction_producer.py','scripts/batch/harbor_instruction_worker.py',
 'scripts/harbor_instruction_campaign.py','scripts/batch/harbor_instruction_lease_renewal.py',
 'scripts/batch/harbor_disclosure_review_resume.py','scripts/batch/harbor_semantic_readiness.py',
 'scripts/batch/harbor_complete_readiness.py','scripts/batch/harbor_transition_codec.py',
 'scripts/batch/harbor_nat_retry.py','scripts/batch/harbor_nat_coverage_retry.py','scripts/batch/harbor_dialogue_semantic_retry.py'}


def changes(binding,root):
    old=read(binding);base=Path(old['root']);expected={x['relative']:x['sha256'] for x in old['files']}
    changed=[]
    for name,digest in expected.items():
        require(record(base/name)['sha256']==digest,'historical code changed')
        require((root/name).is_file(),'historical code removed')
        if record(root/name)['sha256']!=digest:changed.append(name)
    for p in root.rglob('*'):
        if p.is_file() and '__pycache__' not in p.parts and str(p.relative_to(root)) not in expected:changed.append(str(p.relative_to(root)))
    require(set(changed)<=ALLOWED and '.cursor/skills/task-disclosure/scripts/disclose.py' in changed,
            'unreviewed disclosure revision implementation; evaluator must remain exact')
    return sorted(changed)


def historical_code(binding, prior):
    """The nominated base is the actual failed physical worker implementation."""
    snapshot=read(binding);root=Path(snapshot['root'])
    files={item['relative']:item['sha256'] for item in snapshot['files']}
    require(prior.get('current_code'),'physical predecessor has no implementation binding')
    for name,digest in prior['current_code'].items():
        path=Path(name)
        require(path.is_relative_to(root) and files.get(str(path.relative_to(root)))==digest
                and record(path)['sha256']==digest,'revision base differs from actual physical implementation')
    return snapshot


def rejected(review,manifest,manifest_sha):
    """Validate complete evidence without treating a blocking verdict as a pass."""
    from scripts.batch.harbor_semantic_readiness import validate_disclosure_review
    require(review.get('passed') is False and isinstance(review.get('blocking_findings'),list)
            and review['blocking_findings'],'completed substantive rejection required')
    value=copy.deepcopy(review);blocked=[]
    for index,item in enumerate(value.get('sentences',[])):
        require(item.get('verdict') in ('pass','non_blocking','blocking'),'unknown verdict cannot authorize revision')
        if item['verdict']=='blocking':
            require(item.get('findings'),'blocking sentence needs actual findings');blocked.append(index)
            item['verdict']='pass'
    require(blocked,'no substantive rejected sentences')
    value.update(passed=True,blocking_findings=[])
    validate_disclosure_review(value,manifest,manifest_sha)
    return blocked


def date_reference_steps(old,new,disclosure):
    """Permit only source-derived date-reference descriptions in method steps."""
    from scripts.batch import harbor_instruction_producer as producer
    renderer=producer.skill_module(Path(__file__).resolve().parents[2],'task-disclosure','disclose')
    source=Path(disclosure['golden'])
    require(record(source)['sha256']==disclosure['golden_sha256'],'reference-label source changed')
    gold=renderer.Book(source)
    profile=old.get('method_profile') or {}
    require(old.get('source')=='custom_method_detector' and profile.get('complete')
            and old.get('coverage_complete') and old.get('disposition')=='disclosed'
            and old.get('leak_flag')is False,'complete disclosed source method required')
    actual=renderer.formula_profile(gold,dict(band=profile['band'],cell_keys=old['cell_keys'],
        label=profile['label'],pattern=profile['pattern']))
    require({k:v for k,v in actual.items()if k!='ast'}==profile,'retained formula profile differs from source')
    fields=renderer.custom_sentence_fields(actual,gold,lock_cells=renderer._stated_keys(old))
    require(fields.pop('_coverage_complete',False) and fields==new['fields'],
            'corrected method steps were not rederived from source')
    expected=old['fields']['steps'];changed=False
    sheet=profile['representative_cell'].rsplit('!',1)[0]
    for raw in dict.fromkeys(profile['raw_references']):
        refs=renderer.refs_in('='+raw,sheet)
        if len(refs)!=1 or not renderer.date_series_reference(gold,refs[0]):continue
        corrected=renderer.describe_ref(gold,raw,sheet,profile['label'])
        suffix=' containing a date value'
        require(corrected.endswith(suffix),'single source date description required')
        label=gold.row_label(refs[0])
        if not label:continue
        previous=corrected[:-len(suffix)]+' on the row labelled '+renderer.q(label)
        if previous in expected:expected=expected.replace(previous,corrected);changed=True
    require(changed and expected==new['fields']['steps'],
            'reference repair changed more than adjacent date-series labels')


def label_changes(old,new,old_manifest,new_manifest,blocked):
    """No scope, operator, source, row count or passing sentence may change."""
    require(set(old)==set(new),'disclosure schema changed')
    for k in set(old)-{'records','agent_records','method_assessments'}:require(old[k]==new[k],'unrelated disclosure metadata changed: '+k)
    allowed={'label','note','fields'};changed=[]
    require(len(old['records'])==len(new['records']),'disclosure record coverage changed')
    for index,(a,b) in enumerate(zip(old['records'],new['records'])):
        require(set(a)==set(b),'record schema changed')
        if a==b:continue
        if ({k for k in a if a[k]!=b[k]}=={'fields'} and set(a['fields'])==set(b['fields'])
                and {k for k in a['fields']if a['fields'][k]!=b['fields'][k]}=={'steps'}):
            date_reference_steps(a,b,old);changed.append(index);continue
        require({k for k in a if a[k]!=b[k]}<=allowed and set(a['fields'])==set(b['fields'])
                and {k for k in a['fields'] if a['fields'][k]!=b['fields'][k]}=={'label'},'correction changed more than target label')
        oldlabel=a['fields']['label'].strip('"');newlabel=b['fields']['label'].strip('"')
        require(oldlabel and newlabel and oldlabel!=newlabel,'empty or unchanged target label')
        for k in ('label','note'):
            require(b[k]==a[k].replace(oldlabel,newlabel),'label prose changed beyond exact label substitution')
        changed.append(index)
    require(changed,'corrective version has no changes')
    from scripts.batch import harbor_method_assessments as indexed
    require(indexed.logical_length(old['method_assessments'])==indexed.logical_length(new['method_assessments']),'assessment coverage changed')
    for a,b in zip(indexed.rows(old['method_assessments']),indexed.rows(new['method_assessments'])):
        require(set(a)==set(b) and all(a[k]==b[k] for k in a if k!='role_context'),'assessment semantics changed')
    a=old_manifest['sentences'];b=new_manifest['sentences']
    require(len(a)==len(b) and len(old['agent_records'])==len(new['agent_records']),'agent disclosure coverage changed')
    actual=[i for i,(x,y) in enumerate(zip(a,b)) if x['text']!=y['text']]
    require(actual==blocked,'changes must exactly cover rejected sentences and preserve passing sentences')
    require(all(len(x['record_ids'])==len(y['record_ids']) for x,y in zip(a,b)),'sentence record coverage changed')
    return dict(record_indices=changed,sentence_indices=actual,records=len(new['records']),agent_records=len(new['agent_records']))


def corrected_payload(original, detected):
    """Apply rederived shipped records only; retain suppressed reviewer context."""
    value=copy.deepcopy(detected)
    require(len(original['records'])==len(value['records']),'detector record coverage changed')
    for i,(old,new) in enumerate(zip(original['records'],value['records'])):
        require(old.get('cells')==new.get('cells') and old.get('entry')==new.get('entry'),'detector record order or identity changed')
        shipped=any(all(old.get(k)==r.get(k) for k in ('cells','entry','band')) for r in original['agent_records'])
        if not shipped:value['records'][i]=copy.deepcopy(old)
    value['method_assessments']=copy.deepcopy(original['method_assessments'])
    return value


def _history(worker,before):
    from scripts.batch import harbor_disclosure_review_resume as D
    from scripts.batch import harbor_instruction_worker as W
    receipt,records=D.validate_historical_integrity(worker,before)
    request=W.request_saved(Path(receipt['request']['path']));result=E.inspect(request.prepared,worker.campaign)
    from scripts.batch.harbor_model_review_provenance import validate_call
    review=validate_call(result.call,read(result.call.request),policy=request.prepared.policy)['review']
    body=json.loads(D.original_review_request(result.call)['messages'][1]['content'])
    manifest=body['manifest'];blocked=rejected(review,manifest,body['manifest_sha256'])
    step=Path(receipt['request']['path']).parent
    exception=record(step/'exception.json');failure=read(exception)
    require(failure==dict(type='AdmissionError',message_sha256=E.sha(b'blocking or unknown sentence verdict'),automatic_replay=False),
            'owning rejected disclosure exception changed')
    require(not (step/'complete.json').exists(),'completed disclosure cannot be revised')
    require(request.prepared.ordinal==request.prepared.limit==1 and request.prepared.scope.budget_id=='disclosure-review',
            'original logical review budget changed')
    return receipt,records+[exception],request,result,body,blocked


def authorize(worker,selection,lease,*,old_snapshot,directory):
    from scripts.batch import harbor_instruction_worker as W
    from scripts.batch.harbor_instruction_repair import prefix
    with (worker.run/'worker.lock').open('a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        require(worker.disclosure_repair is not None and worker.disclosure_revision is None,'exact original physical disclosure history required')
        require(worker.lease_renewal is None,'revision after an existing successful-step renewal needs an explicit owning transition')
        require(all(getattr(worker,k,None) is None for k in ('retry','dialogue_retry','boundary_recovery','dialogue_semantic_retry','pretransport_recovery','code_repair','warning_requalification')),'later prose work cannot be revised')
        reservation=worker.run/'disclosure-revision.json';require(not reservation.exists(),'global corrective revision already used')
        _,before,tracker=W.active(worker,selection,lease)
        prior,records,request,result,body,blocked=_history(worker,before)
        require(codec.decode(prior['selection'])==selection,'revision predecessor selection changed')
        historical_code(old_snapshot,prior)
        changed=changes(old_snapshot,worker.root)
        require(not any(W.step_directory(worker,n).exists() for n in W.STEPS[2:]),'later instruction work exists')
        proof=prefix(before,selection,worker.context,stop='disclosure')
        directory=E.safe(directory);require(not directory.exists(),'revision namespace already exists')
        # Reserve before any candidate generation; an interrupted revision cannot
        # obtain another allowance by choosing a different output directory.
        W.keep(reservation,dict(schema_version=SCHEMA,directory=str(directory),revision=2,limit=2,predecessor=worker.disclosure_repair))
        producer=directory/'producer';runs=producer/'disclosure-build';shutil.copytree(worker.run/'producer/disclosure-build',runs)
        renderer=W.producer.skill_module(worker.root,'task-disclosure','disclose')
        args=SimpleNamespace(task_dir=selection.content.stage,runs_root=runs,role_resolutions=None)
        detected=renderer.detect_records(args)
        W.keep(directory/'candidate/detected-records.json',detected)
        detected=corrected_payload(body['disclosure'],detected)
        (runs/selection.content.stage.name/'records.json').write_text(json.dumps(detected,indent=2,ensure_ascii=False)+'\n')
        stage=directory/'stages'/selection.content.stage.name
        renderer.write_disclosure(SimpleNamespace(**vars(args),out=stage,force=False,dry_run=False))
        exact=exact_instruction(renderer,body['instruction'],body['disclosure'],read(record(stage/'tests/disclosure.json')))
        (stage/'instruction.md').write_text(exact)
        context=dict(before=before,tracker=tracker,records=records,request=request,result=result,body=body,blocked=blocked,proof=proof,changed=changed)
        return _complete(worker,selection,lease,old_snapshot,directory,context)


def exact_instruction(renderer,original,old_disclosure,new_disclosure):
    old=renderer.render_section(old_disclosure['agent_records']);new=renderer.render_section(new_disclosure['agent_records'])
    require(old and original.count(old)==1,'ambiguous original disclosure boundary')
    return original.replace(old,new,1)


def _complete(worker,selection,lease,old_snapshot,directory,context,*,recovery=None):
    from scripts.batch import harbor_instruction_worker as W
    before=context['before'];tracker=context['tracker'];records=context['records'];request=context['request']
    result=context['result'];body=context['body'];blocked=context['blocked'];proof=context['proof'];changed=context['changed']
    stage=directory/'stages'/selection.content.stage.name;runs=directory/'producer/disclosure-build'
    renderer=W.producer.skill_module(worker.root,'task-disclosure','disclose')
    oldtree=W.static.tree(selection.content.stage);newtree=W.static.tree(stage)
    require(set(oldtree)==set(newtree) and {k for k in oldtree if oldtree[k]!=newtree[k]}=={'instruction.md','tests/disclosure.json'},'revision changed financial stage artifacts')
    old=body['disclosure'];new=read(record(stage/'tests/disclosure.json'))
    from scripts.batch.harbor_semantic_readiness import disclosure_manifest
    golden=selection.content.bindings['source_file'];delivered=record(W.producer.delivered_workbook(stage,selection.content.workbook))
    manifest=disclosure_manifest(record(stage/'instruction.md'),record(stage/'tests/disclosure.json'),golden,delivered,root=worker.root)
    delta=label_changes(old,new,body['manifest'],manifest,blocked)
    require(renderer.strip_agent_sections(body['instruction'])==renderer.strip_agent_sections((stage/'instruction.md').read_text()),'non-disclosure instruction changed')
    faith=renderer.faithcheck_task(stage,runs,golden['path']);verified=renderer.verify_task(stage)
    require(faith['passed'] and not faith['faults'] and verified['passed'] and not verified['faults'],'corrective stage mechanical gates failed')
    selected=W.refreshed(replace(selection,content=replace(selection.content,stage=stage)))
    # Preserve immutable reviewed candidate inputs before later NAT edits.
    instruction=W.snapshot(directory/'candidate/instruction.md',record(stage/'instruction.md'))
    disclosure=W.snapshot(directory/'candidate/disclosure.json',record(stage/'tests/disclosure.json'))
    newmanifest=W.keep(directory/'candidate/manifest.json',manifest)
    oldinputs={k:W.keep(directory/'original'/f'{k}.json',body[k]) for k in ('manifest','disclosure')}
    if recovery is not None:records=[*records,recovery]
    retained=[W.snapshot(directory/'history'/('%05d.blob'%i),b) for i,b in enumerate(records)]
    require(W.active(worker,selection,lease)[1]==before,'tracker changed during revision preparation')
    permit=W.keep(directory/'permit.json',dict(schema_version=SCHEMA,revision=2,limit=2,phase='disclosure-review',
        worker_run=str(worker.run),producer_run=str(directory/'producer'),initial=record(worker.run/'initial.json'),
        predecessor=worker.disclosure_repair,prior_continuations={k:getattr(worker,k) for k in ('disclosure_repair','lease_renewal') if getattr(worker,k,None) is not None},
        original=before['bindings']['original_raw_source'],tracker=W.snapshot(directory/context.get('tracker_name','tracker.before.json'),tracker),
        old_snapshot=old_snapshot,code_changes=changed,current_code=W.code(worker),selection=codec.encode(selected),lease=asdict(lease),
        old_selection=codec.encode(selection),old_tree=oldtree,retained_history=retained,original_inputs=oldinputs,
        campaign=campaign_record(worker.campaign),old_call=codec.encode(result.call),original_attempt_id=request.prepared.attempt_id,original_ordinal=1,original_limit=1,
        physical_count=1,semantic_budget_reset=False,budget_id=BUDGET,instruction=instruction,disclosure=disclosure,manifest=newmanifest,
        delta=delta,proof=proof,faith=faith,verified=verified,**({'preparation_completion':context['completion']} if 'completion' in context else {}),**({'preparation_recovery':recovery} if recovery is not None else {})))
    W.keep(directory/'completion-reservation.json',record(worker.run/'disclosure-revision.json'))
    W.keep(worker.run/'disclosure-revision-complete.json',permit)
    return permit

def validate(worker,before):
    from scripts.batch import harbor_instruction_worker as W
    binding=worker.disclosure_revision;p=read(binding)
    if p.get('source_revision') is not None:
        from scripts.batch.harbor_disclosure_source_revision import validate
        return validate(worker,before)
    if p.get('schema_version')=='harbor-disclosure-manifest-revision/v1':
        from scripts.batch.harbor_disclosure_manifest_revision import validate
        return validate(worker,before)
    if getattr(worker,'warning_requalification',None) is not None:
        from scripts.batch.harbor_dialogue_warning_requalification import validate as validate_warning
        validate_warning(worker,before)
        return p
    if p.get('validation_recovery') is not None:
        from scripts.batch.harbor_disclosure_request_recovery import validate
        return validate(worker,before)
    require(read(record(worker.run/'disclosure-revision-complete.json'))==binding,'completed corrective permit changed')
    require(p['schema_version']==SCHEMA and (p['revision'],p['limit'])==(2,2) and p['phase']=='disclosure-review','invalid corrective revision')
    require(p['worker_run']==str(worker.run) and p['initial']==record(worker.run/'initial.json') and p['original']==before['bindings']['original_raw_source'],'foreign corrective revision')
    require(read(record(worker.run/'disclosure-revision.json'))==dict(schema_version=SCHEMA,directory=str(Path(binding['path']).parent),revision=2,limit=2,predecessor=p['predecessor']),'corrective revision reservation changed')
    require(p['predecessor']==worker.disclosure_repair==p['prior_continuations']['disclosure_repair'],'detached original physical review')
    require(p['current_code']==W.code(worker),'corrective revision implementation changed')
    require((p['original_ordinal'],p['original_limit'],p['physical_count'])==(1,1,1) and p['semantic_budget_reset'] is False and p['budget_id']==BUDGET,'corrective budget history changed')
    prior,_,request,result,body,blocked=_history(worker,before)
    historical_code(p['old_snapshot'],prior)
    require(codec.encode(result.call)==p['old_call'] and request.prepared.attempt_id==p['original_attempt_id'],'original reviewed call changed')
    for b in p['retained_history']:E.checked_bytes(b)
    for b in (p['instruction'],p['disclosure'],p['manifest'],p['tracker'],*p['original_inputs'].values()):E.checked_bytes(b)
    require(changes(p['old_snapshot'],worker.root)==p['code_changes'],'corrective code scope changed')
    require(W.static.tree(codec.decode(p['old_selection']).content.stage)==p['old_tree'],'original rejected stage changed')
    require(label_changes(body['disclosure'],read(p['disclosure']),body['manifest'],read(p['manifest']),blocked)==p['delta'],'corrective label evidence changed')
    validate_preparation_recovery(p)
    validate_renewal_links(worker,before,p)
    return p


def validate_renewal_links(worker,before,p):
    expected=p['prior_continuations'].get('lease_renewal')
    current=worker.lease_renewal
    if current is not None:
        from scripts.batch import harbor_instruction_lease_renewal as renewal
        renewal.validate(worker,before)
    seen=set()
    while current!=expected:
        require(current is not None and current['path'] not in seen and len(seen)<128,'detached corrective lease history')
        seen.add(current['path']);item=read(read(current)['permit'])
        require(item['prior_continuations'].get('disclosure_revision')==worker.disclosure_revision,'renewal omitted corrective version')
        current=item['prior_continuations'].get('lease_renewal')


def permit_history(p):
    c=p['campaign'];worker=SimpleNamespace(run=Path(p['worker_run']),disclosure_repair=p['predecessor'],
        campaign=E.Campaign(Path(c['root']),Path(c['slot_root']),c['model_slots']))
    prior,_,request,result,body,blocked=_history(worker,read(p['tracker']))
    historical_code(p['old_snapshot'],prior)
    require(codec.encode(result.call)==p['old_call'] and request.prepared.attempt_id==p['original_attempt_id'],
            'corrective receipt replaced original call provenance')
    require((p['original_ordinal'],p['original_limit'],p['physical_count'])==(1,1,1),'original review budget changed')
    require(label_changes(body['disclosure'],read(p['disclosure']),body['manifest'],read(p['manifest']),blocked)==p['delta'],
            'corrective changes detached from rejected review')
    validate_preparation_recovery(p)
    return p


def validate_task(task):
    p=read(task.disclosure_revision)
    if p.get('source_revision') is not None:
        from scripts.batch.harbor_disclosure_source_revision import validate_task
        return validate_task(task)
    if p.get('schema_version')=='harbor-disclosure-manifest-revision/v1':
        from scripts.batch.harbor_disclosure_manifest_revision import validate_task
        return validate_task(task)
    if p.get('validation_recovery') is not None:
        from scripts.batch.harbor_disclosure_request_recovery import validate_task
        return validate_task(task)
    require(read(record(Path(p['worker_run'])/'disclosure-revision-complete.json'))==task.disclosure_revision,'corrective request permit not owned')
    require(p['schema_version']==SCHEMA and p['revision']==p['limit']==2 and p['budget_id']==BUDGET and p['semantic_budget_reset'] is False,'invalid corrective request authority')
    require(Path(p['worker_run'])/'producer'==task.run and codec.decode(p['selection']).content.stage==task.stage,'corrective request stage or namespace changed')
    from scripts.batch.harbor_instruction_worker import code
    require(p['current_code']==code(task),'corrective request code changed')
    require(record(task.stage/'instruction.md')['sha256']==p['instruction']['sha256'] and record(task.stage/'tests/disclosure.json')['sha256']==p['disclosure']['sha256'],'corrective request content changed')
    require(read(record(Path(p['worker_run'])/'disclosure-revision.json'))['directory']==str(Path(task.disclosure_revision['path']).parent),'corrective request reservation changed')
    return permit_history(p)


def validate_review_history(binding,call,manifest,instruction,disclosure):
    submitted=read(call.submitted);budget=submitted.get('scope',{}).get('budget_id')
    if binding is None:
        require(budget!=BUDGET,'corrective review requires retained revision authority');return {}
    p=read(binding)
    if p.get('source_revision') is not None:
        from scripts.batch.harbor_disclosure_source_revision import validate_review_history
        return validate_review_history(binding,call,manifest,instruction,disclosure)
    if p.get('schema_version')=='harbor-disclosure-manifest-revision/v1':
        from scripts.batch.harbor_disclosure_manifest_revision import validate_review_history
        return validate_review_history(binding,call,manifest,instruction,disclosure)
    if p.get('validation_recovery') is not None:
        from scripts.batch.harbor_disclosure_request_recovery import review_history
        return review_history(binding,call,manifest,instruction,disclosure)
    require(read(record(Path(p['worker_run'])/'disclosure-revision-complete.json'))==binding,'reviewed corrective permit not owned')
    require(p['schema_version']==SCHEMA and p['revision']==p['limit']==2 and p['semantic_budget_reset'] is False,'invalid reviewed corrective version')
    require(budget==BUDGET and submitted.get('semantic_ordinal')==submitted.get('semantic_limit')==1
            and submitted.get('physical_repair') is None and submitted['attempt_id']!=p['original_attempt_id'],'corrective review reset or repeated original attempt')
    require(manifest['sha256']==p['manifest']['sha256'] and instruction['sha256']==p['instruction']['sha256'] and disclosure['sha256']==p['disclosure']['sha256'],'corrective reviewed version differs')
    require(any(E.checked_bytes(b)==E.checked_bytes(binding) for b in submitted['budget_evidence']),'corrective review omitted global budget receipt')
    permit_history(p)
    old_submitted=read(codec.decode(p['old_call']).submitted)
    require(submitted['scope']=={**old_submitted['scope'],'budget_id':BUDGET},'corrective review original scope changed')
    for b in p['retained_history']:E.checked_bytes(b)
    require(read(record(Path(p['worker_run'])/'disclosure-revision.json'))['directory']==str(Path(binding['path']).parent),'reviewed corrective reservation changed')
    return {'disclosure_revision':binding,'retained_original_call':p['old_call'],'global_revision':2,'global_limit':2}


def no_corrective_request(worker, directory, original):
    require(not (directory/'permit.json').exists() and not (worker.run/'disclosure-revision-complete.json').exists(),
            'corrective permit already completed')
    require(not (worker.run/'producer/requests'/BUDGET).exists() and not (directory/'steps').exists(),
            'corrective request already prepared')
    scope=E.SemanticScope(original.scope.workbook_id,original.scope.original_sha256,original.scope.lane,BUDGET)
    scope_root=worker.campaign.root/E.sha(E.canonical(asdict(scope)))
    require(not scope_root.exists(),'corrective model journal already exists')


def resume_reserved(worker,selection,lease,*,old_snapshot,failed_resource,failed_snapshot,additional_renewal=None):
    """Finish the same pre-permit revision after one proved boundary-newline defect.

    This never replays authorization or renews a lease. It preserves the failed
    clone before restoring exact original outside-section bytes.
    """
    from scripts.batch import harbor_instruction_worker as W
    from scripts.batch.harbor_instruction_repair import prefix
    from scripts.batch.harbor_nat_retry import _renewal_chain
    from scripts import harbor_campaign as C
    with (worker.run/'worker.lock').open('a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        require(worker.disclosure_revision is None and worker.lease_renewal is None,'foreign revision recovery history')
        _,before,tracker=W.active(worker,selection,lease)
        reservation=record(worker.run/'disclosure-revision.json');reserved=read(reservation)
        directory=Path(reserved['directory']);require(reserved==dict(schema_version=SCHEMA,directory=str(directory),revision=2,limit=2,predecessor=worker.disclosure_repair),'global reservation changed')
        resource=read(failed_resource);intent=read(resource['intent'])
        require(failed_resource==record(directory.parent/'resource.json') and resource['intent']==record(directory.parent/'intent.json')
                and resource['returncode']==1 and resource['failure'] is None,'exact completed failed preparation required')
        require(resource['log']==record(directory.parent/'step.log') and E.checked_bytes(resource['log']).endswith(b'AdmissionError: non-disclosure instruction changed\n'),
                'not the known pre-permit boundary failure')
        require(intent['snapshot']==failed_snapshot and intent['old_snapshot']==old_snapshot,'failed preparation snapshot changed')
        E.checked_bytes(intent['driver']);previous=read(intent['original'])
        require(previous['checkpoint']==worker.disclosure_repair and codec.decode(previous['selection'])==selection,'failed preparation predecessor changed')
        frozen=read(failed_snapshot)['snapshot'];base=Path(frozen['root']);changed=[]
        for item in frozen['files']:
            name=item['relative'];require(record(base/name)['sha256']==item['sha256'],'failed frozen implementation changed')
            if record(worker.root/name)['sha256']!=item['sha256']:changed.append(name)
        require(changed==['scripts/batch/harbor_disclosure_revision.py'],'only preparation recovery implementation may change')
        expected={x['relative'] for x in frozen['files']}
        require({str(p.relative_to(worker.root)) for p in worker.root.rglob('*') if p.is_file() and '__pycache__' not in p.parts}==expected,'recovery introduced unrelated code')
        renewed=record(directory.parent/'lease-renewal.json');renewal=read(renewed)
        _renewal_chain(renewal['before'],asdict(C.decode(renewal['previous'])),before,lease)
        validate_additional_renewal(renewal,additional_renewal,before,lease)
        prior,records,request,result,body,blocked=_history(worker,before)
        no_corrective_request(worker,directory,request.prepared)
        historical_code(old_snapshot,prior);delta_code=changes(old_snapshot,worker.root)
        proof=prefix(before,selection,worker.context,stop='disclosure')
        stage=directory/'stages'/selection.content.stage.name;runs=directory/'producer/disclosure-build'
        renderer=W.producer.skill_module(worker.root,'task-disclosure','disclose')
        detected=renderer.detect_records(SimpleNamespace(task_dir=selection.content.stage,runs_root=runs,role_resolutions=None))
        require(detected==read(record(directory/'candidate/detected-records.json')),'failed candidate detection changed')
        require(corrected_payload(body['disclosure'],detected)==read(record(runs/selection.content.stage.name/'records.json')),'failed candidate projection changed')
        oldtree=W.static.tree(selection.content.stage);failedtree=W.static.tree(stage)
        require(set(oldtree)==set(failedtree) and {k for k in oldtree if oldtree[k]!=failedtree[k]}=={'instruction.md','tests/disclosure.json'},'failed clone changed financial artifacts')
        newdisclosure=read(record(stage/'tests/disclosure.json'))
        exact=exact_instruction(renderer,body['instruction'],body['disclosure'],newdisclosure)
        rendered=renderer.render_section(newdisclosure['agent_records'])
        require(exact.count(rendered)==1 and (stage/'instruction.md').read_text()==exact.replace(rendered,'\n'+rendered,1),
                'failed clone is not exactly one extra disclosure-boundary newline')
        recovery_root=directory/'preparation-recovery';require(not recovery_root.exists(),'preparation recovery already consumed')
        archive=recovery_root/'failed-stage'/stage.name;shutil.copytree(stage,archive)
        require(W.static.tree(archive)==failedtree,'failed clone archive differs')
        recovery=W.keep(recovery_root/'receipt.json',dict(schema_version='harbor-disclosure-preparation-recovery/v1',
            reservation=reservation,failed_resource=failed_resource,failed_intent=resource['intent'],failed_snapshot=failed_snapshot,
            failed_log=resource['log'],renewal=renewed,additional_renewal=additional_renewal,failed_stage=str(archive),failed_tree=failedtree,
            original_instruction=record(selection.content.stage/'instruction.md'),restored_instruction_sha256=E.sha(exact.encode()),
            implementation=record(Path(__file__)),model_calls=0,revision=2,limit=2))
        require(W.active(worker,selection,lease)[1]==before,'tracker changed before byte restoration')
        (stage/'instruction.md').write_text(exact)
        context=dict(before=before,tracker=tracker,records=records,request=request,result=result,body=body,blocked=blocked,proof=proof,changed=delta_code)
        return _complete(worker,selection,lease,old_snapshot,directory,context,recovery=recovery)


def validate_preparation_recovery(p):
    binding=p.get('preparation_recovery')
    if binding is None:return
    from scripts.batch import harbor_readiness as static
    value=read(binding)
    require(value['schema_version']=='harbor-disclosure-preparation-recovery/v1' and value['revision']==value['limit']==2
            and value['model_calls']==0,'preparation recovery budget changed')
    for k in ('reservation','failed_resource','failed_intent','failed_snapshot','failed_log','renewal','original_instruction','implementation'):E.checked_bytes(value[k])
    if value.get('additional_renewal') is not None:E.checked_bytes(value['additional_renewal'])
    if p['current_code'].get(value['implementation']['path'])!=value['implementation']['sha256']:
        completion=read(p['preparation_completion'])
        require(completion['schema_version']=='harbor-disclosure-preparation-completion/v1' and completion['revision']==completion['limit']==2 and completion['model_calls']==0,'preparation completion budget changed')
        require(completion['prior_recovery']==binding and completion['implementation'] in [dict(path=k,sha256=v) for k,v in p['current_code'].items()],'preparation completion detached historical recovery')
        historical=read(completion['failed_snapshot'])['snapshot']
        require(value['implementation']==record(Path(historical['root'])/'scripts/batch/harbor_disclosure_revision.py'),'historical recovery implementation differs')
        implementation_transition(completion['failed_snapshot'],Path(completion['implementation']['path']).resolve().parents[2])
        for key in ('failed_resource','failed_intent','previous_tracker','implementation'):E.checked_bytes(completion[key])
        if completion['additional_renewal'] is not None:E.checked_bytes(completion['additional_renewal'])
    require(static.tree(Path(value['failed_stage']))==value['failed_tree'],'failed clone archive changed')
    require(record(Path(value['failed_stage'])/'tests/disclosure.json')['sha256']==p['disclosure']['sha256'], 'recovery altered disclosure semantics')
    restored=E.checked_bytes(p['instruction'])
    require(E.sha(restored)==value['restored_instruction_sha256'],'restored instruction changed')
    marker=b'## Workbook disclosure'
    require(restored.count(marker)==1 and (Path(value['failed_stage'])/'instruction.md').read_bytes()==restored.replace(marker,b'\n'+marker,1),
            'archived preparation differs beyond exact boundary newline')


def validate_additional_renewal(original,binding,before,lease):
    """One new owning CAS may provide guard coverage; never replay the first CAS."""
    from scripts import harbor_campaign as C
    from scripts.batch.harbor_nat_retry import _renewal_chain
    old=C.decode(original['lease'])
    if binding is None:
        require(old==lease,'changed recovery lease needs an explicit additional renewal');return
    value=read(binding);prior=C.decode(value['previous']);current=C.decode(value['lease'])
    require(prior==old and current==lease and lease.revision==old.revision+1,'additional renewal did not extend exact retained lease')
    require(value['result']['revision']==lease.revision and value['result']['sha256']==lease.sha256,'additional renewal CAS result changed')
    require(value['before']['sha256']==old.sha256 and value['tracker']['sha256']==lease.sha256,'additional renewal snapshots differ')
    after=read(value['tracker'])
    require(after==before,'additional renewal tracker differs from current owner')
    _renewal_chain(value['before'],asdict(old),after,lease)


def campaign_record(campaign):
    return dict(root=str(campaign.root),slot_root=str(campaign.slot_root),model_slots=campaign.model_slots)


def implementation_transition(binding,root):
    frozen=read(binding)['snapshot'];base=Path(frozen['root']);changed=[]
    for item in frozen['files']:
        name=item['relative'];require(record(base/name)['sha256']==item['sha256'],'failed frozen implementation changed')
        if record(root/name)['sha256']!=item['sha256']:changed.append(name)
    require(changed==['scripts/batch/harbor_disclosure_revision.py'],'only preparation completion implementation may change')
    require({str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts}=={x['relative'] for x in frozen['files']},'completion introduced unrelated code')


def finish_reserved(worker,selection,lease,*,old_snapshot,failed_resource,failed_snapshot,additional_renewal=None):
    """Complete a fully checked reserved clone after failed JSON serialization.

    No builder, byte restoration, lease operation or model dispatch occurs here.
    Previously written candidate/history artifacts are checked, never replaced.
    """
    from scripts.batch import harbor_instruction_worker as W
    from scripts.batch.harbor_instruction_repair import prefix
    from scripts.batch.harbor_nat_retry import _renewal_chain
    from scripts import harbor_campaign as C
    with (worker.run/'worker.lock').open('a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        require(worker.disclosure_revision is None and worker.lease_renewal is None,'foreign preparation completion')
        _,before,tracker=W.active(worker,selection,lease)
        reservation=record(worker.run/'disclosure-revision.json');saved=read(reservation);directory=Path(saved['directory'])
        require(saved==dict(schema_version=SCHEMA,directory=str(directory),revision=2,limit=2,predecessor=worker.disclosure_repair),'global revision reservation changed')
        resource=read(failed_resource);intent=read(resource['intent'])
        require(resource['intent']==record(Path(failed_resource['path']).parent/'intent.json') and resource['log']==record(Path(failed_resource['path']).parent/'step.log'),'serialization resource bindings changed')
        require(resource['returncode']==1 and resource['failure'] is None and intent['snapshot']==failed_snapshot
                and intent['old_snapshot']==old_snapshot,'exact failed serialization intent required')
        require(E.checked_bytes(resource['log']).endswith(b'TypeError: Object of type PosixPath is not JSON serializable\n'), 'unsupported preparation failure')
        E.checked_bytes(intent['driver']);implementation_transition(failed_snapshot,worker.root)
        recovery=record(directory/'preparation-recovery/receipt.json');recovered=read(recovery)
        require(recovered['failed_resource']==intent['failed_resource'] and recovered['failed_snapshot']==intent['failed_snapshot'], 'completion detached original boundary recovery')
        previous_renewal=read(recovered['additional_renewal'] or recovered['renewal'])
        previous_lease=C.decode(previous_renewal['lease'])
        previous_tracker=record(directory/'tracker.before.json')
        require(previous_tracker['sha256']==previous_lease.sha256,'completed mechanical gate tracker changed')
        _renewal_chain(previous_tracker,asdict(previous_lease),before,lease)
        validate_additional_renewal(previous_renewal,additional_renewal,before,lease)
        prior,records,request,result,body,blocked=_history(worker,before)
        no_corrective_request(worker,directory,request.prepared)
        historical_code(old_snapshot,prior);delta_code=changes(old_snapshot,worker.root)
        proof=prefix(before,selection,worker.context,stop='disclosure')
        stage=directory/'stages'/selection.content.stage.name
        require(record(stage/'instruction.md')['sha256']==record(directory/'candidate/instruction.md')['sha256']
                and record(stage/'tests/disclosure.json')['sha256']==record(directory/'candidate/disclosure.json')['sha256'], 'already checked candidate changed')
        completion_path=directory/'preparation-completion.json';require(not completion_path.exists(),'preparation completion already attempted')
        completion=W.keep(completion_path,dict(schema_version='harbor-disclosure-preparation-completion/v1',
            prior_recovery=recovery,failed_resource=failed_resource,failed_intent=resource['intent'],failed_snapshot=failed_snapshot,
            previous_tracker=previous_tracker,additional_renewal=additional_renewal,implementation=record(Path(__file__)),revision=2,limit=2,model_calls=0))
        context=dict(before=before,tracker=tracker,records=records,request=request,result=result,body=body,blocked=blocked,
            proof=proof,changed=delta_code,tracker_name='tracker.finalized.json',completion=completion)
        return _complete(worker,selection,lease,old_snapshot,directory,context,recovery=recovery)

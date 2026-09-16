"""A source-derived corrective disclosure version after an ordinary rejection.

The existing revision consumer owns review and commit. This module only admits
an exact corrected candidate; the original failed independent Call stays sealed.
"""
from pathlib import Path
from dataclasses import asdict,replace
import importlib.util,json,fcntl,shutil
from scripts.batch.harbor_admission import read,record,require
from scripts.batch import harbor_instruction_worker as W

PROFILE='harbor-disclosure-source-revision-profile/v1'
FIELDS={'schema_version','workbook','original','source','initial','old_snapshot','snapshot',
        'descriptor','request','candidate','candidate_runs','proof','repair','cells',
        'authorization','lease_history'}
AUTH='acffe75c8ed401f66484a5bb6c39a15a0a3e209fc976967da0bddd6125126fd0'
ALLOWED={'.cursor/skills/task-disclosure/scripts/disclose.py',
         '.cursor/skills/task-disclosure/REGISTRY.md',
         'scripts/batch/harbor_disclosure_source_revision.py',
         'scripts/batch/harbor_disclosure_revision.py',
         'scripts/batch/harbor_disclosure_command_repair.py',
         'scripts/batch/harbor_instruction_worker.py',
         'scripts/batch/harbor_instruction_lease_renewal.py',
         '.pipeline-code.json','.pipeline-code-manifest.json'}


def inventory(binding):
    p=read(binding);root=Path(p['root'])
    if 'files'in p:
        rows=p['files'];files={r['relative']:r['sha256']for r in rows}
        require(len(files)==len(rows),'duplicate source revision inventory')
    else:
        files={name:r['sha256']for name,r in p['bindings'].items()}
        require(all(r==record(root/name)for name,r in p['bindings'].items()),
                'original source inventory binding differs')
    require(all(not Path(n).is_absolute()and '..'not in Path(n).parts for n in files),
            'source revision inventory escapes root')
    from scripts.batch.harbor_instruction_worker import static
    require(static.tree(root)==files,'complete source revision code changed')
    return root,files


def profile(binding,worker):
    p=read(binding)
    require(set(p)==FIELDS and p['schema_version']==PROFILE,'closed source revision profile required')
    require(p['initial']==record(worker.run/'initial.json'),'source revision initial differs')
    require(p['authorization']['sha256']==AUTH and record(p['authorization']['path'])==p['authorization'],
            'source revision authorization differs')
    require(record(p['original']['path'])==p['original'] and record(p['source']['path'])==p['source'],
            'original source or retained generated golden changed')
    old,before=inventory(p['old_snapshot']);new,after=inventory(p['snapshot'])
    require(new==worker.root and set(before)<=set(after),'source revision worker or inventory differs')
    delta=sorted(n for n in after if after[n]!=before.get(n))
    require(set(delta)<=ALLOWED and '.cursor/skills/task-disclosure/scripts/disclose.py'in delta,
            'source revision changed undeclared code or evaluator')
    for name in {'.pipeline-code.json','.pipeline-code-manifest.json'}&set(delta):
        require(read(record(new/name))=={k:v for k,v in after.items()if k!=name},
                'source revision manifest differs')
    require(type(p['cells'])is list and p['cells'] and len(set(p['cells']))==len(p['cells'])
            and all(type(c)is str and '!'in c for c in p['cells']), 'closed source cell selection required')
    return p,old,delta


def original_predecessor(p,worker,intent):
    """Authenticate a completed build, optionally followed by its exact renewal."""
    checkpoint=record(worker.run/'steps/disclosure-build/complete.json')
    if intent['previous']==checkpoint:return {}
    from scripts import harbor_instruction_campaign as I
    from scripts.batch import harbor_instruction_lease_renewal as L
    prior=I.worker_decode(read(p['descriptor'])['worker']);old,_=inventory(p['old_snapshot'])
    require(prior.root==old and prior.run==worker.run and prior.context==worker.context
            and prior.lease_renewal==intent['previous']
            and all(getattr(prior,k,None)is None for k in L.CONTINUATIONS if k!='lease_renewal'),
            'source review requires the exact ordinary renewed predecessor')
    before=read(record(worker.context.tracker_root/(p['workbook']+'.json')))
    permit=L.validate_historical_integrity(prior,before,p['old_snapshot'])
    completed=read(prior.lease_renewal)
    require(permit['checkpoint']==checkpoint and completed['selection']==intent['selection']
            and completed['lease']==intent['lease'],'source review renewal changed the completed boundary')
    return {'lease_renewal':prior.lease_renewal}


def original_review(p,worker,selection):
    """Authenticate the ordinary, sealed first Call and its complete input stage."""
    from scripts.batch import harbor_instruction_worker as W,harbor_model_executor as E
    from scripts.batch import harbor_transition_codec as codec
    from scripts.batch.harbor_model_review_provenance import validate_call
    from scripts.batch.harbor_disclosure_evidence import decode_body,scoped_disclosure
    directory=worker.run/'steps/disclosure-review'
    require(p['request']==record(directory/'request.json'),'foreign source review request')
    request=W.request_saved(directory/'request.json');intent=read(record(directory/'intent.json'))
    require(intent['step']=='disclosure-review'and intent['selection']==codec.encode(selection)
            and intent['initial']==p['initial'],
            'ordinary rejected review boundary differs')
    original_predecessor(p,worker,intent)
    require(read(record(directory/'exception.json'))==dict(type='AdmissionError',message_sha256=E.sha(b'blocking or unknown sentence verdict'),automatic_replay=False)
            and not(directory/'complete.json').exists(),'sealed semantic rejection required')
    result=E.inspect(request.prepared,worker.campaign)
    require(result.status=='completed'and result.call is not None,'uncertain or absent review cannot revise')
    prepared=request.prepared
    require(prepared.ordinal==prepared.limit==1 and prepared.scope.budget_id=='disclosure-review'
            and prepared.scope.workbook_id==p['workbook']and prepared.scope.original_sha256==p['original']['sha256']
            and prepared.physical_repair is None,'original review budget or source differs')
    reviewed=validate_call(result.call,read(result.call.request),policy=prepared.policy)['review']
    body=json.loads(read(result.call.request)['messages'][1]['content']);projected='evidence_projection'in body
    if projected:body=decode_body(body)
    stage=selection.content.stage;payload=read(record(stage/'tests/disclosure.json'))
    require(body['instruction']==(stage/'instruction.md').read_text()
            and body['disclosure']==(scoped_disclosure(payload)if projected else payload),
            'original review differs from full retained task')
    context=read(request.context);root,files=inventory(p['old_snapshot'])
    require(context['kind']=='disclosure-review'and context['request']==prepared.request
            and context['stage_tree']==W.static.tree(stage)
            and context['budget_evidence']==list(prepared.budget_evidence),'original review context differs')
    for row in context['code']:
        path=Path(row['path']);require(path.is_relative_to(root)and files.get(str(path.relative_to(root)))==row['sha256'],
                                      'original review code differs');E.checked_bytes(row)
    for name,digest in intent['code'].items():
        path=Path(name);require(path.is_relative_to(root)and files.get(str(path.relative_to(root)))==digest,
                               'original review intent code differs')
    for row in [*context['evidence'],*context['budget_evidence']]:E.checked_bytes(row)
    from scripts.batch.harbor_disclosure_revision import rejected
    rejected(reviewed,body['manifest'],body['manifest_sha256'])
    return request,result,body,reviewed,payload


def candidate(p,worker,selection,body,reviewed,original):
    """Rederive the selected convention from source, then run original mechanics."""
    from scripts.batch import harbor_instruction_worker as W
    from scripts.batch.harbor_semantic_readiness import disclosure_manifest,workbook_evidence
    proof=read(p['proof']);stage=Path(p['candidate']);old=selection.content.stage
    require(proof['status']=='ISOLATED_SOURCE_DISCLOSURE_CANDIDATE'and proof['models']==proof['original_task_mutations']==0
            and proof['qualified']is False and proof['source']==p['source']and proof['repair']==p['repair']
            and proof['original_task']==str(old)and proof['candidate']==str(stage),'source candidate proof differs')
    before=W.static.tree(old);after=W.static.tree(stage)
    require(before==proof['original_tree']and after==proof['candidate_tree']and set(before)==set(after)
            and {k for k in before if before[k]!=after[k]}=={'instruction.md','tests/disclosure.json'},
            'source candidate changed financial task artifacts')
    require(record(stage/'instruction.md')==proof['candidate_instruction']and record(stage/'tests/disclosure.json')==proof['candidate_disclosure'],
            'source candidate inputs differ')
    renderer=W.producer.skill_module(worker.root,'task-disclosure','disclose')
    require(record(p['repair']['path'])==p['repair'],'source derivation implementation changed')
    spec=importlib.util.spec_from_file_location('selected_disclosure_source_derivation',p['repair']['path'])
    repair=importlib.util.module_from_spec(spec);spec.loader.exec_module(repair)
    golden=renderer.Book(Path(p['source']['path']));delivered=renderer.Book(renderer.find_environment(old))
    bands=renderer.read_stage(old,'bands',worker.run/'producer/disclosure-build')
    targets=set(bands['target_keys']);closure=renderer.graded_closure(golden,targets)
    expected,indices=repair.corrected_payload(renderer,golden,delivered,original,set(p['cells']),closure,targets)
    actual=read(proof['candidate_disclosure'])
    require(actual==expected and indices==proof['changed_record_indices'],'candidate is not the source-derived correction')
    oldtext=renderer.strip_agent_sections(body['instruction'])
    require(oldtext.count(renderer.OUTPUT_HEADING)==1,'ambiguous original instruction boundary')
    section=renderer.render_section(renderer.agent_records(actual['records']))
    require((stage/'instruction.md').read_text()==oldtext.replace(renderer.OUTPUT_HEADING,'\n'+section+'\n## Output\n',1),
            'source candidate changed nondisclosure instruction')
    given=record(W.producer.delivered_workbook(old,selection.content.workbook))
    for role,bound in [('golden',p['source']),('delivered',given)]:
        fresh=workbook_evidence(bound);prior=dict(body['workbooks'][role]);prior['path']=fresh['path']
        require(prior==fresh,'original review workbook evidence differs')
    manifest=disclosure_manifest(proof['candidate_instruction'],proof['candidate_disclosure'],p['source'],given,root=worker.root)
    delta=source_changes(original,actual,body['manifest'],manifest,reviewed,body['manifest_sha256'],
        rederive=lambda: repair.corrected_payload(renderer,golden,delivered,original,set(p['cells']),closure,targets))
    faith=renderer.faithcheck_task(stage,Path(p['candidate_runs']),p['source']['path']);verified=renderer.verify_task(stage)
    require(faith['passed']is True and faith['faults']==[]and verified['passed']is True and verified['faults']==[],
            'source candidate failed original mechanics')
    return manifest,delta,faith,verified


def inspect_history(binding,worker):
    """Validate a proposed source correction without reserving its revision."""
    p,old,changes=profile(binding,worker)
    selection=W.codec.decode(read(record(worker.run/'steps/disclosure-review/intent.json'))['selection'])
    request,result,body,reviewed,payload=original_review(p,worker,selection)
    candidate(p,worker,selection,body,reviewed,payload)
    return p,changes


def producer_run(worker):
    """Keep new review diagnostics separate from the rejected producer tree."""
    binding=worker.disclosure_revision
    if binding is None:return worker.run/'producer'
    p=read(binding)
    if p.get('source_revision') is None:return worker.run/'producer'
    owned(binding,p)
    require(p['worker_run']==str(worker.run),'foreign source revision producer')
    return Path(p['producer_run'])


def copy_producer_inputs(original,selected):
    """Carry build evidence into a revision without stale review caches.

    Original requests and responses remain in their original producer tree.
    The new disclosure manifest and its request must be prepared from the
    corrected candidate, as in the ordinary disclosure revision path.
    """
    shutil.copytree(Path(original)/'disclosure-build',Path(selected)/'disclosure-build')


def authorize(worker,selection,lease,*,profile_binding,directory):
    from scripts.batch import harbor_instruction_worker as W,harbor_lease_takeover as T
    from scripts.batch import harbor_disclosure_revision as R
    from scripts.batch.harbor_instruction_repair import prefix
    from scripts.batch.harbor_instruction_lease_renewal import CONTINUATIONS
    require(all(getattr(worker,k,None)is None for k in CONTINUATIONS if k!='lease_renewal'),
            'ordinary source revision required')
    p,old,changed=profile(profile_binding,worker)
    prior=original_predecessor(p,worker,read(record(worker.run/'steps/disclosure-review/intent.json')))
    require(worker.lease_renewal==prior.get('lease_renewal'),'source revision renewal differs from rejected review')
    with (worker.run/'worker.lock').open('a+b')as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        directory=W.executor.safe(directory);marker=worker.run/'disclosure-revision.json'
        require(not directory.exists()and not marker.exists(),'corrective revision already reserved')
        require(not any(W.step_directory(worker,n).exists()for n in W.STEPS[2:]),'later instruction work exists')
        _,before,tracker=W.active(worker,selection,lease)
        require(before['bindings']['original_raw_source']==p['original'],'source revision current source differs')
        initial=read(p['initial'])
        T.continuation_chain(initial['tracker'],initial['lease'],before,lease,
                             takeover=p['lease_history'],tracker_path=tracker['path'])
        request,result,body,reviewed,payload=original_review(p,worker,selection)
        manifest,delta,faith,verified=candidate(p,worker,selection,body,reviewed,payload)
        proof=prefix(before,selection,worker.context,stop='disclosure')
        require(W.active(worker,selection,lease)[2]==tracker,'tracker changed before source revision')
        W.keep(marker,dict(schema_version=R.SCHEMA,directory=str(directory),revision=2,limit=2,predecessor=None))
        stage=directory/'stages'/selection.content.stage.name
        shutil.copytree(p['candidate'],stage)
        require(W.static.tree(stage)==read(p['proof'])['candidate_tree'],'candidate copy differs')
        producer=directory/'producer';copy_producer_inputs(worker.run/'producer',producer)
        selected=W.refreshed(replace(selection,content=replace(selection.content,stage=stage)))
        instruction=W.snapshot(directory/'candidate/instruction.md',record(stage/'instruction.md'))
        disclosure=W.snapshot(directory/'candidate/disclosure.json',record(stage/'tests/disclosure.json'))
        permit=W.keep(directory/'permit.json',dict(schema_version=R.SCHEMA,source_revision=profile_binding,
            revision=2,limit=2,phase='disclosure-review',worker_run=str(worker.run),producer_run=str(producer),
            initial=p['initial'],original=p['original'],predecessor=None,prior_continuations=prior,
            old_snapshot=p['old_snapshot'],current_code=W.code(worker),code_changes=changed,
            old_selection=W.codec.encode(selection),selection=W.codec.encode(selected),lease=asdict(lease),
            tracker=W.snapshot(directory/'tracker.before.json',tracker),
            old_call=W.codec.encode(result.call),original_attempt_id=request.prepared.attempt_id,
            original_ordinal=1,original_limit=1,physical_count=1,semantic_budget_reset=False,budget_id=R.BUDGET,
            instruction=instruction,disclosure=disclosure,manifest=W.keep(directory/'candidate/manifest.json',manifest),
            delta=delta,proof=proof,faith=faith,verified=verified))
        W.keep(worker.run/'disclosure-revision-complete.json',permit)
        return permit


def owned(binding,p):
    from scripts.batch import harbor_disclosure_revision as R
    require(p['schema_version']==R.SCHEMA and p['revision']==p['limit']==2 and p['phase']=='disclosure-review'
            and p['budget_id']==R.BUDGET and p['semantic_budget_reset']is False
            and (p['original_ordinal'],p['original_limit'],p['physical_count'])==(1,1,1),
            'source revision budget differs')
    run=Path(p['worker_run']);directory=Path(binding['path']).parent
    require(read(record(run/'disclosure-revision-complete.json'))==binding
            and read(record(run/'disclosure-revision.json'))==dict(schema_version=R.SCHEMA,directory=str(directory),revision=2,limit=2,predecessor=None)
            and p['predecessor']is None and type(p['prior_continuations'])is dict
            and set(p['prior_continuations'])<={'lease_renewal'},'source revision ownership differs')
    for row in p['prior_continuations'].values():R.E.checked_bytes(row)
    for row in (p['instruction'],p['disclosure'],p['manifest'],p['tracker']):R.E.checked_bytes(row)


def history(binding,p,worker):
    """Current Calls stay live; only the sealed first review uses its old stage."""
    from scripts.batch import harbor_instruction_worker as W
    owned(binding,p);selected,old,delta=profile(p['source_revision'],worker)
    require(p['initial']==selected['initial']and p['original']==selected['original']
            and p['old_snapshot']==selected['old_snapshot']and p['code_changes']==delta,'source revision profile differs')
    selection=W.codec.decode(p['old_selection'])
    require(p['prior_continuations']==original_predecessor(selected,worker,
            read(record(worker.run/'steps/disclosure-review/intent.json'))),
            'source revision detached its original renewal')
    request,result,body,reviewed,payload=original_review(selected,worker,selection)
    require(W.codec.encode(result.call)==p['old_call']and request.prepared.attempt_id==p['original_attempt_id'],
            'source revision original Call differs')
    manifest,change,faith,verified=candidate(selected,worker,selection,body,reviewed,payload)
    require(manifest==read(p['manifest'])and change==p['delta']
            and record(Path(selected['candidate'])/'instruction.md')['sha256']==p['instruction']['sha256']
            and record(Path(selected['candidate'])/'tests/disclosure.json')['sha256']==p['disclosure']['sha256'],
            'source revision reviewed candidate differs')
    from scripts.batch import harbor_lease_takeover as T
    from scripts.batch.harbor_workbook_bootstrap import Lease
    initial=read(selected['initial'])
    T.continuation_chain(initial['tracker'],initial['lease'],read(p['tracker']),Lease(**p['lease']),
                         takeover=selected['lease_history'],tracker_path=str(worker.context.tracker_root/(selected['workbook']+'.json')))
    return p


def historical_lease(worker,before):
    """Delegate the exact retained renewal to its original inventory validator."""
    from scripts import harbor_instruction_campaign as I
    from scripts.batch import harbor_instruction_lease_renewal as L
    p=read(worker.disclosure_revision);owned(worker.disclosure_revision,p)
    source,old,_=profile(p['source_revision'],worker)
    prior=I.worker_decode(read(source['descriptor'])['worker'])
    require(prior.root==old and prior.run==worker.run and prior.context==worker.context
            and prior.lease_renewal==worker.lease_renewal==p['prior_continuations'].get('lease_renewal')
            and all(getattr(prior,k,None)is None for k in L.CONTINUATIONS if k!='lease_renewal')
            and worker.lease_renewal is not None,'foreign source revision renewal adapter')
    return L.validate_historical_integrity(prior,before,source['old_snapshot'])


def validate(worker,before):
    from scripts.batch import harbor_instruction_worker as W,harbor_disclosure_revision as R
    p=history(worker.disclosure_revision,read(worker.disclosure_revision),worker)
    require(p['worker_run']==str(worker.run)and p['original']==before['bindings']['original_raw_source']
            and p['current_code']==W.code(worker),'source revision live worker differs')
    R.validate_renewal_links(worker,before,p)
    return p


def validate_task(task):
    from scripts.batch import harbor_instruction_worker as W
    p=read(task.disclosure_revision);owned(task.disclosure_revision,p)
    require(task.run==Path(p['producer_run'])and task.stage==W.codec.decode(p['selection']).content.stage
            and p['current_code']==W.code(task),'source revision producer differs')
    require(record(task.stage/'instruction.md')['sha256']==p['instruction']['sha256']
            and record(task.stage/'tests/disclosure.json')['sha256']==p['disclosure']['sha256'],'source revision request differs')
    # Reconstruct the actual original worker from its frozen descriptor, retaining
    # the selected current code root only for the explicitly declared renderer.
    from scripts import harbor_instruction_campaign as I
    source=read(p['source_revision']);worker=I.worker_decode(read(source['descriptor'])['worker'])
    worker=replace(worker,root=task.root)
    return history(task.disclosure_revision,p,worker)


def validate_review_history(binding,call,manifest,instruction,disclosure):
    from scripts.batch import harbor_disclosure_revision as R,harbor_transition_codec as codec
    from scripts import harbor_instruction_campaign as I
    p=read(binding);owned(binding,p);source=read(p['source_revision'])
    worker=I.worker_decode(read(source['descriptor'])['worker']);worker=replace(worker,root=Path(read(source['snapshot'])['root']))
    history(binding,p,worker)
    submitted=read(call.submitted);old=read(codec.decode(p['old_call']).submitted)
    require(submitted['scope']=={**old['scope'],'budget_id':R.BUDGET}
            and submitted['semantic_ordinal']==submitted['semantic_limit']==1
            and submitted['attempt_id']!=p['original_attempt_id']and submitted.get('physical_repair')is None,
            'corrective independent Call budget differs')
    require(any(R.E.checked_bytes(row)==R.E.checked_bytes(binding)for row in submitted['budget_evidence']),
            'corrective review omitted permanent revision reservation')
    require(manifest['sha256']==p['manifest']['sha256']and instruction['sha256']==p['instruction']['sha256']
            and disclosure['sha256']==p['disclosure']['sha256'],'corrective reviewed inputs differ')
    return dict(disclosure_revision=binding,retained_original_call=p['old_call'],global_revision=2,global_limit=2)


def source_changes(old,new,old_manifest,new_manifest,review,old_sha,*,rederive=None):
    """Keep source identity/coverage and every originally passing sentence exact."""
    from scripts.batch.harbor_disclosure_revision import rejected
    blocked=rejected(review,old_manifest,old_sha)
    require(set(old)==set(new),'source revision changed disclosure shape')
    require(all(old[k]==new[k]for k in old if k not in {'records','agent_records'}),
            'source revision changed non-rendered metadata')
    require(type(old['records'])is list and len(old['records'])==len(new['records']),
            'source revision changed record coverage')
    changed=[];extended=False
    allowed={'label','note','value','alternatives'}
    for i,(before,after)in enumerate(zip(old['records'],new['records'])):
        if before==after:continue
        keys=set(before)|set(after)
        differences={k for k in keys if k not in before or k not in after or before[k]!=after[k]}
        conventional=set(before)==set(after)and differences<=allowed
        error_fields=(set(before)==set(after)and differences=={'fields'}
            and before.get('source')==after.get('source')=='custom_method_detector'
            and before.get('coverage_complete')is True
            and type(before.get('fields'))is dict and type(after.get('fields'))is dict
            and set(before['fields'])==set(after['fields'])
            and {k for k in before['fields']if before['fields'][k]!=after['fields'][k]}=={'steps'})
        dividend=(before.get('source')==after.get('source')=='convention_detector'
            and before.get('entry')==after.get('entry')=='distribution_policy'
            and before.get('value')=='payout_ratio'and after.get('value')=='formula_mechanics'
            and differences<={'label','note','value','fields','mechanics_references'}
            and set(before)<=set(after)and set(after)-set(before)<={'fields','mechanics_references'}
            and type(after.get('fields'))is dict
            and type(after.get('mechanics_references'))is list
            and len(after['mechanics_references'])==2
            and all(type(v)is str and v for v in after['mechanics_references'])
            and len(set(after['mechanics_references']))==2)
        require(conventional or error_fields or dividend,
                'source revision changed source locator, scope, role, or provenance')
        require(before.get('disposition')==after.get('disposition')=='disclosed',
                'source revision changed an undisclosed source record')
        extended=extended or not conventional
        changed.append(i)
    if extended:
        # The owning loader supplies the hash-bound repair and original books,
        # target closure and cell scope; shape alone cannot authorize fields.
        require(callable(rederive),'extended source fields require source rederivation')
        derived,indices=rederive()
        require(derived==new and indices==changed,'extended source fields differ from source rederivation')
    require(changed,'source revision has no corrected records')
    require(len(old['agent_records'])==len(new['agent_records']),
            'source revision changed shipped coverage')
    a=old_manifest['sentences'];b=new_manifest['sentences']
    require(len(a)==len(b),'source revision changed sentence coverage')
    altered=[]
    for i,(before,after)in enumerate(zip(a,b)):
        require(set(before)==set(after)and before['order']==after['order']==i,
                'source revision changed manifest order')
        require(len(before['record_ids'])==len(after['record_ids']),
                'source revision changed sentence record coverage')
        if before!=after:altered.append(i)
    require(altered==blocked,'source revision must preserve every passing sentence')
    from collections import Counter
    for manifest in (old_manifest,new_manifest):
        ids=[r for s in manifest['sentences']for r in s['record_ids']]
        require(len(ids)==len(set(ids))and Counter(ids)==Counter(manifest['agent_record_ids']),
                'source revision manifest omits or duplicates a record')
    # Record IDs can change with corrected prose. The actual full reviewer still
    # checks every record in the new manifest and both complete workbooks.
    return dict(record_indices=changed,sentence_indices=altered,
                records=len(new['records']),sentences=len(b))

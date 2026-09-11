"""One source-proved sentence repair before the first independent dialogue review."""
import copy,fcntl,json,re,shutil
from dataclasses import asdict,replace
from pathlib import Path
from scripts.batch.harbor_admission import read,record,require
from scripts.batch import harbor_transition_codec as codec,harbor_model_executor as E
SCHEMA='harbor-dialogue-pre-review-repair/v1'
REVIEW_GUIDANCE='\nReview all unchanged 192 claim identities and every final senior turn independently. The source_proved_correction records one deterministic postprocessing sentence change, separately from both original author Calls. For that claim, the generated spoken trailer incorrectly called an absolute worksheet reference a text literal; the exact source formula and required row label are authoritative. Verify the corrected locked-input meaning against that formula, while evaluating every other required clause normally. Do not waive any calculation, sign, scope, or claim coverage requirement.'
ALLOWED={'.cursor/skills/additional-assumptions-dialogue/scripts/aa_lib.py',
 '.cursor/skills/additional-assumptions-dialogue/scripts/validate_dialogue.py',
 'scripts/batch/harbor_dialogue_pre_review_repair.py','scripts/batch/harbor_instruction_worker.py',
 'scripts/harbor_instruction_campaign.py','scripts/batch/harbor_instruction_lease_renewal.py',
 'scripts/batch/harbor_disclosure_manifest_revision.py','scripts/batch/harbor_instruction_producer.py',
 'scripts/batch/harbor_semantic_readiness.py','scripts/batch/harbor_complete_readiness.py',
 'scripts/batch/harbor_transition_codec.py'}


def inventory(root):
    return {str(p.relative_to(root)):record(p)['sha256'] for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts}


def changes(snapshot,root):
    old=read(snapshot);files={x['relative']:x['sha256'] for x in old['files']}
    for name,digest in files.items():require(record(Path(old['root'])/name)['sha256']==digest,'old dialogue implementation changed')
    current={str(p.relative_to(root)):record(p)['sha256'] for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    require(set(files)<=set(current),'dialogue successor removed code')
    delta=sorted(k for k in current if current[k]!=files.get(k));require(set(delta)<=ALLOWED,'unreviewed dialogue repair implementation')
    return delta


def old_worker(p):
    from scripts import harbor_instruction_campaign as C
    from scripts.batch import harbor_instruction_lease_renewal as L
    v=read(p['old_descriptor']);root=Path(read(p['old_snapshot'])['root'])
    require(v['code']==record(root/'scripts/harbor_instruction_campaign.py'),'old coordinator binding changed')
    worker=C.worker_decode(v['worker'])
    require(worker.root==root and str(worker.run)==p['worker_run'] and getattr(worker,'dialogue_pre_review_repair',None) is None,'foreign predecessor worker')
    expected={k:getattr(worker,k) for k in ('disclosure_revision','lease_renewal')}
    require(expected==p['prior_continuations'] and all(expected.values()),'detached disclosure/lease predecessor')
    L._historical_inventory(worker,p['old_snapshot'])
    return worker


def owned(binding,p):
    E.checked_bytes(binding);run=Path(p['worker_run'])
    require(p['schema_version']==SCHEMA and p['phase']=='dialogue-review' and p['round']==1 and p['semantic_budget_reset'] is False
            and p['model_calls_added']==0 and p['sentence_replacements']==1,'invalid pre-review repair authority')
    require(read(record(run/'dialogue-pre-review-repair.json'))==binding,'pre-review repair reservation changed')
    require(p['initial']==record(run/'initial.json'),'repair initial identity changed')
    require(p['reservation']==record(run/'dialogue-pre-review-reservation.json'),'permanent repair reservation changed')
    require(read(p['reservation'])==dict(schema_version=SCHEMA,directory=str(Path(p['dialogue_directory']).parents[1]),blocked=p['blocked'],sentence_replacements=1,model_calls_added=0),'repair reservation identity changed')
    require(p['current_inventory']==inventory(Path(p['current_root'])),'successor implementation bytes changed')
    source=codec.decode(p['selection']).content.bindings['source_file']
    require(p['source']==source,'repair source differs from selected source');E.checked_bytes(p['source'])
    from scripts.batch import harbor_disclosure_manifest_revision as M
    from scripts.batch.harbor_workbook_bootstrap import Lease
    M.validate_renewal(p['failed_lease'],p['renewal'],read(p['tracker']),Lease(**p['lease']))
    for b in [p['blocked'],p['old_descriptor'],p['old_snapshot'],p['old_draft'],p['old_check'],p['claims'],p['corrected_draft'],p['tracker'],*p['history']]:E.checked_bytes(b)
    require(p['predecessor']==p['blocked'] and read(p['blocked'])['step']=='dialogue-paraphraser','repair predecessor changed')


def historical_lease(worker,before):
    from scripts.batch import harbor_instruction_lease_renewal as L
    p=read(worker.dialogue_pre_review_repair);owned(worker.dialogue_pre_review_repair,p)
    require(worker.lease_renewal==p['prior_continuations']['lease_renewal'],'foreign retained lease link')
    return L.validate_historical_integrity(old_worker(p),before,p['old_snapshot'])


def historical_manifest(worker,before):
    from scripts.batch import harbor_disclosure_manifest_revision as M
    p=read(worker.dialogue_pre_review_repair);owned(worker.dialogue_pre_review_repair,p)
    require(worker.disclosure_revision==p['prior_continuations']['disclosure_revision'],'foreign retained manifest link')
    return M.validate_historical_integrity(old_worker(p),before,p['old_snapshot'])


def repair_sentence(draft,claims,source,*,root):
    """Re-express a real fully absolute reference as its exact workbook row label."""
    from scripts.batch.harbor_readiness import skill_module
    from xl_ast_graph import parse_formula
    E.checked_bytes(source)
    renderer=skill_module(root,'task-disclosure','disclose');gold=renderer.Book(Path(source['path']))
    candidates=[]
    pattern=re.compile(r'Keep the text literal "([^"\n]+)" fixed as the formula moves across; the other references shift with position\.')
    for match in pattern.finditer(draft):
        prior=[c for c in claims['claims'] if draft.rfind('<!-- claim:'+c['record_id']+' -->',0,match.start())>=0]
        require(prior,'fixed reference has no owning claim')
        claim=max(prior,key=lambda c:draft.rfind('<!-- claim:'+c['record_id']+' -->',0,match.start()))
        reviewer=claim['reviewer_only'];require(reviewer['source']=='custom_method_detector','fixed reference not source-derived')
        ref=renderer.parse_ref(match.group(1),claim['sheet']);formula=reviewer['evidence'];cells=reviewer['cells']
        require(cells and gold.formula.get(renderer.parse_ref(cells[0],claim['sheet']))==formula,'actual source formula differs')
        nodes=[]
        def walk(n):
            if n.kind=='ref' and renderer.parse_ref(n.name,claim['sheet'])==ref:nodes.append(n.name)
            for child in n.args:walk(child)
        walk(parse_formula(formula))
        require(nodes and all(re.search(r'\$[A-Z]+\$[0-9]+$',n) for n in nodes),'source reference is not fully fixed')
        label=gold.row_label(ref);require(label and '\n' not in label and '"' not in label,'unambiguous fixed-input row label required')
        require('the row labelled '+json.dumps(label) in claim['must_say'] and 'locked input' in claim['must_say'],'fixed source label not a required claim')
        require(match.group(1) in reviewer['steps'] and 'stays fixed while every other reference shifts' in reviewer['steps'],'copied reference rule not source-proved')
        replacement='Keep the locked input on the row labelled '+json.dumps(label)+' fixed as the formula moves across; the other references shift with position.'
        candidates.append(dict(record_id=claim['record_id'],old_sentence=match.group(0),new_sentence=replacement,source=source,formula_cell=cells[0],formula=formula,fixed_ref=ref,row_label=label))
    require(len(candidates)==1,'exactly one proved fixed-reference correction required')
    proof=candidates[0];require(draft.count(proof['old_sentence'])==1,'ambiguous replacement sentence')
    return draft.replace(proof['old_sentence'],proof['new_sentence'],1),proof


def original_pair(p,*,root):
    from scripts.batch import harbor_dialogue_authoring as A
    from scripts.batch.harbor_model_review_provenance import Policy
    calls=tuple(codec.decode(c) for c in p['author_calls']);claims=read(p['claims'])
    checked=A.verify_pair(calls,policy=Policy(**p['policy']),claims=claims,draft=Path(p['old_draft']['path']).read_text(),round=1,
        root=root,workbook=p['workbook'],original_sha256=p['original']['sha256'])
    return calls,checked


def validate(worker,before):
    from scripts.batch import harbor_instruction_worker as W
    p=read(worker.dialogue_pre_review_repair);owned(worker.dialogue_pre_review_repair,p)
    require(p['worker_run']==str(worker.run) and p['original']==before['bindings']['original_raw_source'] and p['current_code']==W.code(worker),'pre-review worker/source/code changed')
    require(p['code_changes']==changes(p['old_snapshot'],worker.root),'pre-review code scope changed')
    require(worker.disclosure_revision==p['prior_continuations']['disclosure_revision'],'detached manifest continuation')
    old=old_worker(p)
    from scripts.batch import harbor_instruction_lease_renewal as L,harbor_disclosure_manifest_revision as M
    L.validate_historical_integrity(old,before,p['old_snapshot']);M.validate_historical_integrity(old,before,p['old_snapshot'])
    original_pair(p,root=worker.root)
    fixed,proof=repair_sentence(Path(p['old_draft']['path']).read_text(),read(p['claims']),p['source'],root=worker.root)
    require(proof==p['sentence_proof'] and fixed==Path(p['corrected_draft']['path']).read_text(),'deterministic sentence proof changed')
    return p


def validate_task(task):
    from scripts.batch.harbor_instruction_worker import code
    p=read(task.dialogue_pre_review_repair);owned(task.dialogue_pre_review_repair,p)
    require(task.run==Path(p['worker_run'])/'producer' and task.stage==codec.decode(p['selection']).content.stage,'pre-review task context changed')
    if getattr(task,'dialogue_semantic_retry',None) is not None:validate_semantic_task(task)
    else:require(p['current_code']==code(task),'pre-review task implementation changed')
    require(read(record(Path(p['dialogue_directory'])/'claims.json'))==read(p['claims']),'corrected dialogue changed claims')
    original_pair(p,root=task.root)
    return p


def context(task,round,*,root):
    p=validate_task(task);require(round==1 or (round==2 and getattr(task,'dialogue_semantic_retry',None) is not None),'remaining correction review requires semantic CAS')
    from scripts.batch import harbor_dialogue_scope_context as scope
    if getattr(task,'dialogue_semantic_retry',None) is None:
        selected=codec.decode(p['selection']).content;scope.validate_equivalence(selected,root=root)
    directory=Path(p['dialogue_directory']);claims=read(p['claims'])
    from scripts.batch.harbor_readiness import skill_module
    composer=skill_module(root,'additional-assumptions-dialogue','compose_draft');template,slots=composer.compose(claims,claims['writer_pack'])
    require((directory/'draft_template.md').read_text()==template and read(record(directory/'slots.json'))==slots,'corrected scaffold changed')
    return directory,claims,template,slots


def verify_pair(binding,calls,*,policy,claims,draft,round,root,workbook,original_sha256):
    p=read(binding);owned(binding,p)
    require(round==1 and workbook==p['workbook'] and original_sha256==p['original']['sha256'] and asdict(policy)==p['policy'],'corrected reviewer identity changed')
    old,checked=original_pair(p,root=root);require(tuple(calls)==old and claims==read(p['claims']),'corrected review detached original authors')
    fixed,proof=repair_sentence(Path(p['old_draft']['path']).read_text(),claims,p['source'],root=root)
    require(proof==p['sentence_proof'] and draft==fixed==Path(p['corrected_draft']['path']).read_text(),'reviewed draft differs from exact source-proved repair')
    return checked


def validate_retained_request(task,request):
    p=validate_task(task);require(request.context in p['author_contexts'],'foreign historical author request')
    c=read(request.context);require(c['request']==request.prepared.request and c['kind'] in ('dialogue-writer','dialogue-paraphraser') and request.prepared.ordinal==1 and request.prepared.limit==2,'historical author budget changed')
    old=read(p['old_snapshot']);inventory={r['relative']:r['sha256'] for r in old['files']};base=Path(old['root'])
    for b in c['code']:
        path=Path(b['path']);require(path.is_relative_to(base) and inventory.get(str(path.relative_to(base)))==b['sha256'],'historical author code differs');E.checked_bytes(b)
    require(c['budget_evidence']==list(request.prepared.budget_evidence),'historical author budget evidence changed')
    for b in [*c['evidence'],*c['budget_evidence']]:E.checked_bytes(b)
    return c


def validate_review_call(binding,call):
    p=read(binding);owned(binding,p);submitted=read(call.submitted)
    require(submitted.get('scope')==dict(workbook_id=p['workbook'],original_sha256=p['original']['sha256'],lane='harbor-dialogue-author',budget_id='dialogue-review')
        and submitted['semantic_ordinal']==1 and submitted['semantic_limit']==2,'pre-review repair changed remaining review scope')
    require(any(E.checked_bytes(b)==E.checked_bytes(binding) for b in submitted['budget_evidence']),'review omitted deterministic repair authority')


def authorize(worker,selection,lease,*,old_snapshot,old_descriptor,blocked,directory,renewal=None):
    from scripts.batch import harbor_instruction_worker as W,harbor_instruction_lease_renewal as L,harbor_disclosure_manifest_revision as M
    from scripts.batch import harbor_dialogue_scope_context as scope
    from scripts.batch.harbor_instruction_repair import prefix
    from scripts.batch.harbor_continuation_audit import validate_selection
    with (worker.run/'worker.lock').open('a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        require(getattr(worker,'dialogue_pre_review_repair',None) is None,'pre-review correction already selected')
        require(all(getattr(worker,k,None) is None for k in L.CONTINUATIONS if k not in ('disclosure_revision','lease_renewal')),'unsupported prior dialogue history')
        _,before,tracker=W.active(worker,selection,lease)
        failure=read(blocked);require(blocked==record(W.step_directory(worker,'dialogue-paraphraser')/'blocked.json')
            and failure['status']=='DIALOGUE_REPAIR_REQUIRED' and failure['step']=='dialogue-paraphraser','exact failed paraphraser checkpoint required')
        old_selection=codec.decode(failure['selection']);validate_selection(replace(old_selection,runtime=selection.runtime,observation=selection.observation),selection)
        M.validate_renewal(failure['lease'],renewal,before,lease)
        require(not (W.step_directory(worker,'dialogue-paraphraser')/'complete.json').exists(),'completed paraphraser cannot be replaced')
        require(not any(W.step_directory(worker,n).exists() for n in W.STEPS[12:]),'later dialogue work already attempted')
        prior_continuations={k:getattr(worker,k) for k in ('disclosure_revision','lease_renewal')}
        seed=dict(old_descriptor=old_descriptor,old_snapshot=old_snapshot,worker_run=str(worker.run),prior_continuations=prior_continuations)
        old=old_worker(seed);old_code_inventory=L._historical_inventory(old,old_snapshot);intent=read(failure['intent'])
        require(intent['code']==old_code_inventory and intent['initial']==record(worker.run/'initial.json') and intent['selection']==failure['selection'],'failed paraphraser implementation/context changed')
        L.validate_historical_integrity(old,before,old_snapshot);M.validate_historical_integrity(old,before,old_snapshot)
        code_changes=changes(old_snapshot,worker.root)
        original=worker.run/'producer/dialogue';claims=record(original/'claims.json');draft=record(original/'round-01/draft.md');check=record(original/'round-01/draft-check.json')
        require(read(check)['passed'] is False and not (original/'review-call-01.json').exists()
            and not (worker.run/'producer/requests/dialogue-review').exists(),'review already prepared, completed, or uncertain')
        requests=[W.request_saved(W.step_directory(worker,k)/'request.json') for k in ('dialogue-writer','dialogue-paraphraser')]
        observed=[E.inspect(q.prepared,worker.campaign) for q in requests]
        require(all(x.status=='completed' and x.call is not None for x in observed),'author call incomplete or uncertain')
        pair=tuple(x.call for x in observed)
        old_inventory={r['relative']:r['sha256'] for r in read(old_snapshot)['files']};old_root=Path(read(old_snapshot)['root'])
        for req in requests:
            context_value=read(req.context)
            require(context_value['request']==req.prepared.request and context_value['budget_evidence']==list(req.prepared.budget_evidence),'old author request context changed')
            for b in context_value['code']:
                path=Path(b['path']);require(path.is_relative_to(old_root) and old_inventory.get(str(path.relative_to(old_root)))==b['sha256'],'old author code differs from pinned snapshot');E.checked_bytes(b)
            for b in [*context_value['evidence'],*context_value['budget_evidence']]:E.checked_bytes(b)
        from scripts.batch import harbor_dialogue_authoring as A
        A.verify_pair(pair,policy=requests[0].prepared.policy,claims=read(claims),draft=Path(draft['path']).read_text(),round=1,root=worker.root,
            workbook=selection.content.workbook,original_sha256=before['bindings']['original_raw_source']['sha256'])
        fixed,sentence_proof=repair_sentence(Path(draft['path']).read_text(),read(claims),selection.content.bindings['source_file'],root=worker.root)
        validator=W.producer.skill_module(worker.root,'additional-assumptions-dialogue','validate_dialogue')
        report=validator.check_draft(fixed,read(claims),selection.content.stage,source_workbook=selection.content.bindings['source_file']);require(report['passed'] is True and not report['faults'],'proposed exact correction still fails mechanical accuracy')
        current_prefix=prefix(before,selection,worker.context,stop='dialogue')
        directory=E.safe(directory);require(not directory.exists(),'pre-review namespace used')
        reserved=worker.run/'dialogue-pre-review-reservation.json';require(not reserved.exists(),'pre-review repair already reserved')
        require(W.active(worker,selection,lease)[1]==before,'tracker changed before pre-review reservation')
        W.keep(reserved,dict(schema_version=SCHEMA,directory=str(directory),blocked=blocked,sentence_replacements=1,model_calls_added=0))
        updated,equivalence=scope.requalify_selected(selection.content,directory/'scope-equivalence',root=worker.root)
        selected=replace(selection,content=updated)
        target=directory/'producer/dialogue';shutil.copytree(original,target)
        corrected=target/'round-01/draft.md';corrected.write_text(fixed)
        # The original failed draft/check and both Calls remain at their owning paths.
        for path in (target/'round-01/draft-check.json',):path.unlink()
        W.keep(target/'round-01/draft-check.json',report)
        history=[blocked,failure['intent'],draft,check,claims,*[q.context for q in requests],*pair[0].__dict__.values(),*pair[1].__dict__.values()]
        history=[x for x in history if x is not None]
        for req in requests:
            context=read(req.context)
            require(context['request']==req.prepared.request and context['budget_evidence']==list(req.prepared.budget_evidence),'old author context changed')
            history.extend([*context['code'],*context['evidence'],*context['budget_evidence']])
        archives=[W.snapshot(directory/'history'/('%05d.blob'%i),b) for i,b in enumerate(history)]
        require(W.active(worker,selection,lease)[1]==before,'tracker changed during pre-review preparation')
        permit=W.keep(directory/'permit.json',dict(schema_version=SCHEMA,phase='dialogue-review',round=1,semantic_budget_reset=False,model_calls_added=0,sentence_replacements=1,
            worker_run=str(worker.run),workbook=selection.content.workbook,initial=record(worker.run/'initial.json'),old_descriptor=old_descriptor,old_snapshot=old_snapshot,
            current_code=W.code(worker),current_root=str(worker.root),current_inventory=inventory(worker.root),code_changes=code_changes,original=before['bindings']['original_raw_source'],source=selection.content.bindings['source_file'],
            selection=codec.encode(selected),old_selection=codec.encode(old_selection),lease=asdict(lease),failed_lease=failure['lease'],renewal=renewal,
            predecessor=blocked,blocked=blocked,prior_continuations=prior_continuations,tracker=W.snapshot(directory/'tracker.before.json',tracker),
            author_calls=[codec.encode(x) for x in pair],author_contexts=[x.context for x in requests],policy=asdict(requests[0].prepared.policy),
            old_draft=draft,old_check=check,claims=claims,corrected_draft=record(corrected),sentence_proof=sentence_proof,scope_equivalence=equivalence,
            dialogue_directory=str(target),history=archives,prefix=current_prefix,mechanical=report,reservation=record(reserved)))
        W.keep(worker.run/'dialogue-pre-review-repair.json',permit)
        return permit


def review_projection(binding,claims,draft,*,root):
    p=read(binding);owned(binding,p)
    require(claims==read(p['claims']),'correction review changed original claims')
    fixed,proof=repair_sentence(Path(p['old_draft']['path']).read_text(),claims,p['source'],root=root)
    require(proof==p['sentence_proof'] and draft==fixed,'correction review source or sentence differs')
    return dict(schema_version='harbor-dialogue-source-proved-correction/v1',authority=binding,
        original_claims=p['claims'],original_draft=p['old_draft'],original_author_calls=p['author_calls'],
        corrected_draft=p['corrected_draft'],sentence_proof=proof,claims_unchanged=len(claims['claims']),
        generated_spoken_trailer_is_not_source_truth=True)


ROUND2_ALLOWED={'scripts/batch/harbor_dialogue_pre_review_repair.py',
 'scripts/batch/harbor_dialogue_semantic_retry.py','scripts/batch/harbor_dialogue_authoring.py',
 'scripts/batch/harbor_instruction_producer.py','scripts/batch/harbor_semantic_readiness.py',
 'scripts/batch/harbor_instruction_worker.py','scripts/harbor_instruction_campaign.py',
 'scripts/batch/harbor_instruction_lease_renewal.py'}


def validate_historical_integrity(worker,before,*,root):
    """Current validators read original Q evidence under an explicit code successor."""
    p=read(worker.dialogue_pre_review_repair);owned(worker.dialogue_pre_review_repair,p)
    require(p['worker_run']==str(worker.run) and p['original']==before['bindings']['original_raw_source']
        and p['source']==before['bindings']['source_file'],'historical correction source identity changed')
    old_inventory=p['current_inventory'];current=inventory(root)
    require(set(old_inventory)<=set(current),'correction successor removed original code')
    require({name for name in current if current[name]!=old_inventory.get(name)}<=ROUND2_ALLOWED,'unreviewed correction successor files')
    require(worker.disclosure_revision==p['prior_continuations']['disclosure_revision'],'historical correction detached manifest')
    old=old_worker(p)
    from scripts.batch import harbor_instruction_lease_renewal as L,harbor_disclosure_manifest_revision as M
    L.validate_historical_integrity(old,before,p['old_snapshot']);M.validate_historical_integrity(old,before,p['old_snapshot'])
    original_pair(p,root=root)
    fixed,proof=repair_sentence(Path(p['old_draft']['path']).read_text(),read(p['claims']),p['source'],root=root)
    require(proof==p['sentence_proof'] and fixed==Path(p['corrected_draft']['path']).read_text(),'historical source correction changed')
    return p


def historical_context(worker,selection,tracker):
    p=validate_historical_integrity(worker,read(tracker),root=worker.root)
    require(selection.content.stage==codec.decode(p['selection']).content.stage,'historical correction stage changed')
    directory=Path(p['dialogue_directory']);claims=read(p['claims'])
    from scripts.batch.harbor_readiness import skill_module
    composer=skill_module(worker.root,'additional-assumptions-dialogue','compose_draft');template,slots=composer.compose(claims,claims['writer_pack'])
    require((directory/'draft_template.md').read_text()==template and read(record(directory/'slots.json'))==slots,'historical correction scaffold changed')
    return directory,claims,template,slots


def validate_semantic_task(task):
    """Bind later authoring to the owning remaining-round CAS, not a task flag."""
    from scripts.batch import harbor_dialogue_semantic_retry as D
    complete=read(task.dialogue_semantic_retry);permit=read(complete['permit']);before=read(task.tracker)
    D.validate_pre_review_link(task.dialogue_semantic_retry,task.dialogue_pre_review_repair,before,task.run.parent,root=task.root)
    return permit


def remaining_feedback(binding,*,root):
    """Source formulas constrain the last ordinary writer revision's feedback."""
    p=read(binding);owned(binding,p);claims=read(p['claims'])
    directory=Path(p['dialogue_directory']);review=read(record(directory/'round-01/review.json'))
    rejected=[row for row in review['accuracy']['claims'] if row['must_say']!='entailed']
    require(len(rejected)==1 and rejected[0]['must_say']=='contradicted','remaining repair requires exact one error-operand ambiguity')
    target=next(c for c in claims['claims'] if c['record_id']==rejected[0]['record_id'])
    require('#REF!' in target['reviewer_only']['evidence'] and any('#REF!' in x for x in rejected[0].get('findings',[])),'remaining repair is not the proved error operand')
    from scripts.batch.harbor_readiness import skill_module
    renderer=skill_module(root,'task-disclosure','disclose');E.checked_bytes(p['source']);gold=renderer.Book(Path(p['source']['path']))
    formulas=[]
    for claim in claims['claims']:
        r=claim.get('reviewer_only',{})
        if r.get('source')!='custom_method_detector':continue
        cell=r['cells'][0];require(gold.formula[renderer.parse_ref(cell,claim['sheet'])]==r['evidence'],'remaining feedback formula changed')
        formulas.append(dict(record_id=claim['record_id'],cell=cell,formula=r['evidence']))
    return dict(schema_version='dialogue-source-proved-remaining-feedback/v1',authority=binding,
        failed_review=record(directory/'round-01/review.json'),error_operand_claim=target['record_id'],
        instruction='Describe multiplication by the #REF! error value explicitly, then the IFERROR fallback to zero. Do not describe it as quoted text. Preserve every other already entailed calculation and all required source formula constants; constants in these formulas are permitted modelling inputs, not final output answers. Preserve the source-proved locked-input correction. Hidden claim/slot trace comments are metadata, not spoken financial claims; preserve the fixed template scaffolding. Improve wording only without changing financial meaning.',
        source=p['source'],source_formulas=formulas,preserved_locked_input_correction=p['sentence_proof'])

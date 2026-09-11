"""Fixed fresh instruction-stage producers; root owns leases and lane CAS.

No generic prompt/validator callback. Preparation is offline; dispatch uses the
installed immutable executor. Owning validators, never model PASS flags, decide
whether an observed response can be consumed. No publication or Docker calls.
"""
from dataclasses import dataclass,asdict
from pathlib import Path
import json,re,uuid,sys
from scripts.batch.harbor_admission import record,read,require
from scripts.batch.harbor_tracker import validate_tracker
from scripts.batch.harbor_tracker_lifecycle import validate_lifecycle,checked_record
from scripts.batch.harbor_readiness import tree,skill_module
from scripts.batch.harbor_runtime_readiness import delivered_workbook
from scripts.batch.harbor_model_executor import PreparedCall,SemanticScope,Campaign,execute,inspect,retain,canonical,sha
from scripts.batch.harbor_model_review_provenance import Policy,validate_call,require_independent
from scripts.batch.harbor_semantic_readiness import disclosure_manifest,workbook_evidence,disclosure_review
from scripts.batch.harbor_review_context import naturalization_review

ROOT=Path(__file__).resolve().parents[2]
NAT='harbor-instruction-naturalizer';DIALOGUE='harbor-dialogue-author';DISCLOSURE='harbor-disclosure-writer'

@dataclass(frozen=True)
class Task:
    tracker: dict                 # Root-selected current strict tracker record.
    stage: Path
    stage_tree: dict
    run: Path                    # Persistent per-original-workbook producer namespace.
    policy: Policy
    root: Path = ROOT
    code_repair: dict | None = None
    scope_equivalence: dict | None = None
    disclosure_revision: dict | None = None
    dialogue_pre_review_repair: dict | None = None
    dialogue_semantic_retry: dict | None = None

@dataclass(frozen=True)
class Request:
    prepared: PreparedCall
    context: dict                 # Immutable exact preparation evidence record.


def text(binding):
    checked_record(binding);return Path(binding['path']).read_bytes().decode('utf-8')


def put(path,value):
    raw=value if isinstance(value,bytes) else canonical(value)
    path=Path(path)
    if path.exists():require(path.read_bytes()==raw,'existing producer artifact changed')
    else:
        path.parent.mkdir(parents=True,exist_ok=True);retain(path,raw)
    return record(path)


def task_context(task):
    require(isinstance(task,Task) and isinstance(task.policy,Policy),'root-selected typed task required')
    before=read(task.tracker);validate_tracker(before);validate_lifecycle(before)
    wb=before['workbook_id'];require(wb in before['requested_ids'],'workbook was not admitted')
    require(before['execution_state'] not in ('PROMOTED','STOPPED','publication_queued'),'terminal task cannot generate new prose')
    require(task.stage.name==wb+'-outputs' and tree(task.stage)==task.stage_tree,'task identity or bytes changed')
    require(not (task.stage/'tests/dialogue-applied.json').exists(),'already applied dialogue must not be regenerated')
    original=before['bindings']['original_raw_source'];checked_record(original)
    require(not task.run.resolve().is_relative_to(task.stage.resolve()),'producer evidence cannot enter delivered task')
    task.run.mkdir(parents=True,exist_ok=True)
    identity={'workbook':wb,'original':original,'policy':asdict(task.policy)}
    put(task.run/'identity.json',identity)
    return before,identity


def dialogue_source_workbook(task):
    """Return only the checked selected-source binding in the bound tracker."""
    before=read(task.tracker);validate_tracker(before);validate_lifecycle(before)
    source=before['bindings']['source_file'];checked_record(source)
    return {'path':source['path'],'sha256':source['sha256']}


def lane_context(task,lane):
    before,identity=task_context(task)
    from scripts.batch.harbor_completed_lanes import predecessors
    kind={NAT:'naturalization',DIALOGUE:'dialogue',DISCLOSURE:'disclosure'}[lane]
    require(predecessors(before,kind)==lane,'canonical lane order changed')
    require(before['lane_state'][lane].get('state') in ('pending','ready','running','repairing'),'completed lane cannot generate again')
    return before,identity


def skill_prompt(root,skill,starts):
    raw=(root/'.cursor/skills'/skill/'SKILL.md').read_text()
    matches=[block for block in re.findall(r'```text\n(.*?)\n```',raw,re.S) if block.startswith(starts)]
    require(len(matches)==1,'owning skill prompt missing or ambiguous')
    return matches[0]


def code_evidence(task):
    from scripts.batch import harbor_semantic_readiness,harbor_review_context,harbor_workbook_evidence,harbor_dialogue_authoring,harbor_dialogue_operand_evidence,harbor_dialogue_scope_context,harbor_dialogue_scope_discovery
    paths=[Path(__file__).resolve(),task.root/'scripts/batch/harbor_model_executor.py',task.root/'scripts/batch/harbor_model_review_provenance.py',Path(harbor_semantic_readiness.__file__),Path(harbor_review_context.__file__),Path(harbor_workbook_evidence.__file__),Path(harbor_dialogue_authoring.__file__),Path(harbor_dialogue_operand_evidence.__file__),Path(harbor_dialogue_scope_context.__file__),Path(harbor_dialogue_scope_discovery.__file__)]
    for skill in ('task-disclosure','naturalize-finance-task-instruction','additional-assumptions-dialogue'):
        directory=task.root/'.cursor/skills'/skill
        paths.append(directory/'SKILL.md');paths.extend(sorted((directory/'scripts').glob('*.py')))
    from scripts import harbor_instruction_code_applicability as applicability
    paths += applicability.paths()
    from scripts.batch import harbor_disclosure_evidence,harbor_disclosure_payload_repair
    paths += [Path(harbor_disclosure_evidence.__file__),Path(harbor_disclosure_payload_repair.__file__)]
    from scripts.batch import harbor_method_assessments,harbor_method_items
    paths += [Path(harbor_method_assessments.__file__),Path(harbor_method_items.__file__)]
    return [record(path) for path in paths]


def _request(task,kind,lane,ordinal,limit,body,prompt,evidence,*,mode='json_object',writer_round=None,structural_round=None):
    before,identity=lane_context(task,lane)
    require(type(ordinal) is int and 1<=ordinal<=limit<=2,'instruction attempt ceiling exceeded')
    if writer_round is not None:
        require(kind=='naturalization-review' and type(writer_round) is int and writer_round in (1,2) and ordinal==limit==1,'invalid per-writer review scope')
    require(structural_round is None or (kind in ('dialogue-writer','dialogue-paraphraser') and type(structural_round) is int and structural_round in (1,2) and ordinal==limit==1 and writer_round is None),'invalid structural correction scope')
    budget_id=('dialogue-structural-round-%02d'%structural_round) if structural_round is not None else (kind if writer_round is None else kind+'-writer-%02d'%writer_round)
    revision=task.disclosure_revision if kind=='disclosure-review' else None
    if revision is not None:
        from scripts.batch.harbor_disclosure_revision import validate_task
        validate_task(task)
        require(ordinal==limit==1 and writer_round is None and structural_round is None,'invalid corrective disclosure scope')
        budget_id='disclosure-review-revision-02';evidence=[*evidence,revision]
    pre_review=task.dialogue_pre_review_repair if kind=='dialogue-review' and ordinal==1 else None
    if pre_review is not None:
        from scripts.batch.harbor_dialogue_pre_review_repair import validate_task
        validate_task(task);require(ordinal==1 and limit==2,'pre-review correction changed review allowance');evidence=[*evidence,pre_review]
    if task.dialogue_pre_review_repair is not None and ordinal==2 and kind in ('dialogue-writer','dialogue-paraphraser','dialogue-review'):
        from scripts.batch import harbor_dialogue_pre_review_repair as Q
        Q.validate_semantic_task(task);evidence=[*evidence,task.dialogue_pre_review_repair,task.dialogue_semantic_retry]
        if kind in ('dialogue-writer','dialogue-paraphraser'):body['source_proved_feedback']=Q.remaining_feedback(task.dialogue_pre_review_repair,root=task.root)
    directory=task.run/'requests'/budget_id/('attempt-%02d'%ordinal)
    payload={'model':task.policy.model,'reasoning_effort':task.policy.reasoning_effort,'messages':[{'role':'system','content':prompt},{'role':'user','content':json.dumps(body,ensure_ascii=False,allow_nan=False)}]}
    request=put(directory/'request.json',payload)
    # Snapshot the real validated tracker/state evidence before mutable state moves.
    snapshot=[]
    for i,b in enumerate([task.tracker,*evidence]):snapshot.append(put(directory/('budget-%02d.snapshot'%i),Path(checked_record(b)['path']).read_bytes()))
    context=put(directory/'context.json',{'schema_version':'harbor-instruction-request/v1','kind':kind,**({'dialogue_pre_review_repair':pre_review} if pre_review is not None else {}),**({'disclosure_revision':revision} if revision is not None else {}),**({'structural_round':structural_round} if structural_round is not None else {}),**({'writer_round':writer_round} if writer_round is not None else {}),'workbook':identity['workbook'],'original':identity['original'],'stage_tree':task.stage_tree,'request':request,'evidence':evidence,'budget_evidence':snapshot,'ordinal':ordinal,'limit':limit,'response_mode':mode,'code':code_evidence(task)})
    attempt_path=directory/'attempt-id.json'
    attempt=read(record(attempt_path))['attempt_id'] if attempt_path.exists() else str(uuid.uuid4())
    put(attempt_path,{'attempt_id':attempt})
    p=PreparedCall(request,task.policy,SemanticScope(identity['workbook'],identity['original']['sha256'],lane,budget_id),ordinal,limit,attempt,tuple(snapshot),response_mode=mode)
    return Request(p,context)


def validate_request(task,request,kind=None):
    before,identity=task_context(task);context=read(request.context);p=request.prepared
    require(context['stage_tree']==task.stage_tree and context['workbook']==identity['workbook'] and context['original']==identity['original'],'prepared task changed')
    writer_round=context.get('writer_round')
    if context['kind']=='naturalization-review':
        require(not (task.run/'requests/naturalization-review').exists(),'legacy reviewer scope needs explicit reconciliation; cannot reset review budget')
        require(type(writer_round) is int and writer_round in (1,2) and p.ordinal==p.limit==1,'invalid per-writer review scope')
    else:require(writer_round is None,'unexpected writer review scope')
    structural_round=context.get('structural_round')
    require(structural_round is None or (context['kind'] in ('dialogue-writer','dialogue-paraphraser') and type(structural_round) is int and structural_round in (1,2) and p.ordinal==p.limit==1 and writer_round is None),'invalid structural correction scope')
    budget_id=('dialogue-structural-round-%02d'%structural_round) if structural_round is not None else (context['kind'] if writer_round is None else context['kind']+'-writer-%02d'%writer_round)
    revision=context.get('disclosure_revision')
    if context['kind']=='disclosure-review' and task.disclosure_revision is not None:
        from scripts.batch.harbor_disclosure_revision import validate_task
        authority=validate_task(task)
        require(revision==authority.get('request_revision',task.disclosure_revision) and p.ordinal==p.limit==1 and p.physical_repair is None,'corrective review history changed')
        require(revision in context['evidence'] and any(Path(checked_record(b)['path']).read_bytes()==Path(checked_record(revision)['path']).read_bytes() for b in context['budget_evidence']),'corrective review budget proof missing')
        budget_id='disclosure-review-revision-02'
    else:require(revision is None,'unexpected corrective review receipt')
    pre_review=context.get('dialogue_pre_review_repair')
    if context['kind']=='dialogue-review' and task.dialogue_pre_review_repair is not None and p.ordinal==1:
        require(pre_review==task.dialogue_pre_review_repair and pre_review in context['evidence'],'pre-review authority omitted or substituted')
        require(any(Path(checked_record(b)['path']).read_bytes()==Path(checked_record(pre_review)['path']).read_bytes() for b in context['budget_evidence']),'pre-review budget proof missing')
    else:require(pre_review is None,'unexpected pre-review authority')
    require(context['request']==p.request and budget_id==p.scope.budget_id and (kind is None or context['kind']==kind),'prepared request/kind substitution')
    require(p.scope.workbook_id==identity['workbook'] and p.scope.original_sha256==identity['original']['sha256'],'prepared original identity changed')
    require((context['ordinal'],context['limit'],context['response_mode'])==(p.ordinal,p.limit,p.response_mode),'prepared budget/protocol changed')
    from scripts import harbor_instruction_code_applicability as applicability
    if p.physical_repair is not None:
        from scripts.batch.harbor_disclosure_review_resume import validate_request_context
        validate_request_context(task,request)
    elif context['kind']=='disclosure-review' and task.disclosure_revision is not None and read(task.disclosure_revision).get('validation_recovery') is not None:
        from scripts.batch.harbor_disclosure_request_recovery import validate_context
        validate_context(task,request)
    elif task.dialogue_pre_review_repair is not None and p.ordinal==1 and context['kind'] in ('dialogue-writer','dialogue-paraphraser'):
        from scripts.batch.harbor_dialogue_pre_review_repair import validate_retained_request
        validate_retained_request(task,request)
    elif task.code_repair is not None and context['code']!=code_evidence(task):
        from scripts.batch.harbor_instruction_repair import validate_retained_request
        validate_retained_request(task,request)
    else:applicability.validate(context,code_evidence(task),request)
    return context


def dispatch(task,request,campaign,*,credential):
    """Root calls only under its task/model coordination; never while tracker locked."""
    before,identity=task_context(task);context=validate_request(task,request)
    require((task.code_repair is None and task.dialogue_pre_review_repair is None) or context['code']==code_evidence(task),
            'historical completed repair requests cannot be dispatched again')
    require(context['stage_tree']==task.stage_tree and context['workbook']==identity['workbook'] and context['original']==identity['original'],'prepared task changed before dispatch')
    require(context['request']==request.prepared.request,'prepared request substitution')
    for binding in context['evidence']:checked_record(binding)
    return execute(request.prepared,campaign,credential=credential)


def observed(request,campaign):
    result=inspect(request.prepared,campaign)
    require(result.status=='completed' and result.call is not None,'model call is incomplete or rejected; do not replay')
    return result.call


def prepare_disclosure_review(task,golden,*,root=ROOT):
    before,_=task_context(task);require(before['lane_state'][DISCLOSURE].get('attempt',0)<=1,'disclosure review budget exhausted')
    stage=task.stage;wb=before['workbook_id'];instruction=record(stage/'instruction.md');disclosure=record(stage/'tests/disclosure.json');delivered=record(delivered_workbook(stage,wb))
    checked_record(golden);require(golden['sha256']==before['raw_source']['sha256'],'disclosure golden differs from admitted source candidate')
    renderer=skill_module(root,'task-disclosure','disclose')
    run=task.run if task.disclosure_revision is None else Path(read(task.disclosure_revision)['producer_run'])
    faith=renderer.faithcheck_task(stage,run/'disclosure-build',golden['path'])
    put(run/'disclosure/fresh-faithcheck.json',faith);require(faith['passed'] is True and faith['faults']==[],'disclosure mechanical faithcheck failed')
    verified=renderer.verify_task(stage);put(run/'disclosure/fresh-verify.json',verified)
    require(verified['passed'] is True and verified['faults']==[],'disclosure mechanical verification failed')
    fresh_manifest=disclosure_manifest(instruction,disclosure,golden,delivered,root=root)
    if task.disclosure_revision is not None and read(task.disclosure_revision).get('schema_version')=='harbor-disclosure-manifest-revision/v1':
        from scripts.batch.harbor_disclosure_manifest_revision import validate_task
        authority=validate_task(task)
        require(fresh_manifest==read(authority['full_manifest']),'current full manifest differs from corrected partition')
        fresh_manifest=read(authority['manifest'])
    manifest=put(run/'disclosure/sentences.json',fresh_manifest)
    body={'manifest':read(manifest),'manifest_sha256':manifest['sha256'],'workbooks':{'golden':workbook_evidence(golden),'delivered':workbook_evidence(delivered)},'instruction':text(instruction),'disclosure':read(disclosure)}
    from scripts.batch.harbor_disclosure_evidence import project_body
    body=project_body(body)
    schema={'schema_version':'harbor-disclosure-faithfulness/v1','model':'gpt-5.6-sol-high','input_hashes':read(manifest)['input_hashes'],'manifest_sha256':manifest['sha256'],'sentences':[{'sentence_id':'exact manifest id','text':'exact manifest text','record_ids':[],'golden_evidence':[],'delivered_evidence':[],'verdict':'pass|blocking|non_blocking','findings':[]}],'blocking_findings':[],'non_blocking_flags':[],'passed':False}
    prompt='You are a fresh independent workbook disclosure reviewer. Independently inspect both supplied complete workbook snapshots and every disclosed sentence. Check reference identity, copied scope, operators, signs, assumptions, required coverage and answer leakage. Never infer correctness from rendered text or an upstream passed flag. Cover each sentence and record exactly once in manifest order. Record concrete independent observations from both workbooks. Fail missing evidence or any semantic change. Return only the exact JSON schema; passed is true only if every sentence is pass or non_blocking and there are no blocking findings. '+json.dumps(schema)
    prompt+=' Workbook cells use the declared lossless table encoding; every cell and workbook metadata item is retained. All disclosed records are retained. Auxiliary method assessments omit only explicit cell scopes unrelated to every disclosed record; uncertain scopes are retained.'
    if task.disclosure_revision is not None and read(task.disclosure_revision).get('schema_version')=='harbor-disclosure-manifest-revision/v1':
        prompt+=' This is a scoped corrective manifest review. Review ONLY the '+str(len(read(manifest)['sentences']))+' exact units and record IDs listed in the supplied manifest. Other retained instruction text and disclosure records are context, not additional review units. Independently verify each listed full unit against BOTH complete workbook snapshots, including its complete calculation and signs. Return exactly the listed manifest rows, in manifest order, with no additional rows. An earlier review is not evidence of correctness for these listed units.'
    return _request(task,'disclosure-review',DISCLOSURE,1,1,body,prompt,[manifest,instruction,disclosure,golden,delivered,record(run/'disclosure/fresh-faithcheck.json'),record(run/'disclosure/fresh-verify.json')]),manifest


def consume_disclosure_review(task,request,manifest,golden,campaign,*,root=ROOT):
    validate_request(task,request,'disclosure-review')
    before,_=task_context(task);call=observed(request,campaign)
    result=disclosure_review(call,manifest,instruction=record(task.stage/'instruction.md'),disclosure=record(task.stage/'tests/disclosure.json'),golden=golden,delivered=record(delivered_workbook(task.stage,before['workbook_id'])),policy=task.policy,root=root,revision=task.disclosure_revision)
    return {'call':call,'result':result}


def initialize_naturalization(task,*,previous=None,root=ROOT):
    before,_=lane_context(task,NAT);r=skill_module(root,'naturalize-finance-task-instruction','naturalize_recovery');n=task.run/'naturalization'
    r.init_recovery(task.stage/'instruction.md',n,instruction_path=task.stage/'instruction.md',task_toml_path=task.stage/'task.toml',answer_key_path=task.stage/'tests/answer_key.json')
    if previous:
        checked_record(previous);r.inherit_attempt_budget(n,Path(previous['path']).parent,previous_state_sha256=previous['sha256'],reason='Root-bound upstream source change; preserve all spent writer attempts')
    state=r._read_state(n);r._verified_budget(n,state)
    require(r._attempts_used(state)>=int(before['lane_state'][NAT].get('attempt') or 0),'tracker writer budget was not inherited')
    r.bind_endpoint(n,'https://litellm.labelbox.com')
    source=r._verified_snapshot(n,r._read_state(n));f=skill_module(root,'naturalize-finance-task-instruction','freeze_protected_spans');writer,spans=f.freeze(source.decode('utf-8'))
    put(n/'writer_source.md',writer.encode('utf-8'));put(n/'frozen_spans.json',{'source_sha256':sha(source),'spans':spans})
    return record(n/'state.json')


def _nat(task,root):
    before,_=lane_context(task,NAT);r=skill_module(root,'naturalize-finance-task-instruction','naturalize_recovery');n=task.run/'naturalization';state=r._read_state(n)
    r._verified_snapshot(n,state);r._verified_budget(n,state);r._verify_attempt_evidence(n,state)
    require(r._attempts_used(state)>=int(before['lane_state'][NAT].get('attempt') or 0),'writer budget regressed from tracker')
    return r,n,state


def naturalization_writer_prompt(root, previous_report=None, *, legacy=False):
    prompt=skill_prompt(root,'naturalize-finance-task-instruction','You are a senior finance professional')
    prompt+='\nLine prefixes such as `10|` are tool metadata and must never be copied into either output.\nFor this stateless API call, return only one JSON object with exactly preamble_body and input_body string fields. Keep every frozen wrapper in those bodies. Do not output filenames or any other fields.'
    if not legacy:
        prompt+='\nReturn only the editable preamble and the body under the Input Data heading. The input_body ends immediately before the next level-two heading in writer_source. Do not include the Input Data heading, What to compute, Workbook disclosure, Output, or any other immutable section or heading. The assembler supplies all unchanged sections. Keep financial meaning and every protected wrapper exact.'
    if previous_report is not None:
        require(previous_report.get('attempt')==1 and previous_report.get('valid') is False and isinstance(previous_report.get('reason_codes'),list) and previous_report['reason_codes'] and all(isinstance(x,str) for x in previous_report['reason_codes']),'exact failed first mechanical report required')
        prompt+='\nYour previous writer attempt failed mechanical validation with reason codes: '+json.dumps(previous_report['reason_codes'])+'. Return only the editable bodies. In input_body, stop before the next level-two heading. Do not include any immutable section or its heading; the assembler retains those sections. Keep all financial meaning and protected spans unchanged.'
    return prompt


def prepare_naturalization_writer(task,*,root=ROOT):
    r,n,state=_nat(task,root);used=r._attempts_used(state)
    require(state['status'] in ('initialized','retry_ready') and used<2,'no remaining naturalization writer attempt')
    evidence=[record(n/'state.json'),record(n/'writer_source.md'),record(n/'frozen_spans.json')]
    previous=None
    if used==1 and state['status']=='retry_ready' and len(state['attempts'])==1 and state['attempts'][0].get('valid') is False:
        previous=read(record(n/'attempt-01/validation.json'))
        evidence += [record(n/'attempt-01'/name) for name in ('validation.json','preamble_body.md','input_body.md')]
        if state['attempts'][0].get('candidate_sha256') is not None:
            candidate=record(n/'attempt-01/candidate.md')
            require(candidate['sha256']==state['attempts'][0]['candidate_sha256'],'assembled candidate identity changed')
            evidence.append(candidate)
        else:
            require(not (n/'attempt-01/candidate.md').exists(),'unassembled failure has an unexpected candidate')
    prompt=naturalization_writer_prompt(root,previous)
    if used==1 and state['attempts'][0].get('valid') is True:
        rejection=record(n/'attempt-01/semantic-review.json')
        require(read(rejection).get('accepted') is False and read(rejection) in state.get('semantic_reviews',[]),
                'remaining revision requires retained semantic rejection')
        call_records=[record(n/'review-call-01.json'),*map(record,sorted(n.glob('coverage-review-call-01-*.json'))) ]
        reviews=[]
        for binding in call_records:
            from scripts.batch.harbor_model_review_provenance import Call
            call=Call(**read(binding))
            reviews.append(validate_call(call,read(call.request),policy=task.policy)['review'])
            evidence.extend([binding,call.request,call.response])
        candidate=record(n/'attempt-01/candidate.md')
        require(candidate['sha256']==read(rejection)['candidate_sha256'],'rejected candidate changed')
        evidence.extend([rejection,candidate])
        prompt+='\nYour first candidate was rejected. This is the remaining writer revision, not a new budget. Preserve all source requirements and frozen spans. Correct the findings without deleting or weakening clauses. Exact rejected candidate:\n'+text(candidate)
        prompt+='\nOwning rejection:\n'+json.dumps(read(rejection),ensure_ascii=False)+'\nExact independent reviewer findings:\n'+json.dumps(reviews,ensure_ascii=False)
    body={'writer_source':text(record(n/'writer_source.md'))}
    return _request(task,'naturalization-writer',NAT,used+1,2,body,prompt,evidence)


def consume_naturalization_writer(task,request,campaign,*,root=ROOT):
    validate_request(task,request,'naturalization-writer')
    r,n,state=_nat(task,root);call=observed(request,campaign);value=validate_call(call,read(request.prepared.request),policy=task.policy)['review']
    require(set(value)=={'preamble_body','input_body'} and all(isinstance(v,str) for v in value.values()),'naturalization writer output must contain exact two bodies')
    expected={'writer_source':text(record(n/'writer_source.md'))}
    require(json.loads(read(call.request)['messages'][1]['content'])==expected,'writer saw different frozen source')
    used=r._attempts_used(state)
    require(request.prepared.ordinal==used+1 or (request.prepared.ordinal==used and state['attempts'] and state['attempts'][-1]['attempt']==used),'writer ordinal is not current or exact resumable attempt')
    mode=state['attempts'][-1].get('submission_mode','plain') if request.prepared.ordinal==used else 'frozen-boundary-v1'
    report=r.submit_attempt(n,value['preamble_body'].encode('utf-8'),value['input_body'].encode('utf-8'),submission_mode=mode,expected_attempt=request.prepared.ordinal)
    put(n/('writer-call-%02d.json'%request.prepared.ordinal),asdict(call))
    return {'call':call,'validation':report,'state':record(n/'state.json')}


def editable_claims(root,source):
    spans=skill_module(root,'naturalize-finance-task-instruction','instruction_spans')
    bodies=spans.extract_editable_bodies(source.encode('utf-8'),spans.scan_instruction(source.encode('utf-8')))
    return [line for body in bodies for line in body.decode('utf-8').splitlines() if line.strip()]


def prepare_naturalization_review(task,*,root=ROOT):
    r,n,state=_nat(task,root);require(state['status']=='validated','mechanical NAT proof must pass before review')
    require(not (task.run/'requests/naturalization-review').exists(),'legacy reviewer scope needs explicit reconciliation; cannot reset review budget')
    source=text(record(n/'source.snapshot.md'));candidate=text(record(n/state['final_candidate']));clauses=editable_claims(root,source)
    prompt='You are a fresh independent semantic reviewer. Review clause by clause the complete source and candidate, focusing on both editable regions. Preserve every permission, obligation, prohibition, scope, procedure, financial convention and exact token. Fail added, missing, weakened or strengthened claims. Return only {"valid":true|false,"blocking_findings":[],"clause_checks":[{"source_claim":"exact source line","candidate_claim":"exact candidate excerpt","preserved":true|false}]}. Cover the following exact nonempty source lines once each, in this order, without omissions or duplicates; every candidate editable line must occur in the quoted candidate excerpts: '+json.dumps(clauses)
    return _request(task,'naturalization-review',NAT,1,1,{'source':source,'candidate':candidate},prompt,[record(n/'state.json'),record(n/'source.snapshot.md'),record(n/state['final_candidate'])],writer_round=state['attempts'][-1]['attempt'])


def consume_naturalization_review(task,request,writer,campaign,*,apply=False,root=ROOT,defer_coverage=False):
    review_context=validate_request(task,request)
    coverage_kind='naturalization-coverage-review-writer-%02d'%writer.prepared.ordinal
    require(review_context['kind'] in ('naturalization-review',coverage_kind),'unexpected naturalization review kind')
    r,n,state=_nat(task,root);call=observed(request,campaign);writer_call=observed(writer,campaign);number=state['attempts'][-1]['attempt'];attempt=n/('attempt-%02d'%number)
    candidate_path=state.get('final_candidate')
    if candidate_path is None:
        # A rejected review clears the current candidate. Exact replay reads
        # only its independently validated retained attempt/recovery artifact.
        require(state['status'] in ('retry_ready','exhausted') and state.get('semantic_reviews'),'no current or reviewed candidate')
        candidate_path=str(Path(state['boundary_recovery']['path']).parent/'candidate.md') if state.get('boundary_recovery') else 'attempt-%02d/candidate.md'%number
        require(record(n/candidate_path)['sha256']==state['semantic_reviews'][-1]['candidate_sha256'],'retained rejected candidate changed')
    source=text(record(n/'source.snapshot.md'));candidate=text(record(n/candidate_path))
    if state.get('boundary_recovery'):
        from scripts.batch.harbor_nat_boundary_recovery import validate_writer
        validate_writer(task,writer,campaign)
    else:validate_request(task,writer,'naturalization-writer')
    require(writer.prepared.ordinal==number,'naturalization writer round differs')
    if review_context['kind']=='naturalization-review':
        require(number==review_context['writer_round'],'naturalization writer/reviewer round differs')
    require(json.loads(read(call.request)['messages'][1]['content'])=={'source':source,'candidate':candidate},'naturalization reviewer context changed')
    call_name=('review-call-%02d.json'%number if review_context['kind']=='naturalization-review'
        else 'coverage-review-call-%02d-%02d.json'%(number,request.prepared.ordinal))
    put(n/call_name,asdict(call))
    try:
        result=naturalization_review(call,request.prepared.request,source=source,candidate=candidate,policy=task.policy,writer_call=writer_call,writer_request=writer.prepared.request,writer_bodies={k:record(attempt/(k+'.md')) for k in ('preamble_body','input_body')},root=root)
        checks=result['review']['clause_checks']
        quoted=[c['candidate_claim'] for c in checks];require(all(q in candidate for q in quoted),'NAT review quotes invented candidate evidence')
        require([c['source_claim'] for c in checks]==editable_claims(root,source),'independent NAT source-clause coverage incomplete')
        require(all(any(line in q for q in quoted) for line in editable_claims(root,candidate)),'independent NAT candidate-clause coverage incomplete')
    except ValueError as exc:
        if defer_coverage and str(exc) in (
                'independent NAT source-clause coverage incomplete',
                'independent NAT candidate-clause coverage incomplete'):
            from scripts.batch.harbor_nat_coverage_retry import coverage_feedback
            return {'call':call,'passed':False,'coverage_retry_required':True,
                'feedback':coverage_feedback(source,candidate,result['review'],root=root),
                'failure':str(exc),'applied':False}
        # Actual complete, independently bound reviewer content failed. The
        # owning state records failure; only root may route its remaining retry.
        if state['status']=='validated':r.reject_semantic_review(n,reason_code='semantic_mismatch',message=str(exc))
        else:
            prior=read(record(attempt/'semantic-review.json'))
            require(state['status'] in ('retry_ready','exhausted') and prior.get('accepted') is False and prior.get('message')==str(exc) and prior in state.get('semantic_reviews',[]),'rejected review checkpoint changed')
        return {'call':call,'passed':False,'failure':str(exc),'attempts_used':r._attempts_used(r._read_state(n)),'applied':False}
    r.accept_semantic_review(n,reviewer='independent-journaled-model-reviewer',message='Complete exact-context editable-clause review and owning validation passed')
    if apply:
        r.apply_recovery(n);r.verify_applied_recovery(n)
    return {'call':call,'passed':True,'review':result,'attempts_used':r._attempts_used(r._read_state(n)),'applied':apply}


def initialize_dialogue(task,*,root=ROOT):
    before,_=lane_context(task,DIALOGUE);extractor=skill_module(root,'additional-assumptions-dialogue','extract_claims');composer=skill_module(root,'additional-assumptions-dialogue','compose_draft')
    from scripts.batch import harbor_dialogue_scope_discovery as discovery
    directory=task.run/'dialogue';scope=None;receipt=None
    if not directory.exists() and extractor.extract(task.stage)['empty']:
        claims=extractor.extract(task.stage)
    elif not directory.exists():
        require('source_file' in before['bindings'],'fresh dialogue requires the exact current source')
        discovered=discovery.initialize_dialogue_claims(task.stage,before['bindings']['source_file'],directory,root=root)
        claims=discovered['claims'];scope=discovered['scope_evidence'];receipt=discovered['receipt']
    elif (directory/'source-scope/prepared.json').exists():
        receipt=record(directory/'source-scope/prepared.json');claims,options=discovery.validate(task.stage,receipt,root=root)
        scope=read(receipt)['scope'] if options else None
    else:
        # Exact retained legacy artifacts, never new scope or a budget reset.
        require((directory/'claims.json').exists(),'incomplete unjournaled dialogue requires reconciliation')
        claims=extractor.extract(task.stage)
    put(directory/'claims.json',claims);put(directory/'writer_pack.json',claims['writer_pack'])
    result={'empty':claims['empty'],'claims':record(directory/'claims.json'),'scope_evidence':scope,'scope_receipt':receipt}
    if claims['empty']:return result
    require(not int(before['lane_state'][DIALOGUE].get('attempt') or 0) or (directory/'review-call-01.json').exists(),'prior dialogue round evidence missing; cannot reset budget')
    template,slots=composer.compose(claims,claims['writer_pack']);put(directory/'draft_template.md',template.encode('utf-8'));put(directory/'slots.json',slots)
    return {**result,'template':record(directory/'draft_template.md'),'slots':record(directory/'slots.json')}


def _dialogue(task,round,root):
    before,_=lane_context(task,DIALOGUE);require(type(round) is int and round in (1,2),'maximum two dialogue writer rounds')
    if task.dialogue_pre_review_repair is not None:
        from scripts.batch.harbor_dialogue_pre_review_repair import context
        return context(task,round,root=root)
    from scripts.batch import harbor_dialogue_scope_context as scope_context,harbor_dialogue_scope_discovery as discovery
    directory=task.run/'dialogue';transition=scope_context.selected_transition(task,root=root) if round==2 else None
    extractor=skill_module(root,'additional-assumptions-dialogue','extract_claims')
    if transition is not None:
        original,expected,value=scope_context.validate_transition(transition,root=root);directory=Path(value['directory'])
    elif (directory/'source-scope/prepared.json').exists():
        bound=record(directory/'source-scope/prepared.json')
        if task.scope_equivalence is not None:
            expected,_=scope_context.validate_task_equivalence(task,task.scope_equivalence,bound,root=root)
        else:expected,_=discovery.validate(task.stage,bound,root=root)
        original=expected
    else:expected=extractor.extract(task.stage);original=expected
    claims=read(record(directory/'claims.json'))
    require(claims==expected and not claims['empty'],'dialogue claim context changed or empty')
    composer=skill_module(root,'additional-assumptions-dialogue','compose_draft');template,slots=composer.compose(claims,claims['writer_pack'])
    require(text(record(directory/'draft_template.md'))==template and read(record(directory/'slots.json'))==slots and read(record(directory/'writer_pack.json'))==claims['writer_pack'],'dialogue scaffold or writer pack changed')
    require(int(before['lane_state'][DIALOGUE].get('attempt') or 0)<=round,'dialogue round budget regressed')
    if round==2:
        previous=read(record(directory/'review-call-01.json'));from scripts.batch.harbor_model_review_provenance import Call
        call=Call(**previous);payload=read(call.request);body=json.loads(payload['messages'][1]['content'])
        require(body=={'claims.json':original,'draft.md':text(record(directory/'round-01/draft.md')),'round':1},'previous dialogue review context changed')
        result=validate_call(call,payload,policy=task.policy);v=skill_module(root,'additional-assumptions-dialogue','validate_dialogue')
        require(not v.review_passed(result['review'],original),'successful dialogue round must not be rewritten')
    return directory,claims,template,slots


def _scope_records(directory):
    from scripts.batch import harbor_dialogue_scope_context as scope_context
    if (directory/'scope-transition.json').exists():return scope_context.evidence(record(directory/'scope-transition.json'))
    path=directory/'source-scope/prepared.json'
    if not path.exists():return []
    v=read(record(path))
    return [record(path),v['source'],v['disclosure'],v['instruction'],v['scope'],v['discovery'],v['producer'],v['scope_validator'],v['extractor']]


def _author_round(request):
    return read(request.context).get('structural_round',request.prepared.ordinal)


def _previous_dialogue(directory,round):
    return None if round==1 else {'previous_draft':text(record(directory/'round-01/draft.md')),'previous_review':read(record(directory/'round-01/review.json'))}


def prepare_dialogue_writer(task,round=1,*,root=ROOT):
    from scripts.batch import harbor_dialogue_authoring as author
    directory,claims,template,slots=_dialogue(task,round,root)
    body=author.author_body(template,slots,claims['writer_pack'],'writer',previous=_previous_dialogue(directory,round))
    evidence=[record(directory/name) for name in ('claims.json','writer_pack.json','draft_template.md','slots.json')]
    evidence+=_scope_records(directory)
    if task.scope_equivalence is not None:
        evidence.append(task.scope_equivalence)
    if round==2:
        history={'schema_version':'dialogue-prior-round/v1','claims':claims,'draft':body['previous_draft'],'review':body['previous_review'],'review_call':read(record(directory/'review-call-01.json')),'author_calls':[read(record(directory/(kind+'-call-01.json'))) for kind in ('writer','paraphraser')]}
        if task.dialogue_pre_review_repair is not None:
            history.update(schema_version='dialogue-prior-round/pre-review-v1',dialogue_pre_review_repair=task.dialogue_pre_review_repair)
        if (directory/'scope-transition.json').exists():
            from scripts.batch import harbor_dialogue_scope_context as scope_context
            transition=record(directory/'scope-transition.json');original,_,_=scope_context.validate_transition(transition,current_claims=claims,root=root)
            history.update(schema_version='dialogue-prior-round/v2',claims=original,source_scope_transition=transition)
        evidence.append(put(directory/'round-02/prior-round.json',history))
    return _request(task,'dialogue-writer',DIALOGUE,round,2,body,author.prompt(root,'writer'),evidence,mode='json_object')


def _accepted_author(task,directory,round,kind,claims,template,slots,*,incoming=None,root):
    from scripts.batch import harbor_dialogue_authoring as author
    from scripts.batch.harbor_model_review_provenance import Call
    binding=record(directory/('round-%02d'%round)/(kind+'-accepted.json'));accepted=read(binding)
    call=Call(**accepted['call'])
    previous=_previous_dialogue(directory,round) if kind=='writer' else None
    checked,filled,faults,proof=author.verify_author(call,policy=task.policy,kind=kind,round=round,template=template,slots=slots,pack=claims['writer_pack'],incoming=incoming,previous=previous,root=root)
    require(not faults and filled==text(accepted['filled']),'accepted author output no longer reconstructs')
    require(proof==read(accepted['slots']),'accepted slot proof changed')
    return call,checked,filled,proof


def _consume_author(task,request,campaign,kind,*,writer=None,root):
    from scripts.batch import harbor_dialogue_authoring as author
    validate_request(task,request,'dialogue-'+kind)
    round=_author_round(request);directory,claims,template,slots=_dialogue(task,round,root);call=observed(request,campaign)
    incoming=None;prior=None
    if kind=='paraphraser':
        require(writer is not None,'paraphraser requires root-selected accepted writer request')
        validate_request(task,writer,'dialogue-writer');require(_author_round(writer)==round,'writer/paraphraser round differs')
        oldcall,prior,incoming,oldproof=_accepted_author(task,directory,round,'writer',claims,template,slots,root=root)
        require(oldcall==observed(writer,campaign),'selected writer is not accepted author')
    previous=_previous_dialogue(directory,round) if kind=='writer' else None
    checked,filled,faults,proof=author.verify_author(call,policy=task.policy,kind=kind,round=round,template=template,slots=slots,pack=claims['writer_pack'],incoming=incoming,previous=previous,root=root)
    if prior is not None:require_independent(checked,[prior])
    suffix='-correction' if read(request.context).get('structural_round') is not None else ''
    run=directory/('round-%02d'%round);name=kind+suffix
    response_text=read(call.response)['choices'][0]['message']['content']
    raw=put(run/(name+'.response.txt'),response_text.encode('utf-8'))
    output=put(run/(name+'.filled.md'),filled.encode('utf-8')) if filled is not None else None
    report=put(run/(name+'.fill.json'),{'structure_faults':faults,'response':raw,'input':output,'template':record(directory/'draft_template.md'),'protocol':author.mode(call),'call':asdict(call)})
    if faults:return {'passed':False,'call':call,'structural_failure':report,'round':round,'kind':kind}
    if kind=='paraphraser':
        for sid in slots['slot_order']:
            if slots['slots'][sid]['kind']=='junior':require(proof['slot_prose_sha256'][sid]==oldproof['slot_prose_sha256'][sid],'paraphraser changed junior prose')
    proof_binding=put(run/(name+'.slots.json'),proof)
    put(run/(kind+'-accepted.json'),{'call':asdict(call),'filled':output,'slots':proof_binding,'request_context':request.context,'structural_failure':None})
    put(directory/('%s-call-%02d.json'%(kind,round)),asdict(call))
    result={'passed':True,'call':call,'slot_proof':proof,'round':round}
    if kind=='paraphraser':
        composer=skill_module(root,'additional-assumptions-dialogue','compose_draft');clean=composer.strip_slot_scaffold(filled)
        put(run/'draft.md',clean.encode('utf-8'));v=skill_module(root,'additional-assumptions-dialogue','validate_dialogue')
        checked=v.check_draft(clean,claims,task.stage,source_workbook=dialogue_source_workbook(task));put(run/'draft-check.json',checked)
        result.update(draft_report=checked,draft=record(run/'draft.md'))
    return result


def consume_dialogue_writer(task,request,campaign,*,root=ROOT):
    return _consume_author(task,request,campaign,'writer',root=root)


def prepare_dialogue_paraphraser(task,round=1,*,root=ROOT):
    from scripts.batch import harbor_dialogue_authoring as author
    directory,claims,template,slots=_dialogue(task,round,root)
    _,_,filled,_=_accepted_author(task,directory,round,'writer',claims,template,slots,root=root)
    body=author.author_body(template,slots,claims['writer_pack'],'paraphraser',incoming=filled)
    evidence=[record(directory/('round-%02d'%round)/'writer-accepted.json'),record(directory/'slots.json'),record(directory/'claims.json')]
    return _request(task,'dialogue-paraphraser',DIALOGUE,round,2,body,author.prompt(root,'paraphraser'),evidence,mode='json_object')


def consume_dialogue_paraphraser(task,request,writer,campaign,*,root=ROOT):
    return _consume_author(task,request,campaign,'paraphraser',writer=writer,root=root)


def prepare_dialogue_structural_correction(task,previous,campaign,*,root=ROOT):
    """One actual same-producer correction, shared by both roles in this round."""
    from scripts.batch import harbor_dialogue_authoring as author
    context=read(previous.context);kind=context['kind'].removeprefix('dialogue-')
    require(kind in ('writer','paraphraser') and context.get('structural_round') is None,'a structural correction cannot be corrected again')
    round=previous.prepared.ordinal;directory,claims,template,slots=_dialogue(task,round,root)
    _,identity=task_context(task)
    require(context['stage_tree']==task.stage_tree and context['workbook']==identity['workbook'] and context['original']==identity['original'],'failed author source context changed')
    require(context['request']==previous.prepared.request and context['ordinal']==round and context['limit']==previous.prepared.limit==2 and previous.prepared.scope.budget_id=='dialogue-'+kind,'failed author identity/budget changed')
    require(bool(context['code']) and context['budget_evidence']==list(previous.prepared.budget_evidence),'failed author provenance missing')
    for binding in context['budget_evidence']:checked_record(binding)
    call=observed(previous,campaign);incoming=None
    if kind=='paraphraser':_,_,incoming,_=_accepted_author(task,directory,round,'writer',claims,template,slots,root=root)
    old,filled,faults,_=author.verify_author(call,policy=task.policy,kind=kind,round=round,template=template,slots=slots,pack=claims['writer_pack'],incoming=incoming,previous=_previous_dialogue(directory,round) if kind=='writer' else None,root=root)
    require(faults,'successful author cannot receive a structural retry')
    run=directory/('round-%02d'%round)
    require(not (run/(kind+'-accepted.json')).exists(),'accepted producer cannot be structurally retried')
    receipt=put(run/'structural-repair.json',{'schema_version':author.REPAIR,'round':round,'kind':kind,'call':asdict(call),'request_context':previous.context,'faults':faults,'semantic_budget_reset':False})
    body=json.loads(read(call.request)['messages'][1]['content'])
    body['structural_correction']={'round':round,'kind':kind,'previous_response':old['review'],'faults':faults,'receipt_sha256':receipt['sha256']}
    prompt=author.prompt(root,kind)+'\nThe previous response failed the fixed structural validator. Correct the same slot prose under the same claims and template. Exact validator errors: '+json.dumps(faults)
    return _request(task,'dialogue-'+kind,DIALOGUE,1,1,body,prompt,[receipt,previous.context,*[getattr(call,k) for k in ('request','submitted','response','completed')]],mode='json_object',structural_round=round)


def dialogue_review_context(task, claims, draft, round, *, root=ROOT):
    body={'claims.json':claims,'draft.md':draft,'round':round}
    prompt=skill_prompt(root,'additional-assumptions-dialogue','You are reviewing notes from a deal-team conversation')
    evidence=[]
    if task.dialogue_pre_review_repair is not None and round==1:
        from scripts.batch import harbor_dialogue_pre_review_repair as Q
        body['source_proved_correction']=Q.review_projection(task.dialogue_pre_review_repair,claims,draft,root=root)
        prompt+=Q.REVIEW_GUIDANCE
        evidence=[task.dialogue_pre_review_repair]
    if round==2:
        from scripts.batch import harbor_dialogue_operand_evidence as operands
        source=task_context(task)[0]['bindings']['source_file']
        key=record(task.stage/'tests/answer_key.json')
        body['operand_evidence']=operands.project(claims,draft,source=source,answer_key=key,task_dir=task.stage,root=root)
        prompt+=operands.GUIDANCE
        evidence=[source,key,record(task.stage/'tests/disclosure.json')]
    return body,prompt,evidence


def prepare_dialogue_review(task,round=1,*,root=ROOT):
    directory,claims,template,slots=_dialogue(task,round,root);draft=record(directory/('round-%02d'%round)/'draft.md');require(text(draft).strip(),'empty dialogue cannot be reviewed')
    body,prompt,evidence=dialogue_review_context(task,claims,text(draft),round,root=root)
    return _request(task,'dialogue-review',DIALOGUE,round,2,body,prompt,[record(directory/'claims.json'),draft,*evidence])


def consume_dialogue_review(task,request,writer,paraphraser,campaign,*,apply=False,root=ROOT):
    validate_request(task,request,'dialogue-review')
    round=request.prepared.ordinal;directory,claims,template,slots=_dialogue(task,round,root);run=directory/('round-%02d'%round);call=observed(request,campaign)
    expected,prompt,_=dialogue_review_context(task,claims,text(record(run/'draft.md')),round,root=root)
    require(read(call.request)['messages'][0]['content']==prompt,'dialogue reviewer prompt changed')
    require(json.loads(read(call.request)['messages'][1]['content'])==expected,'dialogue reviewer context changed')
    result=validate_call(call,read(request.prepared.request),policy=task.policy)
    from scripts.batch import harbor_dialogue_authoring as author
    for producer,kind in ((writer,'dialogue-writer'),(paraphraser,'dialogue-paraphraser')):
        validate_request(task,producer,kind);require(_author_round(producer)==round,'dialogue producer/review round differ')
    calls=(observed(writer,campaign),observed(paraphraser,campaign))
    identity=task_context(task)[1]
    if task.dialogue_pre_review_repair is not None and round==1:
        from scripts.batch import harbor_dialogue_pre_review_repair as Q
        others=Q.verify_pair(task.dialogue_pre_review_repair,calls,policy=task.policy,claims=claims,draft=expected['draft.md'],round=round,root=root,workbook=identity['workbook'],original_sha256=identity['original']['sha256']);Q.validate_review_call(task.dialogue_pre_review_repair,call)
    else:
        others=author.verify_pair(calls,policy=task.policy,claims=claims,draft=expected['draft.md'],round=round,root=root,workbook=identity['workbook'],original_sha256=identity['original']['sha256'])
    require_independent(result,others)
    review=result['review'];require(type(review.get('round')) is int and review['round']==round,'review round changed')
    put(run/'review.json',review);put(directory/('review-call-%02d.json'%round),asdict(call))
    v=skill_module(root,'additional-assumptions-dialogue','validate_dialogue');draft_report=v.check_draft(text(record(run/'draft.md')),claims,task.stage,source_workbook=dialogue_source_workbook(task))
    draft_text=text(record(run/'draft.md'))
    passed=v.review_passed(review,claims,draft_text=draft_text) and draft_report['passed'] is True
    receipt={'passed':passed,'round':round,'draft_report':draft_report,'review_faults':v.review_faults(review,claims,draft_text=draft_text),'review':record(run/'review.json'),'draft':record(run/'draft.md'),'call':asdict(call),'publication_ready':False}
    warnings=v.review_warnings(review,claims,draft_text=draft_text)
    if warnings:receipt['warnings']=warnings
    put(run/'review-result.json',receipt)
    if apply:
        require(passed,'dialogue mechanical/independent review failed')
        # Root's current image-bound runtime adapter MUST run after this mutation.
        # Its final readiness gate, not this offline application, qualifies smoke.
        v.apply(task.stage,run/'draft.md',claims,review,require_review_pass=True,skip_smoke=True,source_workbook=dialogue_source_workbook(task))
        receipt={**receipt,'applied':True,'current_runtime_required':True}
    return receipt


def disclosure_command_plan(task):
    """Fixed commands for root's existing guarded workbook slot, not a PASS."""
    before,_=lane_context(task,DISCLOSURE);bindings=before['bindings']
    needed=('source_file','ast_workbook_root','source_generation','segmentation_generation')
    require(all(name in bindings for name in needed),'current immutable source/AST/segmentation bindings required')
    golden={'path':bindings['source_file']['path'],'sha256':bindings['source_file']['sha256']};checked_record(golden)
    for name in ('source_generation','segmentation_generation'):
        binding=bindings[name];require(re.fullmatch('[0-9a-f]{64}',binding.get('id','')),'immutable generation ID required')
        directory=Path(binding['path']);require(directory.name==binding['id'] and directory.parent.name=='generations' and directory.parent.parent.name==before['workbook_id'],'generation path differs from canonical workbook identity')
        manifest=read({'path':str(directory/'generation-manifest.json'),'sha256':binding['manifest_sha256']})
        require(manifest.get('generation_id')==binding['id'],'generation manifest ID differs')
    common=[sys.executable,'-B',str(task.root/'.cursor/skills/task-disclosure/scripts/disclose.py'),'--runs-root',str(task.run/'disclosure-build')]
    def command(name):return common+[name,'--task-dir',str(task.stage)]
    selected=command('select')+['--golden',golden['path'],'--ast-dir',bindings['ast_workbook_root']['path'],'--seg-root',str(Path(bindings['segmentation_generation']['path']).parents[2]),'--segmentation-mode','strict','--source-generation-root',str(Path(bindings['source_generation']['path']).parents[2]),'--source-generation-id',bindings['source_generation']['id'],'--segmentation-generation-id',bindings['segmentation_generation']['id']]
    return {'schema_version':'harbor-disclosure-command-plan/v1','before_role_resolution':[selected,command('probe'),command('roles')],'after_zero_or_validated_roles':[command('detect'),command('context'),command('write'),command('faithcheck')+['--golden',golden['path']],command('verify')],'cases_path':str(task.run/'disclosure-build'/task.stage.name/'ambiguous_roles.json'),'required_role_gate':'If cases are nonempty, one arbiter plus at most one same-producer format repair; run roles-validate before detect. Root preserves raw calls and never fabricates resolutions.','publication_ready':False}


def main(argv=None):
    """Offline preparation CLI. Model dispatch remains a root-coordinated API."""
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=['disclosure-plan','disclosure-review','nat-init','nat-writer','nat-review','dialogue-init','dialogue-writer','dialogue-paraphraser','dialogue-review'])
    parser.add_argument('--task',type=Path,required=True,help='Root-selected Task JSON; no model token is read')
    parser.add_argument('--golden',type=Path);parser.add_argument('--round',type=int,default=1);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args(argv);value=json.loads(args.task.read_bytes());value['policy']=Policy(**value['policy'])
    for key in ('stage','run','root'):
        if key in value:value[key]=Path(value[key])
    task=Task(**value)
    operations={'disclosure-plan':lambda:disclosure_command_plan(task),'disclosure-review':lambda:prepare_disclosure_review(task,record(args.golden),root=task.root),'nat-init':lambda:initialize_naturalization(task,root=task.root),'nat-writer':lambda:prepare_naturalization_writer(task,root=task.root),'nat-review':lambda:prepare_naturalization_review(task,root=task.root),'dialogue-init':lambda:initialize_dialogue(task,root=task.root),'dialogue-writer':lambda:prepare_dialogue_writer(task,args.round,root=task.root),'dialogue-paraphraser':lambda:prepare_dialogue_paraphraser(task,args.round,root=task.root),'dialogue-review':lambda:prepare_dialogue_review(task,args.round,root=task.root)}
    result=operations[args.operation]()
    def serial(v):
        if isinstance(v,Path):return str(v)
        if hasattr(v,'__dataclass_fields__'):return {k:serial(x) for k,x in asdict(v).items()}
        if isinstance(v,dict):return {k:serial(x) for k,x in v.items()}
        if isinstance(v,(tuple,list)):return [serial(x) for x in v]
        return v
    put(args.out,serial(result));print(json.dumps({'prepared':record(args.out),'model_calls':0,'publication_ready':False}))


if __name__=='__main__':main()

"""Append-only requalification of completed authors after a mechanical checker fix.

No author or reviewer is dispatched here. A separately selected runtime may read
one new completed paraphraser checkpoint while the original blocked checkpoint,
request, draft, author responses and failed check remain immutable.
"""
from dataclasses import asdict
from pathlib import Path
from types import FunctionType,MethodType
import fcntl,hashlib,json,os
SCHEMA='harbor-dialogue-mechanical-requalification/v1'
WARNING_SCHEMA='harbor-retained-dialogue-style-warning/v1'

def require(ok,message):
    if not ok:raise ValueError(message)
def binding(path):
    p=Path(path).absolute();require(p.is_file() and not any(x.is_symlink()for x in (p,*p.parents)),'regular mechanical evidence required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
def checked(row):
    require(set(row)=={'path','sha256'}and binding(row['path'])==row,'mechanical evidence changed')
    return json.loads(Path(row['path']).read_bytes())
def keep(path,value):
    raw=value if isinstance(value,bytes)else(json.dumps(value,sort_keys=True,indent=2)+'\n').encode()
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb')as f:f.write(raw);f.flush();os.fsync(f.fileno())
    return binding(path)

def inspect_boundary(W,P,worker,selection,lease,*,blocked,verify_pair,validator,live=True,takeover=None,takeover_reader=None):
    """Use actual producer observation and pair verification, never cached success."""
    from scripts.batch import harbor_transition_codec as codec
    require(type(live)is bool,'explicit live boundary required')
    failure=checked(blocked);step=W.step_directory(worker,'dialogue-paraphraser')
    require(blocked==binding(step/'blocked.json') and set(failure)=={'step','status','selection','lease','intent'} and
            failure['step']=='dialogue-paraphraser' and failure['status']=='DIALOGUE_REPAIR_REQUIRED','exact mechanical author hold required')
    require(codec.encode(selection)==failure['selection'],'mechanical continuation changed selected content')
    require(not(step/'complete.json').exists() and not(step/'exception.json').exists(),'completed or uncertain author cannot requalify')
    require(not any(W.step_directory(worker,phase).exists()for phase in W.STEPS[W.STEPS.index('dialogue-review'):]),'review or later operation already attempted')
    intent=checked(failure['intent']);initial=binding(worker.run/'initial.json')
    require(intent['initial']==initial and intent['selection']==failure['selection'] and intent['lease']==failure['lease'] and intent['code']==W.code(worker),'mechanical author context changed')
    if live:_,before,tracker=W.active(worker,selection,lease)
    else:
        tracker=binding(worker.context.tracker_root/(selection.content.workbook+'.json'));before=checked(tracker)
    task=W.task(worker,selection,tracker)
    requests=[W.request_saved(W.step_directory(worker,k)/'request.json')for k in ('dialogue-writer','dialogue-paraphraser')]
    calls=tuple(P.observed(q,worker.campaign)for q in requests)
    require(all(c is not None for c in calls),'completed author Calls required')
    from scripts.batch import harbor_lease_takeover as T
    if takeover_reader is not None:T=takeover_reader
    author_context=checked(requests[-1].context)
    getattr(T,'current_chain',T.continuation_chain)(author_context['budget_evidence'][0],failure['lease'],before,lease,takeover=takeover,tracker_path=tracker['path'])
    generation=requests[0].prepared.ordinal
    require(generation==requests[1].prepared.ordinal and all(q.prepared.scope.budget_id==kind for q,kind in zip(requests,('dialogue-writer','dialogue-paraphraser'))),'author generation identity changed')
    directory,claims,_,_=P._dialogue(task,generation,worker.root)
    draft=binding(directory/('round-%02d'%generation)/'draft.md');old_check=binding(Path(draft['path']).parent/'draft-check.json')
    require(checked(old_check)['passed']is False,'original mechanical failure missing')
    text=Path(draft['path']).read_text();verify_pair(task,text,calls=calls)
    source=selection.content.bindings['source_file'];binding(source['path']);require(binding(source['path'])==source,'source changed')
    report=validator.check_draft(text,claims,selection.content.stage,source_workbook=source)
    require(report.get('passed')is True and not report.get('faults') and not report.get('accuracy_faults') and not report.get('cast_faults'),'current draft mechanics failed')
    evidence=[blocked,failure['intent'],initial,draft,old_check,binding(directory/'claims.json'),source,binding(step/'request.json'),*(T.completion_records(takeover)if takeover is not None else[])]
    for req,call in zip(requests,calls):
        context=checked(req.context)
        require(context['request']==req.prepared.request and context['budget_evidence']==list(req.prepared.budget_evidence),'retained author request differs')
        evidence.extend([req.context,*context['code'],*context['evidence'],*context['budget_evidence']])
        evidence.extend(v for v in asdict(call).values()if v is not None)
    evidence.extend(dict(path=k,sha256=v)for k,v in W.code(worker).items())
    unique={r['path']:r for r in evidence}
    require(all(binding(r['path'])==r for r in evidence) and len(unique)==len({r['path']for r in evidence}),'retained author evidence changed')
    return dict(schema_version=SCHEMA,worker_run=str(worker.run),initial=initial,source=source,blocked=blocked,
        original_directory=str(step),selection=failure['selection'],lease=asdict(lease),failed_lease=failure['lease'],takeover=takeover,intent=failure['intent'],
        request=binding(step/'request.json'),draft=draft,claims=binding(directory/'claims.json'),old_check=old_check,
        report=report,author_calls=[asdict(c)for c in calls],author_generation=generation,
        evidence=sorted(unique.values(),key=lambda r:r['path']),tracker=tracker,
        review_performed=False,model_calls_added=0,draft_changed=False,automatic_retry=False)

def authorize(W,P,worker,selection,lease,*,blocked,verify_pair,validator,directory,runtime_profile,takeover=None,takeover_reader=None):
    """Caller supplies its original guarded custody; directory is once-only."""
    with(worker.run/'worker.lock').open('a+b')as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        proof=inspect_boundary(W,P,worker,selection,lease,blocked=blocked,verify_pair=verify_pair,validator=validator,live=True,takeover=takeover,takeover_reader=takeover_reader)
        profile=checked(runtime_profile);require(profile.get('schema_version','').startswith('fleet-retained-runtime/'),'declared corrected runtime required')
        directory=Path(directory);require(directory.is_absolute()and not directory.exists(),'mechanical namespace already used')
        require(W.active(worker,selection,lease)[2]==proof['tracker'],'tracker changed before mechanical append')
        directory.mkdir(parents=True)
        keep(directory/'intent.json',dict(schema_version=SCHEMA,blocked=blocked,tracker=proof['tracker'],runtime_profile=runtime_profile,automatic_retry=False))
        request=keep(directory/'request.json',Path(proof['request']['path']).read_bytes())
        require(request['sha256']==proof['request']['sha256'],'retained request copy differs')
        certificate=keep(directory/'certificate.json',dict(proof,implementation=binding(__file__),runtime_profile=runtime_profile,request_copy=request,directory=str(directory)))
        complete=keep(directory/'complete.json',dict(step='dialogue-paraphraser',status='completed',selection=proof['selection'],lease=proof['lease'],intent=proof['intent'],mechanical_requalification=certificate))
        validate(certificate);return complete

def validate(certificate):
    p=checked(certificate)
    if p.get('schema_version')==WARNING_SCHEMA:return validate_warning(certificate)
    require(p['schema_version']==SCHEMA and p['implementation']==binding(__file__) and p['review_performed']is False and p['model_calls_added']==0 and p['draft_changed']is False and p['automatic_retry']is False,'mechanical certificate changed')
    require(p['report'].get('passed')is True and not p['report'].get('faults'),'mechanical report is not passing')
    checked(p['runtime_profile'])
    require(all(binding(row['path'])==row for row in p['evidence']),'mechanical retained bytes changed')
    require(p['draft']in p['evidence']and p['blocked']in p['evidence']and p['initial']in p['evidence'],'certificate omitted required history')
    failure=checked(p['blocked']);require(failure==dict(step='dialogue-paraphraser',status='DIALOGUE_REPAIR_REQUIRED',selection=p['selection'],lease=p['failed_lease'],intent=p['intent']),'retained mechanical hold changed')
    directory=Path(p['directory']);require(certificate==binding(directory/'certificate.json')and p['request_copy']==binding(directory/'request.json')and p['request_copy']['sha256']==p['request']['sha256'],'projected request changed')
    complete=checked(binding(directory/'complete.json'));require(complete==dict(step='dialogue-paraphraser',status='completed',selection=p['selection'],lease=p['lease'],intent=p['intent'],mechanical_requalification=certificate),'mechanical completion changed')
    return p

def install_projection(original,certificate):
    """Project one retained completion in a declared worker namespace only."""
    p=validate(certificate)
    if p.get('historical_instruction') is not None:install_instruction_history(original,p)
    prior=original.step_directory
    def directory(worker,phase):
        if str(worker.run)==p['worker_run'] and phase==p.get('step','dialogue-paraphraser'):
            current=validate(certificate)
            require(binding(worker.run/'initial.json')==current['initial']and str(prior(worker,phase))==current['original_directory'],'mechanical projection detached from worker')
            return Path(current['directory'])
        return prior(worker,phase)
    namespaces=[original.__dict__]
    namespaces.extend(f.__globals__ for f in list(original.__dict__.values())if isinstance(f,FunctionType))
    if hasattr(original,'declared_namespace'):namespaces.append(original.declared_namespace)
    for namespace in namespaces:
        for key,value in list(namespace.items()):
            if value is prior:namespace[key]=directory
    original.step_directory=directory
    return original

def instruction_history(worker,proof):
    """Preserve a historical stage read using its exact pre-dialogue witness."""
    import inspect
    reader=worker.declared_namespace['_source_cards'];permit=checked(reader.authority)
    old=worker.codec.decode(proof['failed_selection']).content
    require(old.pre_dialogue_witness is not None,'original pre-dialogue stage witness required')
    origin=str(old.stage/'instruction.md');rows=[r for r in permit['history']if r['path']==origin]
    require(rows and all(r==rows[0]for r in rows),'exact historical instruction binding required')
    copy=binding(old.pre_dialogue_witness.stage/'instruction.md');current=binding(origin)
    require(copy['sha256']==rows[0]['sha256']and copy['path']!=origin and current!=rows[0],'changed instruction must retain exact original witness bytes')
    marker=checked(binding(old.stage/'tests/dialogue-applied.json'))
    require(marker['applied']is True and marker['instruction_sha256']==current['sha256'],'current instruction not bound to completed dialogue application')
    return dict(original=rows[0],retained=copy,current=current,marker=binding(old.stage/'tests/dialogue-applied.json'),reader=binding(inspect.getfile(type(reader))),authority=reader.authority)


def install_instruction_history(worker,proof):
    """Clone only the historical owned() reader; live task reads stay current."""
    import inspect
    h=proof['historical_instruction'];reader=worker.declared_namespace['_source_cards']
    require(binding(inspect.getfile(type(reader)))==h['reader']and reader.authority==h['authority'],'historical instruction reader differs')
    def validate_history():
        require(binding(h['retained']['path'])==h['retained']and binding(h['current']['path'])==h['current']and h['original']['path']==h['current']['path']and h['original']['sha256']==h['retained']['sha256'],'historical/current instruction bytes changed')
        require(checked(h['marker'])['instruction_sha256']==h['current']['sha256'],'applied instruction marker changed')
    validate_history();old=type(reader).owned;scope=dict(old.__globals__);ordinary=scope['binding']
    def historical_binding(path):
        if str(path)==h['original']['path']:
            validate_history();return dict(h['original'])
        return ordinary(path)
    scope['binding']=historical_binding
    copied=FunctionType(old.__code__,scope,old.__name__,old.__defaults__,old.__closure__);copied.__kwdefaults__=old.__kwdefaults__
    reader.owned=MethodType(copied,reader)


def inspect_warning(selected,reader,worker,selection,lease,*,blocked,notes,takeover):
    """Authenticate the existing review independently of author-generation number."""
    W=selected['worker'];P=selected['producer'];V=P.skill_module(worker.root,'additional-assumptions-dialogue','validate_dialogue')
    step=W.step_directory(worker,'dialogue-review');failure=checked(blocked)
    require(blocked==binding(step/'blocked.json')and failure['status']=='DIALOGUE_REPAIR_REQUIRED'and failure['step']=='dialogue-review','exact completed rejected review required')
    require(not(step/'complete.json').exists()and not(step/'exception.json').exists(),'uncertain or completed review cannot requalify')
    require(not any(W.step_directory(worker,k).exists()for k in W.STEPS[W.STEPS.index('final-runtime'):]),'later owning steps already exist')
    require(W.codec.encode(selection)==failure['selection'],'warning changed selected financial content')
    _,before,tracker=W.active(worker,selection,lease);task=W.task(worker,selection,tracker)
    requests={k:W.request_saved(W.step_directory(worker,k)/'request.json')for k in ('dialogue-writer','dialogue-paraphraser','dialogue-review')}
    calls={k:P.observed(q,worker.campaign)for k,q in requests.items()}
    require(all(c is not None for c in calls.values()),'warning requires all three completed original Calls')
    generation=requests['dialogue-writer'].prepared.ordinal
    require(generation==requests['dialogue-paraphraser'].prepared.ordinal and requests['dialogue-review'].prepared.ordinal==1,'retained author pair and first independent review differ')
    directory,claims,_,_=P._dialogue(task,1,worker.root)
    draft=binding(directory/('round-%02d'%generation)/'draft.md');review=binding(Path(draft['path']).parent/'review.json');receipt=binding(Path(draft['path']).parent/'review-result.json')
    text=Path(draft['path']).read_text();claim_row=binding(directory/'claims.json');value=checked(review)
    require(claim_row['sha256']==selection.content.dialogue_claims['sha256'] and checked(claim_row)==claims==checked(selection.content.dialogue_claims),'reviewed source claims changed')
    require(review['sha256']==calls['dialogue-review'].parsed['sha256']and checked(receipt)['passed']is False,'actual rejected response missing')
    require(Path(notes['path']).read_text()==V.strip_claim_comments(text),'warning notes differ from reviewed draft')
    warnings=V.review_warnings(value,claims,draft_text=text)
    require(warnings and all(x['code']=='dialogue_style_only'for x in warnings)and V.review_passed(value,claims,draft_text=text),'substantive or unknown findings cannot become style warnings')
    report=V.check_draft(text,claims,selection.content.stage,source_workbook=selection.content.bindings['source_file'])
    require(report.get('passed')is True and not report.get('faults'),'warning draft mechanics failed')
    verified=selected['semantic'].dialogue_review(calls['dialogue-review'],claims=claim_row,notes=notes,reviewed_claims=claim_row,
        policy=selection.content.model_policy,writer_calls=[calls[k]for k in ('dialogue-writer','dialogue-paraphraser')],
        workbook=before['workbook_id'],current_source_sha256=selection.content.bindings['source_file']['sha256'],
        original_sha256=before['bindings']['original_raw_source']['sha256'],source=selection.content.bindings['source_file'],
        answer_key=binding(selection.content.stage/'tests/answer_key.json'),task_dir=selection.content.stage,
        pre_review_repair=worker.dialogue_pre_review_repair,root=worker.root)
    require(verified['provenance']['review']==value,'semantic proof changed original review')
    context=checked(requests['dialogue-review'].context)
    # This review was dispatched after the selected takeover. Its immutable
    # budget snapshot must be the current lease, rather than replaying the
    # pre-takeover ancestry from an already replaced token.
    require(failure['lease']==asdict(lease) and context['budget_evidence'][0]['sha256']==lease.sha256 and
        checked(context['budget_evidence'][0])==before,'review is detached from current completed takeover lease')
    reader.takeover.validate_completion(takeover)
    evidence=[blocked,failure['intent'],binding(worker.run/'initial.json'),draft,review,receipt,claim_row,notes,
              selection.content.bindings['source_file'],*reader.dependencies()]
    for kind,request in requests.items():
        evidence.extend([binding(W.step_directory(worker,kind)/'request.json'),request.context])
        evidence.extend(request.prepared.budget_evidence)
        evidence.extend(v for v in asdict(calls[kind]).values()if v is not None)
    require(all(binding(r['path'])==r for r in evidence),'warning evidence changed')
    return dict(schema_version=WARNING_SCHEMA,step='dialogue-review',worker_run=str(worker.run),initial=binding(worker.run/'initial.json'),
        original_directory=str(step),blocked=blocked,failed_selection=failure['selection'],failed_lease=failure['lease'],intent=failure['intent'],
        source=selection.content.bindings['source_file'],draft=draft,claims=claim_row,review=review,original_receipt=receipt,notes=notes,
        warnings=warnings,report=report,semantic_proof=verified,calls={k:asdict(v)for k,v in calls.items()},author_generation=generation,review_ordinal=1,
        evidence=sorted({r['path']:r for r in evidence}.values(),key=lambda r:r['path']),tracker=tracker,model_calls_added=0,draft_changed=False,review_changed=False,automatic_retry=False)

def validate_warning(certificate):
    p=checked(certificate)
    require(p['schema_version']==WARNING_SCHEMA and p['implementation']==binding(__file__)and p['model_calls_added']==0 and p['draft_changed']is False and p['review_changed']is False and p['automatic_retry']is False,'warning certificate identity changed')
    checked(p['runtime_profile'])
    require(all(binding(r['path'])==r for r in p['evidence']),'retained warning evidence changed')
    require(all(p[k]in p['evidence']for k in ('initial','blocked','draft','claims','review','original_receipt','notes','source')),'warning omitted required original evidence')
    failure=checked(p['blocked'])
    require(failure==dict(step='dialogue-review',status='DIALOGUE_REPAIR_REQUIRED',selection=p['failed_selection'],lease=p['failed_lease'],intent=p['intent']),'original rejected review changed')
    require(p['semantic_proof']['provenance']['review']==checked(p['review'])and p['review']['sha256']==p['calls']['dialogue-review']['parsed']['sha256'],'warning detached authenticated original response')
    require(p['warnings']and all(x['code']=='dialogue_style_only'for x in p['warnings'])and p['report']['passed']is True,'non-style warning certificate')
    if 'serialization_recovery'in p:
        from scripts import harbor_instruction_campaign as C
        old,prior,lease,records=serialization_source(p['serialization_recovery'],C)
        require(p['selection']==old['selection']and p['lease']==asdict(lease)and all(r in p['evidence']for r in records),'serialization recovery changed completed warning')
        require(all(p[k]==old[k]for k in ('blocked','draft','claims','review','original_receipt','semantic_proof','calls','warnings','report','initial','intent','source')),'serialization recovery changed prior quality evidence')
        h=p['historical_instruction'];require(set(h)=={'original','retained','current','marker','reader','authority'}and all(h[k]in p['evidence']for k in ('retained','current','marker','reader','authority'))and h['original']['sha256']==h['retained']['sha256']and h['original']['path']==h['current']['path'],'historical instruction witness changed')
    directory=Path(p['directory']);require(certificate==binding(directory/'certificate.json'),'warning certificate namespace differs')
    complete=checked(binding(directory/'complete.json'))
    require(complete==dict(step='dialogue-review',status='completed',selection=p['selection'],lease=p['lease'],intent=p['intent'],mechanical_requalification=certificate),'warning completed checkpoint differs')
    return p

RUN_SCHEMA='fleet-mechanical-continuation/v1'
WARNING_RUN_SCHEMA='fleet-retained-style-warning-continuation/v1'
WARNING_RESUME_SCHEMA='fleet-retained-style-warning-serialization/v1'
def is_warning(p):return p['schema_version'] in {WARNING_RUN_SCHEMA,WARNING_RESUME_SCHEMA}

def load_context(plan_binding,*,probe):
    """A data-selected retained source-card execution interface; no task IDs."""
    import importlib.util,sys
    p=checked(plan_binding)
    fields={'schema_version','implementation','runtime','runtime_profile','descriptor','blocked','takeover','owner','workbook','source','version','output','operations','factory','authoring_job'}
    warning=p.get('schema_version')in {WARNING_RUN_SCHEMA,WARNING_RESUME_SCHEMA}
    recovery=p.get('schema_version')==WARNING_RESUME_SCHEMA
    require(set(p)==fields|({'prior_authorization','prior_implementation'}if warning else set())|({'retained_warning'}if recovery else set())and p['schema_version']in {RUN_SCHEMA,WARNING_RUN_SCHEMA,WARNING_RESUME_SCHEMA} and p['implementation']==binding(__file__),'closed mechanical continuation plan required')
    require(binding(p['runtime']['path'])==p['runtime'],'mechanical runtime changed')
    spec=importlib.util.spec_from_file_location('mechanical_selected_runtime',p['runtime']['path']);runtime=importlib.util.module_from_spec(spec);sys.modules[spec.name]=runtime;spec.loader.exec_module(runtime)
    scope=runtime.installed(p['runtime_profile']);scope.__enter__()
    try:
        rp=runtime.profile(p['runtime_profile']);require(rp['read_call']['function']=='load' and rp['read_call']['kwargs']=={'probe':True},'declared source-card load interface required')
        entry=runtime.load_module('mechanical_original_execution',rp['entrypoint'])
        context=entry.load(*rp['read_call']['args'],probe=probe)
        require(type(context)is tuple and len(context)==7,'retained execution context differs')
        original_plan,_,reader,selected,driver,env,runner=context
        require(original_plan['owner']==p['owner'] and env[5]['workbook']==p['workbook'] and reader.plan['source']==p['source'] and Path(env[6]).name==p['version'],'mechanical task identity differs')
        descriptor=checked(p['descriptor']);failure=checked(p['blocked']);C=selected['campaign'];W=selected['worker'];P=selected['producer']
        if warning:
            prior_module=runtime.load_module('retained_completed_mechanical',p['prior_implementation'])
            prior_resource=checked(checked(p['prior_authorization'])['resource'])
            prior_plan=checked(prior_resource['intent'])['plan']
            _,prior=prior_module.authorization_outcome(prior_plan,p['prior_authorization'],C)
            require(prior['descriptor']==p['descriptor']and checked(failure['intent'])['previous']==prior['checkpoint'],'warning detached completed mechanical predecessor')
            cert=checked(prior['checkpoint'])['mechanical_requalification']
            require(any(f['reader']==p['prior_implementation']and f['certificate']==cert for f in rp.get('factories',[])),'completed mechanical projection missing')
        worker=C.worker_decode(descriptor['worker']);selection=C.codec.decode(failure['selection'])
        if recovery:
            _,retained,_,_=serialization_source(p['retained_warning'],C)
            selection=C.codec.decode(retained['selection'])
        _,after,lease=reader.takeover.validate_completion(p['takeover'])
        require(binding(worker.context.tracker_root/(p['workbook']+'.json'))==checked(p['takeover'])['after'] or checked(binding(worker.context.tracker_root/(p['workbook']+'.json')))==after,'current mechanical takeover differs')
        require(p['factory']['module']==reader.plan['dependencies'][reader.plan['dependencies'].index(p['factory']['module'])] and set(p['factory'])=={'module','function','function_sha256'},'declared worker factory required')
        require(Path(p['output']).is_absolute() and Path(p['operations']).parent==Path(env[5]['run_root'])/'dispatcher-v2','mechanical namespace differs')
        yield p,runtime,rp,entry,context,worker,selection,lease
    finally:scope.__exit__(None,None,None)

from contextlib import contextmanager
load_context=contextmanager(load_context)

def live_custody(p):
    from scripts.fleet import model_gate
    client,owner,_=model_gate.context();value=client.call('heartbeat',**owner)
    require(value['workbook']==p['workbook'] and value['owner']==p['owner'] and value['source_sha256']==p['source']['sha256'] and value['version']==p['version'] and value['fence']==owner['fence'],'mechanical live custody differs')
    return value

def authorize_child(plan_binding,intent):
    import sys
    with load_context(plan_binding,probe=False)as(p,runtime,rp,entry,context,w,s,l):
        require(sys.argv==[__file__,'authorize-child','--plan',plan_binding['path'],'--sha256',plan_binding['sha256'],'--intent',intent['path'],'--intent-sha256',intent['sha256']],'actual mechanical child argv differs')
        directory=Path(p['output'])/'authorization'
        require(intent==binding(directory/'intent.json')and checked(intent)==dict(schema_version=p['schema_version'],plan=plan_binding,seconds=900,memory_bytes=8589934592,models=False,automatic_retry=False),'mechanical guard intent differs')
        from scripts.batch import harbor_model_executor as E,harbor_tracker_lifecycle as L
        def forbidden(*a,**k):raise ValueError('mechanical requalification cannot dispatch or change leases')
        E.execute=E._open=L.lease_operation=forbidden
        reader,selected=context[2:4];W=selected['worker'];P=selected['producer'];C=selected['campaign'];live_custody(p)
        if p['schema_version']==WARNING_RESUME_SCHEMA:
            return authorize_serialization(p,plan_binding,directory,selected,w,s,l)
        if p['schema_version']==WARNING_RUN_SCHEMA:
            return authorize_warning(p,plan_binding,directory,reader,selected,w,s,l)
        validator=P.skill_module(w.root,'additional-assumptions-dialogue','validate_dialogue')
        complete=authorize(W,P,w,s,l,blocked=p['blocked'],verify_pair=reader.verify_pair,validator=validator,directory=directory/'mechanical',runtime_profile=p['runtime_profile'],takeover=p['takeover'],takeover_reader=reader.takeover)
        old=checked(p['descriptor'])
        descriptor=keep(directory/'descriptor.json',dict(old,initial=dict(selection=C.codec.encode(s),lease=C.base.encode(l)),origin=[*old['origin'],p['descriptor'],p['blocked'],p['takeover'],plan_binding,checked(complete)['mechanical_requalification']]))
        result=keep(directory/'result.json',C._result('STEP_COMPLETE',descriptor,s,l,checkpoint=complete,next_step='dialogue-review',evidence=[p['blocked'],plan_binding,complete]))
        C.selected(descriptor);C._prior(result,descriptor)

def authorize_warning(p,plan_binding,directory,reader,selected,w,s,l):
    from dataclasses import replace
    W=selected['worker'];P=selected['producer'];C=selected['campaign']
    with(w.run/'worker.lock').open('a+b')as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        V=P.skill_module(w.root,'additional-assumptions-dialogue','validate_dialogue')
        task=W.task(w,s,W.active(w,s,l)[2]);home,_,_,_=P._dialogue(task,1,w.root)
        writer=W.request_saved(W.step_directory(w,'dialogue-writer')/'request.json')
        draft=home/('round-%02d'%writer.prepared.ordinal)/'draft.md'
        qualified=directory/'warning';require(not qualified.exists(),'warning already attempted');qualified.mkdir()
        notes=keep(qualified/'reviewed-notes.md',V.strip_claim_comments(draft.read_text()).encode())
        proof=inspect_warning(selected,reader,w,s,l,blocked=p['blocked'],notes=notes,takeover=p['takeover'])
        require(W.active(w,s,l)[2]==proof['tracker'],'tracker changed before warning apply')
        # This is the same owning stage application used by ordinary Q.apply_review.
        # It receives the unmodified draft and actual failed review; current policy
        # must explicitly classify every finding as a presentation warning.
        V.apply(s.content.stage,Path(proof['draft']['path']),checked(proof['claims']),checked(proof['review']),
            require_review_pass=True,skip_smoke=True,source_workbook=s.content.bindings['source_file'])
        from scripts.batch.harbor_model_review_provenance import Call
        content=replace(s.content,dialogue_review=proof['review'],dialogue_reviewed_draft=proof['draft'])
        semantic=replace(s.semantic,dialogue_call=Call(**proof['calls']['dialogue-review']),dialogue_reviewed_claims=proof['claims'],
            dialogue_writer_calls=tuple(Call(**proof['calls'][k])for k in ('dialogue-writer','dialogue-paraphraser')),
            dialogue_pre_review_repair=w.dialogue_pre_review_repair)
        new=W.refreshed(replace(s,content=content,semantic=semantic))
        certificate=keep(qualified/'certificate.json',dict(proof,implementation=binding(__file__),runtime_profile=p['runtime_profile'],
            directory=str(qualified),selection=C.codec.encode(new),lease=asdict(l)))
        complete=keep(qualified/'complete.json',dict(step='dialogue-review',status='completed',selection=C.codec.encode(new),lease=asdict(l),intent=proof['intent'],mechanical_requalification=certificate))
        validate_warning(certificate)
        old=checked(p['descriptor']);descriptor=keep(directory/'descriptor.json',dict(old,initial=dict(selection=C.codec.encode(new),lease=C.base.encode(l)),
            origin=[*old['origin'],p['descriptor'],p['blocked'],p['takeover'],plan_binding,certificate]))
        result=keep(directory/'result.json',C._result('STEP_COMPLETE',descriptor,new,l,checkpoint=complete,next_step='final-runtime',evidence=[p['blocked'],plan_binding,complete]))
        C.selected(descriptor);C._prior(result,descriptor)

def serialization_source(authority,C):
    """Read a fully written warning application whose final checkpoint check failed."""
    import importlib.util,sys
    require(set(authority)=={'implementation','plan','outcome','result','certificate'},'closed retained warning serialization authority required')
    old_plan=checked(authority['plan']);require(old_plan['schema_version']==WARNING_RUN_SCHEMA and old_plan['implementation']==authority['implementation'],'retained warning implementation differs')
    require(binding(authority['implementation']['path'])==authority['implementation'],'retained warning implementation changed')
    spec=importlib.util.spec_from_file_location('retained_warning_serializer',authority['implementation']['path']);old=importlib.util.module_from_spec(spec);sys.modules[spec.name]=old;spec.loader.exec_module(old)
    proof=old.validate_warning(authority['certificate']);outcome=checked(authority['outcome']);directory=Path(old_plan['output'])/'authorization'
    require(authority['outcome']==binding(directory/'outcome.json') and outcome==dict(status='RECONCILIATION_REQUIRED',resource=binding(directory/'resource.json'),result=None,automatic_retry=False),'retained failed guard changed')
    resource=checked(outcome['resource']);intent=checked(resource['intent'])
    require(resource['returncode']==1 and resource['failure']is None and resource['seconds']==900 and resource['memory_bytes']==8589934592 and resource['log']==binding(directory/'step.log') and resource['intent']==binding(directory/'intent.json'),'retained serialization guard differs')
    require(intent==dict(schema_version=WARNING_RUN_SCHEMA,plan=authority['plan'],seconds=900,memory_bytes=8589934592,models=False,automatic_retry=False),'retained authorizer intent differs')
    require(resource['command']==[sys.executable,'-I','-B',authority['implementation']['path'],'authorize-child','--plan',authority['plan']['path'],'--sha256',authority['plan']['sha256'],'--intent',resource['intent']['path'],'--intent-sha256',resource['intent']['sha256']],'retained guard physical command differs')
    require(Path(resource['log']['path']).read_text().rstrip().endswith('AdmissionError: checkpoint selection or lease changed'),'failure is not the closed checkpoint serialization boundary')
    value=checked(authority['result']);complete=checked(value['checkpoint'])
    require(authority['result']==binding(directory/'result.json') and value['schema_version']==C.SCHEMA and value['status']=='STEP_COMPLETE'and value['next_step']=='final-runtime'and value['publication_performed']is False and value['required_action']is None,'retained completed result differs')
    require(value['descriptor']==binding(directory/'descriptor.json') and value['checkpoint']==binding(Path(proof['directory'])/'complete.json') and complete['mechanical_requalification']==authority['certificate'],'retained completed namespace differs')
    lease=C.base.decode(value['lease'])
    require(complete['selection']==value['selection']==proof['selection']and complete['lease']==value['lease']==proof['lease']and complete['lease']!=asdict(lease),'only typed/plain lease serialization mismatch is recoverable')
    require(checked(value['descriptor'])['initial']==dict(selection=value['selection'],lease=value['lease']),'retained descriptor differs')
    require(value['evidence']==[old_plan['blocked'],authority['plan'],value['checkpoint']]and proof['blocked']==old_plan['blocked']and proof['runtime_profile']==old_plan['runtime_profile'],'retained warning result provenance differs')
    return proof,value,lease,[authority[k]for k in authority]+[value['checkpoint'],value['descriptor'],outcome['resource'],resource['intent'],resource['log']]


def authorize_serialization(p,plan_binding,directory,selected,w,s,l):
    C=selected['campaign'];W=selected['worker']
    with(w.run/'worker.lock').open('a+b')as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        proof,prior,lease,evidence=serialization_source(p['retained_warning'],C)
        require(lease==l and prior['selection']==C.codec.encode(s),'serialization cannot alter current selection or lease')
        tracker=W.active(w,s,l)[2];require(tracker['sha256']==l.sha256,'live warning tracker changed')
        history=instruction_history(W,proof)
        qualified=directory/'warning';require(not qualified.exists(),'serialization recovery already attempted');qualified.mkdir()
        value=dict(proof,implementation=binding(__file__),runtime_profile=p['runtime_profile'],directory=str(qualified),lease=asdict(l),serialization_recovery=p['retained_warning'],historical_instruction=history,evidence=sorted({r['path']:r for r in [*proof['evidence'],*evidence,history['retained'],history['current'],history['marker'],history['reader'],history['authority']]}.values(),key=lambda r:r['path']))
        certificate=keep(qualified/'certificate.json',value)
        complete=keep(qualified/'complete.json',dict(step='dialogue-review',status='completed',selection=prior['selection'],lease=asdict(l),intent=proof['intent'],mechanical_requalification=certificate))
        validate_warning(certificate)
        old=checked(prior['descriptor']);descriptor=keep(directory/'descriptor.json',dict(old,origin=[*old['origin'],prior['descriptor'],p['retained_warning']['outcome'],p['retained_warning']['result'],plan_binding,certificate]))
        result=keep(directory/'result.json',C._result('STEP_COMPLETE',descriptor,s,l,checkpoint=complete,next_step='final-runtime',evidence=[p['blocked'],plan_binding,complete]))
        C.selected(descriptor);C._prior(result,descriptor)


def authorization_outcome(plan_binding,outcome,C):
    p=checked(plan_binding);directory=Path(p['output'])/'authorization';value=checked(outcome)
    require(outcome==binding(directory/'outcome.json') and value==dict(status='COMPLETED_OPERATION',resource=binding(directory/'resource.json'),result=binding(directory/'result.json'),automatic_retry=False),'exact completed mechanical guard required')
    resource=checked(value['resource']);intent=checked(resource['intent'])
    require(resource['returncode']==0 and resource['failure']is None and resource['intent']==binding(directory/'intent.json') and resource['log']==binding(directory/'step.log') and resource['seconds']==900 and resource['memory_bytes']==8589934592,'mechanical guard failed or changed')
    require(intent==dict(schema_version=p['schema_version'],plan=plan_binding,seconds=900,memory_bytes=8589934592,models=False,automatic_retry=False),'mechanical intent differs')
    import sys
    require(resource['command']==[sys.executable,'-I','-B',p['implementation']['path'],'authorize-child','--plan',plan_binding['path'],'--sha256',plan_binding['sha256'],'--intent',resource['intent']['path'],'--intent-sha256',resource['intent']['sha256']],'mechanical physical guard command differs')
    result=checked(value['result']);certificate=checked(result['checkpoint'])['mechanical_requalification'];validate(certificate)
    require(result['status']=='STEP_COMPLETE'and result['next_step']==('final-runtime'if is_warning(p) else'dialogue-review')and result['descriptor']==binding(directory/'descriptor.json')and result['evidence']==[p['blocked'],plan_binding,result['checkpoint']],'mechanical result differs')
    C.selected(result['descriptor']);C._prior(value['result'],result['descriptor']);return value['result'],result

def run(plan_binding):
    """One guarded requalification followed by unchanged ordinary authoring."""
    import sys,time
    with load_context(plan_binding,probe=False)as(p,runtime,rp,entry,context,w,s,l):
        reader,selected,driver,env,runner=context[2:];base=env[0];C=selected['campaign'];W=selected['worker']
        live_custody(p);_,before,_=W.active(w,s,l)
        require(before['leases']['task_agent']['expires_at']-time.time_ns()>(900+3330+60)*10**9,'mechanical continuation lease runway missing')
        out=Path(p['output']);require(not out.exists(),'mechanical continuation already attempted')
        driver.operation_namespace(env[5],p['operations']);old_job=checked(p['authoring_job'])
        require(old_job['publish']is False and all(old_job[k]is True for k in ('models','commands','containers')),'mechanical remaining capabilities differ')
        with driver.fleet_slot(p['workbook'])as fleet_fds:
            fd=os.open(base._controls(env[7]),os.O_RDWR|os.O_NOFOLLOW)
            try:
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);out.mkdir();directory=out/'authorization';directory.mkdir()
                intent=keep(directory/'intent.json',dict(schema_version=p['schema_version'],plan=plan_binding,seconds=900,memory_bytes=8589934592,models=False,automatic_retry=False))
                command=[sys.executable,'-I','-B',__file__,'authorize-child','--plan',plan_binding['path'],'--sha256',plan_binding['sha256'],'--intent',intent['path'],'--intent-sha256',intent['sha256']]
                rc=None;failure=None;metrics={}
                try:
                    with(directory/'step.log').open('xb')as log:rc=base._run_in_process_group(command,stdout=log,timeout_seconds=900,memory_limit_bytes=8589934592,grace_seconds=3,metrics=metrics,cwd=env[6],env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},guardian_lock_fds=(fd,*fleet_fds))
                except Exception as exc:failure=dict(type=type(exc).__name__,message_sha256=hashlib.sha256(str(exc).encode()).hexdigest())
                resource=keep(directory/'resource.json',dict(intent=intent,returncode=rc,failure=failure,metrics=metrics,log=binding(directory/'step.log'),command=command,seconds=900,memory_bytes=8589934592))
                result=binding(directory/'result.json')if rc==0 and failure is None and(directory/'result.json').exists()else None
                outcome=keep(directory/'outcome.json',dict(status='COMPLETED_OPERATION'if result else'RECONCILIATION_REQUIRED',resource=resource,result=result,automatic_retry=False))
            finally:os.close(fd)
        require(result is not None,'mechanical guard requires reconciliation; no automatic retry')
        _,value=authorization_outcome(plan_binding,outcome,C);certificate=checked(value['checkpoint'])['mechanical_requalification']
        post=dict(rp,schema_version=runtime.SCHEMA2,factories=[*rp.get('factories',[]),dict(p['factory'],reader=p['implementation'],certificate=certificate)],guard_modules=[binding(base.__file__)],child_plans=[rp['read_call']['args'][0]],journal_roots=[p['operations']])
        post_binding=keep(out/'remaining-runtime.json',post)
        with runtime.installed(post_binding):
            # Reuse the already authenticated owning context. Reinstalling the
            # retained source-card factory onto its own facade is not supported.
            install_projection(selected['worker'],certificate)
            adapted=entry.dispatcher(driver,selected,runner,plan_binding=rp['read_call']['args'][0],entry=rp['entrypoint'],reader=reader)
            current=(env[0],selected['campaign'],env[2],selected['worker'],selected['renewal'],*env[5:])
            job=keep(out/'authoring-job.json',dict(old_job,outcome=outcome,operations=p['operations'],
                **({'models':False}if is_warning(p) else{})))
            def setup(value):
                require(value==checked(job),'unselected mechanical remaining job');return current
            prior_successful=adapted.successful
            def successful(bound,observed=None):
                if bound==outcome:return authorization_outcome(plan_binding,bound,selected['campaign'])
                return prior_successful(bound,observed)
            adapted.declared_namespace.update(setup=setup,successful=successful);adapted.setup=setup;adapted.successful=successful
            adapted.run(job)
            held=checked(binding(Path(p['operations'])/'hold.json'));checkpoint,value=successful(held['outcome'],current)
            return keep(out/'result.json',dict(workbook=p['workbook'],status=value['status'],PUBLICATION_READY=value['status']=='PUBLICATION_READY',publication_performed=False,plan=plan_binding,job=job,outcome=held['outcome'],checkpoint=checkpoint,runtime=post_binding,automatic_retry=False))

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['run','authorize-child']);parser.add_argument('--plan',required=True);parser.add_argument('--sha256',required=True);parser.add_argument('--intent');parser.add_argument('--intent-sha256');args=parser.parse_args();row=dict(path=args.plan,sha256=args.sha256)
    if args.action=='run':
        require(args.intent is None and args.intent_sha256 is None,'run accepts only the selected plan');print(json.dumps(run(row)))
    else:authorize_child(row,dict(path=args.intent,sha256=args.intent_sha256))

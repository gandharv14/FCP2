"""Current source-scope context and exact failed-round grouping transition.

No review result is reused as approval. Source facts, claims and budgets remain
separate: old calls prove consumed round one; new grouped claims need the actual
remaining writer/paraphraser/independent review. All source scope is re-proved.
"""
from pathlib import Path
from scripts.batch.harbor_admission import read,record,require
from scripts.batch.harbor_model_executor import canonical,sha
from scripts.batch.harbor_tracker_lifecycle import checked_record
from scripts.batch.harbor_readiness import skill_module,ROOT
from scripts.batch import harbor_dialogue_scope_discovery as discovery

SCHEMA='dialogue-failed-round-source-scope/v1'
DIRECTORY='dialogue-grouped-round-02'


def _implementation(root):
    records=[record(__file__)]
    for name in ('extract_claims','claim_scope','compose_draft'):
        records.append(record(Path(skill_module(root,'additional-assumptions-dialogue',name).__file__)))
    return records


def _put(path,value):
    from scripts.batch.harbor_instruction_producer import put
    return put(path,value)


def _copy(path,binding):
    checked_record(binding)
    return _put(path,Path(binding['path']).read_bytes())


def _private(stage,directory):
    stage,directory=Path(stage).resolve(),Path(directory).resolve()
    require(directory!=stage and stage not in directory.parents and directory not in stage.parents,'scope context must be private')
    return stage,directory


def prepare_transition(task,scope,*,root=ROOT):
    """Prepare only; the strict root semantic-retry CAS must bind this receipt."""
    from scripts.batch import harbor_instruction_producer as p
    before,_=p.task_context(task);source=before['bindings']['source_file'];checked_record(scope)
    stage,out=_private(task.stage,task.run/DIRECTORY)
    require(not out.exists(),'grouping transition already prepared; explicit reconciliation required')
    old=task.run/'dialogue';extractor=skill_module(root,'additional-assumptions-dialogue','extract_claims')
    old_claims=record(old/'claims.json');original=read(old_claims)
    require(original==extractor.extract(stage),'original claim context differs from current ungrouped source')
    require(original.get('shared_source_links') is None,'only an original ungrouped failed round can be grouped')
    require(not (old/'round-02').exists(),'a second semantic round already started')
    checked_record(source);disclosure=record(stage/'tests/disclosure.json');payload=read(disclosure)
    require(payload.get('golden')==source['path'] and payload.get('golden_sha256')==source['sha256'],'current original source differs')
    grouped=extractor.extract(stage,source_workbook=source['path'],scope_evidence=scope['path'])
    _claims(original,grouped)
    # These immutable copies are witnesses, never replacements for old files.
    witness=out/'witness'/stage.name
    inputs={name:record(stage/name) for name in ('instruction.md','tests/disclosure.json','tests/answer_key.json')}
    copies={name:_copy(witness/name,binding) for name,binding in inputs.items()}
    selected_scope=_copy(out/'scope.json',scope)
    old_inputs={name:record(old/name) for name in ('claims.json','writer_pack.json','draft_template.md','slots.json',
        'writer-call-01.json','paraphraser-call-01.json','review-call-01.json',
        'round-01/draft.md','round-01/review.json','round-01/review-result.json')}
    old_copies={name:_copy(out/'original'/name,binding) for name,binding in old_inputs.items()}
    # Existing producer paths keep round-one history in their usual relative
    # location. Every byte is copied and explicitly bound to its original.
    history={name:_copy(out/name,old_inputs[name]) for name in ('writer-call-01.json','paraphraser-call-01.json',
        'review-call-01.json','round-01/draft.md','round-01/review.json','round-01/review-result.json')}
    composer=skill_module(root,'additional-assumptions-dialogue','compose_draft');template,slots=composer.compose(grouped,grouped['writer_pack'])
    artifacts={'claims':_put(out/'claims.json',grouped),'pack':_put(out/'writer_pack.json',grouped['writer_pack']),
               'template':_put(out/'draft_template.md',template.encode()),'slots':_put(out/'slots.json',slots)}
    receipt=_put(out/'scope-transition.json',dict(schema_version=SCHEMA,implementation=_implementation(root),
        stage=str(stage),directory=str(out),source=source,scope=selected_scope,original_scope=scope,
        inputs=inputs,witness=copies,original_inputs=old_inputs,original_copies=old_copies,history=history,
        artifacts=artifacts,ordered_claim_ids=[c['record_id'] for c in original['claims']],
        consumed_rounds=1,semantic_limit=2,new_review_required=True,semantic_budget_reset=False))
    validate_transition(receipt,current_claims=grouped,root=root)
    return receipt


def _claims(old,new):
    require(old['claims'] and [c['record_id'] for c in old['claims']]==[c['record_id'] for c in new['claims']], 'scope changed original claim order or coverage')
    require(old.get('shared_source_links') is None and new.get('shared_source_links') is not None,'only ungrouped to proved grouped context supported')
    require(any(len(g)>1 for g in new['shared_source_links']['groups']),'scope does not prove a shared exchange')
    for a,b in zip(old['claims'],new['claims']):
        require({k:v for k,v in a.items() if k not in ('spoken','must_say')}=={k:v for k,v in b.items() if k not in ('spoken','must_say')},'scope changed a financial claim or original private evidence')
    require(old['task']==new['task'] and old['workbook']==new['workbook'],'scope changed task identity')


def validate_transition(binding,*,current_claims=None,root=ROOT):
    v=read(binding);require(v.get('schema_version')==SCHEMA,'unknown source-scope transition')
    require(set(v)=={'schema_version','implementation','stage','directory','source','scope','original_scope','inputs','witness','original_inputs','original_copies','history','artifacts','ordered_claim_ids','consumed_rounds','semantic_limit','new_review_required','semantic_budget_reset'},'scope transition fields differ')
    out=Path(v['directory']);stage=Path(v['stage']);_private(stage,out)
    require(binding==record(out/'scope-transition.json') and out.name==DIRECTORY,'scope transition namespace changed')
    require(v['implementation']==_implementation(root),'scope transition implementation changed')
    require(type(v['consumed_rounds']) is int and type(v['semantic_limit']) is int and v['consumed_rounds']==1 and v['semantic_limit']==2 and v['new_review_required'] is True and v['semantic_budget_reset'] is False,'scope transition changed semantic budget')
    require(set(v['inputs'])==set(v['witness'])=={'instruction.md','tests/disclosure.json','tests/answer_key.json'},'incomplete original task witnesses')
    for name,original in v['inputs'].items():
        require(original['path']==str(stage/name),'original task input namespace differs')
        witness=v['witness'][name];checked_record(witness)
        require(witness==record(out/'witness'/stage.name/name) and witness['sha256']==original['sha256'],'original instruction/disclosure/key witness changed')
        # Dialogue intentionally changes only instruction.md. The current golden
        # binding and grading key must always remain byte-identical.
        if name!='instruction.md':checked_record(original)
    for name,b in v['original_inputs'].items():
        require(b['path']==str(out.parent/'dialogue'/name),'original failed-round namespace differs')
        checked_record(b);copied=v['original_copies'][name];checked_record(copied)
        require(copied==record(out/'original'/name) and b['sha256']==copied['sha256'],'original failed-round artifact changed')
    require(set(v['original_inputs'])==set(v['original_copies'])=={'claims.json','writer_pack.json','draft_template.md','slots.json',
        'writer-call-01.json','paraphraser-call-01.json','review-call-01.json','round-01/draft.md','round-01/review.json','round-01/review-result.json'},'incomplete failed-round evidence')
    require(set(v['history'])=={'writer-call-01.json','paraphraser-call-01.json','review-call-01.json','round-01/draft.md','round-01/review.json','round-01/review-result.json'},'incomplete retained producer history')
    for name,b in v['history'].items():
        checked_record(b);require(b==record(out/name) and b['sha256']==v['original_inputs'][name]['sha256'],'copied history changed')
    checked_record(v['source']);checked_record(v['scope']);checked_record(v['original_scope'])
    require(v['scope']==record(out/'scope.json') and v['scope']['sha256']==v['original_scope']['sha256'],'selected source scope changed')
    payload=read(v['inputs']['tests/disclosure.json'])
    require(payload.get('golden')==v['source']['path'] and payload.get('golden_sha256')==v['source']['sha256'],'scope original source differs')
    witness=out/'witness'/stage.name;extractor=skill_module(root,'additional-assumptions-dialogue','extract_claims')
    old=extractor.extract(witness);new=extractor.extract(witness,source_workbook=v['source']['path'],scope_evidence=v['scope']['path'])
    require(old==read(v['original_inputs']['claims.json']),'original claims no longer reproduce')
    _claims(old,new)
    require(v['ordered_claim_ids']==[c['record_id'] for c in old['claims']],'original ordered identity changed')
    composer=skill_module(root,'additional-assumptions-dialogue','compose_draft')
    original_template,original_slots=composer.compose(old,old['writer_pack'])
    require(Path(v['original_inputs']['draft_template.md']['path']).read_text()==original_template
            and read(v['original_inputs']['slots.json'])==original_slots
            and read(v['original_inputs']['writer_pack.json'])==old['writer_pack'],'original scaffold changed')
    template,slots=composer.compose(new,new['writer_pack'])
    expected={'claims':(out/'claims.json',new),'pack':(out/'writer_pack.json',new['writer_pack']),
              'template':(out/'draft_template.md',template.encode()),'slots':(out/'slots.json',slots)}
    require(set(v['artifacts'])==set(expected),'grouped artifact set changed')
    for name,(path,value) in expected.items():
        b=v['artifacts'][name];checked_record(b)
        require(b==record(path) and path.read_bytes()==(value if isinstance(value,bytes) else canonical(value)),'grouped claims/scaffold changed')
    if current_claims is not None:require(current_claims==new,'new author/reviewer claims differ from certified scope')
    return old,new,v


def selected_transition(task,*,root=ROOT):
    """Select only the exact transition authorized in this original's root CAS."""
    from scripts.batch import harbor_instruction_producer as p
    from scripts.batch.harbor_dialogue_semantic_retry import SCHEMA as RETRY
    before,_=p.task_context(task);matches=[]
    for handoff in before['handoffs']:
        if handoff.get('schema_version')==RETRY:
            permit=read(handoff['permit'])
            if permit.get('scope_transition') is not None:matches.append(permit['scope_transition'])
    require(len(matches)<=1,'multiple scope transitions for one remaining round')
    if not matches:return None
    binding=matches[0];old,new,v=validate_transition(binding,root=root)
    require(v['directory']==str(task.run/DIRECTORY) and v['stage']==str(task.stage)
            and v['source']==before['bindings']['source_file'],'root-selected scope belongs to another task/source')
    return binding


def evidence(binding):
    """Exact immutable proof files; no recursively trusted arbitrary dictionaries."""
    v=read(binding);records=[binding,*v['implementation'],v['source'],v['scope'],v['original_scope']]
    records += list(v['witness'].values())+list(v['original_inputs'].values())+list(v['original_copies'].values())+list(v['history'].values())+list(v['artifacts'].values())
    records += [b for k,b in v['inputs'].items() if k!='instruction.md']
    for b in records:checked_record(b)
    return records


def validate_selected(selection,*,root=ROOT):
    binding=selection.dialogue_scope_receipt
    if binding is not None and read(binding).get('schema_version')==EQUIVALENCE_SCHEMA:
        return validate_equivalence(selection,root=root)
    return _validate_selected(selection,root=root)


def _relocated_discovery_implementation(retained,current):
    """Allow only locator changes for the three identical implementation files."""
    roles=('producer','scope_validator','extractor')
    require({k:v for k,v in retained.items() if k not in roles}==
            {k:v for k,v in current.items() if k not in roles},
            'selected discovery receipt changed beyond implementation locators')
    evidence=[]
    for role in roles:
        old,new=retained[role],current[role]
        require(set(old)==set(new)=={'path','sha256'},'closed discovery implementation binding required')
        checked_record(old);checked_record(new)
        require(old['sha256']==new['sha256'] and Path(old['path']).read_bytes()==Path(new['path']).read_bytes(),
                'selected discovery implementation bytes changed')
        if old!=new:evidence.append(new)
    return evidence


def _validate_selected(selection,*,root=ROOT,equivalence=False):
    """Bind private scope proof back to this task and its exact NAT witness."""
    from scripts.batch import harbor_readiness as static
    s=selection;binding=s.dialogue_scope_receipt
    if binding is None:return []
    v=read(binding);witness_stage=static.disclosure_verification_stage(s,root=root)
    require(v['source']==s.bindings['source_file'],'scope receipt belongs to a different source')
    if v.get('schema_version')==SCHEMA:
        _,actual,v=validate_transition(binding,current_claims=read(s.dialogue_claims),root=root)
        require(v['stage']==str(s.stage) and v['scope']==s.scope_evidence,'selected grouping context differs')
        require(v['inputs']['instruction.md']['sha256']==record(witness_stage/'instruction.md')['sha256'],'grouping witness is not the current NAT candidate')
        return evidence(binding)
    require(v.get('schema_version')==discovery.SCHEMA,'unknown selected dialogue scope receipt')
    # Re-derive first-call discovery using the separately validated current NAT
    # witness. Historical instruction locator stays in the original receipt.
    disclosure=record(s.stage/'tests/disclosure.json')
    require(v['disclosure']==disclosure and v['instruction']['path']==str(s.stage/'instruction.md')
            and v['instruction']['sha256']==record(witness_stage/'instruction.md')['sha256'],'first-call discovery context differs')
    require(record(witness_stage/'tests/disclosure.json')['sha256']==disclosure['sha256'],'scope witness disclosure changed')
    extractor=skill_module(root,'additional-assumptions-dialogue','extract_claims');base=extractor.extract(witness_stage)
    payload,details=discovery._derive(base['claims'],v['source'],disclosure,root=root)
    require(payload==read(v['scope']) and details==read(v['discovery']),'current discovery or original headers changed')
    actual=extractor.extract(witness_stage,source_workbook=v['source']['path'],scope_evidence=v['scope']['path'])
    grouped=any(len(g)>1 for g in actual['shared_source_links']['groups'])
    if not grouped:actual=base
    expected=dict(schema_version=discovery.SCHEMA,producer=record(discovery.__file__),scope_validator=record(Path(skill_module(root,'additional-assumptions-dialogue','claim_scope').__file__)),extractor=record(Path(extractor.__file__)),source=v['source'],disclosure=disclosure,instruction=v['instruction'],original_claims_sha256=sha(canonical(base['claims'])),claim_ids=[c['record_id'] for c in base['claims']],scope=v['scope'],discovery=v['discovery'],used=grouped,final_claims_sha256=sha(canonical(actual)),semantic_attempts_added=0,semantic_budget_reset=False)
    relocated=[]
    if equivalence:
        for key in ('producer','scope_validator','extractor'):
            checked_record(v[key])
        retained={k:val for k,val in v.items() if k not in ('producer','scope_validator','extractor')}
        current={k:val for k,val in expected.items() if k not in ('producer','scope_validator','extractor')}
        require(retained==current and actual==read(s.dialogue_claims),'scope equivalence changed claim semantics')
    else:
        relocated=_relocated_discovery_implementation(v,expected)
        require(actual==read(s.dialogue_claims),'selected discovery receipt or claims changed')
    require(s.scope_evidence==(v['scope'] if grouped else None),'selected first-call scope differs')
    return [binding,v['producer'],v['scope_validator'],v['extractor'],v['source'],disclosure,v['scope'],v['discovery'],record(witness_stage/'instruction.md'),*relocated]


EQUIVALENCE_SCHEMA='dialogue-discovery-code-equivalence/v1'


def _equivalence_implementation(root):
    return [record(__file__),record(discovery.__file__),
            record(Path(skill_module(root,'additional-assumptions-dialogue','extract_claims').__file__)),
            record(Path(skill_module(root,'additional-assumptions-dialogue','claim_scope').__file__))]


def requalify_selected(selection,directory,*,root=ROOT):
    """Re-prove exact discovery/claims under current code; retain old receipt."""
    from dataclasses import replace
    binding=selection.dialogue_scope_receipt
    require(binding is not None,'selected discovery receipt required')
    replaces=None
    if read(binding).get('schema_version')==EQUIVALENCE_SCHEMA:
        _validate_equivalence_history(selection,binding,root=root)
        replaces=binding;binding=read(binding)['previous']
    require(read(binding).get('schema_version')==discovery.SCHEMA,'original discovery receipt required')
    evidence=_validate_selected(replace(selection,dialogue_scope_receipt=binding),root=root,equivalence=True)
    directory=Path(directory);require(not directory.exists(),'scope requalification already exists')
    directory.mkdir(parents=True)
    receipt=_put(directory/'scope-equivalence.json',dict(schema_version=EQUIVALENCE_SCHEMA,
        previous=binding,**({'replaces':replaces} if replaces is not None else {}),
        implementation=_equivalence_implementation(root),evidence=evidence,
        stage=str(selection.stage),source=selection.bindings['source_file'],claims=selection.dialogue_claims,
        semantic_attempts_added=0,semantic_budget_reset=False))
    updated=replace(selection,dialogue_scope_receipt=receipt)
    validate_equivalence(updated,root=root)
    return updated,receipt


def validate_equivalence(selection,*,root=ROOT):
    from dataclasses import replace
    binding=selection.dialogue_scope_receipt;v=read(binding)
    require(set(v)-{'replaces'}=={'schema_version','previous','implementation','evidence','stage','source','claims',
                    'semantic_attempts_added','semantic_budget_reset'} and v['schema_version']==EQUIVALENCE_SCHEMA,
            'closed scope equivalence receipt required')
    require(v['implementation']==_equivalence_implementation(root),'scope equivalence code changed')
    require(v['stage']==str(selection.stage) and v['source']==selection.bindings['source_file']
            and v['claims']==selection.dialogue_claims,'scope equivalence selection changed')
    require(v['semantic_attempts_added']==0 and v['semantic_budget_reset'] is False,'scope equivalence cannot reset budget')
    require(read(v['previous']).get('schema_version')==discovery.SCHEMA,'scope equivalence cannot chain arbitrary receipt')
    evidence=_validate_selected(replace(selection,dialogue_scope_receipt=v['previous']),root=root,equivalence=True)
    require(v['evidence']==evidence,'scope equivalence evidence changed')
    for item in v['implementation']:checked_record(item)
    retained=[]
    if 'replaces' in v:
        require(read(v['replaces'])['previous']==v['previous'],'scope successor changed original discovery')
        retained=_validate_equivalence_history(selection,v['replaces'],root=root)
    return [binding,*v['implementation'],*evidence,*retained]


def _validate_equivalence_history(selection,binding,*,root=ROOT,seen=()):
    from dataclasses import replace
    require(binding['path'] not in seen and len(seen)<8,'cyclic or excessive scope receipt history')
    v=read(binding)
    require(set(v)-{'replaces'}=={'schema_version','previous','implementation','evidence','stage','source','claims',
        'semantic_attempts_added','semantic_budget_reset'} and v['schema_version']==EQUIVALENCE_SCHEMA,
        'closed historical scope equivalence required')
    require(v['stage']==str(selection.stage) and v['source']==selection.bindings['source_file']
        and v['claims']==selection.dialogue_claims and v['semantic_attempts_added']==0
        and v['semantic_budget_reset'] is False,'historical scope selection or budget changed')
    require(read(v['previous']).get('schema_version')==discovery.SCHEMA,'historical scope original differs')
    roles=['harbor_dialogue_scope_context.py','harbor_dialogue_scope_discovery.py','extract_claims.py','claim_scope.py']
    require(isinstance(v['implementation'],list) and [Path(i['path']).name for i in v['implementation']]==roles,
        'historical scope implementation roles changed')
    for item in v['implementation']:checked_record(item)
    evidence=_validate_selected(replace(selection,dialogue_scope_receipt=v['previous']),root=root,equivalence=True)
    require(v['evidence']==evidence,'historical scope evidence differs from exact rederived semantics')
    records=[binding,*v['implementation'],*evidence]
    if 'replaces' in v:
        require(read(v['replaces'])['previous']==v['previous'],'scope history changed original discovery')
        records+=_validate_equivalence_history(selection,v['replaces'],root=root,seen=(*seen,binding['path']))
    return records


def validate_task_equivalence(task,binding,original,*,root=ROOT):
    """Explicit Task-carried proof for preparation of the remaining model round."""
    v=read(binding)
    require(v.get('schema_version')==EQUIVALENCE_SCHEMA and v['previous']==original,
            'task scope equivalence detached from original discovery')
    require(v['implementation']==_equivalence_implementation(root),'task scope equivalence code changed')
    require(v['stage']==str(task.stage) and v['semantic_attempts_added']==0 and v['semantic_budget_reset'] is False,
            'task scope equivalence identity or budget changed')
    require(v['source']==read(original)['source'],'task scope equivalence source changed')
    for b in [*v['implementation'],*v['evidence'],v['claims']]:checked_record(b)
    cursor=v;seen={binding['path']}
    while 'replaces' in cursor:
        old=cursor['replaces'];require(old['path'] not in seen and len(seen)<8,'cyclic task scope history')
        seen.add(old['path']);cursor=read(old)
        require(set(cursor)-{'replaces'}==set(v)-{'replaces'} and all(cursor[k]==v[k] for k in
            ('schema_version','previous','evidence','stage','source','claims','semantic_attempts_added','semantic_budget_reset')),
            'task scope predecessor semantics changed')
        require([Path(i['path']).name for i in cursor['implementation']]==
            ['harbor_dialogue_scope_context.py','harbor_dialogue_scope_discovery.py','extract_claims.py','claim_scope.py'],
            'task scope predecessor roles changed')
        for b in cursor['implementation']:checked_record(b)
    actual,options=discovery._validate(task.stage,original,root=root,equivalence=True)
    require(actual==read(v['claims']),'task scope equivalence claims changed')
    return actual,options

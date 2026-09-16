"""Compose all current gates and root-captured runtime evidence, read-only."""
from dataclasses import dataclass,fields,is_dataclass
from pathlib import Path
from scripts.batch import harbor_readiness as static
from scripts.batch.harbor_admission import record,require
from scripts.batch.harbor_tracker_lifecycle import checked_record
from scripts.batch.harbor_runtime_readiness import RuntimeSelection,RuntimeObservation,validate_runtime,delivered_workbook
from scripts.batch.harbor_semantic_readiness import disclosure_review,dialogue_review,HistoricalApplicability

ROOT=Path(__file__).resolve().parents[2]

@dataclass(frozen=True)
class SemanticSelection:
    disclosure_call: object
    disclosure_manifest: dict
    disclosure_instruction: dict
    dialogue_call: object
    dialogue_reviewed_claims: dict
    dialogue_writer_calls: tuple
    historical_dialogue: HistoricalApplicability | None = None
    disclosure_revision: dict | None = None
    dialogue_pre_review_repair: dict | None = None
    disclosure_partition_aggregate: dict | None = None
    dialogue_partition_aggregate: dict | None = None

@dataclass(frozen=True)
class ReadinessSelection:
    content: static.Selection
    semantic: SemanticSelection
    runtime: RuntimeSelection
    observation: RuntimeObservation
    public_profiles: object | None = None


def evidence_records(value):
    """Expand exact tree-hash bindings into independently checked file evidence.

    Directory digests use the existing publication tree contract (paths, sizes,
    algorithms and byte hashes), not a file hash or a caller-supplied manifest.
    Retain no unchecked directory pseudo-record in the final file inventory.
    """
    from scripts.batch import harbor_tracker as schema
    found={};directories={};declared={}

    def file(binding):
        checked_record(binding)
        old=found.get(binding['path'])
        require(old is None or old==binding['sha256'],'conflicting root evidence bindings')
        found[binding['path']]=binding['sha256']

    def binding(v, *, directory=False):
        require(isinstance(v['path'],str) and Path(v['path']).is_absolute(), 'evidence path must be absolute')
        require(isinstance(v['sha256'],str) and schema.HEX.fullmatch(v['sha256']), 'invalid evidence digest')
        path=schema.safe_path(v['path'])
        # Normalize lexical aliases without resolving or accepting a symlink.
        import os
        name=os.path.abspath(path)
        old=declared.get(name)
        require(old is None or old==v['sha256'],'conflicting root evidence bindings')
        declared[name]=v['sha256']
        if directory:
            require(path.is_dir(), 'typed directory evidence is not a directory')
            digest,inventory=static.publication._tree_hash(path)
            require(bool(inventory),'empty evidence directory')
            require(digest==v['sha256'],'changed directory evidence: '+str(path))
            for relative,item in inventory.items():
                child=path/relative
                file({'path':str(child),'sha256':item['sha256']})
            require(static.publication._tree_hash(path)==(digest,inventory),'directory changed during evidence enumeration')
            directories[name]=(path,digest,inventory)
        else:
            file(v)

    def visit(v):
        if is_dataclass(v):
            for field in fields(v):
                child=getattr(v,field.name)
                if isinstance(v,static.Selection) and field.name=='bindings':
                    require(isinstance(child,dict), 'typed selection bindings must be a mapping')
                    for key,item in child.items():
                        if key in {'mcp_build_a','mcp_build_b','mcp_bundle','staged_bundle'}:
                            require(isinstance(item,dict) and set(item)=={'path','sha256'}, 'typed directory binding must have exact path/hash fields')
                            binding(item,directory=True)
                        else:visit(item)
                else:visit(child)
        elif isinstance(v,dict):
            if set(v)=={'path','sha256'}:binding(v)
            else:
                for child in v.values():visit(child)
        elif isinstance(v,(list,tuple)):
            for child in v:visit(child)
    visit(value)
    # A later sibling may overlap an earlier tree. Recheck both file and full
    # membership evidence before returning, including additions and deletions.
    for path,digest,inventory in directories.values():
        schema.safe_path(path)
        require(static.publication._tree_hash(path)==(digest,inventory),'directory changed before evidence return')
    records=[{'path':path,'sha256':digest} for path,digest in sorted(found.items())]
    for item in records:checked_record(item)
    return records


def selected_content_evidence(s):
    roots=[s.stage,s.normalization_run,s.naturalization_root]
    if s.pre_dialogue_witness:roots.append(s.pre_dialogue_witness.stage)
    roots += [p for p in (s.mcp_build_a,s.mcp_build_b) if p is not None]
    records=[]
    for path in roots:
        for relative in static.tree(path):records.append(record(path/relative))
    for name in ('source_generation','segmentation_generation'):
        records.append(record(Path(s.bindings[name]['path'])/'generation-manifest.json'))
    return {'evidence':records}


def check_disclosure(selection,*,root=ROOT):
    s=selection.content;p=selection.semantic
    recovery=static.skill_module(root,'naturalize-finance-task-instruction','naturalize_recovery')
    checked_record(p.disclosure_instruction);reviewed=Path(p.disclosure_instruction['path']).read_bytes()
    applicability='staged_instruction';naturalization_evidence=[]
    if reviewed!=(s.stage/'instruction.md').read_bytes():
        state=recovery._read_state(s.naturalization_root);original=recovery._verified_snapshot(s.naturalization_root,state)
        applicability='original_nat_source'
        if reviewed!=original:
            # A retained review may have followed NAT and preceded dialogue.
            # Prove the whole current chain before accepting that exact input;
            # no unrelated intermediate or historical candidate is admissible.
            proof=check_naturalization(selection,root=root)
            candidate=s.naturalization_root/state['final_candidate']
            require(reviewed==candidate.read_bytes() and p.disclosure_instruction['sha256']==proof['candidate_sha256'],
                    'disclosure review is not for current validated NAT source or final candidate')
            applicability='validated_final_nat_candidate'
            naturalization_evidence=proof['evidence']
    if p.disclosure_partition_aggregate is not None:
        require(p.disclosure_call is None and p.disclosure_revision is None,'partitioned disclosure cannot also claim an ordinary model Call or revision')
        from scripts.batch.harbor_disclosure_partition_review import validate_aggregate
        result=validate_aggregate(p.disclosure_partition_aggregate,p.disclosure_manifest,instruction=p.disclosure_instruction,
            disclosure=record(s.stage/'tests/disclosure.json'),golden=s.bindings['source_file'],
            delivered=record(delivered_workbook(s.stage,s.workbook)),policy=s.model_policy,root=root)
    else:
        require(p.disclosure_call is not None,'disclosure review Call or complete partition aggregate required')
        result=disclosure_review(p.disclosure_call,p.disclosure_manifest,instruction=p.disclosure_instruction,disclosure=record(s.stage/'tests/disclosure.json'),golden=s.bindings['source_file'],delivered=record(delivered_workbook(s.stage,s.workbook)),policy=s.model_policy,root=root,revision=p.disclosure_revision)
    result['instruction_applicability']=applicability
    result['evidence']=evidence_records([result.get('evidence',[]),p.disclosure_partition_aggregate,p.disclosure_call,p.disclosure_manifest,p.disclosure_instruction,record(s.stage/'tests/disclosure.json'),s.bindings['source_file'],record(delivered_workbook(s.stage,s.workbook)),naturalization_evidence])
    return result


def check_dialogue(selection,*,root=ROOT):
    s=selection.content;p=selection.semantic
    if s.dialogue_noop is not None:
        require(p.dialogue_partition_aggregate is None,'dialogue no-op cannot claim partition review evidence')
        from scripts.batch.harbor_dialogue_noop import validate
        return validate(selection,root=root)
    require(s.dialogue_reviewed_draft is not None,'exact reviewed dialogue draft binding required')
    checked_record(s.dialogue_reviewed_draft)
    if p.dialogue_partition_aggregate is not None:
        require(p.dialogue_call is None and p.dialogue_writer_calls==(),
                'partitioned dialogue must retain actual Call arrays only in its aggregate')
        require(p.historical_dialogue is None and p.dialogue_pre_review_repair is None,
                'partitioned dialogue cannot combine unrelated historical or correction authority')
        from scripts.batch.harbor_dialogue_partition_review import validate_aggregate
        result=validate_aggregate(p.dialogue_partition_aggregate,claims=s.dialogue_claims,
            reviewed_claims=p.dialogue_reviewed_claims,draft=s.dialogue_reviewed_draft,
            notes=record(s.stage/'environment/additional-assumptions.md'),policy=s.model_policy,
            workbook=s.workbook,original_sha256=s.bindings['original_raw_source']['sha256'],
            source=s.bindings['source_file'],answer_key=record(s.stage/'tests/answer_key.json'),
            task_dir=s.stage,root=root)
        require(result['review']==static.read(s.dialogue_review),
                'owning dialogue report differs from rederived actual partition reviews')
        result['evidence']=evidence_records([p,s.dialogue_claims,s.dialogue_review,
            s.dialogue_reviewed_draft,record(s.stage/'environment/additional-assumptions.md'),result])
        return result
    require(p.dialogue_call is not None,'dialogue review Call or complete partition aggregate required')
    request=static.read(p.dialogue_call.request)
    import json
    require(json.loads(request['messages'][1]['content'])['draft.md']==Path(s.dialogue_reviewed_draft['path']).read_text(),'owning coverage draft differs from actual review context')
    result=dialogue_review(p.dialogue_call,claims=s.dialogue_claims,notes=record(s.stage/'environment/additional-assumptions.md'),reviewed_claims=p.dialogue_reviewed_claims,policy=s.model_policy,writer_calls=p.dialogue_writer_calls,workbook=s.workbook,current_source_sha256=s.bindings['source_file']['sha256'],historical=p.historical_dialogue,root=root,original_sha256=s.bindings['original_raw_source']['sha256'],source=s.bindings['source_file'],answer_key=record(s.stage/'tests/answer_key.json'),task_dir=s.stage,pre_review_repair=p.dialogue_pre_review_repair)
    require(result['provenance']['review']==static.read(s.dialogue_review),'owning dialogue gate uses a different parsed review')
    if result.get('source_warning_proof') is not None:
        from scripts.batch import harbor_dialogue_source_warnings as warnings
        import copy
        proof=copy.deepcopy(result['source_warning_proof'])
        require(proof['evidence'][0]==p.dialogue_call.parsed
                and p.dialogue_call.parsed['sha256']==s.dialogue_review['sha256'],
                'selected source-warning review is not an exact parsed response copy')
        proof['evidence'][0]=s.dialogue_review
        warnings.revalidate(proof,static.read(s.dialogue_review),static.read(s.dialogue_claims),
            Path(s.dialogue_reviewed_draft['path']).read_text(),root=root)
        result['source_warning_proof']=proof
        result['source_warning_review_copy']={'parsed':p.dialogue_call.parsed,'selected':s.dialogue_review}
    result['evidence']=evidence_records([p,s.dialogue_claims,s.dialogue_review,record(s.stage/'environment/additional-assumptions.md'),result])
    return result


def complete_readiness(selection,*,root=ROOT,runtime_producer=None):
    """Execute no model/container/workbook build or publication operations.

    Root executes run_runtime outside tracker locks, captures its observation,
    then calls this concrete validator again immediately before publication CAS.
    """
    require(isinstance(selection,ReadinessSelection),'typed complete readiness selection required')
    s=selection.content;runtime=selection.runtime
    require(isinstance(s,static.Selection) and isinstance(selection.semantic,SemanticSelection),'typed owning content/semantic selections required')
    require(runtime.workbook==s.workbook and runtime.stage==s.stage and runtime.stage_tree==static.tree(s.stage),'runtime/content task context differs')
    dialogue_result=None;dialogue_error=None
    try:dialogue_result=check_dialogue(selection,root=root)
    except (Exception,SystemExit) as exc:dialogue_error=type(exc).__name__
    source_proof=None if dialogue_result is None else dialogue_result.get('source_warning_proof')
    inspected=static.inspect_readiness(s,root=root,source_warning_proof=source_proof)
    unresolved={'disclosure_and_dialogue_review_transport_and_current_context_applicability','live_http_exact_retrieval_current_image_binding','container_startup_current_image_binding','grader_smoke_score_1_current_container_execution'}
    blockers=[name for name in inspected['blockers'] if name not in unresolved];checks=dict(inspected['checks']);evidence=evidence_records(selection)
    def check(name,operation):
        try:
            result=operation();checks[name]={'checked':True,'result':result};evidence.extend(evidence_records(result));return result
        except (Exception,SystemExit) as exc:
            checks[name]={'checked':False,'error_type':type(exc).__name__};blockers.append(name);return None
    norm=checks.get('normalization_profile_primitives',{})
    require(norm.get('checked') is not True or norm['result']['mode']==runtime.mode,'runtime mode differs from normalized eligibility')
    check('disclosure_review_provenance',lambda:check_disclosure(selection,root=root))
    check('naturalization_current_context',lambda:check_naturalization(selection,root=root))
    if dialogue_error is None:
        checks['dialogue_review_provenance']={'checked':True,'result':dialogue_result}
        evidence.extend(evidence_records(dialogue_result))
    else:
        checks['dialogue_review_provenance']={'checked':False,'error_type':dialogue_error}
        blockers.append('dialogue_review_provenance')
    check('accepted_public_profiles',lambda:check_profiles(selection,root=root))
    check('runtime_observation',lambda:validate_runtime(runtime,selection.observation,root=root, **(
        {'producer_identity':runtime_producer} if runtime_producer is not None else {})))
    check('context_bindings_final',lambda:static.context_bindings(s))
    check('selected_content_evidence',lambda:selected_content_evidence(s))
    require(static.publication._tree_hash(s.stage)[0]==s.stage_sha256,'stage changed during final readiness')
    evidence=evidence_records(evidence)
    return {'schema_version':'harbor-complete-readiness/v1','workbook':s.workbook,'stage_sha256':s.stage_sha256,'publication_ready':not blockers,'checks':checks,'blockers':sorted(set(blockers)),'evidence':evidence,'mutation_performed':False}


def check_naturalization(selection,*,root=ROOT):
    s=selection.content;stage=s.stage
    validator=static.skill_module(root,'naturalize-finance-task-instruction','validate_instruction_rewrite')
    recovery=static.skill_module(root,'naturalize-finance-task-instruction','naturalize_recovery')
    # verify_applied_recovery can WRITE transaction/state; deliberately not used.
    state=recovery._read_state(s.naturalization_root)
    require(state.get('status')=='applied','naturalization is not recorded applied')
    original=recovery._verified_snapshot(s.naturalization_root,state)
    recovery._verified_budget(s.naturalization_root,state);recovery._verify_attempt_evidence(s.naturalization_root,state)
    key=recovery._verified_answer_key(s.naturalization_root,state)
    review=recovery._verified_semantic_review(s.naturalization_root,state)
    from scripts.batch.harbor_review_context import naturalization_review
    number=state['attempts'][-1]['attempt'];attempt=s.naturalization_root/('attempt-%02d'%number)
    reviewed_candidate=(s.naturalization_root/state['final_candidate']).read_text()
    if state.get('span_restoration'):
        restoration=static.skill_module(root,'naturalize-finance-task-instruction','span_restoration')
        receipt=restoration.validate(recovery,s.naturalization_root,state)
        require(s.naturalization_call.request['sha256']==receipt['proof']['original_evidence'][receipt['proof']['original_review_request']],'restored review request changed')
        require(s.naturalization_call.response['sha256']==receipt['proof']['original_evidence'][receipt['proof']['original_review_response']],'restored review response changed')
        reviewed_candidate=(attempt/'candidate.md').read_text()
    naturalization_review(s.naturalization_call,s.naturalization_request,
        source=original.decode(),candidate=reviewed_candidate,policy=s.model_policy,
        writer_call=s.naturalization_writer_call,writer_request=s.naturalization_writer_request,
        writer_bodies={key:record(attempt/(key+'.md')) for key in ('preamble_body','input_body')},root=root)
    candidate=s.naturalization_root/state['final_candidate']
    require(static.digest(candidate)==state['final_instruction_sha256'],'naturalized candidate changed')
    validator.validate(original,candidate.read_bytes(),key)
    require(key==static.read(record(stage/'tests/answer_key.json')),'naturalizer answer key changed')
    staged=(stage/'instruction.md').read_text()
    if staged!=candidate.read_text():
        dialogue=static.skill_module(root,'additional-assumptions-dialogue','validate_dialogue')
        require(dialogue.rewrite_instruction(candidate.read_text(),stage,static.read(s.dialogue_claims))==staged,'staged instruction is not exact naturalized or dialogue-transformed content')
    recovery.verify_naturalizer_metadata(stage/'instruction.md',stage/'task.toml')
    return {'valid':True,'candidate_sha256':static.digest(candidate),'budget_preserved':True,'attempts_used':recovery._attempts_used(state),'evidence':evidence_records([s.naturalization_call,s.naturalization_writer_call,s.naturalization_request,s.naturalization_writer_request,record(candidate),record(s.naturalization_root/'state.json'),record(stage/'tests/answer_key.json'),record(stage/'task.toml')])}


def check_runtime(selection,*,root=ROOT):
    return validate_runtime(selection.runtime,selection.observation,root=root)


def check_profiles(selection,*,root=ROOT):
    require(selection.public_profiles is not None,'owning public/generic profile evidence required')
    from scripts.batch.harbor_profile_readiness import validate_profiles,ProfileSelection
    p=selection.public_profiles;s=selection.content
    require(isinstance(p,ProfileSelection) and isinstance(s,static.Selection),'typed current task/profile context required')
    b=s.bindings
    require(p.policy==s.model_policy,'profile model policy differs from current task')
    require(s.normalization_run.name==s.workbook+'-variable-sources','foreign profile normalization run')
    for attribute,key in [('phase1','normalized_phase1'),('inventory','variable_source_inventory'),('profiles','source_profiles'),('mapping','source_profile_mapping')]:
        require(getattr(p,attribute)==b[key],'profile selection differs from current task: '+key)
    for binding in (p.phase1,p.profiles,p.mapping,b['normalized_final']):
        require(Path(binding['path']).parent==s.normalization_run,'profile artifact belongs to another normalization run')
        checked_record(binding)
    require(Path(p.profiles['path']).name=='source_profiles.json','profile catalog is not the generator input')
    phase1=static.read(p.phase1);final=static.read(b['normalized_final'])
    require(phase1.get('environment_id')==final.get('environment_id')=='fcp-'+s.workbook,'profile specification belongs to another workbook')
    require(phase1.get('pipeline_bindings')==final.get('pipeline_bindings'),'profile phase1 and final source context differ')
    for kind in ('source','segmentation'):
        bindings=phase1.get('pipeline_bindings') or {}
        require(bindings.get(kind+'_generation_id')==b[kind+'_generation']['id'] and bindings.get(kind+'_manifest_sha256')==b[kind+'_generation']['manifest_sha256'],'profile source/segmentation context differs')
    result=validate_profiles(p,root=root)
    require(final.get('source_profiles',[])==static.read(p.profiles)['profiles'],'final spec catalog differs from independently reviewed profiles')
    require(result.get('confidence')=='high' or (result.get('confidence')=='medium' and result.get('skipped_profiles',0)>0 and result['accepted_profiles']+result['skipped_profiles']==result['public_sources']),'public profile gap lacks verified generic fallback')
    return result

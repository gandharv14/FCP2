"""Read-only production readiness inspection; unresolved adapters never grant PASS.

Root selects paths and exact artifact hashes. Agent handoff flags are not inputs.
No publications, repairs, model calls, subprocesses, HTTP or container operations.
"""
from dataclasses import dataclass,field
import importlib.util
import json
from pathlib import Path
import sys

from scripts.batch.harbor_admission import record,read,require
from scripts.batch.harbor_tracker_lifecycle import checked_record,check_records
from scripts.corpus import digest
import xl_release_publication as publication

ROOT=Path(__file__).resolve().parents[2]  # supported installed module location


@dataclass(frozen=True)
class PreDialogueWitness:
    stage: Path
    tree_sha256: str


@dataclass(frozen=True)
class Selection:
    workbook: str
    stage: Path
    stage_sha256: str
    bindings: dict
    normalization_run: Path
    naturalization_root: Path
    dialogue_claims: dict
    dialogue_review: dict
    normalization_records: tuple = ()
    scope_evidence: dict | None = None
    mcp_build_a: Path | None = None
    mcp_build_b: Path | None = None
    allowlist: dict | None = None
    context_trees: dict = field(default_factory=dict)
    normalization_prepared: dict | None = None
    normalization_call: object = None
    naturalization_call: object = None
    naturalization_writer_call: object = None
    naturalization_request: dict | None = None
    naturalization_writer_request: dict | None = None
    model_policy: object = None
    dialogue_reviewed_draft: dict | None = None
    trusted_public_key: str = '/etc/harbor/excel-runner-public.pem'
    pre_dialogue_witness: PreDialogueWitness | None = None
    dialogue_noop: dict | None = None
    dialogue_scope_receipt: dict | None = None


def tree(path):
    path=Path(path)
    require(path.is_dir() and not path.is_symlink(),'missing/unsafe tree')
    records={}
    for file in sorted(path.rglob('*')):
        require(not file.is_symlink(),'symlink in evidence tree')
        if file.is_file():records[file.relative_to(path).as_posix()]=digest(file)
        elif not file.is_dir():raise ValueError('nonregular evidence entry')
    require(bool(records),'empty evidence tree')
    return records


# Fixed sibling dependency graph. Import only current repository-owned modules;
# lazy absolute imports must remain resolvable after restoring sys.path.
_SKILL_FILES = {
    'naturalize-finance-task-instruction': (
        'instruction_spans', 'validate_instruction_rewrite',
        'freeze_protected_spans', 'span_restoration', 'frozen_submission', 'boundary_submission',
        'naturalize_recovery'),
    'task-disclosure': ('disclose',),
    'additional-assumptions-dialogue': (
        'aa_lib', 'spoken_formula', 'claim_scope', 'compose_draft',
        'validate_dialogue', 'extract_claims'),
    'profile-mcp-sources': ('validate_source_profiles', 'assemble_profiles'),
}


def skill_module(root,skill,name):
    """Load fixed current skill modules including their known lazy siblings."""
    require(skill in _SKILL_FILES and name in _SKILL_FILES[skill],
            'unsupported current skill validator')
    root=Path(root).resolve()
    groups=['task-disclosure',skill] if skill=='additional-assumptions-dialogue' else [skill]
    expected={module:root/'.cursor/skills'/group/'scripts'/(module+'.py')
              for group in groups for module in _SKILL_FILES[group]}
    # Check every origin before executing any module: sys.modules takes priority
    # over sys.path, and a foreign cached sibling must never be consumed.
    for module,path in expected.items():
        require(path.is_file() and not any(p.is_symlink() for p in (path,*path.parents)),
                'missing/unsafe current skill validator')
        loaded=sys.modules.get(module)
        require(loaded is None or Path(getattr(loaded,'__file__','')).resolve()==path,
                'foreign cached skill module: '+module)
    previous=list(sys.path)
    try:
        for group in groups:sys.path.insert(0,str(root/'.cursor/skills'/group/'scripts'))
        for module,path in expected.items():
            loaded=importlib.import_module(module)
            require(Path(loaded.__file__).resolve()==path,
                    'foreign current skill module: '+module)
        return sys.modules[name]
    finally:sys.path[:]=previous


def grader_content(stage,workbook,source,segmentation,baseline,*,root=ROOT):
    """Exact current grader, target/group reconstruction and unchanged tolerance.

    This establishes content/wiring only, never a measured smoke score.
    """
    import xl_output_task as builder
    stage=Path(stage);gold=Path(source);baseline=Path(baseline)
    outputs,targets=builder.collect(workbook,gold.parent,Path(segmentation).parent,baseline.parent,seg_dir=segmentation)
    expected={'kind':'cell_value','tolerance':{'numeric_abs':1e-6,'numeric_rel':1e-6},'targets':targets,'groups':{o['band']:o['refs'] for o in outputs}}
    require(read(record(stage/'tests/answer_key.json'))==expected,'answer key targets/groups/tolerances changed')
    require(read(record(stage/'tests/outputs.json'))==outputs,'output descriptions differ from exact current curation')
    require((stage/'tests/test.sh').read_text()==builder.TEST_SH,'grader launcher changed')
    require((stage/'tests/run_grader.py').read_bytes()==(root/'grader/run_grader.py').read_bytes(),'grader runner changed')
    expected_files={k:v for k,v in tree(root/'grader/finance_grader').items() if '__pycache__' not in k.split('/') and not k.endswith('.pyc')}
    require(tree(stage/'tests/finance_grader')==expected_files,'grader package changed')
    return {'targets':len(targets),'groups':len(expected['groups']),'current_grader_content':True}


def generation_content(selection):
    from scripts.qualified_publication import validate_audit_bindings
    from scripts.batch.harbor_source_evidence import validate_source_lineage
    import xl_source_publication as source
    from xl_seg.publication import resolve_generation_by_id
    b=selection.bindings;wb=selection.workbook
    for key in ('original_raw_source','source_file','baseline_inputs','baseline_segmentation','curation'):checked_record(b[key])
    for key in ('source_generation','segmentation_generation'):
        binding=b[key];path=Path(binding['path']);require(path.name==binding['id'],'generation path identity changed')
        require(digest(path/'generation-manifest.json')==binding['manifest_sha256'],'generation manifest changed')
    sg=Path(b['source_generation']['path']);gg=Path(b['segmentation_generation']['path'])
    manifest=source.validate_source_generation(sg,expected_generation_id=sg.name)
    require(manifest['layout']['workbook_id']==wb,'foreign source workbook')
    actual=sg/manifest['layout']['source_workbook'];ast=sg/manifest['layout']['ast_directory']
    require(record(actual)==b['source_file'],'source file binding changed')
    validate_source_lineage(b['original_raw_source']['path'],sg,
        [read(r) for r in selection.normalization_records],
        trusted_public_key=selection.trusted_public_key)
    current=source.create_ast_provenance(actual,ast,builder_args=dict(production=True,workbook_id=wb,max_range_expand=100,include_constants=True,include_cached_values=True))
    project=lambda p:[{k:v[k] for k in ('name','sha256','size_bytes')} for v in p['builder']['code']]
    require(project(manifest['ast_provenance'])==project(current),'AST builder changed')
    resolve_generation_by_id(gg.parent.parent,gg.name,source_path=actual,ast_dir=ast,source_generation_dir=sg,require_pass=True,validate_live_evidence=True)
    require(digest(gg/'curation.toml')==b['curation']['sha256'],'curation changed')
    bindings=read(record(selection.stage/'tests/pipeline_bindings.json'))
    context=publication.resolve_build_context(wb,source_root=sg.parent.parent,source_generation_id=sg.name,segmentation_root=gg.parent.parent,segmentation_generation_id=gg.name)
    require(bindings==context['bindings'],'staged pipeline bindings differ from current exact pair')
    audit=validate_audit_bindings(b,expected_generations={'source':sg.name,'segmentation':gg.name})
    return {'source':sg.name,'segmentation':gg.name,'audit_files':len(audit)}


def normalized_content(selection):
    from mcp_env.build import validate_spec,profile_catalog
    from xl_variable_mcp import verify_against_golden
    import gen_normalizer
    import plain_eligibility
    run=selection.normalization_run;b=selection.bindings
    require(run.name==selection.workbook+'-variable-sources','normalization run identity mismatch')
    for key,name in [('normalized_final','normalized.json'),('normalization_draft','draft.json'),('normalization_report','normalization_report.json')]:
        checked_record(b[key]);require(Path(b[key]['path'])==run/name,'normalization binding location mismatch')
    spec=read(b['normalized_final']);mode=plain_eligibility.evaluate(run)
    require(mode['mode'] in {'plain','mcp'},'plain eligibility rejects normalized task')
    if mode['mode']=='mcp':validate_spec(spec);profile_catalog(spec)
    else:require(spec.get('variables')==[] and mode['eligible'] is True,'plain eligibility failed')
    require(not verify_against_golden(spec,Path(b['source_file']['path'])),'normalized values disagree with golden')
    draft,rows=gen_normalizer.analyse(selection.workbook,b['source_file']['path'],b['source_file']['sha256'],runs_root=run.parent,inputs_root=Path(b['baseline_inputs']['path']).parent)
    from scripts.batch.harbor_review_context import normalization_review
    if b.get('normalization_partitioned_review') is not None:
        require(selection.normalization_call is None,'partition normalization cannot fabricate one Call')
        from scripts.batch.harbor_normalization_validator_bridge import validate
        reviewed=validate(b['normalization_partitioned_review'],prepared=selection.normalization_prepared,
            source=b['source_file'],baseline=b['baseline_inputs'],draft=b['normalization_draft'],
            rows=rows,bindings=b,policy=selection.model_policy)
        require(read(record(run/'normalization-review.json'))==reviewed['review'],'normalizer uses different partition decisions')
        no_candidates=None
    elif selection.normalization_call is None:
        from scripts.batch.harbor_zero_candidate_normalization import validate_no_candidates
        no_candidates=validate_no_candidates(selection,rows=rows,draft=draft)
    else:
        no_candidates=None
        applicability=b.get('normalization_audit_applicability')
        original_locator=b.get('normalization_original_locator_code')
        require((applicability is None)==(original_locator is None),'normalization applicability/code must be selected together')
        if applicability is None:
            reviewed=normalization_review(selection.normalization_call,selection.normalization_prepared,
                source=b['source_file'],baseline=b['baseline_inputs'],draft=b['normalization_draft'],
                rows=rows,bindings=b,policy=selection.model_policy)
        else:
            from scripts.batch.harbor_normalization_audit_applicability import validate_applicability
            reviewed=validate_applicability(applicability,selection.normalization_prepared,selection.normalization_call,
                source=b['source_file'],baseline=b['baseline_inputs'],draft=b['normalization_draft'],rows=rows,
                bindings=b,policy=selection.model_policy,original_locator_code=original_locator)
        require(read(record(run/'normalization-review.json'))==reviewed['review'],'normalizer uses a different model review')
    from scripts.batch.harbor_normalization_equivalence import validate_emission
    validate_emission(selection.workbook,draft,rows,run,
                      read(record(selection.stage/'tests/pipeline_bindings.json')))
    return {'mode':mode['mode'],'reviewed_candidates':len(rows),'variables':len(spec['variables'])}



def mcp_transport_content(stage,build):
    from mcp_env.server_assets import SERVER_PY,SIDECAR_DOCKERFILE
    import xl_output_task as builder
    require((build/'server.py').read_text()==SERVER_PY,'MCP server code differs from current generator')
    require((build/'Dockerfile').read_text()==SIDECAR_DOCKERFILE,'MCP Dockerfile differs from current generator')
    expected={k:v for k,v in tree(build).items() if k in {'server.py','Dockerfile'} or k.startswith('runtime/')}
    require(tree(stage/'environment/mcp-server')==expected,'packaged MCP sidecar differs from validated build')
    require((stage/'environment/docker-compose.yaml').read_text()==builder.COMPOSE_YAML,'MCP compose changed')
    return {'current_transport_content':True}

def environment_content(selection,mode):
    import plain_eligibility
    import xl_mcp_oracle as oracle
    from maskability import spec_mask_and_audit
    from mcp_env.validate import validate
    s=selection;stage=s.stage;gold=Path(s.bindings['source_file']['path'])
    from scripts.batch.harbor_runtime_readiness import delivered_workbook
    delivered=delivered_workbook(stage,s.workbook)
    publication._reject_external_package_artifacts(stage)
    if mode=='plain':
        result=plain_eligibility.check_plain_environment(stage,s.workbook);require(result['valid'] is True,'plain hygiene failed');return result
    require(s.mcp_build_a is not None and s.mcp_build_b is not None,'two retained MCP builds required')
    require(s.mcp_build_a.resolve()!=s.mcp_build_b.resolve(),'MCP builds must be distinct artifacts')
    require(tree(s.mcp_build_a)==tree(s.mcp_build_b),'MCP builds differ')
    validate(s.mcp_build_a);validate(s.mcp_build_b)
    mcp_transport_content(stage,s.mcp_build_a)
    spec=read(s.bindings['normalized_final']);mask,audit=spec_mask_and_audit(spec)
    require(bool(mask) and bool(audit),'empty MCP mask')
    require(read(record(s.mcp_build_a/'mask_cells.json'))==mask,'MCP mask differs from normalized spec')
    actual=read(record(s.mcp_build_a/'masked_inputs.json'))
    projection=lambda row:{k:row[k] for k in ('variable_id','refs','cell_value','mcp_value','unit')}
    require([projection(r) for r in actual]==audit,'MCP audit map differs from normalized spec')
    require(read(record(stage/'tests/masked_inputs.json'))==actual,'packaged mask map changed')
    allow=None
    if s.allowlist:checked_record(s.allowlist);allow=Path(s.allowlist['path'])
    for workbook,blank in [(gold,False),(delivered,True)]:
        result=oracle.scan_value_leaks(workbook,mask,audit,allow,require_blank_mask=blank)
        require(result['valid'] is True,'golden/delivered mask or leak check failed')
    require(oracle.check_environment(stage,delivered)['valid'] is True,'MCP hygiene failed')
    return {'mode':'mcp','masked_cells':len(mask),'identical_retained_builds':True}


def disclosure_verification_stage(selection, *, root=ROOT):
    """Select exact pre-dialogue proof inputs without executing witness files.

    verify_task reads instruction.md, tests/disclosure.json, the selected input
    workbook, and tests/answer_key.json (also used by audit_text). Its external
    source/AST/generation references come solely from disclosure.json. No other
    witness code, metadata, cached bytecode, or retained verification result is
    loaded. Current task packaging/notes/grader remain separate mandatory gates.
    """
    s=selection;w=s.pre_dialogue_witness
    if w is None:return s.stage
    require(isinstance(w,PreDialogueWitness),'typed pre-dialogue witness required')
    require(w.stage.resolve()!=s.stage.resolve(),'witness must be a distinct retained stage')
    tree(w.stage)
    require(publication._tree_hash(w.stage)[0]==w.tree_sha256,'pre-dialogue witness tree changed')
    from scripts.batch.harbor_complete_readiness import check_naturalization,ReadinessSelection
    proof=check_naturalization(ReadinessSelection(s,None,None,None),root=root)
    require(digest(w.stage/'instruction.md')==proof['candidate_sha256'],
            'witness instruction is not the exact current validated NAT candidate')
    disclose=skill_module(root,'task-disclosure','disclose')
    from scripts.batch.harbor_runtime_readiness import delivered_workbook
    current=delivered_workbook(s.stage,s.workbook)
    delivered=delivered_workbook(w.stage,s.workbook)
    require(disclose.find_environment(s.stage)==current and disclose.find_environment(w.stage)==delivered,
            'disclosure workbook selection is ambiguous or differs from current task')
    require(delivered.name==current.name and digest(delivered)==digest(current),
            'witness delivered workbook differs from current task')
    for relative in ('tests/disclosure.json','tests/answer_key.json'):
        require(digest(w.stage/relative)==digest(s.stage/relative),
                'witness verifier input differs from current task: '+relative)
    payload=read(record(s.stage/'tests/disclosure.json'))
    source=s.bindings['source_file'];checked_record(source)
    require(record(Path(payload['golden']))==source and payload.get('golden_sha256')==source['sha256'],
            'disclosure source differs from current source binding')
    require(publication._tree_hash(w.stage)[0]==w.tree_sha256,'witness changed during selection')
    return w.stage


def disclosure_mechanics(selection, *, root=ROOT):
    s=selection;disclose=skill_module(root,'task-disclosure','disclose')
    stage=disclosure_verification_stage(s,root=root)
    require(disclose.verify_task(stage)['passed'] is True,'disclosure structure/leak gate failed')
    require(disclose.faithcheck_task(s.stage,golden=s.bindings['source_file']['path'])['passed'] is True,
            'disclosure independent workbook mechanics failed')
    if s.pre_dialogue_witness:
        require(publication._tree_hash(stage)[0]==s.pre_dialogue_witness.tree_sha256,
                'witness changed during disclosure verification')
    return {'valid':True,'verified_stage':str(stage),'current_stage_faithcheck':True}


def dialogue_packaging_content(selection, *, root=ROOT):
    from scripts.batch.harbor_runtime_readiness import delivered_workbook
    s=selection;stage=s.stage
    dialogue=skill_module(root,'additional-assumptions-dialogue','validate_dialogue')
    workbook=delivered_workbook(stage,s.workbook).name
    require(not dialogue.packaging_faults(stage,(stage/'environment/Dockerfile').read_text(),workbook),
            'dialogue packaging failed')
    return {'valid':True,'workbook':workbook}


def prose_content(selection,*,root=ROOT,source_warning_proof=None):
    s=selection;stage=s.stage
    disclosure_mechanics(s,root=root)
    validator=skill_module(root,'naturalize-finance-task-instruction','validate_instruction_rewrite')
    recovery=skill_module(root,'naturalize-finance-task-instruction','naturalize_recovery')
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
        restoration=skill_module(root,'naturalize-finance-task-instruction','span_restoration')
        receipt=restoration.validate(recovery,s.naturalization_root,state)
        require(s.naturalization_call.request['sha256']==receipt['proof']['original_evidence'][receipt['proof']['original_review_request']],'restored review request changed')
        require(s.naturalization_call.response['sha256']==receipt['proof']['original_evidence'][receipt['proof']['original_review_response']],'restored review response changed')
        reviewed_candidate=(attempt/'candidate.md').read_text()
    checked_review=naturalization_review(s.naturalization_call,s.naturalization_request,
        source=original.decode(),candidate=reviewed_candidate,policy=s.model_policy,
        writer_call=s.naturalization_writer_call,writer_request=s.naturalization_writer_request,
        writer_bodies={key:record(attempt/(key+'.md')) for key in ('preamble_body','input_body')},root=root)
    from scripts.batch.harbor_instruction_producer import editable_claims
    checks=checked_review['review']['clause_checks']
    require([c['source_claim'] for c in checks]==editable_claims(root,original.decode()),
            'independent NAT source-clause coverage incomplete')
    quoted=[c['candidate_claim'] for c in checks]
    require(all(q in reviewed_candidate for q in quoted),'NAT review quotes invented candidate evidence')
    require(all(any(line in q for q in quoted) for line in editable_claims(root,reviewed_candidate)),
            'independent NAT candidate-clause coverage incomplete')
    from scripts.batch.harbor_nat_coverage_retry import validate_exception
    validate_exception(s.naturalization_root,s.naturalization_call,
        writer_call=s.naturalization_writer_call,writer_request=s.naturalization_writer_request,
        writer_bodies={key:record(attempt/(key+'.md')) for key in ('preamble_body','input_body')},
        policy=s.model_policy,root=root)
    candidate=s.naturalization_root/state['final_candidate']
    require(digest(candidate)==state['final_instruction_sha256'],'naturalized candidate changed')
    validator.validate(original,candidate.read_bytes(),key)
    require(key==read(record(stage/'tests/answer_key.json')),'naturalizer answer key changed')
    recovery.verify_naturalizer_metadata(stage/'instruction.md',stage/'task.toml')
    if s.dialogue_noop is not None:
        from scripts.batch.harbor_dialogue_noop import validate_content
        validate_content(s,root=root)
        return {'disclosure_mechanics':True,'naturalization_spans_and_budget':True,'dialogue_claim_coverage':True}
    claims=read(s.dialogue_claims);review=read(s.dialogue_review)
    extraction=skill_module(root,'additional-assumptions-dialogue','extract_claims')
    dialogue=skill_module(root,'additional-assumptions-dialogue','validate_dialogue')
    options={}
    if s.scope_evidence:
        checked_record(s.scope_evidence);options=dict(source_workbook=s.bindings['source_file']['path'],scope_evidence=s.scope_evidence['path'])
    fresh=extraction.extract(stage,**options)
    require(fresh==claims,'dialogue claims differ from current disclosure')
    notes=(stage/'environment/additional-assumptions.md').read_text()
    raw_notes=notes
    if s.dialogue_reviewed_draft:
        checked_record(s.dialogue_reviewed_draft);raw_notes=Path(s.dialogue_reviewed_draft['path']).read_text()
        require(dialogue.strip_claim_comments(raw_notes)==notes,'shipped notes differ from exact reviewed draft')
    require(dialogue.check_draft(raw_notes,claims,stage,source_workbook=s.bindings['source_file'])['passed'] is True,'dialogue draft/coverage/leak failure')
    if source_warning_proof is not None:
        require(s.dialogue_reviewed_draft is not None
                and source_warning_proof['evidence'][0:3]==[s.dialogue_review,s.dialogue_claims,s.dialogue_reviewed_draft]
                and source_warning_proof['evidence'][6]==s.bindings['source_file']
                and source_warning_proof['task_dir']==str(stage.resolve()),
                'source warning proof differs from selected prose context')
    require(dialogue.review_passed(review,claims,draft_text=raw_notes,source_warning_proof=source_warning_proof),'dialogue semantic coverage failure')
    require(dialogue.rewrite_instruction(candidate.read_text(),stage,claims)==(stage/'instruction.md').read_text(),'dialogue instruction transformation changed')
    dialogue_packaging_content(s,root=root)
    return {'disclosure_mechanics':True,'naturalization_spans_and_budget':True,'dialogue_claim_coverage':True}


UNIMPLEMENTED=(
    'disclosure_and_dialogue_review_transport_and_current_context_applicability',
    'live_http_exact_retrieval_current_image_binding',
    'container_startup_current_image_binding',
    'grader_smoke_score_1_current_container_execution',
)



def context_bindings(selection):
    roots=[selection.normalization_run,selection.naturalization_root]
    if selection.pre_dialogue_witness:roots.append(selection.pre_dialogue_witness.stage)
    roots += [p for p in (selection.mcp_build_a,selection.mcp_build_b) if p is not None]
    require(set(selection.context_trees)=={str(p) for p in roots},'exact retained context tree bindings required')
    for path in roots:
        tree(path)
        require(publication._tree_hash(path)[0]==selection.context_trees[str(path)],'retained context tree changed')
    check_records(selection.normalization_records)
    checked_record(selection.dialogue_claims)
    if selection.dialogue_noop is not None:
        from scripts.batch.harbor_dialogue_noop import validate_content
        validate_content(selection,root=ROOT)
    else:checked_record(selection.dialogue_review)
    if selection.dialogue_reviewed_draft:checked_record(selection.dialogue_reviewed_draft)
    scope_records=[]
    if selection.dialogue_scope_receipt is not None:
        from scripts.batch.harbor_dialogue_scope_context import validate_selected
        scope_records=validate_selected(selection,root=ROOT)
    if selection.scope_evidence:checked_record(selection.scope_evidence)
    if selection.allowlist:checked_record(selection.allowlist)
    for binding in (selection.normalization_prepared,selection.naturalization_request,selection.naturalization_writer_request):checked_record(binding)
    partitioned=None
    if selection.bindings.get('normalization_partitioned_review') is not None:
        from scripts.batch.harbor_normalization_validator_bridge import validate_content
        partitioned=validate_content(selection)
    elif selection.normalization_call is None:
        from scripts.batch.harbor_zero_candidate_normalization import validate_no_candidates
        validate_no_candidates(selection)
    for role,call in (('normalization',selection.normalization_call),('naturalization',selection.naturalization_call),('writer',selection.naturalization_writer_call)):
        if call is None:
            require(role=='normalization','missing required model Call')
            continue
        for binding in (call.request,call.submitted,call.response,call.completed):checked_record(binding)
        if call.parsed:checked_record(call.parsed)
    result={'bound_context_trees':len(roots),'dialogue_scope_evidence':scope_records}
    if partitioned is not None:result['partitioned_evidence']=[dict(path=p,sha256=h) for p,h in sorted(partitioned['checked_files'].items())]
    return result

def inspect_readiness(selection,*,root=ROOT,source_warning_proof=None):
    """Return checks plus explicit blockers; never accepts agent PASS flags."""
    require(isinstance(selection,Selection),'root-selected typed selection required')
    require(__import__('re').fullmatch(r'\d{4}',selection.workbook),'invalid workbook identity')
    original_tree=tree(selection.stage)
    require(publication._tree_hash(selection.stage)[0]==selection.stage_sha256,'stage hash changed')
    results={};blockers=list(UNIMPLEMENTED)
    def checked(name,operation):
        try:
            result=operation();results[name]={'checked':True,'result':result};return result
        except (Exception,SystemExit) as exc:
            # Diagnostics intentionally do not copy arbitrary model/cell text.
            results[name]={'checked':False,'error_type':type(exc).__name__};blockers.append(name);return None
    context=checked('context_bindings',lambda:context_bindings(selection))
    checked('source_segmentation_audit',lambda:generation_content(selection))
    norm=checked('normalization_profile_primitives',lambda:normalized_content(selection))
    if norm:
        if norm['mode']=='plain':blockers.remove('live_http_exact_retrieval_current_image_binding')
        checked('environment_mask_static_mcp',lambda:environment_content(selection,norm['mode']))
    else:blockers.append('environment_mask_static_mcp')
    checked('prose_mechanics',lambda:prose_content(selection,root=root,source_warning_proof=source_warning_proof))
    b=selection.bindings
    checked('grader_content',lambda:grader_content(selection.stage,selection.workbook,b['source_file']['path'],b['segmentation_generation']['path'],b['baseline_inputs']['path'],root=root))
    require(tree(selection.stage)==original_tree,'stage changed during read-only validation')
    if context:checked('context_bindings_after',lambda:context_bindings(selection))
    return {'schema_version':'harbor-readiness-inspection/v1','workbook':selection.workbook,'stage_sha256':selection.stage_sha256,'publication_ready':False,'checks':results,'blockers':sorted(set(blockers)),'mutation_performed':False}

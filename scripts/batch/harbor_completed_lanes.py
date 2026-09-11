"""Fixed later-lane validators and exact serial invocation order; no mutation."""
from dataclasses import dataclass
from pathlib import Path
import copy
import json
from types import SimpleNamespace

from scripts.batch import harbor_tracker as schema
from scripts.batch import harbor_lane_notes as notes
from scripts.batch import harbor_readiness as static
from scripts.batch.harbor_admission import require,read,record
from scripts.batch.harbor_tracker_lifecycle import checked_record

STEPS=(('source','harbor-source-warden'),('segmentation','harbor-segmentation-analyst'),
       ('research','harbor-research-auditor'),('normalization_phase1','harbor-normalization-engineer'),
       ('source_profile','harbor-source-profiler'),('normalization_phase2','harbor-normalization-engineer'),
       ('environment','harbor-environment-packager'),('disclosure','harbor-disclosure-writer'),
       ('naturalization','harbor-instruction-naturalizer'),('verification','harbor-independent-verifier'),
       ('dialogue','harbor-dialogue-author'))

@dataclass(frozen=True)
class CompletionEvidence:
    kind: str
    selection: object

def predecessors(tracker,kind):
    require(kind in dict(STEPS),'unknown canonical invocation')
    index=[s for s,_ in STEPS].index(kind)
    for step,lane in STEPS[:index]:
        row=tracker['lane_state'][lane]
        if step=='normalization_phase1':
            require(row.get('phase') in {'phase1_complete','phase2_complete'},'normalization phase1 has not completed')
        else:
            require(row.get('state')=='passed','prior canonical lane has not passed: '+step)
    if kind=='normalization_phase2':
        require(tracker['lane_state']['harbor-normalization-engineer'].get('phase')=='phase1_complete','normalization phase2 requires frozen phase1')
    return STEPS[index][1]

def downstream(kind):
    index=[s for s,_ in STEPS].index(kind)
    return {lane for _,lane in STEPS[index+1:]}

class ContextRejection(ValueError):
    def __init__(self, lane, gate, failure_class, cause):
        super().__init__(str(cause))
        self.lane=lane;self.gate=gate;self.failure_class=failure_class


def context_check(lane,gate,failure_class,operation):
    try:return operation()
    except (Exception,SystemExit) as exc:
        raise ContextRejection(lane,gate,failure_class,exc) from exc


def pair_and_audit(before,selection,context):
    b=selection.bindings
    for key in ('original_raw_source','source_generation','source_file','segmentation_generation','curation'):
        require(b[key]==before['bindings'][key],'current immutable pair differs from tracker: '+key)
    source=notes.GateEvidence('source',record(Path(b['source_generation']['path'])/'generation-manifest.json'),selection.normalization_records)
    source_result=context_check(notes.SOURCE,'source','source_integrity',lambda:notes.check_gate(before,source,context))
    seg=notes.GateEvidence('segmentation',record(Path(b['segmentation_generation']['path'])/'generation-manifest.json'),selection.normalization_records)
    seg_result=context_check(notes.SEGMENTATION,'segmentation','preflight',lambda:notes.check_gate(before,seg,context))
    context_check(notes.SEGMENTATION,'segmentation','preflight',lambda:require(source_result['passed'] and seg_result['passed'],'current source/segmentation gate failed'))
    from scripts.qualified_publication import validate_audit_bindings
    audit=context_check('harbor-research-auditor','research','research_audit',lambda:validate_audit_bindings(b,expected_generations={k:b[k+'_generation']['id'] for k in ('source','segmentation')}))
    return source_result['evidence']+seg_result['evidence']+[{'path':str(p),'sha256':h} for p,h in audit.items()]

def normalization_attempt(s):
    """Project only the already-proven call's bound original semantic ordinal.

    Historical packets without fresh producer budget fields retain their prior
    tracker count; no zero or inferred invocation is invented for them.
    """
    if s.normalization_call is None:return None
    submitted=read(s.normalization_call.submitted);manifest=read(s.normalization_prepared)
    budget=manifest.get('budget');number=submitted.get('semantic_ordinal')
    if budget is None and number is None:return None
    require(isinstance(budget,dict),'normalization ordinal lacks prepared budget')
    require(type(number) is int and 1<=number<=2,'normalization ordinal missing or invalid')
    limit=submitted.get('semantic_limit')
    require(type(limit) is int and number<=limit<=2,'normalization limit invalid')
    require((number,limit,submitted['attempt_id'])==(budget.get('ordinal'),budget.get('limit'),budget.get('attempt_id')),'normalization prepared ordinal/identity changed')
    require(submitted.get('scope')==dict(workbook_id=s.workbook,original_sha256=s.bindings['original_raw_source']['sha256'],lane='harbor-normalization-engineer',budget_id='normalization-review'),'normalization budget scope differs')
    require(bool(budget.get('evidence')) and submitted.get('budget_evidence')==budget['evidence'],'normalization budget evidence differs')
    for binding in budget['evidence']:checked_record(binding)
    require(manifest.get('semantic_budget_reset') is False and submitted.get('semantic_budget_reset') is False,'normalization budget reset rejected')
    return number


def normalization_projection(s,*,phase1=False):
    """Rebuild atomic evidence and run only freshly generated code in memory."""
    import gen_normalizer as gen
    from scripts.batch.harbor_review_context import normalization_review
    from scripts.batch.harbor_normalization_equivalence import expected_outputs
    from xl_variable_mcp import verify_against_golden
    import plain_eligibility
    from mcp_env.build import validate_spec
    run=s.normalization_run;b=s.bindings
    require(run.name==s.workbook+'-variable-sources','foreign normalization run')
    for key,name in [('normalization_draft','draft.json'),('normalized_final','normalized.json'),('normalization_report','normalization_report.json')]:
        checked_record(b[key]);require(Path(b[key]['path'])==run/name,'normalization artifact location changed')
    draft,rows=gen.analyse(s.workbook,b['source_file']['path'],b['source_file']['sha256'],runs_root=run.parent,inputs_root=Path(b['baseline_inputs']['path']).parent)
    partitioned=b.get('normalization_partitioned_review')
    partitioned_result=None
    if partitioned is not None:
        require(s.normalization_call is None,'partition review cannot fabricate a single model Call')
        from scripts.batch.harbor_normalization_validator_bridge import validate
        partitioned_result=validate(partitioned,prepared=s.normalization_prepared,source=b['source_file'],
            baseline=b['baseline_inputs'],draft=b['normalization_draft'],rows=rows,bindings=b,policy=s.model_policy)
        require(read(record(run/'normalization-review.json'))==partitioned_result['review'],'aggregate review differs from exact partition decisions')
        no_candidates=None
    elif s.normalization_call is None:
        from scripts.batch.harbor_zero_candidate_normalization import validate_no_candidates
        no_candidates=validate_no_candidates(s,rows=rows,draft=draft)
    else:
        no_candidates=None
        applicability=b.get('normalization_audit_applicability')
        original_locator=b.get('normalization_original_locator_code')
        require((applicability is None)==(original_locator is None),'normalization applicability/code must be selected together')
        if applicability is None:
            reviewed=normalization_review(s.normalization_call,s.normalization_prepared,source=b['source_file'],baseline=b['baseline_inputs'],draft=b['normalization_draft'],rows=rows,bindings=b,policy=s.model_policy)
        else:
            from scripts.batch.harbor_normalization_audit_applicability import validate_applicability
            reviewed=validate_applicability(applicability,s.normalization_prepared,s.normalization_call,
                source=b['source_file'],baseline=b['baseline_inputs'],draft=b['normalization_draft'],rows=rows,
                bindings=b,policy=s.model_policy,original_locator_code=original_locator)
        require(read(record(run/'normalization-review.json'))==reviewed['review'],'normalizer review differs from independent response')
    pair=static.publication.resolve_build_context(s.workbook,source_root=Path(b['source_generation']['path']).parent.parent,source_generation_id=b['source_generation']['id'],segmentation_root=Path(b['segmentation_generation']['path']).parent.parent,segmentation_generation_id=b['segmentation_generation']['id'])
    script,projected=expected_outputs(s.workbook,draft,rows,run,pair['bindings'])
    require((run/f'normalize_{s.workbook}.py').read_text()==script,'normalizer differs from current generator')
    final=projected[run/'normalized.json']
    if phase1:
        # Only the fixed generator's optional catalog insertion is omitted.
        # Variables/source identities/dimensions stay byte-semantic equivalent.
        unprofiled=copy.deepcopy(final);unprofiled.pop('source_profiles',None)
        checked_record(b['normalized_phase1'])
        require(read(b['normalized_phase1'])==unprofiled,'frozen unprofiled phase1 differs from current atomic projection')
        spec=unprofiled
    else:
        for path,value in projected.items():require(read(record(path))==value,'normalization final emission differs')
        spec=final
    if spec['variables']:validate_spec(spec)
    require(not verify_against_golden(spec,Path(b['source_file']['path'])),'normalized values differ from source')
    mode=plain_eligibility.evaluate(run)
    require(mode['mode'] in {'plain','mcp'} and (mode['mode']!='plain' or mode['eligible'] is True),'plain eligibility failed')
    return {'mode':mode['mode'],'variables':len(spec['variables']),'reviewed_candidates':len(rows),
        'no_candidate_evidence':no_candidates['evidence'] if no_candidates else [],
        'partitioned_evidence':[dict(path=p,sha256=h) for p,h in sorted(partitioned_result['checked_files'].items())] if partitioned_result else [],
        'semantic_attempt':partitioned_result['semantic_attempt'] if partitioned_result else normalization_attempt(s)}

def profile_context(profile, content):
    from scripts.batch.harbor_profile_readiness import ProfileSelection
    require(isinstance(profile,ProfileSelection),'typed profile selection required')
    b=content.bindings;run=content.normalization_run
    require(profile.policy==content.model_policy,'profile model policy differs from current task')
    require(run.name==content.workbook+'-variable-sources','foreign profile normalization run')
    require(profile.phase1==b['normalized_phase1'] and profile.inventory==b['variable_source_inventory'],'profile context differs from current inputs')
    require(all(Path(item['path']).parent==run for item in (profile.phase1,profile.profiles,profile.mapping)),'profile artifacts belong to another normalization run')
    require(Path(profile.profiles['path']).name=='source_profiles.json','profile catalog is not generator input')
    phase1=read(profile.phase1)
    require(phase1.get('environment_id')=='fcp-'+content.workbook,'foreign profile workbook')
    for kind in ('source','segmentation'):
        pipeline=phase1.get('pipeline_bindings') or {}
        require(pipeline.get(kind+'_generation_id')==b[kind+'_generation']['id'] and pipeline.get(kind+'_manifest_sha256')==b[kind+'_generation']['manifest_sha256'],'profile source/segmentation context differs')


def check_completed_gate(before,packet,context,*,runtime_producer=None):
    from scripts.batch.harbor_complete_readiness import ReadinessSelection,evidence_records,check_disclosure,check_dialogue
    require(isinstance(packet,CompletionEvidence) and isinstance(packet.selection,ReadinessSelection),'root-selected typed gate context required')
    lane=predecessors(before,packet.kind)
    p=packet.selection;s=p.content;b=s.bindings
    require(s.workbook==before['workbook_id'],'foreign readiness workbook')
    evidence=pair_and_audit(before,s,context);bindings={};confidence='high';phase=None;disposition=None;attempt=None
    def bind(*keys):
        for key in keys:
            require(key in schema.BINDINGS and key in b,'missing canonical gate binding: '+key)
            bindings[key]=b[key]
    if packet.kind=='research':
        bind('baseline_inputs','baseline_segmentation','variable_source_audit','variable_source_inventory','variable_source_metadata')
        for key in ('audit_equivalence','audit_current_inventory','audit_validator_upgrade','audit_upgrade_original_code'):
            if b.get(key):bind(key)
    elif packet.kind in {'normalization_phase1','normalization_phase2'}:
        result=normalization_projection(s,phase1=packet.kind.endswith('phase1'));disposition=result['mode'];attempt=result['semantic_attempt']
        bind('normalization_draft','normalizer','normalization_prepared_manifest')
        if s.normalization_call is not None:bind('normalization_prepared_request')
        evidence.extend(result['no_candidate_evidence']);evidence.extend(result['partitioned_evidence'])
        for key in ('normalization_audit_applicability','normalization_original_locator_code','normalization_partitioned_review'):
            if b.get(key):bind(key)
        if packet.kind.endswith('phase1'):
            bind('normalized_phase1');phase='phase1_complete'
        else:
            from scripts.batch.harbor_profile_readiness import validate_profiles
            profile_context(p.public_profiles,s)
            require(p.public_profiles.phase1==before['bindings']['normalized_phase1'],'profile frozen phase1 changed')
            profile=validate_profiles(p.public_profiles);evidence.extend(profile['evidence'])
            bind('normalized_final','normalization_report','normalization_exclusions');phase='phase2_complete'
            from maskability import spec_mask_and_audit
            from xl_mcp_oracle import scan_value_leaks
            mask,audit=spec_mask_and_audit(read(b['normalized_final']))
            allow=b.get('oracle_allowlist');allow_path=None
            if allow:checked_record(allow);allow_path=Path(allow['path'])
            for workbook in (b['source_file'],b['baseline_inputs']):
                checked_record(workbook)
                require(scan_value_leaks(workbook['path'],mask,audit,allow_path,require_blank_mask=False)['valid'] is True,'normalization maskability/leak gate failed')
    elif packet.kind=='source_profile':
        from scripts.batch.harbor_profile_readiness import validate_profiles
        profile_context(p.public_profiles,s)
        require(p.public_profiles.phase1==before['bindings']['normalized_phase1'],'profile context differs from frozen phase1')
        result=validate_profiles(p.public_profiles);evidence.extend(result['evidence']);confidence=result['confidence']
        bindings.update(source_profiles=p.public_profiles.profiles,source_profile_mapping=p.public_profiles.mapping)
        disposition='not_applicable' if result['public_sources']==0 else 'accepted'
    elif packet.kind=='environment':
        mode=normalization_projection(s)['mode'];static.environment_content(s,mode)
        require(static.publication._tree_hash(s.stage)[0]==s.stage_sha256,'packaged stage changed')
        bindings['staged_bundle']={'path':str(s.stage),'sha256':s.stage_sha256}
    elif packet.kind=='disclosure':evidence.extend(check_disclosure(p,root=static.ROOT)['evidence'])
    elif packet.kind=='naturalization':
        from scripts.batch.harbor_complete_readiness import check_naturalization
        evidence.extend(check_naturalization(p,root=static.ROOT)['evidence'])
        recovery=static.skill_module(static.ROOT,'naturalize-finance-task-instruction','naturalize_recovery')
        attempt=recovery._attempts_used(recovery._read_state(s.naturalization_root))
    elif packet.kind=='verification':
        from scripts.batch.harbor_runtime_readiness import validate_runtime
        require(p.runtime.workbook==s.workbook and p.runtime.stage==s.stage,'runtime workbook/stage mismatch')
        require(p.runtime.stage_tree==static.tree(s.stage),'runtime stage changed')
        validate_runtime(p.runtime,p.observation,root=static.ROOT, **(
            {'producer_identity':runtime_producer} if runtime_producer is not None else {}))
    elif packet.kind=='dialogue':
        reviewed=check_dialogue(p,root=static.ROOT);evidence.extend(reviewed['evidence']);attempt=reviewed['round']
        if s.dialogue_noop is not None:
            require(not (before['lane_state'][lane].get('attempt') or 0), 'spent dialogue rounds cannot become a no-op')
            disposition='not_applicable'
    else:raise ValueError('unsupported completion kind')
    evidence.extend(evidence_records(p))
    return dict(lane=lane,gate=packet.kind,passed=True,bindings=bindings,evidence=notes.evidence_list(evidence),classification=None,terminal=False,confidence=confidence,phase=phase,disposition=disposition,attempt=attempt)

def pending_dependents(before,kind):
    for lane in downstream(kind)-{dict(STEPS)[kind]}:
        row=before['lane_state'][lane]
        if kind=='source_profile' and lane=='harbor-normalization-engineer':
            if row.get('state')=='running' and row.get('phase')=='phase1_complete':continue
        if row.get('state')!='pending':return False
    return True


FAILURE_CLASSES = {
    'research':'research_audit', 'normalization_phase1':'normalization',
    'source_profile':'source_profile', 'normalization_phase2':'normalization',
    'environment':'packaging', 'disclosure':'disclosure',
    'naturalization':'naturalization', 'verification':'oracle_readiness',
    'dialogue':'dialogue',
}


def evaluate_completed_gate(before, packet, context):
    """Capture a real owning rejection; callers cannot nominate a failure class.

    Expected and observed bindings are distinct in the diagnostic digest. A
    missing/changed file does not become source invalidity or a quality pass.
    Raw error prose is hashed, never placed in user-visible failure notes.
    """
    from dataclasses import fields,is_dataclass
    from scripts.batch.harbor_complete_readiness import ReadinessSelection
    from scripts.batch.harbor_transition_codec import encode
    require(isinstance(packet,CompletionEvidence) and isinstance(packet.selection,ReadinessSelection), 'typed owning gate required')
    require(packet.kind in FAILURE_CLASSES, 'unknown later gate')
    lane=predecessors(before,packet.kind)
    require(packet.selection.content.workbook==before['workbook_id'], 'foreign owning gate')
    try:
        return check_completed_gate(before,packet,context)
    except (Exception,SystemExit) as exc:
        evidence=[];missing=[];expected=[]
        def observe(value):
            if is_dataclass(value):
                for field in fields(value):observe(getattr(value,field.name))
            elif isinstance(value,dict):
                if set(value)=={'path','sha256'}:
                    expected.append(value)
                    path=schema.safe_path(value['path'])
                    if path.is_file():evidence.append(record(path))
                    else:missing.append(str(path))
                else:
                    for child in value.values():observe(child)
            elif isinstance(value,(list,tuple)):
                for child in value:observe(child)
        observe(packet.selection)
        s=packet.selection.content
        # State, delivered prose and generated artifacts are owning evidence,
        # even when they are not separately root-bound Call records.
        for directory in (s.stage,s.naturalization_root,s.normalization_run):
            if directory.is_dir():
                for path in sorted(directory.rglob('*')):
                    schema.safe_path(path)
                    if path.is_file():evidence.append(record(path))
        for path in sorted((static.ROOT/'scripts/batch').glob('harbor_*.py')):evidence.append(record(path))
        evidence.append(record(Path(__file__)))
        require(evidence,'owning rejection has no inspectable evidence')
        diagnosis={'kind':packet.kind,'exception_type':type(exc).__name__,
                   'exception_sha256':schema.sha(str(exc).encode()),
                   'selection_sha256':schema.sha(json.dumps(encode(packet),sort_keys=True,separators=(',',':')).encode()),
                   'expected':expected,'missing':sorted(missing),'observed':notes.evidence_list(evidence)}
        result=dict(lane=lane,gate=packet.kind,passed=False,bindings={},
            evidence=notes.evidence_list(evidence),classification='owning_validator_rejected',
            failure_class=FAILURE_CLASSES[packet.kind],terminal=False,
            diagnostic_sha256=schema.sha(json.dumps(diagnosis,sort_keys=True,separators=(',',':')).encode()))
        if isinstance(exc,ContextRejection):
            result.update(lane=exc.lane,gate=exc.gate,failure_class=exc.failure_class)
        if packet.kind=='naturalization' and not isinstance(exc,ContextRejection):
            # Only the owning durable protocol can establish exhaustion. Missing
            # files or an applied-stage mismatch remain repairable failures.
            try:
                recovery=static.skill_module(static.ROOT,'naturalize-finance-task-instruction','naturalize_recovery')
                state=recovery._read_state(s.naturalization_root)
                recovery._verified_budget(s.naturalization_root,state)
                recovery._verify_attempt_evidence(s.naturalization_root,state)
                if state['status']=='exhausted' and recovery._attempts_used(state)==2:
                    result.update(terminal=True,fixability='exhausted',classification='writer_budget_exhausted',attempt=2)
            except (Exception,SystemExit):
                pass  # No claim of exhaustion without the owning budget proof.
        return result

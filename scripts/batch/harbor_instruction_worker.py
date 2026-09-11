"""Fixed root-owned environment-to-enqueue worker; never publishes artifacts.

One step at a time, one workbook lock, fixed owning validators and producers.
Unknown or interrupted side effects require explicit root reconciliation; they
never create a semantic verdict, replay a request, or grant another attempt.
"""
from dataclasses import dataclass, replace, asdict
from pathlib import Path
import fcntl
import json
import os
import shutil
import sys

from scripts.batch import harbor_instruction_producer as producer
from scripts.batch import harbor_role_producer as roles
from scripts.batch import harbor_model_executor as executor
from scripts.batch import harbor_readiness as static
from scripts.batch import harbor_runtime_readiness as runtime
from scripts.batch import harbor_transition_codec as codec
from scripts.batch.harbor_admission import read, record, require
from scripts.batch.harbor_tracker_lifecycle import checked_record
from scripts.batch.harbor_lane_notes import RootContext
from scripts.batch.harbor_lane_lifecycle import HarborLaneLifecycle
from scripts.batch.harbor_completed_lanes import CompletionEvidence, check_completed_gate
from scripts.batch.harbor_complete_readiness import ReadinessSelection, SemanticSelection, complete_readiness
from scripts.batch.harbor_workbook_bootstrap import Lease
from scripts.batch.hardened_runner import _run_in_process_group

ROOT = static.ROOT
STEPS = ('disclosure-build', 'disclosure-review', 'disclosure-commit',
         'nat-init', 'nat-writer', 'nat-review', 'nat-commit',
         'verification-runtime', 'verification-commit', 'dialogue-init',
         'dialogue-writer', 'dialogue-paraphraser', 'dialogue-review',
         'final-runtime', 'dialogue-commit', 'enqueue')
MODEL_STEPS = {'disclosure-review', 'nat-writer', 'nat-review',
               'dialogue-writer', 'dialogue-paraphraser', 'dialogue-review'}


@dataclass(frozen=True)
class Worker:
    context: RootContext
    run: Path
    campaign: executor.Campaign
    root: Path = ROOT
    retry: dict | None = None
    dialogue_retry: dict | None = None
    boundary_recovery: dict | None = None
    dialogue_semantic_retry: dict | None = None
    pretransport_recovery: dict | None = None
    code_repair: dict | None = None
    warning_requalification: dict | None = None
    disclosure_repair: dict | None = None
    lease_renewal: dict | None = None
    disclosure_revision: dict | None = None
    dialogue_pre_review_repair: dict | None = None


@dataclass(frozen=True)
class Result:
    status: str
    selection: ReadinessSelection
    lease: Lease
    next_step: str | None
    evidence: dict
    publication_performed: bool = False


def keep(path, value):
    return producer.put(path, value)


def snapshot(path, binding):
    return keep(path, Path(checked_record(binding)['path']).read_bytes())


def code(worker):
    paths = [Path(__file__), Path(roles.__file__), Path(producer.__file__)]
    paths += sorted((worker.root/'scripts/batch').glob('harbor_*.py'))
    paths += [worker.root/'scripts/batch/hardened_runner.py', worker.root/'scripts/batch/process_guardian.py']
    from scripts import harbor_pretransport_recovery,harbor_instruction_code_applicability
    paths += [Path(harbor_pretransport_recovery.__file__),Path(harbor_instruction_code_applicability.__file__)]
    return {str(p.resolve()): record(p)['sha256'] for p in paths}


def active(worker, selection, lease):
    require(isinstance(worker, Worker) and isinstance(selection, ReadinessSelection) and isinstance(lease, Lease), 'typed root-selected worker/readiness/lease required')
    require(worker.root.resolve() == ROOT.resolve(), 'worker uses installed code only')
    require(worker.context.trusted_public_key == selection.content.trusted_public_key, 'root source trust key differs')
    __import__('scripts.batch.harbor_tracker',fromlist=['safe_path']).safe_path(worker.run)
    facade = HarborLaneLifecycle(worker.context)
    wb = selection.content.workbook
    facade.require_synchronized(wb)
    path = worker.context.tracker_root/(wb+'.json')
    raw = path.read_bytes()
    before = json.loads(raw)
    facade._active(before, raw, lease.revision, lease.sha256, lease.owner, lease.token, 'workbook:'+wb)
    require(before['execution_state'] not in {'STOPPED', 'PROMOTED', 'publication_queued'}, 'terminal work requires root lifecycle handling')
    require(before['promotion_failure'] is None, 'existing failure requires root-owned snapshot/repair before continuing')
    require(before['lane_state']['harbor-environment-packager']['state'] == 'passed', 'environment lane must pass before instructions')
    for key in ('original_raw_source', 'source_file', 'source_generation', 'segmentation_generation', 'curation'):
        require(before['bindings'][key] == selection.content.bindings[key], 'root-selected current task identity differs: '+key)
    require(static.publication._tree_hash(selection.content.stage)[0] == selection.content.stage_sha256, 'selected stage changed')
    require(not worker.run.resolve().is_relative_to(selection.content.stage.resolve()), 'worker evidence cannot enter delivered stage')
    return facade, before, record(path)


def refreshed(selection):
    s = selection.content
    roots = [s.normalization_run, s.naturalization_root]
    if s.pre_dialogue_witness:roots.append(s.pre_dialogue_witness.stage)
    roots += [p for p in (s.mcp_build_a, s.mcp_build_b) if p is not None]
    context = {str(p): static.publication._tree_hash(p)[0] for p in roots if p.is_dir()}
    return replace(selection, content=replace(s, stage_sha256=static.publication._tree_hash(s.stage)[0], context_trees=context))


def step_directory(worker, phase):
    if worker.warning_requalification is not None:
        from scripts.batch.harbor_dialogue_warning_requalification import PHASES
        if phase in PHASES:
            return Path(worker.warning_requalification['path']).parent/'steps'/phase
    if worker.code_repair is not None:
        repair=read(worker.code_repair)
        if phase==repair['phase']:
            return Path(worker.code_repair['path']).parent/'steps'/phase
    if worker.pretransport_recovery is not None and phase=='dialogue-writer':
        complete=read(worker.pretransport_recovery);permit=read(complete['permit'])
        require(permit['worker_run']==str(worker.run),'foreign pretransport step')
        return Path(complete['permit']['path']).parent/'steps/dialogue-writer'
    disclosure_base=worker.disclosure_revision or worker.disclosure_repair
    base = (Path(disclosure_base['path']).parent if disclosure_base is not None and phase in STEPS[1:] else worker.run) / "steps" / phase
    if worker.dialogue_semantic_retry is not None and phase in ('dialogue-writer','dialogue-paraphraser','dialogue-review'):
        if worker.dialogue_pre_review_repair is not None:
            base=Path(worker.dialogue_pre_review_repair['path']).parent/'steps'/phase
        return base/'semantic-round-02'
    if worker.dialogue_retry is not None:
        permit=read(read(worker.dialogue_retry)['permit'])
        if phase==permit['phase']:return base/'structural-correction'
    if worker.retry is not None:
        permit=read(read(worker.retry)['permit'])
        if phase=='nat-writer' or (phase=='nat-review' and permit.get('rejection_phase')=='nat-review'):
            return base/'round-02'
    if worker.dialogue_pre_review_repair is not None and phase in STEPS[12:]:
        return Path(worker.dialogue_pre_review_repair['path']).parent/'steps'/phase
    return base


def task(worker, selection, tracker):
    return producer.Task(tracker, selection.content.stage, static.tree(selection.content.stage),
                         worker.run/'producer', selection.content.model_policy, worker.root,worker.code_repair,
                         selection.content.dialogue_scope_receipt if selection.content.dialogue_scope_receipt is not None
                         and read(selection.content.dialogue_scope_receipt).get('schema_version')=='dialogue-discovery-code-equivalence/v1' else None,**({'disclosure_revision':worker.disclosure_revision} if worker.disclosure_revision is not None else {}),
                         **({'dialogue_pre_review_repair':worker.dialogue_pre_review_repair} if worker.dialogue_pre_review_repair is not None else {}),
                         **({'dialogue_semantic_retry':worker.dialogue_semantic_retry} if worker.dialogue_semantic_retry is not None else {}))


def request_saved(path, request=None):
    if request is not None:
        keep(path, dict(prepared=asdict(request.prepared), context=request.context))
        return request
    value = read(record(path)); p = value['prepared']
    p['policy'] = producer.Policy(**p['policy'])
    p['scope'] = executor.SemanticScope(**p['scope'])
    p['budget_evidence'] = tuple(p['budget_evidence'])
    return producer.Request(executor.PreparedCall(**p), value['context'])


def _model(worker, t, request, dispatch, credential):
    producer.validate_request(t, request)
    location = executor.location(request.prepared, worker.campaign)
    observed = executor.inspect(request.prepared, worker.campaign) if location.exists() else None
    if observed is None or observed.status == 'incomplete':
        # A prepared-only directory may be inspected, but submitted/partial work
        # is never retried by this coordinator. The executor owns HTTP retries.
        if location.exists() and any(location.iterdir()):
            return 'MODEL_RECONCILIATION_REQUIRED'
        if not dispatch:
            return 'MODEL_REQUIRED'
        require(isinstance(credential, str) and credential, 'explicit root credential required for model dispatch')
        observed = producer.dispatch(t, request, worker.campaign, credential=credential)
    return None if observed.status == 'completed' else 'MODEL_RECONCILIATION_REQUIRED'


def _guarded(worker, t, directory, name, argv, lock_fd):
    """Fresh fixed disclose command under the installed parent-death guardian."""
    require(sys.version_info[:2] == (3, 12), 'persistent Python3.12 required')
    require(argv[:3] == [sys.executable, '-B', str(worker.root/'.cursor/skills/task-disclosure/scripts/disclose.py')], 'only fixed disclosure CLI can execute')
    entry = record(argv[2]); out = directory/name
    out.mkdir(parents=True, exist_ok=True)
    intent = out/'intent.json'
    if intent.exists():
        result_path = out/'complete.json'
        require(result_path.is_file(), 'interrupted disclosure command requires root reconciliation')
        old = read(record(intent)); result = read(record(result_path))
        require(old['argv'] == argv and old['entry'] == entry, 'command/code changed')
        require(result['returncode'] == 0, 'recorded disclosure command failed')
        checked_record(result['log'])
        return
    keep(intent, dict(argv=argv, entry=entry, before=static.tree(t.stage)))
    metrics = {}
    with (out/'command.log').open('xb') as log:
        rc = _run_in_process_group(argv, stdout=log, timeout_seconds=900, grace_seconds=3,
            memory_limit_bytes=8*1024**3, metrics=metrics, cwd=worker.root,
            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}, guardian_lock_fds=(lock_fd,))
        log.flush(); os.fsync(log.fileno())
    keep(out/'complete.json', dict(returncode=rc, metrics=metrics, log=record(out/'command.log'), after=static.tree(t.stage)))
    require(rc == 0, 'disclosure command failed; retained logs require owning diagnosis')


def role_inventory(t, plan):
    # Prove zero cases too: absence of a model call never proves ambiguity absent.
    renderer = producer.skill_module(t.root, 'task-disclosure', 'disclose')
    directory = t.run/'disclosure-build'/t.stage.name
    bands = read(record(directory/'bands.json'))
    before = read(t.tracker)
    source = record(bands['golden'])
    require(source == {k:before['bindings']['source_file'][k] for k in ('path','sha256')}, 'role source differs from current selected workbook')
    derived = renderer.collect_ambiguous_role_cases(renderer.prepare_method_items(renderer.Book(Path(source['path'])),bands.get('bands',[])))
    require(plan['cases_path']==str(directory/'ambiguous_roles.json'), 'role inventory locator differs')
    value = read(record(plan['cases_path']))
    expected = dict(schema_version='1.0',task=t.stage.name,cases=derived,case_count=len(derived),band_count=sum(c['band_count'] for c in derived))
    require(value == expected, 'role case inventory differs from independent current reconstruction')
    return value


def _commit(worker, selection, lease, kind):
    facade, before, _ = active(worker, selection, lease)
    packet = CompletionEvidence(kind, selection)
    # The facade independently evaluates the real fixed owning gate again.
    from scripts.batch.harbor_lane_notes import check_gate
    result = check_gate(before, packet, worker.context)
    fn = facade.complete if result['passed'] else facade.record_failure
    changed = fn(selection.content.workbook, packet, expected_revision=lease.revision,
                 expected_sha256=lease.sha256, owner=lease.owner, token=lease.token)
    new = Lease(lease.owner, lease.token, changed['revision'], changed['sha256'])
    return new, None if result['passed'] else 'OWNING_GATE_FAILED'


def _runtime(worker, selection, directory):
    s = selection.content
    mode = __import__('plain_eligibility').evaluate(s.normalization_run)['mode']
    mcp = s.mcp_build_a if mode == 'mcp' else None
    selected = runtime.RuntimeSelection(s.workbook, s.stage, static.tree(s.stage), mode,
        runtime.code_bindings(worker.root), mcp, static.tree(mcp) if mcp else None, s.allowlist)
    require(not directory.exists(), 'runtime already attempted; exact interrupted evidence requires root reconciliation')
    observed = runtime.run_runtime(selected, directory, root=worker.root, timeout_seconds=900)
    observation = runtime.RuntimeObservation(record(directory/'execution.json'), observed['run_id'])
    runtime.validate_runtime(selected, observation, root=worker.root)
    return replace(selection, runtime=selected, observation=observation)


def _operation(worker, selection, lease, phase, directory, *, models, containers, commands, credential, lock_fd):
    if worker.warning_requalification is not None and phase=='dialogue-review':
        from scripts.batch.harbor_dialogue_warning_requalification import apply_review
        return apply_review(worker,selection,lease,directory)
    facade, before, tracker = active(worker, selection, lease)
    t = task(worker, selection, tracker)
    s = selection.content
    golden = {k:s.bindings['source_file'][k] for k in ('path','sha256')}
    if phase == 'disclosure-build':
        plan = producer.disclosure_command_plan(t)
        keep(directory/'command-plan.json', plan)
        if not commands:
            return selection, lease, 'COMMANDS_REQUIRED'
        for i, argv in enumerate(plan['before_role_resolution']):
            _guarded(worker, t, directory, 'before-%02d'%i, argv, lock_fd)
        cases = role_inventory(t,plan)
        require(type(cases.get('case_count')) is int and cases['case_count'] == len(cases['cases']), 'invalid arbitration case count')
        if cases['case_count']:
            for ordinal in (1,2):
                path = directory/('role-request-%02d.json'%ordinal)
                previous = request_saved(directory/'role-request-01.json') if ordinal == 2 else None
                req = request_saved(path) if path.exists() else request_saved(path, roles.prepare(t, worker.campaign, previous=previous))
                pending = _model(worker,t,req,models,credential)
                if pending:return selection,lease,pending
                resolved = roles.consume(t,req,worker.campaign,apply=False)
                if resolved['passed']:
                    roles.consume(t,req,worker.campaign,apply=True)
                    argv = plan['before_role_resolution'][0][:5]+['roles-validate','--task-dir',str(t.stage)]
                    _guarded(worker,t,directory,'roles-validate',argv,lock_fd)
                    break
            else:return selection,lease,'ROLE_FORMAT_BUDGET_EXHAUSTED'
        for i,argv in enumerate(plan['after_zero_or_validated_roles']):
            _guarded(worker,t,directory,'after-%02d'%i,argv,lock_fd)
        return refreshed(selection),lease,None
    if phase == 'nat-init':
        require(not before['lane_state'][producer.NAT].get('attempt'), 'old NAT budget requires explicit root-selected inherited recovery adapter')
        producer.initialize_naturalization(t,root=worker.root)
        return replace(selection,content=replace(s,naturalization_root=t.run/'naturalization')),lease,None
    if phase == 'dialogue-init':
        witness=directory/'pre-dialogue'/s.stage.name
        require(not witness.exists(), 'pre-dialogue witness already attempted; inspect retained evidence')
        witness.parent.mkdir(parents=True,exist_ok=True)
        shutil.copytree(s.stage,witness)
        require(static.tree(witness)==static.tree(s.stage), 'pre-dialogue snapshot changed bytes')
        s=replace(s,pre_dialogue_witness=static.PreDialogueWitness(witness,static.publication._tree_hash(witness)[0]))
        static.disclosure_verification_stage(s,root=worker.root)
        result = producer.initialize_dialogue(t,root=worker.root)
        s = replace(s,dialogue_claims=result['claims'],scope_evidence=result.get('scope_evidence'),dialogue_scope_receipt=result.get('scope_receipt'))
        if result['empty']:
            previous = snapshot(directory/'before-tracker.json',tracker)
            proof = keep(directory/'no-op.json',dict(schema_version='harbor-empty-dialogue/v1',before_tracker=previous,
                stage=str(s.stage),stage_tree=static.tree(s.stage),claims=result['claims']))
            s = replace(s,dialogue_noop=proof,dialogue_review=None,dialogue_reviewed_draft=None)
        return refreshed(replace(selection,content=s)),lease,None
    if phase in MODEL_STEPS:
        if phase.startswith('dialogue-') and s.dialogue_noop is not None:
            from scripts.batch.harbor_dialogue_noop import validate
            validate(selection,root=worker.root)
            return selection,lease,None
        request_path = directory/'request.json'
        if request_path.exists():req=request_saved(request_path)
        elif phase=='disclosure-review':
            req,manifest=producer.prepare_disclosure_review(t,golden,root=worker.root)
            keep(directory/'manifest.json',manifest)
            # The reviewer sees exactly the staged instruction; retain its bytes
            # before NAT changes that path, then use this snapshot downstream.
            snapshot(directory/'instruction.snapshot.md',record(s.stage/'instruction.md'))
            request_saved(request_path,req)
        elif worker.dialogue_semantic_retry is None and worker.dialogue_retry is not None and phase==read(read(worker.dialogue_retry)['permit'])['phase']:
            prior=read(read(worker.dialogue_retry)['permit'])['prior_request']
            req=request_saved(request_path,producer.prepare_dialogue_structural_correction(t,request_saved(Path(prior['path'])),worker.campaign,root=worker.root))
        else:
            prepare={'nat-writer':producer.prepare_naturalization_writer,'nat-review':producer.prepare_naturalization_review,
                'dialogue-writer':producer.prepare_dialogue_writer,'dialogue-paraphraser':producer.prepare_dialogue_paraphraser,'dialogue-review':producer.prepare_dialogue_review}[phase]
            req=request_saved(request_path,prepare(t,**({'round':2} if worker.dialogue_semantic_retry is not None and phase.startswith('dialogue-') else {}),root=worker.root))
        pending=_model(worker,t,req,models,credential)
        if pending:return selection,lease,pending
        if phase=='disclosure-review':
            manifest=read(record(directory/'manifest.json'))
            result=producer.consume_disclosure_review(t,req,manifest,golden,worker.campaign,root=worker.root)
            semantic=SemanticSelection(result['call'],manifest,record(directory/'instruction.snapshot.md'),None,None,(),**({'disclosure_revision':worker.disclosure_revision} if worker.disclosure_revision is not None else {}))
            selection=replace(selection,semantic=semantic)
        elif phase=='nat-writer':
            result=producer.consume_naturalization_writer(t,req,worker.campaign,root=worker.root)
            s=replace(s,naturalization_writer_call=result['call'],naturalization_writer_request=req.prepared.request)
            selection=replace(selection,content=s)
            recovery = producer.skill_module(worker.root, 'naturalize-finance-task-instruction', 'naturalize_recovery')
            used = recovery._attempts_used(recovery._read_state(s.naturalization_root))
            if used != req.prepared.ordinal:
                return refreshed(selection),lease,'NAT_BUDGET_RECONCILIATION_REQUIRED'
            if not result['validation']['valid']:return refreshed(selection),lease,'NAT_REPAIR_REQUIRED'
        elif phase=='nat-review':
            writer=request_saved(step_directory(worker, 'nat-writer')/'request.json')
            from scripts.batch.harbor_nat_coverage_retry import run as coverage_review
            result,pending=coverage_review(worker,t,req,writer,directory,models=models,credential=credential)
            if pending:return selection,lease,pending
            s=replace(s,naturalization_call=result['call'],naturalization_request=result['request'].prepared.request)
            selection=replace(selection,content=s)
            if not result['passed']:return refreshed(selection),lease,'NAT_REPAIR_REQUIRED'
        elif phase=='dialogue-writer':
            result=producer.consume_dialogue_writer(t,req,worker.campaign,root=worker.root)
            if not result['passed']:return refreshed(selection),lease,'DIALOGUE_STRUCTURAL_REPAIR_REQUIRED'
        elif phase=='dialogue-paraphraser':
            writer=request_saved(step_directory(worker,'dialogue-writer')/'request.json')
            result=producer.consume_dialogue_paraphraser(t,req,writer,worker.campaign,root=worker.root)
            if not result['passed']:return refreshed(selection),lease,'DIALOGUE_STRUCTURAL_REPAIR_REQUIRED'
            if result['draft_report']['passed'] is not True:return selection,lease,'DIALOGUE_REPAIR_REQUIRED'
        else:
            writer=request_saved(step_directory(worker,'dialogue-writer')/'request.json')
            paraphraser=request_saved(step_directory(worker,'dialogue-paraphraser')/'request.json')
            result=producer.consume_dialogue_review(t,req,writer,paraphraser,worker.campaign,apply=False,root=worker.root)
            if not result['passed']:return selection,lease,'DIALOGUE_REPAIR_REQUIRED'
            producer.consume_dialogue_review(t,req,writer,paraphraser,worker.campaign,apply=True,root=worker.root)
            selected=replace(s,dialogue_review=result['review'],dialogue_reviewed_draft=result['draft'])
            semantic=replace(selection.semantic,dialogue_call=producer.observed(req,worker.campaign),dialogue_reviewed_claims=s.dialogue_claims,
                dialogue_writer_calls=(producer.observed(writer,worker.campaign),producer.observed(paraphraser,worker.campaign)),
                dialogue_pre_review_repair=worker.dialogue_pre_review_repair if worker.dialogue_semantic_retry is None else None)
            selection=replace(selection,content=selected,semantic=semantic)
        return refreshed(selection),lease,None
    if phase in ('verification-runtime','final-runtime'):
        if not containers:return selection,lease,'RUNTIME_REQUIRED'
        return _runtime(worker,selection,directory/'runtime'),lease,None
    if phase.endswith('-commit'):
        kind={'disclosure-commit':'disclosure','nat-commit':'naturalization','verification-commit':'verification','dialogue-commit':'dialogue'}[phase]
        new,status=_commit(worker,selection,lease,kind)
        return selection,new,status
    require(phase=='enqueue','unknown worker operation')
    proof=complete_readiness(selection,root=worker.root)
    keep(directory/'readiness.json',proof)
    require(proof['publication_ready'] is True and not proof['blockers'],'full current readiness did not qualify')
    result=facade.transition(s.workbook,'enqueue',selection,expected_revision=lease.revision,
        expected_sha256=lease.sha256,owner=lease.owner,token=lease.token)
    return selection,Lease(lease.owner,lease.token,result['revision'],result['sha256']),None


def step(worker, selection, lease, *, phase=None, checkpoint=None, models=False, containers=False,
         commands=False, credential=None):
    """Root selects the exact checkpoint; no discovery of a latest task or lease.

    Only the three explicit execution booleans permit side effects of that kind.
    A returned failure/reconciliation status stops the fixed pipeline. The root
    repair facade must independently authorize any further semantic round.
    """
    require(all(type(v) is bool for v in (models,containers,commands)), 'execution capabilities must be explicit booleans')
    active(worker,selection,lease)
    worker.run.mkdir(parents=True,exist_ok=True)
    with (worker.run/'worker.lock').open('a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return _step(worker,selection,lease,phase=phase,checkpoint=checkpoint,models=models,containers=containers,
                     commands=commands,credential=credential,lock_fd=lock.fileno())


def _step(worker, selection, lease, *, phase, checkpoint, **options):
    facade,before,tracker=active(worker,selection,lease)
    require(worker.pretransport_recovery is None or worker.dialogue_semantic_retry is not None,'pretransport recovery requires original semantic permit')
    initial=worker.run/'initial.json'
    if not initial.exists():
        require(phase in (None,STEPS[0]),'fresh worker must begin with disclosure')
        check_completed_gate(before,CompletionEvidence('environment',selection),worker.context)
        keep(initial,dict(selection=codec.encode(selection),lease=asdict(lease),tracker=snapshot(worker.run/'initial-tracker.json',tracker),code=code(worker)))
    saved=read(record(initial))
    if worker.dialogue_pre_review_repair is not None:
        if worker.dialogue_semantic_retry is not None:
            from scripts.batch.harbor_dialogue_semantic_retry import historical_pre_review
            historical_pre_review(worker,before)
        else:
            from scripts.batch.harbor_dialogue_pre_review_repair import validate as validate_pre_review
            validate_pre_review(worker,before)
    if worker.disclosure_revision is not None:
        if worker.dialogue_pre_review_repair is not None:
            from scripts.batch.harbor_dialogue_pre_review_repair import historical_manifest
            historical_manifest(worker,before)
        else:
            from scripts.batch.harbor_disclosure_revision import validate as validate_revision
            validate_revision(worker,before)
    renewal = None
    if worker.lease_renewal is not None:
        from scripts.batch.harbor_instruction_lease_renewal import validate
        renewal = validate(worker,before)
    retry = None
    structural = None
    boundary = None
    semantic = None
    require(not (worker.dialogue_retry is not None and worker.boundary_recovery is not None),'independent continuation kinds cannot be combined')
    if worker.code_repair is not None:
        from scripts.batch import harbor_instruction_repair as repair
        if worker.dialogue_semantic_retry is not None or worker.warning_requalification is not None:
            repair.validate_historical_integrity(worker, before)
        else:
            repair.validate(worker,before)
    if worker.dialogue_semantic_retry is not None:
        if worker.warning_requalification is not None:
            from scripts.batch.harbor_dialogue_semantic_retry import validate_completed_integrity as validate
        elif worker.pretransport_recovery is not None:
            from scripts.harbor_pretransport_recovery import validate
        else:
            from scripts.batch.harbor_dialogue_semantic_retry import validate
        semantic=validate(worker,before)
        require(semantic['initial']==record(initial),'semantic continuation changed initial history')
        if worker.boundary_recovery is not None:boundary=read(read(worker.boundary_recovery)['permit'])
        if worker.dialogue_retry is not None:structural=read(read(worker.dialogue_retry)['permit'])
        if worker.retry is not None:retry=read(read(worker.retry)['permit'])
    elif worker.boundary_recovery is not None:
        from scripts.batch.harbor_nat_boundary_resume import validate
        boundary=validate(worker,before)
        require(boundary['initial']==record(initial),'boundary recovery changed initial history')
    elif worker.dialogue_retry is not None:
        from scripts.batch.harbor_dialogue_retry import validate
        structural=validate(worker,before)
        require(structural['initial']==record(initial),'structural refresh changed initial history')
    elif worker.retry is not None:
        from scripts.batch.harbor_nat_retry import validate
        retry = validate(worker, before)
        require(retry['initial'] == record(initial), 'retry initial evidence changed')
    elif worker.code_repair is None and worker.disclosure_repair is None and renewal is None and worker.disclosure_revision is None:
        require(saved['code']==code(worker),'worker or owning implementation changed; root revalidation required')
    if worker.disclosure_repair is not None:
        from scripts.batch.harbor_disclosure_review_resume import validate
        validate(worker,before)
    if worker.warning_requalification is not None:
        from scripts.batch.harbor_dialogue_warning_requalification import validate
        validate(worker,before)
    require(codec.decode(saved['selection']).content.bindings['original_raw_source']==selection.content.bindings['original_raw_source'],'worker original identity changed')
    finished=[]
    for name in STEPS:
        if worker.dialogue_pre_review_repair is not None and worker.dialogue_semantic_retry is None and name=='dialogue-paraphraser':
            # The repair receipt proves accepted authors plus the corrected draft;
            # the original failed mechanical checkpoint remains unchanged.
            finished.append(name);continue
        if boundary is not None and name=='nat-writer':
            finished.append(name);continue
        path=step_directory(worker,name)/'complete.json'
        if not path.exists():break
        value=read(record(path));finished.append(name)
    if checkpoint is not None:
        seen=set();current=checkpoint
        while current is not None:
            require(current['path'] not in seen and len(seen)<128, 'cyclic or overlong worker checkpoint chain')
            seen.add(current['path']);bound=read(current)
            if 'intent' in bound:
                intent_bound=read(bound['intent'])
                require(intent_bound['initial']==record(initial), 'worker initial context changed')
                current=intent_bound['previous']
            elif worker.lease_renewal is not None and bound.get('schema_version')=='harbor-instruction-lease-renewal/v1':
                from scripts.batch.harbor_instruction_lease_renewal import checkpoint_predecessor
                current=checkpoint_predecessor(worker,current)
            elif worker.dialogue_pre_review_repair is not None and current==worker.dialogue_pre_review_repair:
                current=read(worker.dialogue_pre_review_repair)['predecessor']
            elif worker.disclosure_revision is not None and current==worker.disclosure_revision:
                current=read(worker.disclosure_revision)['predecessor']
            elif worker.disclosure_repair is not None and current==worker.disclosure_repair:
                current=read(worker.disclosure_repair)['blocked']
            elif worker.warning_requalification is not None and current==worker.warning_requalification:
                current=read(worker.warning_requalification)['blocked']
            elif worker.pretransport_recovery is not None and current==worker.pretransport_recovery:
                current=worker.dialogue_semantic_retry
            elif semantic is not None and current==worker.dialogue_semantic_retry:
                current=semantic['blocked']
            elif boundary is not None and current==worker.boundary_recovery:
                current=boundary['blocked']
            elif boundary is not None and worker.retry is not None and current==worker.retry:
                current=read(read(worker.retry)['permit'])['blocked']
            elif structural is not None and current==worker.dialogue_retry:
                current=structural['checkpoint']
            elif structural is not None and worker.retry is not None and current==worker.retry:
                current=read(read(worker.retry)['permit'])['blocked']
            elif retry is not None and current == worker.retry:
                current = retry['blocked']
            elif worker.code_repair is not None and current == worker.code_repair:
                current = read(worker.code_repair)['predecessor']
            else:require(False, 'unsupported root-selected checkpoint')
    next_phase = STEPS[len(finished)] if len(finished)<len(STEPS) else 'enqueue'
    pending_now=sorted(step_directory(worker,next_phase).glob('pending-*.json'))
    preceding=worker.boundary_recovery if boundary is not None and finished and finished[-1]=='nat-writer' else ((worker.dialogue_pre_review_repair if worker.dialogue_pre_review_repair is not None and worker.dialogue_semantic_retry is None and finished[-1]=='dialogue-paraphraser' else record(step_directory(worker,finished[-1])/'complete.json')) if finished else None)
    if worker.dialogue_pre_review_repair is not None and worker.dialogue_semantic_retry is None and finished and finished[-1]=='dialogue-paraphraser':
        preceding=worker.dialogue_pre_review_repair
    required=record(pending_now[-1]) if pending_now else preceding
    if retry is not None and next_phase == 'nat-writer' and not pending_now:
        required = worker.retry
    if semantic is None and structural is not None and next_phase==structural['phase'] and not pending_now:
        required=worker.dialogue_retry
    if semantic is not None and next_phase=='dialogue-writer' and not pending_now:required=worker.pretransport_recovery or worker.dialogue_semantic_retry
    if worker.warning_requalification is not None and next_phase=='dialogue-review' and not pending_now:required=worker.warning_requalification
    if (worker.disclosure_revision is not None or worker.disclosure_repair is not None) and next_phase=='disclosure-review' and not pending_now:required=worker.disclosure_revision or worker.disclosure_repair
    if renewal is not None and preceding==renewal['checkpoint'] and not pending_now and required==preceding:
        required=worker.lease_renewal
    require(checkpoint==required, 'exact root-selected preceding checkpoint hash required')
    expected=STEPS[len(finished)] if len(finished)<len(STEPS) else None
    require(expected is not None and phase in (None,expected),'noncanonical or completed worker step')
    predecessor=boundary if boundary is not None and expected=='nat-review' else (read(worker.dialogue_pre_review_repair if worker.dialogue_pre_review_repair is not None and worker.dialogue_semantic_retry is None and finished[-1]=='dialogue-paraphraser' else record(step_directory(worker,finished[-1])/'complete.json')) if finished else saved)
    if worker.code_repair is not None and expected==read(worker.code_repair)['phase']:
        predecessor=read(worker.code_repair)
    if retry is not None and expected == 'nat-writer':
        predecessor = retry
    if semantic is None and structural is not None and expected==structural['phase']:predecessor=structural
    if semantic is not None and expected=='dialogue-writer':predecessor=semantic
    if worker.warning_requalification is not None and expected=='dialogue-review':predecessor=read(worker.warning_requalification)
    if (worker.disclosure_revision is not None or worker.disclosure_repair is not None) and expected=='disclosure-review':predecessor=read(worker.disclosure_revision or worker.disclosure_repair)
    if renewal is not None and preceding==renewal['checkpoint'] and checkpoint==worker.lease_renewal:
        predecessor=read(worker.lease_renewal)
    require(codec.encode(selection)==predecessor['selection'] and asdict(lease)==predecessor['lease'],'caller did not select exact previous worker checkpoint')
    directory=step_directory(worker,expected);directory.mkdir(parents=True,exist_ok=True)
    intent=directory/'intent.json'
    if intent.exists():
        pending_files=sorted(directory.glob('pending-*.json'))
        if (directory/'exception.json').exists() or (directory/'blocked.json').exists() or not pending_files:
            return Result('RECONCILIATION_REQUIRED',selection,lease,expected,record(intent))
        observed=read(record(pending_files[-1]))
        require(observed['status'] in {'COMMANDS_REQUIRED','MODEL_REQUIRED','RUNTIME_REQUIRED'},'recorded failure requires root repair')
        require(observed['selection']==codec.encode(selection) and observed['lease']==asdict(lease),'pending context changed')
        # Pending-only effects may be re-entered; each nested command/model
        # independently refuses uncertain replay. Remove no old journal record.
    else:keep(intent,dict(step=expected,selection=codec.encode(selection),lease=asdict(lease),code=code(worker),initial=record(initial),previous=checkpoint))
    try:
        new,newlease,status=_operation(worker,selection,lease,expected,directory,**options)
    except BaseException as exc:
        keep(directory/'exception.json',dict(type=type(exc).__name__,message_sha256=executor.sha(str(exc).encode()),automatic_replay=False))
        raise
    payload=dict(step=expected,status=status or 'completed',selection=codec.encode(new),lease=asdict(newlease),intent=record(intent))
    if status:
        target=directory/(('pending-%04d.json'%len(list(directory.glob('pending-*.json')))) if status in {'COMMANDS_REQUIRED','MODEL_REQUIRED','RUNTIME_REQUIRED'} else 'blocked.json')
        evidence=keep(target,payload)
        return Result(status,new,newlease,expected,evidence)
    evidence=keep(directory/'complete.json',payload)
    index=STEPS.index(expected)+1
    return Result('PUBLICATION_READY' if index==len(STEPS) else 'STEP_COMPLETE',new,newlease,STEPS[index] if index<len(STEPS) else None,evidence)


def run(worker, selection, lease, **options):
    """Run fixed steps serially until a required capability, failure or enqueue."""
    checkpoint=options.pop('checkpoint',None)
    while True:
        result=step(worker,selection,lease,checkpoint=checkpoint,**options)
        if result.status!='STEP_COMPLETE':return result
        selection,lease,checkpoint=result.selection,result.lease,result.evidence

"""Explicit lease handoff after an actual completed instruction checkpoint.

The root's original takeover owns the lease CAS. This reader never replaces a
lease or repeats a completed command; its handoff preserves the old checkpoint.
"""
import ast
import hashlib
import inspect
import json
import textwrap
from types import CodeType, FunctionType
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

SCHEMA = 'fleet-completed-checkpoint-takeover/v1'
FIELDS = {'schema_version', 'implementation', 'takeover_module', 'takeover_plan',
          'completion', 'outcome', 'result', 'descriptor', 'guard', 'worker_run',
          'worker_root', 'workbook', 'source', 'tracker', 'directory'}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def binding(path):
    path = Path(path)
    require(path.is_absolute() and path.is_file() and not any(
        p.is_symlink() for p in (path, *path.parents)), 'regular checkpoint artifact required')
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def read(row):
    require(set(row) == {'path', 'sha256'} and binding(row['path']) == row,
            'completed checkpoint bytes changed')
    return json.loads(Path(row['path']).read_bytes())


def validate(profile, I, W, T, successful, *, live=True):
    p = read(profile)
    require(set(p) in (FIELDS,FIELDS|{'pending_history','pending_reader'},FIELDS|{'prior_takeover_chain'},
            FIELDS|{'initial_start'},FIELDS|{'initial_start','prior_takeover_chain'},FIELDS|{'completed_guard_input'}) and p['schema_version'] == SCHEMA
            and p['implementation'] == binding(__file__)
            and p['takeover_module'] == binding(T.__file__), 'closed takeover handoff required')
    tp = read(p['takeover_plan'])
    require(tp['schema_version'] == 'harbor-allowance-takeover-direct/v1'
            and tp['shared']['takeover'] == p['takeover_module']
            and tp['shared']['lifecycle'] == binding(T.life.__file__), 'different takeover implementation')
    actual, value = successful(p['outcome'])
    require(actual == p['result'] and read(actual) == value
            and value['status'] == ('INSTRUCTION_READY'if 'initial_start'in p else 'MODEL_REQUIRED'if 'pending_history'in p else 'STEP_COMPLETE') and value['descriptor'] == p['descriptor']
            and value['next_step'] in W.STEPS and value['publication_performed'] is False,
            'actual completed original instruction required')
    old_value, selection, previous = I._prior(p['result'], p['descriptor'])
    require(old_value == value, 'original checkpoint decoder differs')
    worker, controls, recipe = I.selected(p['descriptor'])
    require(str(worker.run) == p['worker_run'] and str(worker.root) == p['worker_root']
            and selection.content.workbook == p['workbook']
            and selection.content.bindings['original_raw_source'] == p['source'],
            'takeover changed original worker or source')
    if 'pending_history'in p:
        pending_preparation(p,value,I,W,worker,live=live)
    if 'initial_start'in p:
        initial_start(p,value,I,W,worker,live=live)
    if 'completed_guard_input'in p:
        completed_guard_input(p,tp,I,value,previous)
    before, after, current = T.validate_completion(p['completion'])
    done = read(p['completion']); stop = read(done['stopped_owner_proof'])
    require(T._selection(stop['selection']) == T._selection(tp['selection'])
            and checkpoint_custody(p,tp,T,previous,before,after,current,stop)
            and p['tracker'] == dict(path=str(worker.context.tracker_root / (p['workbook'] + '.json')),
                                     sha256=done['after']['sha256'])
            and before['workbook_id'] == after['workbook_id'] == p['workbook']
            and before['bindings']['original_raw_source'] == after['bindings']['original_raw_source'] == p['source'],
            'takeover is detached from the actual completed custody')
    if live:
        require(binding(p['tracker']['path']) == p['tracker'], 'current takeover tracker changed')
        W.active(worker, selection, current)
    return p, value, worker, selection, previous, current


def initial_start(p,value,I,W,worker,*,live):
    """A guarded startup has no worker predecessor or task directory yet."""
    require(p['initial_start'] is True and value['checkpoint'] is None
        and value['next_step']==W.STEPS[0]
        and read(p['descriptor'])['initial']==dict(selection=value['selection'],lease=value['lease']),
        'initial takeover cannot invent an instruction predecessor')
    if live:require(not worker.run.exists(),'initial instruction already started')



def checkpoint_custody(p,tp,T,previous,before,after,current,stop):
    """Retain the real instruction checkpoint across an explicit prior-T chain."""
    if 'prior_takeover_chain'not in p:
        return (stop['before']['sha256']==previous.sha256 and before['revision']==previous.revision
            and (before['leases']['task_agent']['owner'],before['leases']['task_agent']['token'])==(previous.owner,previous.token)
            and tp['selection']['checkpoint']==p.get('completed_guard_input',p['result'])and tp['selection']['guard']==p['guard'])
    chain=p['prior_takeover_chain']
    require(set(chain)=={'original_tracker','prior_completion','guard'}
        and chain['prior_completion']==tp['selection'].get('prior_completion')==tp['selection']['checkpoint']
        and chain['guard']==tp['selection']['guard']
        and p['guard']==read(p['outcome'])['resource'],
        'prior takeover checkpoint or actual instruction guard differs')
    original=read(chain['original_tracker'])
    require(chain['original_tracker']['sha256']==previous.sha256 and original['revision']==previous.revision
        and original['workbook_id']==p['workbook']and original['bindings']['original_raw_source']==p['source']
        and (original['leases']['task_agent']['owner'],original['leases']['task_agent']['token'])==(previous.owner,previous.token),
        'prior takeover chain detached from actual instruction lease')
    # The original recursive lifecycle validator proves every completed T and
    # intervening renewal. It rejects lane mutations or a changed source/owner.
    T.continuation_chain(chain['original_tracker'],asdict(previous),after,current,
        takeover=p['completion'],tracker_path=p['tracker']['path'])
    return True


def completed_guard_input(p,tp,I,value,previous):
    """Retain a guard's completed output when its input proved stopped custody."""
    source=p['completed_guard_input'];outcome=read(p['outcome']);guard=read(p['guard']);intent=read(guard['intent'])
    require(source==tp['selection']['checkpoint'] and source!=p['result']
        and p['guard']==tp['selection']['guard']==outcome['resource'] and outcome['result']==p['result']
        and guard['returncode']==0 and guard['failure']is None
        and intent['action']=='step'and intent['arguments']==dict(descriptor=p['descriptor'],checkpoint=source)
        and intent['code']==binding(I.__file__)and intent['base_code']==binding(I.base.__file__)
        and value['descriptor']==p['descriptor']and value['evidence'][0]==source,
        'completed output detached from original guarded input')
    prior,selection,lease=I._prior(source,p['descriptor'])
    require(prior==read(source)and prior['descriptor']==p['descriptor']and asdict(lease)==asdict(previous)
        and selection.content.workbook==p['workbook']
        and selection.content.bindings['original_raw_source']==p['source'],
        'completed guard changed original input lease or source')


def handoff(profile, I, W, T, successful, keep):
    """Append a named handoff only after the original completed T is validated."""
    p, value, worker, selection, previous, current = validate(profile, I, W, T, successful)
    directory = Path(p['directory'])
    require(directory.is_absolute() and not directory.exists(), 'handoff namespace already used')
    require(not any(x.is_symlink() for x in (directory, *directory.parents)), 'unsafe handoff namespace')
    directory.mkdir(parents=True)
    descriptor = keep(directory / 'descriptor.json', dict(read(p['descriptor']),
        origin=[*read(p['descriptor'])['origin'], profile, p['completion']],
        initial=dict(selection=value['selection'], lease=I.base.encode(current))))
    result = keep(directory / 'result.json', I._result(value['status'], descriptor, selection, current,
        checkpoint=value['checkpoint'], next_step=value['next_step'],
        evidence=[profile, p['completion'], p['outcome'], p['result']]))
    complete = keep(directory / 'handoff.json', dict(schema_version=SCHEMA, profile=profile, result=result,
        descriptor=descriptor, original_result=p['result'], checkpoint=value['checkpoint'],
        selection=value['selection'], lease=asdict(current), next_step=value['next_step'],
        completion=p['completion'], models_dispatched=0, semantic_budget_reset=False))
    return complete


@contextmanager
def prior_scope(row, profile, I, W, T, successful, *, completed_successor=None,
                failed_successor=None, original_reader=None, failed_lease=None):
    """Decode only this new handoff; all historical results remain original."""
    old = I._prior
    require(failed_successor is None or completed_successor is None,
            'failed and completed handoff successors are distinct')
    require(failed_lease is None or failed_successor is not None and callable(failed_lease),
            'renewed custody requires the exact failed successor')
    validator = validate
    if original_reader is not None:
        require(failed_successor is not None and
                read(profile)['implementation'] == binding(original_reader.__file__),
                'historical handoff reader must be the original selected implementation')
        validator = original_reader.validate
    target = read(row)['result']
    def selected(bound, descriptor):
        if bound != target:
            return old(bound, descriptor)
        successor=completed_successor()if callable(completed_successor)else completed_successor
        failed=failed_successor()if callable(failed_successor)else failed_successor
        # Validation delegates to the original decoder, avoiding recursion.
        I._prior = old
        try:
            p, value, worker, selection, previous, current = validator(
                profile, I, W, T, successful, live=successor is None and failed is None)
        finally:
            I._prior = selected
        actual = read(row); d = read(descriptor); original = read(p['descriptor'])
        require(row['path'] == str(Path(p['directory']) / 'handoff.json')
                and descriptor == binding(Path(p['directory']) / 'descriptor.json')
                and d == dict(original, origin=[*original['origin'], profile, p['completion']],
                              initial=dict(selection=value['selection'], lease=I.base.encode(current)))
                and target == binding(Path(p['directory']) / 'result.json')
                and actual == dict(schema_version=SCHEMA, profile=profile, descriptor=descriptor, result=target,
                    original_result=p['result'], checkpoint=value['checkpoint'], selection=value['selection'],
                    lease=asdict(current), next_step=value['next_step'], completion=p['completion'],
                    models_dispatched=0, semantic_budget_reset=False), 'takeover handoff changed')
        decoded = read(target)
        require(decoded == I._result(value['status'], descriptor, selection, current,
            checkpoint=value['checkpoint'], next_step=value['next_step'],
            evidence=[profile, p['completion'], p['outcome'], p['result']]),
            'instruction handoff lost original completed evidence')
        if successor is not None:
            completed_checkpoint_successor(successor,row,I,W,successful)
        if failed is not None:
            failed_checkpoint_successor(failed,row,I,W,worker)
            live_lease=current if failed_lease is None else failed_lease(current)
            if failed_lease is None:
                require(binding(p['tracker']['path']) == p['tracker'],
                        'failed successor changed current takeover tracker')
            # The authenticated failed command may have written its stage.
            # Check current custody against original refreshed content, while
            # returning the historical selection and handoff bytes unchanged.
            W.active(worker,W.refreshed(selection),live_lease)
        return decoded, selection, current
    I._prior = selected
    try:
        yield
    finally:
        I._prior = old


def failed_checkpoint_successor(proof, row, I, W, worker):
    """Retain an actually failed first child without relabeling it completed."""
    p=read(proof);handoff=read(row)
    require(set(p)=={'schema_version','handoff','outcome','directory','tree','worker_intent','exception'}
        and p['schema_version']=='fleet-failed-checkpoint-successor/v1'and p['handoff']==row,
        'closed failed handoff successor required')
    outcome=read(p['outcome']);guard=read(outcome['resource']);intent=read(guard['intent'])
    directory=Path(p['outcome']['path']).parent
    require(outcome==dict(status='RECONCILIATION_REQUIRED',resource=outcome['resource'],
        automatic_retry=False,source_invalidity_claimed=False,terminal_verdict=None)
        and guard['returncode']==1 and guard['failure']is None
        and outcome['resource']==binding(directory/'resource.json')
        and guard['intent']==binding(directory/'intent.json')and guard['log']==binding(directory/'step.log')
        and not(directory/'result.json').exists()
        and intent['action']=='step'and intent['arguments']==dict(descriptor=handoff['descriptor'],checkpoint=handoff['result'])
        and intent['code']==binding(I.__file__)and intent['base_code']==binding(I.base.__file__)
        and intent['timeout_seconds']==I.guard_seconds('step',intent['arguments'])
        and intent['memory_limit_bytes']==8589934592,
        'failed successor is not the original sealed first child')
    step=Path(p['directory'])
    require(step==W.step_directory(worker,handoff['next_step'])and step.is_absolute()
        and p['worker_intent']==binding(step/'intent.json')and p['exception']==binding(step/'exception.json')
        and not(step/'complete.json').exists(),'failed owning step differs or completed')
    actual={}
    for path in step.rglob('*'):
        require(not path.is_symlink(),'failed owning tree contains a symbolic link')
        if path.is_file():actual[str(path.relative_to(step))]=binding(path)['sha256']
        else:require(path.is_dir(),'unexpected failed owning tree entry')
    require(actual==p['tree']and 2<=len(actual)<=256,'failed owning command evidence changed')
    wi=read(p['worker_intent']);failure=read(p['exception'])
    require(wi==dict(code=W.code(worker),initial=binding(worker.run/'initial.json'),lease=handoff['lease'],
        previous=handoff['checkpoint'],selection=handoff['selection'],step=handoff['next_step'])
        and failure['automatic_replay']is False and failure['type']=='AdmissionError',
        'failed first step changed original source, lease, or checkpoint')
    return outcome,guard


def completed(row, I):
    """Read the real result named by the explicit root handoff certificate."""
    raw = read(row)
    value, _, _ = I._prior(raw['result'], raw['descriptor'])
    require(read(raw['result']) == value, 'completed handoff decoder differs from stored bytes')
    return raw['result'], value


def completed_checkpoint_successor(outcome, row, I, W, successful):
    """Prove the first real owning child consumed this handoff exactly once.

    This is historical validation only. Later owning I/W and publication APIs
    remain responsible for their actual current checkpoint and live custody.
    """
    handoff=read(row);target=handoff['result'];raw=read(outcome);guard=read(raw['resource']);intent=read(guard['intent'])
    require(raw['status']=='COMPLETED_OPERATION'and guard['returncode']==0 and guard['failure']is None
        and intent['action']=='step'and intent['arguments']==dict(descriptor=handoff['descriptor'],checkpoint=target)
        and intent['code']==binding(I.__file__)and intent['timeout_seconds']==I.guard_seconds('step',intent['arguments']),
        'historical handoff requires its actual completed first child')
    result,value=successful(outcome)
    require(result==raw['result']and read(result)==value and value['descriptor']==handoff['descriptor']
        and value['status']in {'STEP_COMPLETE','PUBLICATION_READY'} and value['evidence'][0]==target,
        'completed successor detached from root handoff')
    checkpoint=read(value['checkpoint']);worker,_,_=I.selected(value['descriptor'])
    worker_intent=read(checkpoint['intent'])
    require(value['checkpoint']==binding(W.step_directory(worker,handoff['next_step'])/'complete.json')
        and checkpoint['status']=='completed'and checkpoint['step']==handoff['next_step']
        and checkpoint['selection']==value['selection']
        and checkpoint['lease']==asdict(I.base.decode(value['lease'])),
        'completed owning step differs')
    require(checkpoint['status']=='completed'and checkpoint['step']==handoff['next_step']
        and worker_intent['previous']==handoff['checkpoint']and worker_intent['lease']==handoff['lease']
        and worker_intent['initial']==binding(worker.run/'initial.json'),
        'completed worker lost original checkpoint or current takeover lease')
    return result,value


def enqueue_failure(row, profile, I, W, T, successful):
    """Authenticate a failed readiness check that made no tracker mutation."""
    p, value, worker, selection, previous, current = validate(profile, I, W, T, successful, live=False)
    f = read(row)
    require(set(f) == {'schema_version','profile','outcome','directory','tree','worker_intent','exception'}
            and f['schema_version'] == 'fleet-enqueue-readiness-failure/v1'
            and f['profile'] == profile and value['next_step'] == 'enqueue', 'closed stopped enqueue proof required')
    outcome = read(f['outcome']); guard = read(p['guard']); intent = read(guard['intent'])
    require(outcome['status'] == 'RECONCILIATION_REQUIRED' and outcome.get('result') is None
            and outcome['resource'] == p['guard'] and guard['returncode'] == 1 and guard['failure'] is None
            and intent['action'] == 'step' and intent['arguments'] == dict(descriptor=p['descriptor'],checkpoint=p['result'])
            and intent['code'] == binding(I.__file__) and intent['timeout_seconds'] == I.guard_seconds('step',intent['arguments']),
            'stopped enqueue does not match the original guard')
    directory = Path(f['directory'])
    require(directory == W.step_directory(worker,'enqueue') and directory.is_absolute()
            and set(f['tree']) == {'intent.json','exception.json','readiness.json'}, 'unexpected failed enqueue effects')
    actual = {}
    for path in directory.rglob('*'):
        require(not path.is_symlink() and path.is_file(), 'unexpected failed enqueue entry')
        actual[str(path.relative_to(directory))] = binding(path)['sha256']
    require(actual == f['tree'] and f['worker_intent'] == binding(directory/'intent.json')
            and f['exception'] == binding(directory/'exception.json'), 'failed enqueue evidence changed')
    failed = read(f['worker_intent'])
    require(failed == dict(code=W.code(worker),initial=binding(worker.run/'initial.json'),lease=asdict(previous),
                          previous=value['checkpoint'],selection=value['selection'],step='enqueue'),
            'enqueue attempted another checkpoint or lease')
    require(read(f['exception']) == dict(type='AdmissionError',automatic_replay=False,
                message_sha256=hashlib.sha256(b'complete current readiness required').hexdigest()),
            'not the exact readiness facade refusal')
    report = read(binding(directory/'readiness.json'))
    require(report['publication_ready'] is True and not report['blockers'] and report['checks'],
            'original selected-worker readiness did not pass')
    # validate() ties the original checkpoint lease SHA to the immutable T
    # before snapshot, proving this failed enqueue did not advance the tracker.
    return worker, Path(p['directory'])/'steps/enqueue'


@contextmanager
def enqueue_directory_scope(row, profile, I, W, T, successful):
    worker, target = enqueue_failure(row, profile, I, W, T, successful)
    original = W.step_directory
    def selected(other,phase):
        if other.run == worker.run and other.root == worker.root and phase == 'enqueue':
            return target
        return original(other,phase)
    W.step_directory = selected
    try:
        yield selected
    finally:
        W.step_directory = original


def lease_predicate_function(original, source, allowed, *, directory=None, module_source=None):
    """Change exactly the predecessor lease assertion in a declared AST.

    The unmodified AST must compile to the actual installed function bytecode,
    including pre-existing transformations. Line numbers alone may differ.
    """
    target = ast.parse("require(codec.encode(selection)==predecessor['selection'] and asdict(lease)==predecessor['lease'],'caller did not select exact previous worker checkpoint')").body[0]
    replacement = ast.parse("require(codec.encode(selection)==predecessor['selection'] and (asdict(lease)==predecessor['lease'] or _completed_takeover_lease(worker,selection,lease,checkpoint,predecessor)), 'caller did not select exact previous worker checkpoint')").body[0]
    count = 0
    class Change(ast.NodeTransformer):
        def visit_Expr(self, node):
            nonlocal count
            if ast.dump(node, include_attributes=False) == ast.dump(target, include_attributes=False):
                count += 1
                return ast.copy_location(replacement, node)
            return self.generic_visit(node)
    raw = ast.parse(source)
    require(len(raw.body) == 1 and isinstance(raw.body[0], ast.FunctionDef)
            and not raw.body[0].decorator_list and not original.__closure__,
            'plain explicit installed worker function required')
    def signature(code):
        return (code.co_code, code.co_names, code.co_varnames, code.co_argcount,
                code.co_posonlyargcount, code.co_kwonlyargcount, code.co_flags,
                tuple(signature(x) if isinstance(x, CodeType) else x for x in code.co_consts))
    compilation = raw
    if module_source is not None:
        compilation = ast.parse(module_source)
        declared = [x for x in compilation.body if isinstance(x,ast.FunctionDef) and x.name==original.__name__]
        require(len(declared)==1 and ast.dump(declared[0])==ast.dump(raw.body[0]),
                'module function differs from declared predecessor source')
    candidate = compile(compilation, original.__code__.co_filename, 'exec')
    functions = [x for x in candidate.co_consts if isinstance(x, CodeType) and x.co_name==original.__name__]
    require(len(functions) == 1 and signature(functions[0]) == signature(original.__code__),
            'declared source differs from actual installed worker function')
    # Python 3.12 emits different module-call opcodes when an import is known
    # in the enclosing module. Preserve that compiler context without executing
    # its imports or any other module-level statements.
    target_function = raw.body[0] if module_source is None else declared[0]
    tree = Change().visit(target_function)
    require(count == 1, 'original predecessor lease assertion changed')
    scope = dict(original.__globals__); scope['_completed_takeover_lease'] = allowed
    if directory is not None:
        require(callable(directory) and callable(scope.get('step_directory')), 'original worker directory callback required')
        scope['step_directory'] = directory
    if module_source is None:compilation=ast.Module(body=[tree],type_ignores=[])
    else:compilation.body[compilation.body.index(declared[0])]=tree
    compiled=compile(ast.fix_missing_locations(compilation), original.__code__.co_filename, 'exec')
    selected=[x for x in compiled.co_consts if isinstance(x,CodeType) and x.co_name==original.__name__]
    require(len(selected)==1,'one compiled lease predicate required')
    fn=FunctionType(selected[0],scope,original.__name__,original.__defaults__)
    fn.__kwdefaults__=original.__kwdefaults__
    return fn


def lease_predicate(profile, I, W, T, successful):
    def allowed(worker, selection, lease, checkpoint, predecessor):
        p, value, selected, original, previous, current = validate(profile, I, W, T, successful)
        require(worker == selected and I.codec.encode(selection) == value['selection']
                and checkpoint == value['checkpoint'] and read(checkpoint) == predecessor
                and predecessor['lease'] == asdict(previous) and asdict(lease) == asdict(current),
                'takeover did not preserve the exact original completed predecessor')
        return True
    return allowed


@contextmanager
def step_scope(W, allowed, *, wrapper=None, directory=None):
    """Install a single lease predicate, preserving declared wrapper behavior."""
    outer = W._step
    if wrapper is None:
        require(not hasattr(outer, '_retained_step_ast'), 'wrapped worker requires its explicit implementation')
        original = outer
        path=Path(original.__code__.co_filename)
        require(str(path)==original.__globals__.get('__file__'),'worker function detached from selected module')
        binding(path)
        selected = lease_predicate_function(original, textwrap.dedent(inspect.getsource(original)), allowed,
                                            directory=directory,module_source=path.read_text())
        W._step = selected
        try:
            yield
        finally:
            W._step = original
        return
    require(binding(outer.__code__.co_filename) == wrapper, 'different completed worker wrapper')
    metadata = getattr(outer, '_retained_step_ast', None)
    require(isinstance(metadata, tuple) and len(metadata) == 3, 'declared transformed worker AST missing')
    scope, name, source = metadata
    require(isinstance(scope, dict) and name in scope and isinstance(source, str), 'invalid retained worker slot')
    # This is the exact scope referenced by the existing wrapper closure.
    require(inspect.getclosurevars(outer).nonlocals.get('scope') is scope,
            'retained worker slot is detached from the actual closure')
    original = scope[name]
    scope[name] = lease_predicate_function(original, source, allowed, directory=directory)
    try:
        yield
    finally:
        scope[name] = original


def pending_preparation(p,value,I,W,worker,*,live):
    """Retain the actual prepared request; a takeover does not consume it."""
    import importlib.util,sys
    row=p['pending_reader'];require(binding(row['path'])==row,'pending reader changed')
    spec=importlib.util.spec_from_file_location('completed_pending_history',row['path'])
    M=importlib.util.module_from_spec(spec);sys.modules[spec.name]=M;spec.loader.exec_module(M)
    q=read(p['pending_history'])
    require(q['helper']==row and q['outcome']==p['outcome']and q['result']==p['result']
        and q['descriptor']==p['descriptor']and q['source']==p['source']and q['workbook']==p['workbook'],
        'takeover omitted actual prepared history')
    got,pending,wi,_,_=M.retained(q,closed=live)
    require(got==value and pending['lease']==asdict(I.base.decode(value['lease']))
        and q['directory']==str(W.step_directory(worker,value['next_step'])),
        'pending takeover source directory differs')
    request=W.request_saved(Path(q['directory'])/'request.json');E=W.executor
    E.prepared_plan(request.prepared,worker.campaign)
    if live:
        require(not E.location(request.prepared,worker.campaign).exists()
            and not(Path(worker.campaign.root)/'attempt-identities'/(request.prepared.attempt_id+'.json')).exists(),
            'prepared request already admitted; reconcile without replay')
    return M,q,pending,wi


@contextmanager
def pending_step_scope(profile,I,W,T,successful,*,wrapper):
    """Select the new T lease for an unchanged, proven unsubmitted pending step."""
    p,value,worker,selection,previous,current=validate(profile,I,W,T,successful)
    M,q,pending,wi=pending_preparation(p,value,I,W,worker,live=True)
    outer=W._step
    require(binding(outer.__code__.co_filename)==wrapper,'original pending wrapper changed')
    original=outer.__globals__.get('_continued_step')
    require(callable(original)and binding(original.__code__.co_filename)==wrapper,
            'original pending implementation absent')
    def allowed(w,s,l,checkpoint,observed):
        cp,v,expected,selected,old,live=validate(profile,I,W,T,successful)
        require(w==expected and s==selected and l==live and checkpoint==v['checkpoint']
            and observed in (read(q['predecessor']),read(q['pending']))
            and observed['lease']==asdict(old)and observed['selection']==v['selection'],
            'pending lease substitution differs from completed takeover')
        return True
    selected=M.changed_function(original,[
        "require(codec.encode(selection)==predecessor['selection'] and asdict(lease)==predecessor['lease'],'caller did not select exact previous worker checkpoint')",
        "require(observed['selection']==codec.encode(selection) and observed['lease']==asdict(lease),'pending context changed')"],[
        "require(codec.encode(selection)==predecessor['selection'] and (asdict(lease)==predecessor['lease'] or _pending_takeover_lease(worker,selection,lease,checkpoint,predecessor)),'caller did not select exact previous worker checkpoint')",
        "require(observed['selection']==codec.encode(selection) and (observed['lease']==asdict(lease) or _pending_takeover_lease(worker,selection,lease,checkpoint,observed)),'pending context changed')"],
        {'_pending_takeover_lease':allowed})
    outer.__globals__['_continued_step']=selected
    try:yield
    finally:outer.__globals__['_continued_step']=original


@contextmanager
def declared_step_scope(W,allowed,*,wrapper):
    """Retain an authenticated global-dispatch wrapper around the owning step."""
    outer=W._step
    require(binding(outer.__code__.co_filename)==wrapper and '_continued_step'in outer.__code__.co_names,
            'declared original step route required')
    original=outer.__globals__.get('_continued_step')
    require(callable(original) and binding(original.__code__.co_filename)==wrapper
        and original.__globals__ is outer.__globals__,'declared step is detached from actual route')
    source=Path(wrapper['path']).read_text()
    selected=lease_predicate_function(original,textwrap.dedent(inspect.getsource(original)),allowed,module_source=source)
    outer.__globals__['_continued_step']=selected
    try:yield
    finally:outer.__globals__['_continued_step']=original

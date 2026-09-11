"""Root-selected strict Harbor publication, with explicit interrupted resume.

Only an exact queued instruction checkpoint can start this coordinator. It runs
no model, workbook conversion, runtime observation or solve. Publication is
serialized with the root-selected fleet queue and actual workbook slot.
"""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
import argparse
import copy
import fcntl
import json
import os
import re
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import harbor_campaign as guard
from scripts import qualified_publication as qualified
from scripts.batch import harbor_tracker as schema
from scripts.batch import harbor_tracker_lifecycle as lifecycle
from scripts.batch import harbor_ready_transitions as ready
from scripts.batch import harbor_promotion_evidence as qualification
from scripts.batch import harbor_transition_codec as codec
from scripts.batch import harbor_instruction_worker as instruction
from scripts.batch.harbor_admission import read, record, require
from scripts.batch.harbor_lane_notes import RootContext
from scripts.batch.harbor_complete_readiness import ReadinessSelection
from scripts.batch.harbor_workbook_bootstrap import Lease
import xl_release_publication as release

ROOT = guard.ROOT
SCHEMA = 'harbor-publication-coordinator/v1'
OWNER = 'harbor-orchestrator-publication'
DURATION_NS = 3600 * 10**9


@dataclass(frozen=True)
class Request:
    selection: ReadinessSelection
    lease: Lease
    checkpoint: dict  # Exact owning steps/enqueue/complete.json.
    tracker_root: Path
    publication_root: Path
    output: Path
    queue_lock: Path
    slot_lock: Path


def encode(request):
    require(type(request) is Request, 'typed root-selected publication required')
    return dict(selection=codec.encode(request.selection), lease=asdict(request.lease),
                checkpoint=request.checkpoint, **{name: str(getattr(request, name)) for name in
                ('tracker_root', 'publication_root', 'output', 'queue_lock', 'slot_lock')})


def decode(value):
    require(type(value) is dict and set(value) == {'selection', 'lease', 'checkpoint',
            'tracker_root', 'publication_root', 'output', 'queue_lock', 'slot_lock'}, 'closed publication request required')
    require(set(value['lease']) == {'owner', 'token', 'revision', 'sha256'}, 'closed lease required')
    return Request(codec.decode(value['selection']), Lease(**value['lease']), value['checkpoint'],
                   *(Path(value[k]) for k in ('tracker_root', 'publication_root', 'output', 'queue_lock', 'slot_lock')))


def _keep(path, value):
    raw = value if type(value) is bytes else lifecycle.canonical(value)
    if path.exists():
        require(not path.is_symlink() and path.read_bytes() == raw, 'immutable publication checkpoint differs')
    else:
        guard.executor.retain(path, raw)
    return record(path)


def _codes():
    return [record(Path(module.__file__).resolve()) for module in
            (sys.modules[__name__], qualified, release, lifecycle, ready, qualification)]


def _tree(path):
    schema.safe_path(path)
    return release._tree_hash(path)[0] if path.exists() else None


def _paths(r, journal):
    require(type(r.selection) is ReadinessSelection and type(r.lease) is Lease, 'typed readiness and lease required')
    wb = r.selection.content.workbook
    require(type(wb) is str and len(wb) == 4 and wb.isdigit(), 'invalid workbook identity')
    roots = [r.tracker_root, r.publication_root, r.output, journal, r.selection.content.stage]
    for path in roots + [r.queue_lock, r.slot_lock]:
        require(path.is_absolute(), 'absolute root-selected paths required')
        schema.safe_path(path)
    require(r.queue_lock != r.slot_lock, 'queue and workbook slot must be distinct')
    require(r.queue_lock.parent.is_dir() and r.slot_lock.parent.is_dir(), 'existing root-controlled lock directories required')
    require(r.tracker_root.is_dir() and r.output.parent.is_dir() and r.publication_root.parent.is_dir(), 'existing root-selected parents required')
    source = Path(r.selection.content.bindings['source_generation']['path']).parent.parent
    segmentation = Path(r.selection.content.bindings['segmentation_generation']['path']).parent.parent
    protected = roots + [source, segmentation]
    for i, left in enumerate(protected):
        for right in protected[i+1:]:
            require(left != right and left not in right.parents and right not in left.parents,
                    'publication paths overlap evidence or mutable roots')
    evidence_trees = [r.selection.content.normalization_run, r.selection.content.naturalization_root]
    evidence_trees += [Path(path) for path in r.selection.content.context_trees]
    for evidence in evidence_trees:
        schema.safe_path(evidence)
        for mutable in (r.publication_root, r.output, journal):
            require(mutable != evidence and mutable not in evidence.parents and evidence not in mutable.parents,
                    'publication journal/output overlaps selected proof context')
    for lock in (r.queue_lock, r.slot_lock):
        require(all(lock != p and p not in lock.parents for p in protected + evidence_trees), 'lock overlaps publication/evidence trees')
    return r.tracker_root / (wb + '.json'), source, segmentation


def _checkpoint(r):
    item = read(r.checkpoint)
    require(set(item) == {'step', 'status', 'selection', 'lease', 'intent'} and
            item['step'] == 'enqueue' and item['status'] == 'completed', 'exact completed enqueue required')
    require(item['selection'] == codec.encode(r.selection) and item['lease'] == asdict(r.lease), 'queued selection/lease differs')
    intent = read(item['intent'])
    require(intent['step'] == 'enqueue' and intent['selection'] == item['selection'], 'owning enqueue intent differs')
    # Installed worker code is independently enumerated; an arbitrary hash list
    # supplied by a task cannot declare stale calls current.
    class Installed:
        root = ROOT
    require(intent['code'] == instruction.code(Installed()), 'queued worker code changed')
    lifecycle.checked_record(intent['initial'])
    return item


def _tracker(path):
    raw = path.read_bytes()
    value = schema.validate_tracker(schema.strict_json(raw))
    lifecycle.validate_lifecycle(value)
    return raw, value


def _live(lease, owner, token, scope):
    now = time.time_ns()
    require(lease and (lease['owner'], lease['token'], lease['scope']) == (owner, token, scope)
            and lease['acquired_at'] <= lease['heartbeat_at'] <= now < lease['expires_at'], 'exact live lease required')


def _authority(path, before, operation, kind, owner, token):
    def exact(action, target, current, request):
        return (action == operation and target == str(path) and current == before and
                request['lease_kind'] == kind and request['owner'] == owner and
                request['scope'] == ('publication:' if kind == 'publication' else 'workbook:') + before['workbook_id'] and
                request['token'] == token and request['prior_lease'] is None and request['stopped_owner_proof'] is None)
    return lifecycle.RootAuthority('harbor-orchestrator', DURATION_NS, exact)


def _lease_step(path, journal, before, operation, kind, owner, token=None):
    """Resume only the exact one-event mutation authorized by this retained before."""
    name = operation + '-' + kind
    before_binding = _keep(journal / (name + '-before.json'), lifecycle.canonical(before))
    done = journal / (name + '.json')
    if done.exists():
        result = read(record(done))
        require(result['before'] == before_binding, 'lease checkpoint predecessor changed')
        after = read(result['after'])
    else:
        raw, observed = _tracker(path)
        if raw == lifecycle.canonical(before):
            result = lifecycle.lease_operation(path,
                authority=_authority(path, before, operation, kind, owner, token),
                operation=operation, lease_kind=kind, owner=owner,
                scope=('publication:' if kind == 'publication' else 'workbook:') + before['workbook_id'],
                token=token, expected_revision=before['revision'], expected_sha256=schema.sha(raw),
                duration_ns=DURATION_NS if operation == 'acquire' else None, clock=time.time_ns)
            after = result['tracker']
        else:
            # An actual atomic lease commit may precede its coordinator receipt.
            # This is reconstruction of that exact mutation, never a new token,
            # expired-owner replacement, or acceptance of arbitrary tracker edits.
            after = observed
        _check_lease_event(before, after, operation, kind, owner, token)
        bound = _keep(journal / (name + '-after.json'), lifecycle.canonical(after))
        _keep(done, dict(before=before_binding, after=bound))
    _check_lease_event(before, after, operation, kind, owner, token)
    return after


def _check_lease_event(before, after, operation, kind, owner, token):
    schema.validate_tracker(after)
    lifecycle.validate_lifecycle(after)
    require(len(after['handoffs']) == len(before['handoffs']) + 1, 'unexpected lease history mutation')
    event = after['handoffs'][-1]
    require(event['schema_version'] == lifecycle.EVENT_SCHEMA and event['operation'] == operation and
            event['lease_kind'] == kind and event['root_actor'] == 'harbor-orchestrator' and
            event['prior_tracker_sha256'] == schema.sha(lifecycle.canonical(before)) and
            event['before'] == before['leases'][kind] and event['after'] == after['leases'][kind] and
            event['revision'] == before['revision'] + 1 and event['at'] >= before['updated_at'], 'lease event differs from authorized operation')
    if operation == 'acquire':
        lease = event['after']
        require(event['before'] is None and lease['owner'] == owner and
                lease['scope'] == 'publication:' + before['workbook_id'] and
                lease['acquired_at'] == lease['heartbeat_at'] == event['at'] and
                lease['expires_at'] == event['at'] + DURATION_NS and token is None, 'foreign publication acquisition')
    else:
        require(event['after'] is None and event['before']['owner'] == owner and event['before']['token'] == token,
                'foreign lease release')
    derived = copy.deepcopy(before)
    derived['leases'][kind] = event['after']
    derived.update(revision=event['revision'], updated_at=event['at'])
    derived['handoffs'].append(event)
    require(derived == after, 'lease commit changed unrelated tracker data')


def _current(path, expected):
    require(path.read_bytes() == lifecycle.canonical(expected), 'tracker conflict before publication operation')


def _output(r, manifest, initial, journal):
    task = Path(manifest['task_generation']['generation_path'])
    expected = release._tree_hash(task)[0]
    observed = _tree(r.output)
    if observed != expected:
        require(observed == initial, 'root-selected compatibility output changed')
        backup = r.output.with_name('.' + r.output.name + '.previous-coordinator-' + journal.name)
        require(not backup.exists(), 'partial output replacement requires explicit reconciliation')
        release._materialize_compatibility(manifest, {'task': r.output},
            operation_id='coordinator-' + journal.name, root=r.publication_root)
    require(_tree(r.output) == expected, 'published compatibility output differs from immutable task')
    _keep(journal / 'output.json', dict(path=str(r.output), sha256=expected))


def _release_tracker_guard(path, acquired, selection, journal):
    raw = path.read_bytes()
    if raw == lifecycle.canonical(acquired):
        return
    done = journal / 'promoted.json'
    if done.exists():
        promoted = read(read(record(done))['tracker'])
        qualification.after_releases(promoted, read(record(path)))
        return
    matches = []
    for candidate in (path.parent / (path.stem + '-notes-transactions')).glob('*/transaction.json'):
        d = read(record(candidate))
        if d.get('schema_version') == ready.SCHEMA and d.get('action') == 'promoted':
            require(d['before']['sha256'] == schema.sha(lifecycle.canonical(acquired)) and
                    d['selection'] == codec.encode(selection), 'foreign interrupted promotion')
            lifecycle.checked_record(d['after'])
            matches.append(Path(d['after']['path']).read_bytes())
    require(matches == [raw], 'tracker diverged before release mutation')


def _promote(facade, path, journal, before, selection, published):
    """Recover a single exact owning transaction, including split notes/tracker writes."""
    chosen = []
    for candidate in (path.parent / (path.stem + '-notes-transactions')).glob('*/transaction.json'):
        descriptor = read(record(candidate))
        if descriptor.get('schema_version') == ready.SCHEMA and descriptor.get('action') == 'promoted':
            require(descriptor['before']['sha256'] == schema.sha(lifecycle.canonical(before)) and
                    descriptor['selection'] == codec.encode(selection) and descriptor['published'] == codec.encode(published),
                    'foreign existing promotion transaction')
            chosen.append(record(candidate))
    require(len(chosen) <= 1, 'ambiguous promotion transactions')
    lease = before['leases']['publication']
    if chosen:
        result = facade.recover_ready(path.stem, chosen[0], owner=lease['owner'], token=lease['token'])
        result['journal'] = chosen[0]
    else:
        _current(path, before)
        result = facade.transition(path.stem, 'promoted', selection,
            expected_revision=before['revision'], expected_sha256=schema.sha(lifecycle.canonical(before)),
            owner=lease['owner'], token=lease['token'], published=published)
    _keep(journal / 'promoted.json', dict(tracker=_keep(journal / 'promoted-tracker.json', lifecycle.canonical(result['tracker'])),
          transaction=result['journal']))
    return result['tracker']


def execute(intent_binding):
    """Guarded child entry point; root owns both inherited OS locks."""
    intent = read(intent_binding)
    require(intent['schema_version'] == SCHEMA and intent['code'] == _codes(), 'publication intent/code changed')
    r = decode(intent['request']); journal = Path(intent_binding['path']).parent
    path, source, segmentation = _paths(r, journal)
    _checkpoint(r)
    before = read(intent['queued_tracker'])
    require(before['revision'] == r.lease.revision and intent['queued_tracker']['sha256'] == r.lease.sha256 and
            before['workbook_id'] == r.selection.content.workbook and before['execution_state'] == 'publication_queued' and
            before['promotion_failure'] is None and before['leases']['publication'] is None and
            before['terminal_verdict'] == 'PUBLICATION_READY' and
            all(row['state'] == 'passed' and row['current_confidence'] == 'high' for row in before['lane_state'].values()),
            'not the exact queued strict tracker')
    facade = ready.ReadyTransitions(RootContext(r.tracker_root, r.selection.content.trusted_public_key))
    # Entire current financial/semantic/runtime proof; no subprocesses or flags.
    proof = ready._proof(before, r.selection)
    _keep(journal / 'readiness.json', proof)
    if not (journal / 'acquire-publication.json').exists():
        _live(before['leases']['task_agent'], r.lease.owner, r.lease.token, 'workbook:' + before['workbook_id'])
    acquired = _lease_step(path, journal, before, 'acquire', 'publication', OWNER)
    public_lease = acquired['leases']['publication']
    promotion_done = journal / 'promoted.json'
    if not promotion_done.exists():
        _live(acquired['leases']['task_agent'], r.lease.owner, r.lease.token, 'workbook:' + before['workbook_id'])
        _live(public_lease, OWNER, public_lease['token'], 'publication:' + before['workbook_id'])
    b = r.selection.content.bindings
    pair = release.resolve_build_context(before['workbook_id'], source_root=source,
        source_generation_id=b['source_generation']['id'], segmentation_root=segmentation,
        segmentation_generation_id=b['segmentation_generation']['id'])
    qualified.validate_audit_bindings(b, expected_generations=dict(source=b['source_generation']['id'], segmentation=b['segmentation_generation']['id']))
    task_record = journal / 'task.json'
    if not task_record.exists():
        _current(path, acquired)
        require(_tree(r.output) == intent['initial_output_sha256'], 'compatibility output changed before publication')
        directory, task = release.publish_task_generation(r.selection.content.stage, r.publication_root,
            before['workbook_id'], bindings=pair['bindings'])
        _keep(task_record, dict(manifest=record(directory / 'generation-manifest.json'), generation_id=task['generation_id']))
    task = read(record(task_record)); lifecycle.checked_record(task['manifest'])
    task_dir = r.publication_root / 'task-generations' / task['generation_id']
    require(Path(task['manifest']['path']) == task_dir / 'generation-manifest.json', 'task checkpoint escaped publication root')
    task_manifest = release.validate_task_generation(task_dir, expected_generation_id=task['generation_id'])
    require(task_manifest['bindings'] == pair['bindings'], 'task tuple differs from selected current pair')
    members = lambda path: {k:v for k,v in release._tree_hash(path)[1].items() if k != 'generation-manifest.json'}
    require(members(task_dir) == members(r.selection.content.stage), 'task differs from qualified stage')
    release_intent = journal / 'release-intent.json'
    resuming_release = release_intent.exists()
    _keep(release_intent, dict(source_root=str(source), segmentation_root=str(segmentation),
        task=record(task_record), previous_release_id=before['promotion_ids']['release_id'],
        output=str(r.output), initial_output_sha256=intent['initial_output_sha256']))
    require(_tree(r.output) in (intent['initial_output_sha256'], release._tree_hash(task_dir)[0]),
            'compatibility output changed before release mutation')
    _release_tracker_guard(path, acquired, r.selection, journal)
    # Immutable publication helper supports the exact post-CAS tuple only. After
    # tracker promotion, inspect the retained exact release; never republish a
    # rolled-back pointer under cover of an idempotent coordinator invocation.
    if promotion_done.exists():
        retained = codec.decode(read(record(journal / 'release.json')))
        directory, manifest = release.resolve_current_release(r.publication_root, source_root=source,
            segmentation_root=segmentation, task_root=r.publication_root, expected_release_id=retained.release_id)
    else:
        directory, manifest = qualified.publish_or_resume(r.publication_root, before['workbook_id'],
            source_root=source, source_generation_id=b['source_generation']['id'],
            segmentation_root=segmentation, segmentation_generation_id=b['segmentation_generation']['id'],
            task_generation_id=task['generation_id'], expected_current_release_id=before['promotion_ids']['release_id'],
            output_path=r.output, resuming=resuming_release)
    published = ready.ReleaseSelection(r.publication_root, source, segmentation, r.publication_root,
        manifest['release_id'], record(r.publication_root / 'current-release.json'), record(directory / 'release-manifest.json'))
    _keep(journal / 'release.json', codec.encode(published))
    _output(r, manifest, intent['initial_output_sha256'], journal)
    if promotion_done.exists():
        promoted = read(read(record(promotion_done))['tracker'])
    else:
        promoted = _promote(facade, path, journal, acquired, r.selection, published)
    original = dict(workbook_id=before['workbook_id'], **before['bindings']['original_raw_source'])
    observed = qualification.validate(original, path, trusted_public_key=r.selection.content.trusted_public_key)
    require(observed['verified_promoted'] is True and observed['revalidation_required'] is False, 'independent current qualification failed')
    current = promoted
    for kind in ('publication', 'task_agent'):
        old = current['leases'][kind]
        current = _lease_step(path, journal, current, 'release', kind, old['owner'], old['token'])
    require(_tree(r.output) == release._tree_hash(Path(manifest['task_generation']['generation_path']))[0],
            'compatibility output changed during publication')
    final = qualification.validate(original, path, trusted_public_key=r.selection.content.trusted_public_key)
    require(final['verified_promoted'] is True and all(x is None for x in current['leases'].values()), 'post-release qualification failed')
    return _keep(journal / 'result.json', dict(status='PROMOTED', qualification=final,
        tracker=record(path), publication=codec.encode(published), automatic_retry=False))


@contextmanager
def locks(r):
    descriptors = []
    try:
        for path in (r.queue_lock, r.slot_lock):
            schema.safe_path(path)
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            descriptors.append(fd)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield tuple(descriptors)
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def _run_publication(request, journal, *, resume=None, guardian_lock_fds=()):
    """Fresh root invocation or exact root-selected retained intent; no retries."""
    guardian_lock_fds = guard._guardian_fds(guardian_lock_fds)
    journal = Path(journal)
    path, _, _ = _paths(request, journal)
    with locks(request) as fds:
        if resume is None:
            require(not journal.exists(), 'existing publication needs explicit exact resume')
            _checkpoint(request)
            raw, before = _tracker(path)
            require(before['revision'] == request.lease.revision and schema.sha(raw) == request.lease.sha256,
                    'stale queued tracker CAS')
            _live(before['leases']['task_agent'], request.lease.owner, request.lease.token,
                  'workbook:' + before['workbook_id'])
            journal.mkdir(parents=True, mode=0o700)
            initial = _keep(journal / 'queued-tracker.json', raw)
            intent = _keep(journal / 'intent.json', dict(schema_version=SCHEMA, request=encode(request),
                queued_tracker=initial, initial_output_sha256=_tree(request.output), code=_codes()))
        else:
            require(Path(resume['path']) == journal / 'intent.json', 'foreign resume intent')
            saved = read(resume)
            require(saved['request'] == encode(request) and saved['code'] == _codes(), 'resume request/code changed')
            intent = resume
        invocation = journal / ('invocation-' + str(uuid.uuid4())); invocation.mkdir()
        logfile = invocation / 'publication.log'; metrics = {}; failure = None; rc = None
        try:
            with logfile.open('xb') as stream:
                rc = guard._run_in_process_group([sys.executable, '-B', str(Path(__file__).resolve()), '_child',
                    intent['path'], intent['sha256']], stdout=stream, timeout_seconds=guard.SECONDS,
                    grace_seconds=3, memory_limit_bytes=guard.MEMORY, metrics=metrics, cwd=ROOT,
                    env={**os.environ, 'PYTHONDONTWRITEBYTECODE':'1', 'PYTHONPATH':str(ROOT)}, guardian_lock_fds=(*fds,*guardian_lock_fds))
                stream.flush(); os.fsync(stream.fileno())
        except Exception as exc:
            failure = dict(type=type(exc).__name__, message_sha256=schema.sha(str(exc).encode()))
        resource = _keep(invocation / 'resource.json', dict(intent=intent, returncode=rc, metrics=metrics,
                         log=record(logfile), failure=failure))
        success = rc == 0 and failure is None and (journal / 'result.json').is_file()
        return _keep(invocation / 'outcome.json', dict(status='PROMOTED' if success else 'RECONCILIATION_REQUIRED',
            result=record(journal / 'result.json') if success else None, resource=resource,
            automatic_retry=False, source_invalidity_claimed=False))


@dataclass(frozen=True)
class VerificationPolicy:
    """Root-selected private custody location and installed verifier image."""
    bundle: Path
    verifier_image: str


def run(request, journal, *, resume=None, verification=None, guardian_lock_fds=()):
    """Publish, then automatically transfer custody and clean selected scratch.

    Publication remains successful if private verification is unavailable; the
    result explicitly records pending cleanup and retains all evidence. Retrying
    finalization uses harbor_finalize directly, not a second publication.
    """
    if verification is not None:
        require(type(verification) is VerificationPolicy, 'typed verification policy required')
        require(verification.bundle.is_absolute(), 'absolute private package location required')
        require(type(verification.verifier_image) is str and
                re.fullmatch(r'sha256:[0-9a-f]{64}', verification.verifier_image), 'pinned verifier image required')
        require(verification.bundle != request.output and request.output not in verification.bundle.parents
                and verification.bundle not in request.output.parents,
                'private verification cannot be exposed inside final task')
    custody=guard._guardian_fds(guardian_lock_fds)
    outcome = _run_publication(request, journal, resume=resume, **({'guardian_lock_fds':custody} if custody else {}))
    if verification is None:
        return outcome
    published = read(outcome)
    if published['status'] != 'PROMOTED':
        return outcome
    from scripts import harbor_finalize as finalize
    try:
        content = request.selection.content
        path = request.tracker_root / (content.workbook + '.json')
        tracker = read(record(path))
        source = dict(workbook_id=content.workbook, **tracker['bindings']['original_raw_source'])
        # The capture collector independently checks these exact roots against the
        # selected proof and removes protected overlaps. It never accepts a broad
        # corpus parent as a disposable root.
        roots = [content.stage, content.normalization_run, content.naturalization_root,
                 Path(request.selection.observation.execution['path']).parent]
        roots += [p for p in (content.mcp_build_a, content.mcp_build_b) if p is not None]
        # Nested selected roots are represented by their outermost exact root.
        roots = sorted(set(map(Path, roots)))
        roots = [p for p in roots if not any(q != p and q in p.parents for q in roots)]
        result = finalize.finalize(finalize.FinalizeRequest(
            source=source, tracker_path=str(path), trusted_public_key=content.trusted_public_key,
            bundle=str(verification.bundle), verifier_image=verification.verifier_image,
            disposable_roots=tuple(map(str, roots)), queue_lock=str(request.queue_lock),
            slot_lock=str(request.slot_lock)))
        cleanup = dict(state='COMPLETE', result=result)
    except Exception as exc:
        # A failed private proof never changes the already-published verdict and
        # never authorizes cleanup. An interrupted cleanup remains journaled.
        cleanup = dict(state='PENDING', error_type=type(exc).__name__, message=str(exc))
    return _keep(Path(outcome['path']).parent / 'finalization.json',
                 dict(status='PROMOTED', publication=outcome, cleanup=cleanup))


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] == '_child':
        require(len(args) == 3, 'exact child intent required')
        execute(dict(path=args[1], sha256=args[2])); return 0
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('checkpoint', 'checkpoint-sha256', 'tracker-root', 'publication-root', 'output', 'queue-lock', 'slot-lock', 'journal'):
        parser.add_argument('--' + key, required=True)
    parser.add_argument('--resume-intent'); parser.add_argument('--resume-sha256')
    parser.add_argument('--verification-image', default=os.environ.get('FCP_VERIFICATION_IMAGE'),
                        help='pinned local Python 3.12 verifier image ID; enables automatic custody and cleanup')
    parser.add_argument('--verification-bundle', help='private sidecar directory outside the final task')
    a = parser.parse_args(args); checkpoint = dict(path=a.checkpoint, sha256=a.checkpoint_sha256)
    item = read(checkpoint)
    r = Request(codec.decode(item['selection']), Lease(**item['lease']), checkpoint,
        *(Path(getattr(a, key)) for key in ('tracker_root', 'publication_root', 'output', 'queue_lock', 'slot_lock')))
    require(bool(a.resume_intent) == bool(a.resume_sha256), 'exact resume path/hash pair required')
    require(not a.verification_bundle or a.verification_image, 'verification bundle requires pinned image')
    policy = None if not a.verification_image else VerificationPolicy(
        Path(a.verification_bundle) if a.verification_bundle else r.output.with_name(r.output.name + '-verification'),
        a.verification_image)
    outcome = run(r, Path(a.journal), resume=None if not a.resume_intent else dict(path=a.resume_intent, sha256=a.resume_sha256),
                  verification=policy)
    print(json.dumps(outcome)); return 0


if __name__ == '__main__':
    raise SystemExit(main())

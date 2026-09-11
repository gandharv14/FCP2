"""Root-owned final status transactions; financial proof is a fixed validator.

No CLI, model call, artifact publication, arbitrary mutation or quality callback.
Released STOPPED rows can resume only after complete current readiness; this
operation cannot grant another semantic attempt or a task-agent lease.
"""
import copy
from dataclasses import dataclass
from pathlib import Path
import time
import uuid

from scripts.batch import harbor_tracker as schema
from scripts.batch import harbor_tracker_lifecycle as lifecycle
from scripts.batch import harbor_lane_notes as notes
from scripts.batch.harbor_admission import read, record, require
from scripts.batch.harbor_readiness import ROOT
from scripts.batch.harbor_transition_codec import encode, decode

SCHEMA = 'harbor-ready-transition/v1'

@dataclass(frozen=True)
class ReleaseSelection:
    publication_root: Path
    source_root: Path
    segmentation_root: Path
    task_root: Path
    release_id: str
    pointer: dict
    manifest: dict


def _semantic_attempts(before,selection,proof):
    """Budget bounds only; caller must already have full current readiness."""
    s=selection.content
    from scripts.batch import harbor_readiness as static
    recovery=static.skill_module(ROOT,'naturalize-finance-task-instruction','naturalize_recovery')
    attempts={
        'harbor-instruction-naturalizer':recovery._attempts_used(recovery._read_state(s.naturalization_root)),
        'harbor-dialogue-author':proof['checks']['dialogue_review_provenance']['result']['round'],
    }
    for lane,count in attempts.items():
        minimum=0 if lane=='harbor-dialogue-author' and s.dialogue_noop is not None else 1
        require(type(count) is int and minimum <= count <= 2 and count >= (before['lane_state'][lane].get('attempt') or 0), 'current semantic journal reset previously consumed attempts')
    return attempts


def _proof(before, selection, *, runtime_producer=None):
    from scripts.batch.harbor_complete_readiness import complete_readiness
    s = selection.content
    require(s.workbook == before['workbook_id'], 'foreign readiness workbook')
    for key in ('original_raw_source', 'source_generation', 'source_file',
                'segmentation_generation', 'curation'):
        require(s.bindings[key] == before['bindings'][key], 'readiness identity differs: ' + key)
    # Re-enter all canonical owning checks in order in an ephemeral view.
    # This records no lane PASS flags and issues no model/runtime operations.
    # It permits complete-proof recovery after an early stopped gate without
    # mistaking old pending lane flags for current financial evidence.
    from scripts.batch.harbor_completed_lanes import STEPS,CompletionEvidence,check_completed_gate
    view=copy.deepcopy(before);view['promotion_failure']=None
    for row in view['lane_state'].values():row.update(state='pending',phase=None)
    invocation_evidence=[];invocations=[]
    for kind,lane in STEPS:
        if kind in {'source','segmentation'}:
            binding=s.bindings[kind+'_generation']
            packet=notes.GateEvidence(kind,record(Path(binding['path'])/'generation-manifest.json'),s.normalization_records)
            result=notes.check_gate(view,packet,notes.RootContext(Path('.'),s.trusted_public_key))
        else:
            result=check_completed_gate(view,CompletionEvidence(kind,selection),notes.RootContext(Path('.'),s.trusted_public_key),
                                        **({'runtime_producer':runtime_producer} if runtime_producer is not None else {}))
        require(result['passed'] and result.get('confidence','high')=='high','canonical owning revalidation failed: '+kind)
        view['bindings'].update(result['bindings'])
        view['lane_state'][lane].update(state='running' if kind=='normalization_phase1' else 'passed',phase=result.get('phase'))
        invocation_evidence.extend(result['evidence']);invocations.append(kind)
    proof = complete_readiness(selection, root=ROOT, **(
        {'runtime_producer':runtime_producer} if runtime_producer is not None else {}))
    proof['canonical_invocations']=invocations
    proof['evidence']=notes.evidence_list(proof['evidence']+invocation_evidence)
    require(proof.get('schema_version') == 'harbor-complete-readiness/v1' and proof.get('workbook') == s.workbook and proof.get('stage_sha256') == s.stage_sha256, 'readiness proof identity differs')
    require(proof['publication_ready'] is True and not proof['blockers'] and proof['checks'], 'complete current readiness required')
    require(all(v.get('checked') is True for v in proof['checks'].values()), 'unchecked readiness gate')
    lifecycle.check_records(proof['evidence'])
    attempts=_semantic_attempts(before,selection,proof)
    require(proof['checks']['accepted_public_profiles']['result']['confidence'] == 'high', 'profile confidence is not high')
    proof['semantic_attempts']=attempts
    return proof


def _release(before, selection, published):
    from xl_release_publication import resolve_current_release, resolve_task_generation_by_id, _tree_hash
    require(isinstance(published, ReleaseSelection), 'root-selected immutable release required')
    lifecycle.checked_record(published.pointer)
    lifecycle.checked_record(published.manifest)
    require(Path(published.pointer['path']) == published.publication_root / 'current-release.json', 'foreign publication pointer')
    release, manifest = resolve_current_release(published.publication_root,
        source_root=published.source_root, segmentation_root=published.segmentation_root,
        task_root=published.task_root, expected_release_id=published.release_id)
    require(record(release / 'release-manifest.json') == published.manifest, 'published manifest differs')
    identity = manifest['identity']
    require(identity['workbook_id'] == before['workbook_id'], 'foreign published workbook')
    for kind in ('source', 'segmentation'):
        require(identity[kind + '_generation']['generation_id'] == before['bindings'][kind + '_generation']['id'], 'published generation tuple differs')
    prior = before['promotion_ids']['release_id']
    require(identity.get('prior_release_id') == prior, 'publication predecessor differs from tracker CAS')
    task_id = identity['task_generation']['generation_id']
    task, _ = resolve_task_generation_by_id(published.task_root, task_id)
    def members(path):
        return {key:value for key,value in _tree_hash(path)[1].items() if key != 'generation-manifest.json'}
    require(members(task) == members(selection.content.stage), 'published task bytes differ from qualified stage')
    return dict(task_generation_id=task_id, release_id=published.release_id, previous_release_id=prior), [published.pointer, published.manifest]


def _old_notes(before):
    if before['agent_notes']['path']:
        raw = Path(before['agent_notes']['path']).read_bytes()
        notes.validate_notes_pair(before, raw)
        return raw
    require(before['promotion_failure'] is None, 'failure has no synchronized notes')
    return None


def _after(before, action, selection, proof, now, notespath, published=None, prior_notes=None):
    require(action in {'clear', 'resume', 'enqueue', 'promoted'}, 'unsupported ready transition')
    if prior_notes is None:
        _old_notes(before)
    else:
        notes.validate_notes_pair(before, prior_notes)
    old_failure = before['promotion_failure']
    if action in {'clear', 'resume'}:
        require(old_failure is not None, 'there is no failure to clear')
        # The complete fixed checker must cover a gate that actually owned the
        # failure. Unknown or intake-only failures cannot be relabeled here.
        require(old_failure['lane'] in schema.LANES, 'owning failure lane is missing')
        require(proof.get('canonical_invocations') == [kind for kind,_ in __import__('scripts.batch.harbor_completed_lanes',fromlist=['STEPS']).STEPS], 'complete canonical revalidation order is missing')
    else:
        require(old_failure is None and all(row.get('state') == 'passed' and row.get('current_confidence') == 'high' for row in before['lane_state'].values()), 'all canonical lanes must already pass')
    if action == 'resume':
        require(before['execution_state'] == 'STOPPED' and all(v is None for v in before['leases'].values()), 'resume requires STOPPED with independently released leases')
    elif action == 'promoted':
        require(before['execution_state'] == 'publication_queued', 'promotion must follow publication enqueue')
    else:
        require(before['execution_state'] not in {'STOPPED', 'PROMOTED'}, 'terminal state requires its explicit transition')
    after = copy.deepcopy(before)
    after['revision'] += 1
    after['updated_at'] = now
    evidence = notes.evidence_list(proof['evidence'])
    signature = schema.sha(lifecycle.canonical({'workbook':before['workbook_id'], 'action':action, 'proof':proof}))
    if action in {'clear', 'resume'}:
        after['failure_history'].append(dict(event='clear', revision=after['revision'], failure=copy.deepcopy(old_failure), result='complete_current_readiness', evidence_paths=evidence))
        after['promotion_failure'] = None
        for row in after['lane_state'].values():
            row.update(state='passed', current_confidence='high', evidence_paths=evidence)
        after['lane_state']['harbor-normalization-engineer']['phase']='phase2_complete'
        for row in after['lane_state'].values():
            row['confidence_history'].append(dict(level='high', reason='complete_current_readiness', revision=after['revision']))
    after['current_confidence'] = dict(level='high', reason_codes=['complete_current_readiness'], contributors=list(schema.LANES), computed_at_revision=after['revision'])
    after['confidence_history'].append(copy.deepcopy(after['current_confidence']))
    after.update(current_gate=None, last_gate=action, execution_state='running' if action in {'clear', 'resume'} else 'publication_queued', terminal_verdict=None if action in {'clear', 'resume'} else 'PUBLICATION_READY', final_status=None, stop_reason=None, staged_path=str(selection.content.stage))
    after['bindings']['staged_bundle'] = dict(path=str(selection.content.stage), sha256=proof['stage_sha256'])
    if action == 'promoted':
        ids, extra = _release(before, selection, published)
        evidence = notes.evidence_list(evidence + extra)
        after['promotion_ids'] = ids
        after['generation_ids'].update(task=ids['task_generation_id'], release=ids['release_id'])
        after.update(execution_state='PROMOTED', terminal_verdict='PROMOTED', final_status='PROMOTED')
    for lane,count in proof.get('semantic_attempts',{}).items():
        require(count >= (before['lane_state'][lane].get('attempt') or 0), 'semantic consumption decreased')
        after['lane_state'][lane]['attempt']=count
    after['handoffs'].append(dict(schema_version=SCHEMA, operation=action, revision=after['revision'], diagnostic_signature=signature, evidence_paths=evidence, stage_sha256=proof['stage_sha256'], prior_failure_signature=old_failure['diagnostic_signature'] if old_failure else None, budget_limits_unchanged=True, consumed_attempts=copy.deepcopy(proof.get('semantic_attempts',{}))))
    if action == 'promoted':
        # Seal the exact historical descriptor inputs into the promoted tracker.
        # A future report may establish integrity while current code has drifted;
        # it must never substitute a newly selected historical context.
        after['handoffs'][-1].update(
            prior_tracker_sha256=schema.sha(lifecycle.canonical(before)),
            selection_sha256=schema.sha(lifecycle.canonical(encode(selection))),
            published_selection_sha256=schema.sha(lifecycle.canonical(encode(published))))
    after['agent_notes'] = dict(path=str(notespath), sha256=None, diagnostic_signature=signature, evidence_set_sha256=schema.sha(lifecycle.canonical(evidence)), updated_at=now)
    rendered = notes.render_notes(after)
    after['agent_notes']['sha256'] = schema.sha(rendered)
    # These fields are copied rather than re-created from current defaults.
    require(after['repairs'] == before['repairs'] and after['scheduler'] == before['scheduler'], 'consumed repair/scheduler budget changed')
    require(all((after['lane_state'][k].get('attempt') or 0) >= (before['lane_state'][k].get('attempt') or 0) for k in schema.LANES), 'semantic attempts decreased')
    schema.validate_tracker(after)
    lifecycle.validate_lifecycle(after)
    notes.validate_notes_pair(after, rendered)
    return after, rendered


class ReadyTransitions(notes.LaneNotes):
    def transition(self, wb, action, selection, *, expected_revision, expected_sha256,
                   owner=None, token=None, authority=None, published=None, _fault=lambda _:None):
        path = self._path(wb)
        # Runtime/model work is never performed here or while holding this lock.
        with lifecycle.locked(path):
            self.require_synchronized(wb)
            raw = path.read_bytes(); before = schema.strict_json(raw)
            schema.validate_tracker(before); lifecycle.validate_lifecycle(before)
            require(before['revision'] == expected_revision and schema.sha(raw) == expected_sha256, 'stale tracker CAS')
            now = time.time_ns()
            lease_kind = 'publication' if action == 'promoted' else 'task_agent'
            if action == 'resume':
                lifecycle.authorize(authority, 'resume_complete', path, before, {'expected_revision':expected_revision, 'expected_sha256':expected_sha256})
            else:
                lease = before['leases'][lease_kind]
                require(lease and (lease['owner'], lease['token']) == (owner, token), 'wrong exact lease owner/token')
                require(lease['scope'] == ('workbook:' + wb if lease_kind == 'task_agent' else 'publication:' + wb), 'wrong lease scope')
                require(lease['acquired_at'] <= lease['heartbeat_at'] <= now < lease['expires_at'], 'lease expired')
            proof = _proof(before, selection)
            notespath = path.with_name(wb + '-agent-notes.md')
            after, rendered = _after(before, action, selection, proof, now, notespath, published)
            journal = path.parent / (wb + '-notes-transactions'); journal.mkdir(exist_ok=True)
            tx = journal / (str(after['revision']) + '-' + str(uuid.uuid4())); tx.mkdir()
            oldnotes = _old_notes(before)
            for name, data in [('before.json',raw), ('after.json',lifecycle.canonical(after)), ('notes.md',rendered)]: notes._immutable(tx / name, data)
            if oldnotes is not None: notes._immutable(tx / 'prior-notes.md', oldnotes)
            descriptor = dict(schema_version=SCHEMA, workbook=wb, action=action, prepared_at=now,
                before=record(tx/'before.json'), after=record(tx/'after.json'), notes=record(tx/'notes.md'), prior_notes=record(tx/'prior-notes.md') if oldnotes is not None else None,
                selection=encode(selection), published=encode(published), lease_kind=lease_kind,
                lease=before['leases'][lease_kind], root_authorized=action=='resume')
            notes._immutable(tx/'transaction.json', lifecycle.canonical(descriptor)); notes._fsync_directory(tx); notes._fsync_directory(journal)
            _fault('prepared')
            require(path.read_bytes() == raw, 'tracker changed before commit')
            require(_proof(before, selection) == proof, 'current readiness changed before commit')
            if action != 'resume': require(time.time_ns() < before['leases'][lease_kind]['expires_at'], 'lease expired')
            require(_after(before,action,selection,proof,now,notespath,published) == (after,rendered), 'derived status changed')
            notes._replace(notespath, rendered); _fault('notes_replaced')
            if action != 'resume': require(time.time_ns() < before['leases'][lease_kind]['expires_at'], 'lease expired')
            notes._replace(path,lifecycle.canonical(after)); _fault('tracker_replaced')
            notes._immutable(tx/'complete.json',lifecycle.canonical(dict(tracker_sha256=record(path)['sha256'],notes_sha256=record(notespath)['sha256'])))
            notes._fsync_directory(tx)
            return dict(revision=after['revision'],sha256=record(path)['sha256'],tracker=after,journal=record(tx/'transaction.json'))

    def recover_ready(self, wb, journal, *, owner=None, token=None, authority=None):
        path = self._path(wb); tx = Path(journal['path']).parent
        require(tx.parent == path.parent/(wb+'-notes-transactions') and Path(journal['path']).name == 'transaction.json', 'foreign transaction')
        with lifecycle.locked(path):
            d = read(journal)
            require(d['schema_version'] == SCHEMA and d['workbook'] == wb, 'invalid ready journal')
            for key,name in [('before','before.json'),('after','after.json'),('notes','notes.md'),('prior_notes','prior-notes.md')]:
                if d[key]: require(Path(d[key]['path']) == tx/name, 'journal path escaped'); lifecycle.checked_record(d[key])
            before = read(d['before']); after = read(d['after'])
            schema.validate_tracker(before); lifecycle.validate_lifecycle(before)
            if d['action'] == 'resume':
                lifecycle.authorize(authority,'recover_resume_complete',path,before,{'journal':journal})
                require(d['root_authorized'] is True and all(v is None for v in before['leases'].values()), 'unapproved resume journal')
            else:
                lease = before['leases'][d['lease_kind']]
                require(d['lease'] == lease and (lease['owner'],lease['token']) == (owner,token), 'recovery lease identity differs')
                require(lease['heartbeat_at'] <= d['prepared_at'] < lease['expires_at'], 'transaction prepared outside lease')
            selection, published = decode(d['selection']), decode(d['published'])
            proof = _proof(before,selection)
            prior_bytes = Path(d['prior_notes']['path']).read_bytes() if d['prior_notes'] else None
            rebuilt, rendered = _after(before,d['action'],selection,proof,d['prepared_at'],path.with_name(wb+'-agent-notes.md'),published,prior_bytes)
            require(after == rebuilt and Path(d['notes']['path']).read_bytes() == rendered, 'prepared transition was forged')
            old = Path(d['before']['path']).read_bytes(); new = Path(d['after']['path']).read_bytes()
            require(path.read_bytes() in (old,new), 'tracker diverged from transaction')
            notespath = Path(after['agent_notes']['path'])
            previous = Path(d['prior_notes']['path']).read_bytes() if d['prior_notes'] else None
            require((notespath.read_bytes() if notespath.exists() else None) in (previous,rendered), 'notes diverged from transaction')
            done = tx/'complete.json'; expected = dict(tracker_sha256=d['after']['sha256'],notes_sha256=d['notes']['sha256'])
            if done.exists(): require(read(record(done)) == expected, 'invalid completion marker')
            notes._replace(notespath,rendered); notes._replace(path,new)
            if not done.exists(): notes._immutable(done,lifecycle.canonical(expected)); notes._fsync_directory(tx)
            return dict(revision=after['revision'],sha256=record(path)['sha256'],tracker=after)

    def release_stopped(self, wb, *, authority, expected_revision, expected_sha256,
                        owner, token):
        """Release a still-live stopped owner only with exact canonical notes.

        Expired owner replacement/release still needs the separate independently
        proven stopped-owner lifecycle path; this facade never invents it.
        """
        path = self._path(wb)
        self.require_synchronized(wb)
        def failure_validator(before, after, events):
            require(before['execution_state'] == 'STOPPED' and before['promotion_failure'] is not None, 'not a stopped failure')
            require(events == {'stop', 'lease_release'}, 'unexpected failure release event')
            _old_notes(before)
            require(after['promotion_failure'] == before['promotion_failure'], 'release altered failure')
            require(after['failure_history'] == before['failure_history'] and after['repairs'] == before['repairs'], 'release altered failure history or budget')
            return True
        return lifecycle.lease_operation(path, authority=authority, operation='release',
            lease_kind='task_agent', owner=owner, token=token, scope='workbook:'+wb,
            expected_revision=expected_revision, expected_sha256=expected_sha256,
            failure_validator=failure_validator, clock=time.time_ns)

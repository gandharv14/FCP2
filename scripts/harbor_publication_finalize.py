"""Finalize a retained promotion after a stopped publication invocation.

This performs no authoring, model call, release publication or promotion. It
revalidates the actual promotion before and after exact lease releases. The root
caller must hold authenticated fleet publication custody and run this primitive
under its bounded guard; the original native publication locks are also held.
"""
from pathlib import Path
import os
import time

SCHEMA = 'harbor-retained-publication-finalization/v1'


def stopped(P, intent_binding, failure_binding, *, original_plan, original_publisher):
    intent = P.read(intent_binding)
    failure = P.read(failure_binding)
    resource = P.read(failure['resource'])
    P.require(intent['schema_version'] == P.SCHEMA and intent['code'] == P._codes(),
              'retained original publication code changed')
    P.lifecycle.checked_record(resource['log'])
    directory = Path(intent_binding['path']).parent
    timed_out=(isinstance(resource.get('failure'),dict)
        and resource['failure'].get('type')=='ConversionTimeout' and resource.get('returncode')is None
        and resource.get('metrics',{}).get('limit_reason')=='wall_time')
    typed_release=(resource.get('failure')is None and resource.get('returncode')==1
        and Path(resource['log']['path']).read_text().endswith('scripts.batch.harbor_admission.AdmissionError: typed release selection required\n')
        and (directory/'promoted.json').is_file() and not(directory/'result.json').exists())
    P.require(failure['status'] == 'RECONCILIATION_REQUIRED' and failure['automatic_retry'] is False
              and failure['result'] is None and resource['intent'] == intent_binding
              and (timed_out or typed_release), 'exact stopped owning finalization boundary required')
    side=None
    if typed_release:
        side=P.read(P.record(Path(failure_binding['path']).parent/'declared-publication-reader.json'))
        P.require(side['schema_version']=='fleet-declared-publication-child-reader/v1'
            and side['producer']==P.record(P.__file__) and side['intent']==intent_binding
            and side['models_allowed']is False and side['automatic_retry']is False
            and side['timeout_seconds']==3300 and side['memory_limit_bytes']==8589934592,
            'stopped publication reader detached from original intent')
        for row in [side['reader_plan'],side['child_wrapper'],side['adapter'],side['guard'],*side['reader_evidence']]:
            P.lifecycle.checked_record(row)
    P.require(Path(failure_binding['path']).parent.parent == directory,
              'failure belongs to another publication journal')
    controller = P.read(P.record(directory.parent / 'intent.json'))
    P.require(controller['plan'] == original_plan and controller['driver'] == original_publisher
              and controller['action'] == 'remote' and controller['automatic_retry'] is False,
              'original publisher process identities detached from launch')
    needles = (intent_binding['path'], str(directory), original_plan['path'], original_publisher['path'])
    if side is not None:needles+=tuple(side[k]['path']for k in ('reader_plan','child_wrapper'))
    for item in Path('/proc').iterdir():
        if not item.name.isdigit() or int(item.name) == os.getpid():
            continue
        try:
            state = (item / 'stat').read_text().rsplit(')', 1)[1].split()[0]
            if state == 'Z':
                continue
            args = [part.decode(errors='replace') for part in (item / 'cmdline').read_bytes().split(b'\0') if part]
            P.require(not any(a == n or a.startswith(n.rstrip('/') + '/') for a in args for n in needles),
                      'original publisher or descendant remains active')
        except (FileNotFoundError, ProcessLookupError):
            continue
    return intent


def finalize(P, lifecycle, request, *, intent, failure, original_plan, original_publisher, directory):
    """Root-selected exact continuation, preserving the original failed journal."""
    directory = Path(directory)
    P.require(not directory.exists(), 'finalization namespace consumed; reconcile before continuation')
    for binding in (original_plan, original_publisher):
        P.lifecycle.checked_record(binding)
    with P.locks(request):
        old_intent = stopped(P, intent, failure, original_plan=original_plan, original_publisher=original_publisher)
        P.require(old_intent['request'] == P.encode(request), 'finalization selected another request')
        journal = Path(intent['path']).parent
        marker = P.read(P.record(journal / 'promoted.json'))
        promoted = P.read(marker['tracker'])
        path = request.tracker_root / (request.selection.content.workbook + '.json')
        current = P.read(P.record(path))
        P.require(current['execution_state'] == 'PROMOTED', 'actual completed promotion required')
        # Check the selected release implementation against the actual retained
        # event schema before expensive qualification or a new recovery intent.
        # Explicit allowance events remain part of history, never normalized away.
        lifecycle.validate_lifecycle(current)
        P.qualification.after_releases(promoted, current)
        # This reruns the canonical current validators, not the saved PASS flag.
        source = dict(workbook_id=current['workbook_id'], **current['bindings']['original_raw_source'])
        observed = P.qualification.validate(source, path, trusted_public_key=request.selection.content.trusted_public_key)
        P.require(observed['verified_promoted'] is True and observed['revalidation_required'] is False,
                  'independent current promotion qualification failed')
        stopped(P, intent, failure, original_plan=original_plan, original_publisher=original_publisher)
        directory.mkdir()
        selection = P._keep(directory / 'intent.json', dict(schema_version=SCHEMA, original_intent=intent,
            original_failure=failure, original_plan=original_plan, original_publisher=original_publisher,
            promoted=marker, tracker=P.record(path), qualification=observed, automatic_retry=False))
        for kind in ('publication', 'task_agent'):
            current = P.read(P.record(path))
            P.qualification.after_releases(promoted, current)
            old = current['leases'][kind]
            if old is None:
                continue
            proof = P._keep(directory / (kind + '-stopped.json'), dict(schema_version=SCHEMA,
                selection=selection, prior_tracker=P.record(path), lease_kind=kind, lease=old,
                original_processes=[], native_publication_locks_held=True, at=time.time_ns()))
            expected = P.record(path)
            def exact(action, target, before, selected):
                return (action == 'release' and target == str(path) and before == current
                    and selected == dict(operation='release', lease_kind=kind, owner=old['owner'],
                        scope=old['scope'], token=old['token'], duration_ns=None, prior_lease=None,
                        stopped_owner_proof=proof))
            def independently_stopped(before, lease, selected):
                stopped(P, intent, failure, original_plan=original_plan, original_publisher=original_publisher)
                return before == current and lease == old and selected == proof
            authority = lifecycle.RootAuthority('harbor-orchestrator', P.DURATION_NS, exact)
            result = lifecycle.lease_operation(path, authority=authority, operation='release', lease_kind=kind,
                owner=old['owner'], scope=old['scope'], token=old['token'],
                expected_revision=current['revision'], expected_sha256=expected['sha256'],
                stopped_owner_proof=proof, prior_owner_stopped_validator=independently_stopped)
            P.qualification.after_releases(promoted, result['tracker'])
            P._keep(directory / (kind + '-released.json'), result)
        final = P.qualification.validate(source, path, trusted_public_key=request.selection.content.trusted_public_key)
        current = P.read(P.record(path))
        P.require(final['verified_promoted'] is True and final['revalidation_required'] is False
                  and all(v is None for v in current['leases'].values()), 'post-release qualification failed')
        return P._keep(directory / 'result.json', dict(status='PROMOTED', qualification=final,
            tracker=P.record(path), publication=P.read(P.record(journal / 'release.json')),
            continuation=selection, original_publication=intent, automatic_retry=False))

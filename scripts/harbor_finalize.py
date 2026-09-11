"""Finish an already-published task by transferring evidence custody, then cleanup.

This coordinator never builds tasks or publishes releases. Its disposable roots
are exact owning-workbook scratch directories selected by the private manifest.
Unrepresented files remain in place. A completed immutable association pins the
bundle for later resumption; unassociated bundles require identical fresh native
qualification and recapture before the association can be established.
"""
from dataclasses import dataclass
from contextlib import ExitStack
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from scripts import harbor_retention as retention

SCHEMA = 'harbor-finalization-association/v1'
PLAN_SCHEMA = 'harbor-finalization-cleanup-selection/v1'


@dataclass(frozen=True)
class FinalizeRequest:
    source: dict
    tracker_path: str
    trusted_public_key: str
    bundle: str
    verifier_image: str
    disposable_roots: tuple[str, ...]
    queue_lock: str
    slot_lock: str


def _require(value, message):
    if not value:
        raise retention.RetentionError(message)


def _request(request):
    _require(type(request) is FinalizeRequest, 'typed finalization request required')
    _require(type(request.source) is dict and {'path','sha256','workbook_id'} <= set(request.source),
             'original workbook identity required')
    _require(type(request.disposable_roots) is tuple and request.disposable_roots,
             'explicit immutable scratch root tuple required')
    _require(len(set(request.disposable_roots)) == len(request.disposable_roots), 'duplicate scratch roots')
    for value in (request.source['path'],request.tracker_path,request.trusted_public_key,
                  request.bundle,request.queue_lock,request.slot_lock,*request.disposable_roots):
        retention._path(value)
    _require(re.fullmatch(r'sha256:[0-9a-f]{64}', request.verifier_image) is not None,
             'root-selected immutable verifier image digest required')
    value=dict(source=request.source, tracker_path=request.tracker_path,
               trusted_public_key=request.trusted_public_key,bundle=request.bundle,
               verifier_image=request.verifier_image,disposable_roots=list(request.disposable_roots),
               queue_lock=request.queue_lock,slot_lock=request.slot_lock)
    # Materialize a detached canonical snapshot before any filesystem actions.
    return json.loads(retention.canonical(value))


def _binding(path):
    record=retention._record(retention._path(path))
    return dict(path=path,sha256=record['sha256'])


def _closed_manifest(manifest, requested):
    required={'schema_version','source','tracker','release_pointer','release_manifest','release_id',
              'final_task_root','files','directories','objects','protected_roots','protected_files','scratch_roots'}
    _require(type(manifest) is dict and required <= set(manifest) and
             manifest['schema_version']=='harbor-verification-package/v1','complete private manifest required')
    _require(manifest['source']==requested['source'] and manifest['tracker']['path']==requested['tracker_path'],
             'package belongs to a different original workbook or tracker')
    _require(manifest.get('trusted_public_key')==requested['trusted_public_key'], 'trusted key differs')
    root=retention._path(manifest['final_task_root'])
    bundle=retention._path(requested['bundle'])
    _require(not retention._overlap(root,bundle), 'private bundle overlaps final agent task')
    _require(not retention._inside(bundle.parent/(bundle.name+'.association.json'),root),
             'private association must be outside final task')
    for key in ('tracker','release_pointer','release_manifest'):
        _require(_binding(manifest[key]['path'])==manifest[key], 'current publication identity changed: '+key)
    directories=set(manifest['directories'])
    scratch=set(manifest['scratch_roots'])
    protected=[retention._path(x) for x in manifest['protected_roots']]+[root,bundle]
    protected_files={retention._path(x) for x in manifest['protected_files']}
    protected_files.update(retention._path(manifest[key]['path'])
                           for key in ('tracker','release_pointer','release_manifest'))
    protected_files.update((retention._path(requested['source']['path']),retention._path(requested['trusted_public_key'])))
    for value in requested['disposable_roots']:
        candidate=retention._path(value)
        _require(value in directories and value in scratch, 'scratch root not selected by owning workbook manifest')
        _require(not any(retention._overlap(candidate,p) for p in protected), 'scratch/protected roots overlap')
        _require(not any(retention._inside(p,candidate) for p in protected_files),
                 'scratch root contains protected code, source, key, or publication metadata')
    return protected,protected_files


def _association(requested,manifest,manifest_sha256):
    return dict(schema_version=SCHEMA,request=requested,manifest_sha256=manifest_sha256,
                tracker=manifest['tracker'],release_pointer=manifest['release_pointer'],
                release_manifest=manifest['release_manifest'],release_id=manifest['release_id'],
                final_task_root=manifest['final_task_root'])


def _walk_files(root):
    """Enumerate only inside an explicitly authorized root without symlinks."""
    with retention._directory(root) as parent_fd:
        entries=sorted(os.listdir(parent_fd))
        for name in entries:
            st=os.stat(name,dir_fd=parent_fd,follow_symlinks=False)
            path=root/name
            _require(not stat.S_ISLNK(st.st_mode), 'symlink in selected scratch root')
            if stat.S_ISDIR(st.st_mode):
                # Retention metadata from interrupted/completed execution is
                # exclusively owned by the durable cleanup journal.
                if name.startswith('.harbor-retention-'):
                    continue
                yield from _walk_files(path)
            else:
                _require(stat.S_ISREG(st.st_mode),'nonregular scratch entry')
                yield path


def _selection(requested,manifest,protected,protected_files):
    available={row['sha256']:row['size'] for row in manifest['objects']}
    captured={row['path']:row for row in manifest['files']}
    candidates=[];preserved=[]
    for root in requested['disposable_roots']:
        for path in _walk_files(retention._path(root)):
            if path in protected_files or path.name.endswith('.lock'):
                preserved.append(dict(path=str(path),reason='protected'))
                continue
            if str(path) not in captured:
                preserved.append(dict(path=str(path),reason='not captured by owning verification proof'))
                continue
            record=retention._record(path)
            bound=captured[str(path)]
            if (available.get(record['sha256'])!=record['identity']['size'] or
                    bound['sha256']!=record['sha256'] or bound['size']!=record['identity']['size']):
                preserved.append(dict(path=str(path),reason='no byte-identical packaged evidence'))
                continue
            candidates.append(dict(path=str(path),retained_path=str(Path(requested['bundle'])/'objects'/record['sha256'])))
    plan=None
    if candidates:
        plan=retention.plan_cleanup(candidates,disposable_roots=requested['disposable_roots'],
                                    protected_roots=[str(p) for p in protected if p!=Path(requested['bundle'])],
                                    retained_roots=[requested['bundle']])
    return dict(schema_version=PLAN_SCHEMA,request=requested,plan=plan,preserved=preserved)


def finalize(request):
    """Acquire the existing root-selected publication queue and workbook locks.

    Call only after a publisher releases its own copies of these locks. Holding
    both across capture, independent verification and deletion excludes other
    cooperating publication and workbook operations throughout custody transfer.
    """
    requested=_request(request)
    identities=set()
    with ExitStack() as stack:
        for key in ('queue_lock','slot_lock'):
            path=retention._path(requested[key])
            _require(not any(retention._inside(path,retention._path(root))
                             for root in requested['disposable_roots']), 'cleanup cannot own its coordination lock')
            parent_fd=stack.enter_context(retention._directory(path.parent))
            fd=os.open(path.name,os.O_RDWR|os.O_NOFOLLOW,dir_fd=parent_fd)
            stack.callback(os.close,fd)
            st=os.fstat(fd)
            _require(stat.S_ISREG(st.st_mode),'existing regular coordination lock required')
            identity=(st.st_dev,st.st_ino)
            _require(identity not in identities,'distinct queue and slot locks required')
            identities.add(identity)
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return _finalize_locked(requested)


run=finalize


def _finalize_locked(requested):
    """Return verified custody and cleanup status for a strictly promoted task.

    If a crash occurs after package creation but before association publication,
    freshly qualify and recapture the still-present original evidence. The
    existing package must equal that independently derived identity exactly.
    """
    from scripts import harbor_verification_package as package
    bundle=retention._path(requested['bundle'])
    association_name=bundle.name+'.association.json'
    journal=bundle.parent/(bundle.name+'.cleanup')
    with retention._directory(bundle.parent) as parent_fd:
        lock_fd=os.open(bundle.name+'.finalize.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600,dir_fd=parent_fd)
        try:
            _require(stat.S_ISREG(os.fstat(lock_fd).st_mode),'regular finalization lock required')
            fcntl.flock(lock_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            if retention._exists(parent_fd,association_name):
                association=retention._read_json(parent_fd,association_name)
                _require(association.get('schema_version')==SCHEMA and association.get('request')==requested,
                         'immutable association differs from requested finalization')
                manifest_hash=association['manifest_sha256']
            else:
                if retention._exists(parent_fd,bundle.name):
                    manifest_hash=package.recover_package(requested['source'],requested['tracker_path'],
                                                         trusted_public_key=requested['trusted_public_key'],bundle=str(bundle))
                else:
                    manifest_hash=package.build_package(requested['source'],requested['tracker_path'],
                                                        trusted_public_key=requested['trusted_public_key'],destination=str(bundle))
                association=None
            _require(type(manifest_hash) is str and re.fullmatch(r'[0-9a-f]{64}',manifest_hash),
                     'package builder returned invalid manifest identity')
            manifest=package.inspect_package(str(bundle),expected_manifest_sha256=manifest_hash)
            protected,protected_files=_closed_manifest(manifest,requested)
            selected_association=_association(requested,manifest,manifest_hash)
            if association is None:
                retention._atomic_json(parent_fd,association_name,selected_association)
            else:
                _require(association==selected_association,'current publication detached from retained association')
            try:
                os.mkdir(journal.name,0o700,dir_fd=parent_fd)
                os.fsync(parent_fd)
            except FileExistsError:
                pass
            with retention._directory(journal) as journal_fd:
                if retention._exists(journal_fd,'selection.json'):
                    selected=retention._read_json(journal_fd,'selection.json')
                    _require(type(selected) is dict and set(selected)=={'schema_version','request','plan','preserved'} and
                             selected['schema_version']==PLAN_SCHEMA and selected['request']==requested,
                             'cleanup selection differs')
                else:
                    selected=_selection(requested,manifest,protected,protected_files)
                    retention._atomic_json(journal_fd,'selection.json',selected)
                plan=selected['plan']
                if plan is None:
                    receipt=package.verify_package(str(bundle),expected_manifest_sha256=manifest_hash,verifier_image=requested['verifier_image'])
                    result=dict(deleted_files=0,cleanup=None,verification_receipt=receipt)
                else:
                    authorization=package.authorize_cleanup(str(bundle),expected_manifest_sha256=manifest_hash,verifier_image=requested['verifier_image'],plan=plan)
                    cleanup=retention.execute_cleanup(plan,authorization=authorization,journal_dir=journal)
                    receipt=package.validate_cleanup_authorization(authorization,plan)
                    result=dict(deleted_files=len(plan['files']),cleanup=cleanup,verification_receipt=receipt)
                result.update(association=str(bundle.parent/association_name),manifest_sha256=manifest_hash,
                              preserved=selected['preserved'],production_started=False)
                retention._atomic_json(journal_fd,'result.json',result)
                return result
        finally:
            os.close(lock_fd)

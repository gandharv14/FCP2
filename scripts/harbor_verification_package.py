"""Private, content-addressed release evidence and isolated proof replay.

The manifest preserves signed bytes and their original logical paths. It is not
an agent-facing task asset. Cleanup capabilities are issued only in this process
following successful Docker-isolated replay; they cannot be deserialized from a
JSON ``passed`` flag. The caller must pin the manifest and verifier image.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid

SCHEMA = 'harbor-verification-package/v1'
ROOT = Path(__file__).resolve().parents[1]
_HEX = re.compile(r'[0-9a-f]{64}\Z')
_CAPABILITIES = {}


class PackageError(ValueError):
    pass


def require(value, message):
    if not value:
        raise PackageError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block := stream.read(1024 * 1024):
            h.update(block)
    return h.hexdigest()


def _path(value):
    require(type(value) is str and value and not any(x in value for x in ('\x00', '\n', '\r')), 'invalid path')
    p = Path(value)
    require(p.is_absolute() and str(p) == value and '..' not in p.parts, 'canonical absolute path required')
    return p


def _safe(path):
    p = _path(str(path))
    for part in [p, *p.parents]:
        require(not part.is_symlink(), 'symlink in custody path')
    return p


def _inside(path, root):
    return path == root or root in path.parents


def _binding(row):
    require(type(row) is dict and {'path', 'sha256'} <= set(row), 'file binding required')
    p = _safe(_path(row['path']))
    require(type(row['sha256']) is str and _HEX.fullmatch(row['sha256']), 'invalid file digest')
    require(p.is_file() and digest(p) == row['sha256'], 'bound file changed: ' + str(p))


def _sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _publish_directory(source, destination):
    """Atomically publish without replacing even an empty concurrent target."""
    import ctypes
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform == 'darwin':
        rename = library.renamex_np
        rename.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
        arguments = (os.fsencode(source), os.fsencode(destination), 0x00000004)  # RENAME_EXCL
    elif sys.platform.startswith('linux') and hasattr(library, 'renameat2'):
        rename = library.renameat2
        rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
        arguments = (-100, os.fsencode(source), -100, os.fsencode(destination), 1)  # AT_FDCWD, RENAME_NOREPLACE
    else:
        raise PackageError('exclusive directory publication is unsupported on this host')
    rename.restype = ctypes.c_int
    if rename(*arguments):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))
    _sync_directory(Path(destination).parent)


def _atomic(path, raw):
    path = _safe(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.custody-', delete=False) as out:
        tmp = Path(out.name)
        try:
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
            os.link(tmp, path)  # no overwrite of an existing immutable checkpoint
        finally:
            tmp.unlink(missing_ok=True)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _code_files():
    """Use the same installed-code/configuration inventory as native capture."""
    from scripts.harbor_verification_capture import _code_files as inventory
    return inventory(ROOT)



def _clone_file(source, destination):
    """Attempt the trusted native clone operation; callers validate all bytes."""
    if sys.platform != 'darwin':
        return False
    completed = subprocess.run(['/bin/cp', '-c', str(source), str(destination)],
                               capture_output=True, timeout=300, check=False)
    return completed.returncode == 0


def _verified_copy(source, destination, expected_sha256):
    """Publish an independent exact copy, using APFS shared extents if available.

    A clone failure falls back inside a fresh private staging directory. Neither
    the source nor a preexisting destination is ever replaced. The temporary
    publication link is removed before return; source/destination never share an
    inode, so subsequent writes cannot propagate between evidence artifacts.
    """
    from scripts.harbor_retention import _record
    source, destination = _safe(Path(source)), _safe(Path(destination))
    require(type(expected_sha256) is str and _HEX.fullmatch(expected_sha256),
            'expected copy digest required')
    require(not destination.exists(), 'copy destination already exists')
    before = _record(source)
    require(before['sha256'] == expected_sha256, 'copy source differs from expected bytes')
    with tempfile.TemporaryDirectory(prefix='.evidence-copy-', dir=destination.parent) as work:
        temporary = Path(work) / 'copy'
        cloned = _clone_file(source, temporary)
        if not cloned:
            # A failed native copy may have left partial bytes. This one path is
            # inside our newly created private namespace, never a caller file.
            if temporary.exists() or temporary.is_symlink():
                require(not temporary.is_dir(), 'clone left a directory')
                temporary.unlink()
            shutil.copyfile(source, temporary)
        copied = _record(temporary)
        after = _record(source)
        require(after['identity'] == before['identity'] and after['sha256'] == before['sha256'],
                'copy source changed during transfer')
        require(copied['sha256'] == expected_sha256 and
                copied['identity']['size'] == before['identity']['size'], 'copied evidence differs')
        require((copied['identity']['device'], copied['identity']['inode']) !=
                (before['identity']['device'], before['identity']['inode']),
                'evidence copy must have an independent inode')
        with temporary.open('rb') as stream:
            os.fsync(stream.fileno())
        # Atomic no-overwrite publication, followed by removal of the private
        # temporary link. This never hard-links an original or custody object.
        os.link(temporary, destination, follow_symlinks=False)
        temporary.unlink()
        published = _record(destination)
        require(published['sha256'] == expected_sha256 and all(
                    published['identity'][key] == copied['identity'][key]
                    for key in ('device', 'inode', 'size', 'mtime_ns')),
                'published copy changed')
        final_source = _record(source)
        require(final_source['identity'] == before['identity'] and
                final_source['sha256'] == expected_sha256, 'copy source changed during publication')
        fd = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return {'method': 'apfs-clone' if cloned else 'copy', 'sha256': expected_sha256,
            'logical_bytes': copied['identity']['size']}


def build_package(source, tracker_path, trusted_public_key, destination):
    """Capture a qualified native release atomically; no deletion or execution."""
    from scripts.harbor_verification_capture import collect
    captured = collect(source, Path(tracker_path), str(trusted_public_key))
    destination = _safe(Path(destination))
    require(not destination.exists(), 'package destination already exists')
    require(not _inside(destination, Path(captured['final_task_root'])), 'private evidence cannot be placed in final task')
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.verification-', dir=destination.parent))
    try:
        (staging / 'objects').mkdir()
        files = {row['path']: dict(row) for row in captured['files']}
        verifier_files = []
        for p in _code_files():
            _safe(p)
            row = {'path': str(p), 'sha256': digest(p)}
            require(str(p) in files and files[str(p)]['sha256'] == row['sha256'],
                    'verifier code changed after native capture')
            verifier_files.append(row)
        key_path = str(Path(trusted_public_key))
        require(key_path in files and files[key_path]['sha256'] == digest(trusted_public_key),
                'trusted key changed after native capture')
        objects, rows = {}, []
        for row in sorted(files.values(), key=lambda r: r['path']):
            _binding(row)
            original = Path(row['path'])
            require(not _inside(destination, original) and not _inside(original, destination), 'package overlaps captured evidence')
            h = row['sha256']
            target = staging / 'objects' / h
            if h not in objects:
                _verified_copy(original, target, h)
                os.chmod(target, 0o444)
                with target.open('rb') as stream:
                    os.fsync(stream.fileno())
                objects[h] = {'sha256': h, 'size': target.stat().st_size}
            _binding(row)
            rows.append({'path': row['path'], 'sha256': h, 'size': objects[h]['size']})
        manifest = dict(captured, schema_version=SCHEMA, files=rows,
                        objects=sorted(objects.values(), key=lambda r: r['sha256']),
                        trusted_public_key=str(Path(trusted_public_key)), verifier_files=verifier_files)
        manifest['protected_files'] = sorted(set(captured.get('protected_files', [])) |
                                              {r['path'] for r in verifier_files} | {str(Path(trusted_public_key))})
        raw = canonical(manifest)
        _atomic(staging / 'manifest.json', raw)
        h = hashlib.sha256(raw).hexdigest()
        inspect_package(staging, h)
        require(not destination.exists(), 'package destination appeared')
        _sync_directory(staging / "objects")
        _sync_directory(staging)
        _publish_directory(staging, destination)
        fd = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        return h
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def recover_package(source, tracker_path, trusted_public_key, bundle):
    """Recover a rename-before-association crash using fresh native proof.

    Never derive authority from the existing bundle's own manifest. Rebuild a
    temporary package from the still-present original evidence through the same
    native qualification and capture path, then require exact manifest/object
    equality with that newly established identity. Only our new temporary copy
    is discarded; the unassociated package and original evidence stay intact.
    """
    bundle = _safe(Path(bundle))
    require(bundle.is_dir(), 'unassociated package directory is missing')
    association = _safe(bundle.parent / (bundle.name + '.association.json'))
    require(not association.exists(), 'associated package must use its retained manifest identity')
    with tempfile.TemporaryDirectory(prefix='.verification-recapture-', dir=bundle.parent) as temporary:
        fresh = Path(temporary) / 'package'
        expected = build_package(source, tracker_path, trusted_public_key, fresh)
        inspect_package(bundle, expected)
        require(not association.exists(), 'package association appeared during recovery')
    return expected


def inspect_package(bundle, expected_manifest_sha256):
    """Check the complete package, including membership, before any replay."""
    bundle = _safe(Path(bundle))
    require(type(expected_manifest_sha256) is str and _HEX.fullmatch(expected_manifest_sha256), 'root-pinned manifest digest required')
    manifest_path = _safe(bundle / 'manifest.json')
    require(manifest_path.is_file() and digest(manifest_path) == expected_manifest_sha256, 'manifest changed')
    value = json.loads(manifest_path.read_bytes())
    require(value.get('schema_version') == SCHEMA, 'unsupported package schema')
    require({x.name for x in bundle.iterdir()} == {'objects', 'manifest.json'}, 'unexpected package member')
    object_dir = _safe(bundle / 'objects')
    require(object_dir.is_dir(), 'objects directory missing')
    objects = {}
    for row in value['objects']:
        require(type(row) is dict and set(row) == {'sha256', 'size'} and
                type(row['sha256']) is str and _HEX.fullmatch(row['sha256']) and
                type(row['size']) is int and row['size'] >= 0, 'invalid object record')
        h = row['sha256']
        require(h not in objects, 'duplicate object')
        obj = _safe(object_dir / h)
        require(obj.is_file() and obj.stat().st_size == row['size'] and digest(obj) == h, 'object changed: ' + h)
        objects[h] = row['size']
    require({p.name for p in object_dir.iterdir()} == set(objects), 'object inventory differs')
    paths = {}
    for row in value['files']:
        require(type(row) is dict and set(row) == {'path', 'sha256', 'size'}, 'invalid mapped file')
        p = _path(row['path'])
        require(str(p) not in paths and row['sha256'] in objects and objects[row['sha256']] == row['size'], 'invalid mapped object')
        paths[str(p)] = row['sha256']
    require(set(objects) == set(paths.values()), 'unreferenced object')
    dirs = [_path(p) for p in value['directories']]
    require(len(set(dirs)) == len(dirs), 'duplicate directory')
    for p in [*dirs, *map(Path, paths)]:
        require(not any(str(parent) in paths for parent in p.parents), 'file used as directory')
    require(not any(str(p) in paths for p in dirs), 'file/directory collision')
    for name in ('tracker', 'release_pointer', 'release_manifest'):
        row = value[name]
        require(paths.get(row['path']) == row['sha256'], 'missing publication anchor')
    require(value['trusted_public_key'] in paths, 'missing trusted key')
    for row in value['verifier_files']:
        require(paths.get(row['path']) == row['sha256'], 'missing verifier code')
    for p in value['protected_roots'] + value.get('protected_files', []):
        _path(p)
    require(not _inside(bundle, _path(value['final_task_root'])), 'private package inside task')
    return value


def materialize_package(bundle, expected_manifest_sha256, destination):
    """Restore bytes into a NEW namespace, never into their original paths."""
    value = inspect_package(bundle, expected_manifest_sha256)
    destination = _safe(Path(destination))
    bundle_path = _safe(Path(bundle))
    require(not _inside(destination, bundle_path) and not _inside(bundle_path, destination),
            'namespace overlaps private package')
    original_roots = set(value['directories'] + value['protected_roots'] + value.get('scratch_roots', []) +
                         [value['final_task_root'], value['verifier_root']])
    original_roots.update(row['path'] for row in value['files'])
    require(not any(_inside(destination, _path(p)) or _inside(_path(p), destination)
                    for p in original_roots), 'namespace overlaps original evidence')
    require(not destination.exists(), 'namespace destination must not exist')
    destination.mkdir(parents=True)
    for directory in value['directories']:
        (destination / _path(directory).relative_to('/')).mkdir(parents=True, exist_ok=True)
    for row in value['files']:
        target = destination / _path(row['path']).relative_to('/')
        target.parent.mkdir(parents=True, exist_ok=True)
        # Independent copies prevent writable materialization from modifying custody.
        _verified_copy(Path(bundle) / 'objects' / row['sha256'], target, row['sha256'])
        os.chmod(target, 0o444)
    return value


def _check_installed_verifier(value):
    captured_root = _path(value['verifier_root'])
    expected = {str(p.relative_to(ROOT)): digest(p) for p in _code_files()}
    captured = {}
    for row in value['verifier_files']:
        p = _path(row['path'])
        require(captured_root in p.parents, 'foreign verifier path')
        captured[str(p.relative_to(captured_root))] = row['sha256']
    require(captured == expected, 'installed verifier differs from package; explicit migration required')


def _mounts(namespace):
    # Mount only captured namespace roots. Never bind host originals, /, /proc,
    # /dev, Docker's socket, or executable system directories over the image.
    allowed = {'Users', 'home', 'opt', 'workspace', 'mnt', 'private', 'tmp', 'Volumes'}
    result = []
    namespace = _safe(Path(namespace))
    for p in sorted(namespace.iterdir()):
        _safe(p)
        require(p.is_dir(), 'namespace root must be a directory')
        if p.name in allowed:
            targets = [(p, '/' + p.name)]
        elif p.name == 'etc' and {x.name for x in p.iterdir()} <= {'harbor'}:
            targets = [(p / 'harbor', '/etc/harbor')]
        else:
            raise PackageError('unsupported original namespace root: ' + p.name)
        for source, target in targets:
            require(',' not in str(source), 'comma in mount path')
            result += ['--mount', f'type=bind,src={source},dst={target},readonly']
    return result


def verify_package(bundle, expected_manifest_sha256, verifier_image):
    """Replay owning financial/publication validators in an isolated container.

    ``verifier_image`` is a locally installed, operator-approved image ID, with
    Python 3.12 at /usr/local/bin/python and the documented pipeline dependencies.
    This function neither builds/pulls images nor runs an agent solve.
    """
    require(type(verifier_image) is str and re.fullmatch(r'sha256:[0-9a-f]{64}', verifier_image), 'pinned local verifier image ID required')
    bundle = _safe(Path(bundle))
    require(',' not in str(bundle), 'comma in private package mount path')
    value = inspect_package(bundle, expected_manifest_sha256)
    _check_installed_verifier(value)
    docker = shutil.which('docker')
    require(docker is not None, 'isolated verifier requires Docker')
    image = subprocess.run([docker, 'image', 'inspect', verifier_image], capture_output=True, text=True, timeout=30, check=False)
    require(image.returncode == 0, 'configured verifier image is unavailable')
    inspected = json.loads(image.stdout)
    require(len(inspected) == 1 and inspected[0]['Id'] == verifier_image and inspected[0]['Os'] == 'linux', 'verifier image identity differs')
    with tempfile.TemporaryDirectory(prefix='harbor-proof-') as tmp:
        namespace = Path(tmp).resolve() / 'namespace'
        materialize_package(bundle, expected_manifest_sha256, namespace)
        container_name = 'harbor-private-proof-' + uuid.uuid4().hex
        command = [docker, 'run', '--rm', '--name', container_name, '--pull', 'never', '--network', 'none', '--read-only',
                   '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--pids-limit', '128',
                   '--memory', '8g', '--cpus', '2', '--tmpfs', '/run:rw,nosuid,nodev,size=128m',
                   '--env', 'TMPDIR=/run', '--env', 'PYTHONDONTWRITEBYTECODE=1',
                   '--entrypoint', '/usr/local/bin/python', *_mounts(namespace),
                   '--mount', f'type=bind,src={bundle},dst=/custody,readonly',
                   verifier_image, str(Path(value['verifier_root']) / 'scripts/harbor_verification_package.py'),
                   '--replay', expected_manifest_sha256]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=900, check=False)
        except BaseException:
            # Killing the Docker client does not stop a daemon-owned container.
            # Only this invocation's unguessable name is eligible for removal.
            try:
                subprocess.run([docker, 'rm', '--force', container_name],
                               capture_output=True, text=True, timeout=30, check=False)
            except (OSError, subprocess.SubprocessError):
                pass
            raise
        require(completed.returncode == 0, 'isolated proof replay failed: ' + completed.stderr[-2000:])
        try:
            outcome = json.loads(completed.stdout)
        except (ValueError, TypeError) as error:
            raise PackageError('isolated verifier returned malformed result') from error
        require(outcome.get('state') == 'PROMOTED' and outcome.get('verified_promoted') is True and
                outcome.get('release_id') == value['release_id'] and
                outcome.get('revalidation_required') is False, 'isolated release proof did not qualify')
    inspect_package(bundle, expected_manifest_sha256)
    return {'schema_version': 'harbor-private-verification/v1', 'manifest_sha256': expected_manifest_sha256,
            'verifier_image': verifier_image, 'release_id': value['release_id'],
            'isolated': True, 'qualification': outcome, 'command_sha256': hashlib.sha256(canonical(command)).hexdigest()}


@dataclass(frozen=True)
class CleanupAuthorization:
    """Process-local capability, issued only by authorize_cleanup after replay."""
    token: str


def _directory_identity(root):
    """Cheap immutable-custody check, including membership and ctime.

    Hashes establish initial custody. These identities detect later mutations
    without rereading a multi-gigabyte package for every individual deletion.
    Cooperative writers must respect the enclosing workbook/publication locks.
    """
    root = _safe(Path(root))
    require(root.is_dir(), 'custody directory missing')
    result = {}
    for p in [root, *sorted(root.rglob('*'))]:
        _safe(p)
        st = p.lstat()
        require(stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode), 'nonregular custody entry')
        result[str(p)] = (st.st_dev, st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
    return result


def _live_release(value):
    for name in ('tracker', 'release_pointer', 'release_manifest'):
        _binding(value[name])
    _binding(value['source'])
    mapped = {row['path']: row for row in value['files']}
    for name in value.get('protected_files', []):
        require(name in mapped, 'protected file missing from package')
        _binding(mapped[name])
    root = _safe(_path(value['final_task_root']))
    expected = {row['path']: row['sha256'] for row in value['files']
                if root in _path(row['path']).parents}
    require(expected and root.is_dir(), 'live final task missing')
    actual = {}
    for p in sorted(root.rglob('*')):
        _safe(p)
        if not p.is_dir():
            require(p.is_file(), 'nonregular live task member')
            actual[str(p)] = digest(p)
    require(actual == expected, 'live final task differs from private proof')


def qualify_retained_release(bundle, expected_manifest_sha256, verifier_image, *, source, tracker_path, trusted_public_key):
    """Explicit archive qualification after original scratch has been removed."""
    value = inspect_package(bundle, expected_manifest_sha256)
    require(value['source'] == source and value['tracker']['path'] == str(tracker_path) and
            value['trusted_public_key'] == str(trusted_public_key), 'private package belongs to another release')
    _live_release(value)
    receipt = verify_package(bundle, expected_manifest_sha256, verifier_image)
    _live_release(value)
    import xl_release_publication as release
    result = dict(receipt['qualification'])
    if result.get('output_path') is not None:
        result['original_stage'] = dict(path=result['output_path'], sha256=result['output_sha256'])
    result.update(output_path=value['final_task_root'],
                  output_sha256=release._tree_hash(Path(value['final_task_root']))[0],
                  private_verification=receipt)
    return result


def _file_identities(paths):
    result = {}
    for p in paths:
        p = _safe(Path(p))
        st = p.lstat()
        require(stat.S_ISREG(st.st_mode), 'regular protected evidence required')
        result[str(p)] = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
    return result


def authorize_cleanup(bundle, expected_manifest_sha256, verifier_image, plan):
    from scripts.harbor_retention import plan_sha256
    before = _directory_identity(bundle)
    initial = inspect_package(bundle, expected_manifest_sha256)
    protected_live = [Path(initial['source']['path']), Path(initial['trusted_public_key']),
                      *map(Path, initial.get('protected_files', [])), *_code_files()]
    live_before = _file_identities(protected_live)
    receipt = verify_package(bundle, expected_manifest_sha256, verifier_image)
    value = inspect_package(bundle, expected_manifest_sha256)
    require(_directory_identity(bundle) == before, 'package changed during authorization')
    final_before = _directory_identity(value['final_task_root'])
    _live_release(value)
    require(_directory_identity(value['final_task_root']) == final_before, 'final task changed during authorization')
    require(_file_identities(protected_live) == live_before, 'source or verifier changed during authorization')
    token = uuid.uuid4().hex
    authorization = CleanupAuthorization(token)
    _CAPABILITIES[token] = {'authorization': authorization, 'bundle': str(bundle),
                            'manifest_sha256': expected_manifest_sha256, 'manifest': value,
                            'plan_sha256': plan_sha256(plan), 'receipt': receipt,
                            'bundle_identity': before, 'final_identity': final_before, 'live_identity': live_before}
    try:
        validate_cleanup_authorization(authorization, plan)
    except BaseException:
        _CAPABILITIES.pop(token, None)
        raise
    return authorization


def validate_cleanup_authorization(authorization, plan):
    from scripts.harbor_retention import plan_sha256
    require(type(authorization) is CleanupAuthorization, 'closed cleanup capability required')
    state = _CAPABILITIES.get(authorization.token)
    require(state is not None and state['authorization'] is authorization, 'unissued cleanup capability')
    require(plan_sha256(plan) == state['plan_sha256'], 'cleanup plan changed')
    bundle = _safe(Path(state['bundle']))
    require(_directory_identity(bundle) == state['bundle_identity'], 'private custody changed after replay')
    value = state['manifest']
    for name in ('tracker', 'release_pointer', 'release_manifest'):
        _binding(value[name])
    current_live = [Path(value['source']['path']), Path(value['trusted_public_key']),
                    *map(Path, value.get('protected_files', [])), *_code_files()]
    require(_file_identities(current_live) == state['live_identity'], 'source, trust anchor or verifier changed after replay')
    require(_directory_identity(value['final_task_root']) == state['final_identity'], 'final task changed after replay')
    files = {row['path']: row['sha256'] for row in value['files']}
    protected = [_path(p) for p in value['protected_roots']] + [_path(value['final_task_root'])]
    protected_files = set(value.get('protected_files', []))
    require(plan['retained_roots'] == [str(bundle)], 'retained root must be private package')
    scratch = {_path(p) for p in value['scratch_roots']}
    require(set(map(_path, plan['disposable_roots'])) <= scratch, 'foreign scratch roots')
    for row in plan['files']:
        p = _path(row['path'])
        require(any(r in p.parents for r in scratch), 'file outside selected workbook scratch')
        require(not any(_inside(p, r) for r in protected) and str(p) not in protected_files, 'protected evidence cannot be deleted')
        h = files.get(str(p))
        require(h is not None and row['source']['sha256'] == h and
                row['retained_path'] == str(bundle / 'objects' / h), 'cleanup file has no packaged provenance')
    return state['receipt']


def _validate_captured_scope(value, captured):
    """Derive custody geometry from the owning capture, never manifest claims."""
    from scripts.harbor_verification_capture import SCHEMA as CAPTURE_SCHEMA
    require(type(captured) is dict and captured.get('schema_version') == CAPTURE_SCHEMA,
            'owning capture returned an invalid schema')
    require(set(value) == set(captured) | {'objects', 'trusted_public_key', 'verifier_files'},
            'private package metadata differs from owning capture')
    for key, expected in captured.items():
        if key == 'schema_version':
            require(value[key] == SCHEMA, 'private package schema differs')
        elif key == 'files':
            actual_files = [{'path': row['path'], 'sha256': row['sha256']} for row in value[key]]
            expected_files = [{'path': row['path'], 'sha256': row['sha256']} for row in expected]
            require(actual_files == expected_files,
                    'private package file closure differs from owning capture')
        else:
            require(value[key] == expected,
                    'private package custody scope differs from owning capture: ' + key)


def _replay(expected):
    require(sys.version_info[:2] == (3, 12), 'verification runtime must be Python 3.12')
    value = inspect_package(Path('/custody'), expected)
    _check_installed_verifier(value)
    from scripts.batch.harbor_promotion_evidence import validate
    from scripts.batch.harbor_runtime_readiness import RuntimeProducerIdentity
    from scripts.harbor_verification_capture import collect
    producer = RuntimeProducerIdentity(**value['producer_identity'])
    result = validate(value['source'], Path(value['tracker']['path']),
                      trusted_public_key=value['trusted_public_key'],
                      runtime_producer=producer)
    require(result.get('verified_promoted') is True, 'owning verifier rejected package')
    captured = collect(value['source'], Path(value['tracker']['path']),
                       value['trusted_public_key'], runtime_producer=producer)
    _validate_captured_scope(value, captured)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
    require(len(sys.argv) == 3 and sys.argv[1] == '--replay', 'internal isolated replay entry point only')
    _replay(sys.argv[2])

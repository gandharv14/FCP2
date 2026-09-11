"""Opt-in real Docker controls; never a successful financial-task fixture.

Use the supported ``scripts/test_pipeline.py --host-controls`` entry point.
FCP_PRIVATE_VERIFIER_TEST_IMAGE may select an already installed immutable image
with verifier dependencies. No test builds, pulls, or executes agent tasks.
"""
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from scripts import harbor_verification_package as package

pytestmark = pytest.mark.host_controls
BASE_IMAGE = 'sha256:adbc7c33e0abc183557d1d14ce5eb5d261aaadff5451c81a8db636b3ebefcdf6'


@pytest.fixture
def docker_image():
    docker = shutil.which('docker')
    if docker is None:
        pytest.skip('Docker is not configured for host-control integration')
    image = os.environ.get('FCP_PRIVATE_VERIFIER_TEST_IMAGE', BASE_IMAGE)
    assert re.fullmatch(r'sha256:[0-9a-f]{64}', image), 'test image must be an immutable local ID'
    result = subprocess.run([docker, 'image', 'inspect', image], capture_output=True, text=True, timeout=30)
    if result.returncode:
        if 'FCP_PRIVATE_VERIFIER_TEST_IMAGE' in os.environ:
            pytest.fail('explicit private verifier test image is unavailable')
        pytest.skip('pinned local base image or Docker daemon is unavailable; no pull attempted')
    inspected = json.loads(result.stdout)
    assert len(inspected) == 1 and inspected[0]['Id'] == image and inspected[0]['Os'] == 'linux'
    return docker, image


def synthetic_package(tmp_path, *, include_verifier=False):
    """Storage-only fixture with an intentionally invalid tracker."""
    original = tmp_path / 'original/source.xlsx'
    original.parent.mkdir()
    original.write_bytes(b'exact synthetic original bytes\x00\xff')
    sentinel = tmp_path / 'original/host-only-sentinel'
    sentinel.write_text('This host file must never be visible in the namespace.')
    tracker = tmp_path / 'state/tracker.json'
    tracker.parent.mkdir()
    tracker.write_text('{}')
    key = tmp_path / 'state/public.pem'
    key.write_text('synthetic public key; never used for accepted evidence')
    paths = [original, tracker, key]
    verifier_paths = package._code_files() if include_verifier else []
    paths.extend(verifier_paths)
    bundle = tmp_path / 'bundle'
    (bundle / 'objects').mkdir(parents=True)
    rows = []
    objects = {}
    for path in paths:
        digest = package.digest(path)
        data = path.read_bytes()
        if digest not in objects:
            (bundle / 'objects' / digest).write_bytes(data)
            objects[digest] = {'sha256': digest, 'size': len(data)}
        rows.append({'path': str(path), 'sha256': digest, 'size': len(data)})
    by_path = {row['path']: row for row in rows}
    binding = lambda path: {key: by_path[str(path)][key] for key in ('path', 'sha256')}
    manifest = {
        'schema_version': package.SCHEMA, 'source': {'workbook_id': '0001', **binding(original)},
        'tracker': binding(tracker), 'release_pointer': binding(tracker), 'release_manifest': binding(tracker),
        'release_id': 'a' * 64, 'final_task_root': str(tmp_path / 'final-task'),
        'verifier_root': str(package.ROOT), 'trusted_public_key': str(key),
        'producer_identity': {'python_executable': '/captured/python', 'docker_executable': '/captured/docker'},
        'files': sorted(rows, key=lambda row: row['path']),
        'objects': sorted(objects.values(), key=lambda row: row['sha256']),
        'directories': [str(original.parent), str(tracker.parent)],
        'verifier_files': [binding(path) for path in verifier_paths],
        'protected_files': [str(original), str(key), str(tracker)],
        'protected_roots': [str(tmp_path / 'final-task')], 'scratch_roots': [],
    }
    raw = package.canonical(manifest)
    (bundle / 'manifest.json').write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    package.inspect_package(bundle, digest)
    expected_bytes = original.read_bytes()
    original.unlink()
    return bundle, digest, original, sentinel, expected_bytes


def test_original_path_namespace_is_readonly_offline_and_has_no_host_fallback(tmp_path, docker_image):
    docker, image = docker_image
    bundle, digest, original, sentinel, expected = synthetic_package(tmp_path)
    namespace = tmp_path / 'namespace'
    package.materialize_package(bundle, digest, namespace)
    assert not original.exists() and sentinel.exists()
    probe = '''import errno,fcntl,hashlib,json,socket,struct
from pathlib import Path
original=Path(%r);sentinel=Path(%r)
assert hashlib.sha256(original.read_bytes()).hexdigest()==%r
assert not sentinel.exists(), 'host file leaked into namespace'
assert not Path('/var/run/docker.sock').exists() and not Path('/run/docker.sock').exists()
with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as control:
 for _,name in socket.if_nameindex():
  flags=struct.unpack_from('H',fcntl.ioctl(control.fileno(),0x8913,struct.pack('256s',name.encode())),16)[0]
  assert name=='lo' or not flags&1, 'UP non-loopback interface present'
assert len(Path('/proc/net/route').read_text().splitlines())==1, 'network route present'
try:
 original.write_bytes(b'changed')
except OSError as error:
 assert error.errno==errno.EROFS, error
else:
 raise AssertionError('evidence mount was writable')
try:
 socket.create_connection(('198.51.100.1',9),timeout=.2)
except OSError:
 pass
else:
 raise AssertionError('external connection succeeded')
print(json.dumps({'read_exact_bytes':True,'readonly':True,'host_fallback':False,'network':False,'docker_socket':False}))
''' % (str(original), str(sentinel), hashlib.sha256(expected).hexdigest())
    command = [docker, 'run', '--rm', '--pull', 'never', '--network', 'none', '--read-only',
               '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--pids-limit', '128',
               '--memory', '256m', '--tmpfs', '/run:rw,nosuid,nodev,size=16m',
               '--env', 'TMPDIR=/run', '--entrypoint', '/usr/local/bin/python',
               *package._mounts(namespace), image, '-I', '-c', probe]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'read_exact_bytes': True, 'readonly': True,
                                        'host_fallback': False, 'network': False, 'docker_socket': False}
    assert not original.exists() and sentinel.exists()
    assert package.inspect_package(bundle, digest)['source']['sha256'] == hashlib.sha256(expected).hexdigest()


def test_actual_isolated_financial_validator_rejects_malformed_release(tmp_path, docker_image):
    _, image = docker_image
    bundle, digest, original, sentinel, _ = synthetic_package(tmp_path, include_verifier=True)
    assert not original.exists()
    # No validator or subprocess replacement: the actual container entry point
    # must reach the current tracker schema check and reject this malformed row.
    with pytest.raises(package.PackageError) as failure:
        package.verify_package(bundle, digest, image)
    message = str(failure.value)
    if 'ModuleNotFoundError' in message and 'FCP_PRIVATE_VERIFIER_TEST_IMAGE' not in os.environ:
        pytest.skip('base image lacks verifier dependencies: ' + message[-400:])
    # The wrapper deliberately retains only stderr's final 2,000 characters;
    # the exception class can be truncated for an empty tracker. These two
    # independent schema errors establish actual owning validation was reached.
    assert '/schema_version: expected version2' in message, message
    assert '/execution_state: invalid closed execution state' in message, message
    assert not original.exists() and sentinel.exists()

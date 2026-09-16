"""Install the verified privilege-separated runtime in every new build context.

The platform/bootstrap remains root; the model and all its tools run as UID
60100. Only public workbooks/assumptions are copied into the solver workspace.
Research files and the research service remain behind root-only directories.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import struct

SCRIPTS = Path(__file__).resolve().parent
BINARY_SHA256 = '110ee88f21d7770164100dfadb424f07139fab60753c6ebba14d65182ff932a0'
C_SOURCE_SHA256 = '6f46de1fefdcafa248628e1684d597b8b6685764bba904ca8c5dd8a3f4f0b7b1'
MANIFEST = 'isolation-scaffold.json'
DOCKERFILE = '''ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER root
RUN rm -rf /opt/fcp/tasks && mkdir -p /opt/fcp/tasks && chmod 0700 /opt/fcp/tasks \\
    && rm -f /app/*-inputs.xlsx /app/*-inputs.xlsm
COPY --chown=0:0 tasks /opt/fcp/tasks
COPY --chown=0:0 isolation-rootfs/ /
COPY --chown=0:0 fcp-inactivity-watchdog /usr/local/bin/fcp-inactivity-watchdog
RUN find /opt/fcp/tasks -type d -exec chmod 0700 {} + \\
    && find /opt/fcp/tasks -type f -exec chmod 0600 {} + \\
    && chmod 0755 /usr/local/bin/fcp-inactivity-watchdog
WORKDIR /app
'''


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def runtime_files():
    specs = [
        ('recursion_hardening_runtime.py', 'usr/local/bin/fcp-isolated-runtime', 0o755),
        ('recursion_isolation_probe.py', 'usr/local/bin/fcp-isolation-probe', 0o755),
        ('recursion_model_identity.py', 'usr/local/bin/fcp_model_identity.py', 0o644),
        ('recursion_solver_python.py', 'usr/local/bin/fcp_solver_python.py', 0o644),
        ('recursion_demote_exec.c', 'usr/local/share/fcp-demote-exec.c', 0o644),
        ('runtime/fcp-demote-exec-linux-amd64', 'usr/local/libexec/fcp-demote-exec.template', 0o555),
    ]
    binary = SCRIPTS / specs[-1][0]
    assert sha(binary) == BINARY_SHA256, 'Unverified native demotion helper'
    assert sha(SCRIPTS / 'recursion_demote_exec.c') == C_SOURCE_SHA256, 'Rebuild and verify native helper after C source changes'
    data = binary.read_bytes()
    assert data[:6] == b'\x7fELF\x02\x01' and struct.unpack_from('<H', data, 18)[0] == 62
    entries = [(destination, (SCRIPTS / source).read_bytes(), mode) for source, destination, mode in specs]
    for name, model in [('fcp-wrapper', 'claude'), ('fcp-opencode-wrapper', 'opencode')]:
        entries.append(('usr/local/bin/' + name,
                        f'#!/bin/sh\nset -eu\nexec python /usr/local/bin/fcp-isolated-runtime {model} "$@"\n'.encode(), 0o755))
    return entries


def install(root):
    root = Path(root)
    assert not root.is_symlink() and (root / 'tasks').is_dir() and not (root / 'tasks').is_symlink(), 'Build context must contain regular task directories'
    for path in (root / 'tasks').rglob('*'):
        assert not path.is_symlink(), 'Task-asset symlinks are forbidden'
    for path in [root / 'isolation-rootfs', *(root / 'isolation-rootfs').rglob('*')]:
        assert not path.is_symlink(), 'Runtime-payload symlinks are forbidden'
    records = []
    for destination, data, mode in runtime_files():
        relative = 'isolation-rootfs/' + destination
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(mode)
        records.append({'path': relative, 'destination': destination,
                        'sha256': hashlib.sha256(data).hexdigest(), 'mode': mode, 'uid': 0, 'gid': 0})
        # Keep the established top-level build-context names consistent too.
        if Path(destination).name in ('fcp-wrapper', 'fcp-opencode-wrapper'):
            public = root / Path(destination).name
            public.write_bytes(data)
            public.chmod(mode)
    (root / 'Dockerfile').write_text(DOCKERFILE)
    for path in [root / 'tasks', *(root / 'tasks').rglob('*')]:
        path.chmod(0o700 if path.is_dir() else 0o600)
    manifest = {'schemaVersion': 1, 'solverUid': 60100, 'researchOwnerUid': 0,
                'taskDirectoryMode': 0o700, 'taskFileMode': 0o600,
                'dockerfileSha256': sha(root / 'Dockerfile'), 'files': records}
    (root / MANIFEST).write_text(json.dumps(manifest, indent=2) + '\n')
    return validate(root)


def validate(root):
    root = Path(root)
    assert (root / MANIFEST).is_file(), 'Legacy build context lacks research isolation; regenerate the context before publishing'
    manifest = json.loads((root / MANIFEST).read_text())
    assert manifest['schemaVersion'] == 1 and manifest['solverUid'] == 60100 and manifest['researchOwnerUid'] == 0
    assert manifest['taskDirectoryMode'] == 0o700 and manifest['taskFileMode'] == 0o600
    assert (root / 'Dockerfile').read_text() == DOCKERFILE
    assert manifest['dockerfileSha256'] == sha(root / 'Dockerfile')
    expected = {destination: (hashlib.sha256(data).hexdigest(), mode) for destination, data, mode in runtime_files()}
    assert len(manifest['files']) == len(expected)
    assert {item['destination'] for item in manifest['files']} == set(expected)
    for item in manifest['files']:
        assert item['path'] == 'isolation-rootfs/' + item['destination']
        assert item['uid'] == item['gid'] == 0
        assert (item['sha256'], item['mode']) == expected[item['destination']]
        path = root / item['path']
        assert not path.is_symlink() and sha(path) == item['sha256']
        assert path.stat().st_mode & 0o7777 == item['mode']
    for path in [root / 'isolation-rootfs', *(root / 'isolation-rootfs').rglob('*')]:
        assert not path.is_symlink(), 'Runtime-payload symlinks are forbidden'
    actual = {p.relative_to(root).as_posix() for p in (root / 'isolation-rootfs').rglob('*') if p.is_file()}
    assert actual == {item['path'] for item in manifest['files']}, 'Unexpected runtime payload files'
    for name in ('fcp-wrapper', 'fcp-opencode-wrapper'):
        assert (root / name).read_bytes() == (root / 'isolation-rootfs/usr/local/bin' / name).read_bytes()
    for path in [root / 'tasks', *(root / 'tasks').rglob('*')]:
        assert not path.is_symlink()
        assert path.stat().st_mode & 0o7777 == (0o700 if path.is_dir() else 0o600)
    return manifest

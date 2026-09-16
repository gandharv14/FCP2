"""Remote stdin installer; strictly validates tar members and every code byte."""
from __future__ import annotations
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import socket
import subprocess
import sys
import tarfile
import time


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_members(archive):
    seen = set()
    for member in archive.getmembers():
        name = PurePosixPath(member.name)
        if not member.isfile() or name.is_absolute() or '..' in name.parts or member.name in seen:
            raise ValueError('unsafe or duplicate bundle member: '+member.name)
        seen.add(member.name)
        yield member


def verify_code(code, release):
    manifest = json.loads((code/'FLEET_CODE_MANIFEST.json').read_text())
    computed = hashlib.sha256(json.dumps(manifest['files'], sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    if computed != release or manifest['release'] != release:
        raise ValueError('release manifest hash mismatch')
    actual = {p.relative_to(code).as_posix() for p in code.rglob('*') if p.is_file()}
    if actual != set(manifest['files'])|{'FLEET_CODE_MANIFEST.json'}:
        raise ValueError('unexpected or missing release files')
    for name, record in manifest['files'].items():
        path = code/name
        if path.is_symlink() or sha(path) != record['sha256'] or path.stat().st_size != record['size_bytes'] or path.stat().st_mode & 0o777 != record['mode']:
            raise ValueError('code hash mismatch: '+name)
    pipeline = json.loads((code/'.pipeline-code.json').read_text())
    expected_pipeline = {name: row['sha256'] for name, row in manifest['files'].items() if name != '.pipeline-code.json'}
    if pipeline != expected_pipeline or hashlib.sha256(json.dumps(pipeline, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest() != manifest['pipeline_code_hash']:
        raise ValueError('pipeline fingerprint mismatch')
    return manifest


def main(config):
    os.umask(0o077)
    root = Path(config['root']).expanduser()
    archive = Path(config['archive']).expanduser()
    release = root/'releases'/config['release']
    if sha(archive) != config['sha256']:
        raise ValueError('uploaded archive hash mismatch')
    if sys.version_info[:2] != (3, 12):
        raise ValueError('Python 3.12 required')
    release.mkdir(parents=True, exist_ok=True)
    code = release/'code'
    if not code.exists():
        staging = release/f'code.staging.{os.getpid()}'
        staging.mkdir()
        with tarfile.open(archive) as stream:
            stream.extractall(staging, members=safe_members(stream), filter='data')
        verify_code(staging, config['release'])
        staging.rename(code)
    manifest = verify_code(code, config['release'])
    venv = release/'venv'
    subprocess.run([sys.executable, '-m', 'venv', str(venv)], check=True)
    python = venv/'bin/python'
    piplog = release/f'dependencies-{time.time_ns()}.log'
    with piplog.open('w') as stream:
        install = subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check', '-r', str(code/('requirements-linux-lock.txt' if (code/'requirements-linux-lock.txt').exists() else 'requirements-lock.txt'))], stdout=stream, stderr=subprocess.STDOUT)
    if install.returncode:
        # pip has no credentials here; log retained remotely, surface only tail.
        print(piplog.read_text()[-8000:], flush=True)
        raise ValueError('dependency installation failed')
    subprocess.run([str(python), '-m', 'pip', 'check'], check=True)
    freeze = subprocess.check_output([str(python), '-m', 'pip', 'freeze', '--all'], text=True)
    (release/'requirements-linux-resolved.txt').write_text(freeze)
    registry_probe = 'import json; from xl_inventory_approval import load_pinned_registry; r=load_pinned_registry(); print(json.dumps({"registry_sha256":r["registry_sha256"],"approved_inventories":len(r["approvals"])}))'
    registry = json.loads(subprocess.check_output([str(python), '-B', '-c', registry_probe], cwd=code, env={**os.environ, 'PYTHONDONTWRITEBYTECODE':'1'}, text=True))
    probe = 'from scripts import corpus; print("pipeline code hash: "+corpus.code_hash()); import openpyxl,yaml,fastmcp,httpx,pytest; import scripts.batch.harbor_instruction_worker; print("pipeline imports passed")'
    subprocess.run([str(python), '-B', '-c', probe], cwd=code, env={**os.environ, 'PYTHONDONTWRITEBYTECODE':'1'}, check=True)
    native_docker = shutil.which('docker')
    access = subprocess.run([native_docker, 'info', '--format', '{{json .}}'], capture_output=True, text=True)
    docker_executable = native_docker
    if access.returncode:
        subprocess.run(['sudo', '-n', native_docker, 'info', '--format', '{{.ServerVersion}}'], check=True)
        docker_executable = str(venv/'bin/docker')
        Path(docker_executable).write_text('#!/bin/sh\nexec sudo -n '+native_docker+' "$@"\n')
        Path(docker_executable).chmod(0o755)
    docker = json.loads(subprocess.check_output([docker_executable, 'info', '--format', '{{json .}}'], text=True))
    # Isolated bounded container check; no daemon-global settings modified.
    subprocess.run([docker_executable, 'run', '--rm', '--network', 'none', '--memory', '128m', '--cpus', '0.5', '--pids-limit', '32', '--read-only', 'hello-world@sha256:5e23090353324d887c48ad5e5c56d294eab81588df9605b07d1afe895f9cc8f8'], check=True)
    image = json.loads(subprocess.check_output([docker_executable, 'image', 'inspect', 'hello-world@sha256:5e23090353324d887c48ad5e5c56d294eab81588df9605b07d1afe895f9cc8f8'], text=True))[0]
    memory = {line.split(':')[0]: int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith(('MemTotal:', 'MemAvailable:'))}
    report = dict(schema_version='harbor-fleet-install/v1', host=socket.gethostname(), release=config['release'], code=str(code), executable=str(python), python=platform.python_version(), architecture=platform.machine(), cpus=os.cpu_count(), memory=memory, load_average=os.getloadavg(), disk_free_bytes=shutil.disk_usage(root).free, source_approval_registry=registry, pipeline_code_hash=manifest['pipeline_code_hash'], code_files_verified=len(manifest['files']), archive_sha256=config['sha256'], requirements_sha256=sha(release/'requirements-linux-resolved.txt'), docker_executable=docker_executable, docker_server_version=docker['ServerVersion'], docker_cgroup_version=docker.get('CgroupVersion'), container_image_id=image['Id'], container_repo_digests=image.get('RepoDigests'), container_guard_check_passed=True, historical_artifacts_rewritten=False, authoring_started=False, completed_at_ns=time.time_ns())
    (release/f'install-{time.time_ns()}.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report), flush=True)

if __name__ == '__main__':
    main(json.loads(sys.argv[1]))

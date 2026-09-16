"""Content-addressed, secret-free Linux fleet deployment. Does not claim work.

bundle --output DIR snapshots dirty working code bytes, without following symlinks.
deploy --bundle MANIFEST --output DIR installs each release in an isolated venv.
Historical data and checkpoints are deliberately outside the deployment bundle.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shlex
import subprocess
import tarfile
import time

HOSTS = ('hhu-server', *(f'hhu-server-{i}' for i in range(2, 9)))
CODE_DIRS = ('.cursor', 'scripts', 'xl_seg', 'mcp_env', 'grader', 'tests', 'verification_manifests')
SUFFIXES = {'.py', '.sh', '.md', '.json', '.yaml', '.yml', '.txt', '.toml', '.ini'}
EXCLUDED = {'__pycache__', '.pytest_cache', '.git', '.venv', 'node_modules'}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def code_paths(root):
    candidates = list(root.iterdir())
    for name in CODE_DIRS:
        base = root / name
        if base.is_dir() and not base.is_symlink():
            for directory, dirs, files in os.walk(base, followlinks=False):
                dirs[:] = sorted(d for d in dirs if d not in EXCLUDED and not (Path(directory)/d).is_symlink())
                candidates.extend(Path(directory)/name for name in files)
    for path in sorted(set(candidates)):
        relative = path.relative_to(root)
        if (not path.is_file() or path.is_symlink() or (path.suffix not in SUFFIXES and path.name != '.python-version')
            or any(part in EXCLUDED for part in relative.parts)
            or path.name in {'.pipeline-code.json', 'FLEET_CODE_MANIFEST.json'} or path.name.startswith('.env') or any(x in path.name.lower() for x in ('credential', 'secret', '.pem', '.key'))):
            continue
        yield path


def bundle(root, output, exclude=()):
    root = root.resolve()
    excluded=set(exclude)
    if any(PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts for name in excluded):
        raise ValueError('bundle exclusions must be relative file names')
    output.mkdir(parents=True, exist_ok=False)
    files = {}
    payloads = {}
    for path in code_paths(root):
        name = path.relative_to(root).as_posix()
        if name in excluded:continue
        data = path.read_bytes()
        mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
        files[name] = dict(sha256=digest(data), size_bytes=len(data), mode=mode)
        payloads[name] = data
    pipeline_records = {name: record['sha256'] for name, record in files.items()}
    pipeline_code_hash = digest(canonical(pipeline_records))
    pipeline_manifest = canonical(pipeline_records)+b'\n'
    payloads['.pipeline-code.json'] = pipeline_manifest
    files['.pipeline-code.json'] = dict(sha256=digest(pipeline_manifest), size_bytes=len(pipeline_manifest), mode=0o644)
    release = digest(canonical(files))
    manifest = dict(schema_version='harbor-fleet-code/v1', release=release, pipeline_code_hash=pipeline_code_hash, files=files)
    payloads['FLEET_CODE_MANIFEST.json'] = canonical(manifest)+b'\n'
    target = output/f'{release}.tar.gz'
    with target.open('wb') as raw, gzip.GzipFile(fileobj=raw, mode='wb', mtime=0, filename='') as zipped, tarfile.open(fileobj=zipped, mode='w') as archive:
        for name, data in sorted(payloads.items()):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = files.get(name, {}).get('mode', 0o644)
            info.mtime = 0
            archive.addfile(info, io.BytesIO(data))
    head = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'], capture_output=True, text=True)
    result = dict(schema_version='harbor-fleet-bundle/v1', release=release, pipeline_code_hash=pipeline_code_hash, archive=str(target.resolve()), sha256=digest(target.read_bytes()), files=len(files), source_git_head=head.stdout.strip(), created_at=datetime.now(timezone.utc).isoformat(), historical_artifacts_included=False, excluded_files=sorted(excluded))
    (output/'bundle.json').write_text(json.dumps(result, indent=2)+'\n')
    return result


def ssh(host, command, project, zone):
    # Bound detection of an unresponsive established channel. A lost response
    # still requires reconciliation; keepalives never retry a remote command.
    return ['gcloud', 'compute', 'ssh', host, f'--project={project}', f'--zone={zone}', '--tunnel-through-iap', '--quiet', '--ssh-flag=-oBatchMode=yes', '--ssh-flag=-oConnectTimeout=20', '--ssh-flag=-oServerAliveInterval=15', '--ssh-flag=-oServerAliveCountMax=3', '--command='+command]


def deploy_host(host, manifest, output, project, zone, remote_root):
    started = time.monotonic()
    log = output/(host+'.log')
    status = dict(host=host, release=manifest['release'], status='INSTALL_FAILED', authoring_started=False)
    upload = f'{remote_root}/incoming/{manifest["sha256"]}.tar.gz'
    installer = Path(__file__).with_name('fleet')/'install_release.py'
    # Installer is sent as stdin; credential-bearing environment is never serialized.
    config = dict(archive=upload, sha256=manifest['sha256'], release=manifest['release'], root=remote_root)
    try:
        with log.open('w') as stream:
            def run(argv, timeout, stdin=None):
                result = subprocess.run(argv, input=stdin, text=True, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout)
                stream.flush()
                if result.returncode: raise RuntimeError(f'command exit {result.returncode}; see {log.name}')
            run(ssh(host, 'mkdir -p '+shlex.quote(remote_root+'/incoming'), project, zone), 90)
            run(['gcloud', 'compute', 'scp', manifest['archive'], f'{host}:{upload}', f'--project={project}', f'--zone={zone}', '--tunnel-through-iap', '--quiet'], 180)
            command = 'python3 - '+shlex.quote(json.dumps(config))
            run(ssh(host, command, project, zone), 1800, installer.read_text())
        # Last line is machine-readable native runtime verification.
        rows = log.read_text().splitlines()
        verified = next(json.loads(row) for row in reversed(rows) if row.startswith('{"schema_version": "harbor-fleet-install/v1"'))
        status.update(status='DEPENDENCIES_AND_CONTAINER_VALIDATED', verification=verified)
    except Exception as exc:
        status['error'] = str(exc)
    status['elapsed_seconds'] = time.monotonic()-started
    (output/(host+'.json')).write_text(json.dumps(status, indent=2)+'\n')
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    create = sub.add_parser('bundle')
    create.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    create.add_argument('--output', type=Path, required=True)
    create.add_argument('--exclude', action='append', default=[])
    deploy = sub.add_parser('deploy')
    deploy.add_argument('--bundle', type=Path, required=True)
    deploy.add_argument('--output', type=Path, required=True)
    deploy.add_argument('--hosts', nargs='+', choices=HOSTS, default=list(HOSTS))
    deploy.add_argument('--project', default='saleseng')
    deploy.add_argument('--zone', default='us-central1-a')
    deploy.add_argument('--remote-root', default='/home/gmahajan_labelbox_com/synthetic-data-fleet')
    args = parser.parse_args()
    if args.action == 'bundle':
        print(json.dumps(bundle(args.root, args.output, args.exclude)))
        return 0
    manifest = json.loads(args.bundle.read_text())
    if digest(Path(manifest['archive']).read_bytes()) != manifest['sha256']:
        raise ValueError('bundle archive hash mismatch before transfer')
    args.output.mkdir(parents=True, exist_ok=False)
    with ThreadPoolExecutor(max_workers=len(args.hosts)) as pool:
        results = list(pool.map(lambda host: deploy_host(host, manifest, args.output, args.project, args.zone, args.remote_root), args.hosts))
    report = dict(release=manifest['release'], workers=results, end_to_end_pipeline_validated=False)
    (args.output/'deployment.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(dict(output=str(args.output), ready=sum(row['status']=='DEPENDENCIES_AND_CONTAINER_VALIDATED' for row in results), requested=len(results), release=manifest['release'])))
    return 0 if all(row['status']=='DEPENDENCIES_AND_CONTAINER_VALIDATED' for row in results) else 1

if __name__ == '__main__':
    raise SystemExit(main())

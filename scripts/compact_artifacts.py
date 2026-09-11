"""Reclaim duplicate immutable artifact storage with independent APFS clones.

Logical files and their exact bytes remain available to evidence validators.
Unlike hard links, clones do not propagate a later write to another artifact.
Only manifest-bound files inside completed generation directories are considered.
"""
import argparse
from collections import defaultdict
import fcntl
import json
import os
from pathlib import Path
import stat
import re
import subprocess
import tempfile

from scripts.corpus import digest
from scripts.batch.hardened_runner import atomic_write_json


def completed_artifact(path, root):
    """Only select files declared by a completed immutable generation."""
    relative = path.relative_to(root)
    if any(part.startswith('.') for part in relative.parts):
        return False
    for parent in path.parents:
        if parent == root:
            break
        if parent.parent.name != 'generations':
            continue
        if not re.fullmatch(r'[0-9a-f]{64}', parent.name):
            return False
        manifest = parent / 'generation-manifest.json'
        if manifest.is_symlink() or not manifest.is_file():
            return False
        try:
            document = json.loads(manifest.read_text())
            record = document['artifacts'][path.relative_to(parent).as_posix()]
            return (document.get('generation_id') == parent.name
                    and isinstance(record, dict)
                    and record.get('size_bytes') == path.stat().st_size
                    and record.get('sha256') == digest(path))
        except (OSError, ValueError, KeyError, TypeError):
            return False
    return False


def identity(path):
    value = path.lstat()
    if not stat.S_ISREG(value.st_mode):
        raise ValueError('artifact is not a regular file')
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns


def clone_duplicate(canonical, duplicate, expected):
    canonical, duplicate = Path(canonical), Path(duplicate)
    before = identity(duplicate)
    if digest(canonical) != expected or digest(duplicate) != expected:
        raise ValueError('duplicate evidence bytes changed')
    with tempfile.TemporaryDirectory(prefix='.compact-', dir=duplicate.parent) as work:
        target = Path(work) / duplicate.name
        subprocess.run(['/bin/cp', '-c', '-p', str(canonical), str(target)], check=True)
        if digest(target) != expected or identity(duplicate) != before:
            raise ValueError('artifact changed during compaction')
        # Preserve the destination's metadata, not the canonical file's timestamp.
        info = duplicate.stat()
        os.chmod(target, stat.S_IMODE(info.st_mode))
        os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns))
        if digest(duplicate) != expected:
            raise ValueError('artifact changed during compaction')
        with target.open('rb') as stream:
            os.fsync(stream.fileno())
        os.replace(target, duplicate)
        descriptor = os.open(duplicate.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return {'path': str(duplicate), 'canonical': str(canonical),
            'sha256': expected, 'logical_bytes': before[2]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--roots', nargs='+', required=True)
    parser.add_argument('--report', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    report = Path(args.report).resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    with Path(str(report) + '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if report.exists():
            raise ValueError('use a new immutable compaction report path')
        groups = defaultdict(list)
        for root in map(lambda p: Path(p).resolve(strict=True), args.roots):
            for path in root.rglob('*'):
                if ('generations' in path.relative_to(root).parts
                        and not any(p.startswith('.') for p in path.relative_to(root).parts)
                        and not path.is_symlink() and path.is_file()
                        and path.stat().st_size >= 1024 * 1024
                        and completed_artifact(path, root)):
                    groups[path.stat().st_size].append(path)
        result = {'schema_version': 'artifact-compaction/v1',
                  'mode': 'apply' if args.apply else 'inspect', 'files': []}
        atomic_write_json(report, result)
        for paths in groups.values():
            if len(paths) < 2:
                continue
            seen = {}
            for path in sorted(set(paths)):
                fingerprint = digest(path)
                canonical = seen.setdefault(fingerprint, path)
                if canonical == path:
                    continue
                record = (clone_duplicate(canonical, path, fingerprint) if args.apply else
                          {'path': str(path), 'canonical': str(canonical),
                           'sha256': fingerprint, 'logical_bytes': path.stat().st_size})
                result['files'].append(record)
                atomic_write_json(report, result)
        result['complete'] = True
        result['duplicate_logical_bytes'] = sum(row['logical_bytes'] for row in result['files'])
        atomic_write_json(report, result)
        print(f"{len(result['files'])} duplicate files; {result['duplicate_logical_bytes']} logical bytes")


if __name__ == '__main__':
    main()

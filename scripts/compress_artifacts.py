"""Opt-in native compression of completed manifest-bound artifacts on macOS.

Only .json/.csv/.md/.txt text artifacts (case-insensitive) are candidates;
workbooks and other binaries are left untouched. Reports per-file allocated
blocks, never summed APFS free-space savings. Inspect
is the default. Each apply run probes its actual filesystem/execution context;
a successful ditto exit alone is insufficient. No reconstruction or model calls.
"""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.compact_artifacts import completed_artifact
from scripts.corpus import digest

MIN_BYTES = 1024 * 1024
COMPRESSED = getattr(stat, 'UF_COMPRESSED', 32)
# Target repetitive retained AST/proof/document text. Workbook ZIP containers
# and other binaries usually do not benefit and need no native compression.
TEXT_EXTENSIONS = frozenset({'.json', '.csv', '.md', '.txt'})


def safe_path(path):
    """Do not resolve away a symlink, including in root/report ancestry."""
    path = Path(os.path.abspath(path))
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError(f'symlink ancestry: {parent}')
    return path


def snapshot(path):
    safe_path(path)
    s = path.lstat()
    if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1:
        raise ValueError('artifact must be a regular file with one link')
    return dict(device=s.st_dev, inode=s.st_ino, logical_bytes=s.st_size,
                allocated_bytes=s.st_blocks * 512, mode=stat.S_IMODE(s.st_mode),
                uid=s.st_uid, gid=s.st_gid, mtime_ns=s.st_mtime_ns,
                ctime_ns=s.st_ctime_ns, flags=getattr(s, 'st_flags', 0))


def generation_for(path, root):
    path.relative_to(root)
    for parent in path.parents:
        if parent == root:
            break
        if parent.parent.name == 'generations':
            return parent
    raise ValueError('not inside a completed generation')


def validate_generation(generation):
    """Use existing full manifest validators, not a caller's completion flag."""
    safe_path(generation / 'generation-manifest.json')
    schema = json.loads((generation / 'generation-manifest.json').read_text()).get('schema_version')
    if schema in {'source-generation/v1', 'source-generation/v2'}:
        from xl_source_publication import validate_source_generation
        validate_source_generation(generation, expected_generation_id=generation.name)
    elif schema in {'segmentation-generation/v1', 'segmentation-generation/v2'}:
        from xl_seg.publication import validate_generation_directory
        validate_generation_directory(generation, expected_generation_id=generation.name,
                                      require_pass=True)
    else:
        raise ValueError('unsupported completed generation schema')


def eligible(path, root):
    path, root = safe_path(path), safe_path(root)
    before = snapshot(path)
    if (path.suffix.casefold() not in TEXT_EXTENSIONS
            or before['logical_bytes'] < MIN_BYTES or before['flags'] & COMPRESSED):
        return None
    if not completed_artifact(path, root):
        raise ValueError('artifact is not bound to a completed manifest')
    generation = generation_for(path, root)
    validate_generation(generation)
    return generation, before


def native_copy(source, destination):
    subprocess.run(['/usr/bin/ditto', '--hfsCompression', '--noclone',
                    str(source), str(destination)], check=True, timeout=300,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def verified_copy(destination, before, expected, *, require_compressed=True):
    after = snapshot(destination)
    if after['logical_bytes'] != before['logical_bytes'] or digest(destination) != expected:
        raise ValueError('copy logical hash or size mismatch')
    if (after['device'], after['inode']) == (before['device'], before['inode']):
        raise ValueError('copy does not have an independent inode')
    for key in ('mode', 'uid', 'gid', 'mtime_ns'):
        if after[key] != before[key]:
            raise ValueError(f'compressed copy changed metadata: {key}')
    if require_compressed and not after['flags'] & COMPRESSED:
        raise ValueError('copy missing UF_COMPRESSED')
    return after


def fsync_dir(path):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def capability_probe(directory):
    directory = safe_path(directory)
    with tempfile.TemporaryDirectory(prefix='.compression-probe-', dir=directory) as work:
        source, target = Path(work) / 'source.csv', Path(work) / 'compressed.csv'
        source.write_bytes(b'cell,kind,formula\nA1,formula,SUM(B1:B99)\n' * 40000)
        before = snapshot(source)
        expected = digest(source)
        native_copy(source, target)
        after = verified_copy(target, before, expected)
        if after['allocated_bytes'] >= before['allocated_bytes']:
            raise ValueError('native compression probe saved no allocated blocks')
        if snapshot(source) != before or digest(source) != expected:
            raise ValueError('native compression probe changed its source')
        return {'status': 'passed', 'device': before['device'], 'sha256': expected,
                'before': before, 'after': after}


@contextmanager
def exclusive(path):
    safe_path(path)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        if os.fstat(fd).st_nlink != 1:
            raise ValueError('unsafe compression lock')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def compress_one(path, root, event):
    """Called serially under the run lock; every commit has a durable intent."""
    path, root = safe_path(path), safe_path(root)
    selection = eligible(path, root)
    if selection is None:
        return {'path': str(path), 'status': 'skipped_format_size_or_already_compressed'}
    generation, before = selection
    manifest = generation / 'generation-manifest.json'
    manifest_before, manifest_sha = snapshot(manifest), digest(manifest)
    expected = digest(path)
    # Outside the generation, so full artifact-set validation stays meaningful.
    with tempfile.TemporaryDirectory(prefix='.compression-stage-', dir=generation.parent.parent) as work:
        staged = Path(work) / path.name
        native_copy(path, staged)
        after = verified_copy(staged, before, expected, require_compressed=False)
        if (snapshot(path) != before or digest(path) != expected
                or snapshot(manifest) != manifest_before or digest(manifest) != manifest_sha):
            raise ValueError('artifact or manifest changed during native copy')
        if not after['flags'] & COMPRESSED:
            # Exact native no-op: do not infer why this file was not compressed.
            # The independent staged copy is discarded; no commit is attempted.
            return {'path': str(path), 'status': 'skipped_native_not_compressed',
                    'sha256': expected, 'manifest_sha256': manifest_sha,
                    'before': before, 'staged': after}
        if after['allocated_bytes'] >= before['allocated_bytes']:
            return {'path': str(path), 'status': 'skipped_no_allocated_saving',
                    'before': before, 'staged': after}
        if after['device'] != before['device']:
            raise ValueError('staging must use the artifact filesystem')
        # Bind every ancestor inode, not only the final file's bytes.
        ancestry = [(p, p.stat().st_dev, p.stat().st_ino) for p in path.parents]
        with path.open('rb') as original, manifest.open('rb') as manifest_stream:
            fcntl.flock(original, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(manifest_stream, fcntl.LOCK_SH | fcntl.LOCK_NB)
            validate_generation(generation)
            event({'status': 'replacement_prepared', 'path': str(path),
                   'sha256': expected, 'manifest_sha256': manifest_sha,
                   'before': before, 'staged': after})
            safe_path(path)
            if (snapshot(path) != before or digest(path) != expected
                    or snapshot(manifest) != manifest_before or digest(manifest) != manifest_sha
                    or not completed_artifact(path, root)
                    or any(p.stat().st_dev != dev or p.stat().st_ino != ino
                           for p, dev, ino in ancestry)):
                raise ValueError('artifact, manifest or ancestry changed before replacement')
            with staged.open('rb') as stream:
                os.fsync(stream.fileno())
            os.replace(staged, path)
            fsync_dir(path.parent)
            final = verified_copy(path, before, expected)
            if digest(manifest) != manifest_sha:
                raise ValueError('manifest changed after replacement')
            validate_generation(generation)
    return {'path': str(path), 'status': 'compressed', 'sha256': expected,
            'manifest_sha256': manifest_sha, 'before': before, 'after': final,
            'allocated_block_difference_bytes': before['allocated_bytes'] - final['allocated_bytes'],
            'physical_savings_scope': 'per-file st_blocks; not exclusive APFS extents or filesystem free space'}


def write_once(path, value):
    """Publish each event/final report atomically without replacing history."""
    safe_path(path)
    data = (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()
    descriptor, temporary = tempfile.mkstemp(prefix='.compression-report-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)  # Exclusive creation: an existing event cannot change.
        os.unlink(temporary)
        fsync_dir(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Journal:
    def __init__(self, report):
        self.report = safe_path(report)
        if any(parent.parent.name == 'generations' for parent in self.report.parents):
            raise ValueError('compression reports must be outside immutable generations')
        self.report.parent.mkdir(parents=True, exist_ok=True)
        if report.exists():
            raise ValueError('use a new immutable compression report path')
        self.directory = Path(str(report) + '.events')
        self.directory.mkdir()  # A killed run cannot silently reset its history.
        fsync_dir(self.directory.parent)
        self.count = 0

    def event(self, value):
        write_once(self.directory / f'{self.count:08d}.json', value)
        self.count += 1

    def finish(self, value):
        write_once(self.report, {**value, 'event_count': self.count,
                               'events': str(self.directory)})


def run(roots, report, apply=False, max_files=None):
    if max_files is not None and (type(max_files) is not int or max_files <= 0):
        raise ValueError('max_files must be a positive integer')
    roots = sorted(set(safe_path(root) for root in roots))
    report = safe_path(report)
    journal = Journal(report)
    summary = {'schema_version': 'artifact-compression/v1', 'mode': 'apply' if apply else 'inspect',
               'complete': False, 'files': [], 'committed_files': 0, 'max_files': max_files,
               'physical_accounting': 'per-file only; no sum of APFS shared extents'}
    journal.event({'status': 'started', 'roots': list(map(str, roots)), 'mode': summary['mode']})
    try:
        # Single host-wide worker, including overlapping roots in separate runs.
        lock = Path(tempfile.gettempdir()).resolve() / f'pipeline-native-compression-{os.getuid()}.lock'
        with exclusive(lock):
            for root in roots:
                if max_files is not None and summary['committed_files'] >= max_files:
                    break
                if not root.is_dir():
                    raise ValueError('compression root is not a directory')
                probed_devices = set()
                for directory, dirs, names in os.walk(root, followlinks=False):
                    if max_files is not None and summary['committed_files'] >= max_files:
                        break
                    dirs[:] = sorted(d for d in dirs if not d.startswith('.') and not (Path(directory)/d).is_symlink())
                    for name in sorted(names):
                        if max_files is not None and summary['committed_files'] >= max_files:
                            break
                        path = Path(directory) / name
                        if (name.startswith('.') or path.is_symlink()
                                or path.suffix.casefold() not in TEXT_EXTENSIONS):
                            continue
                        try:
                            s = path.stat()
                            if s.st_size < MIN_BYTES or not stat.S_ISREG(s.st_mode):
                                continue
                            if not completed_artifact(path, root):
                                continue
                            try:
                                selection = eligible(path, root)
                            except (ValueError, OSError) as exc:
                                record = {'path': str(path), 'status': 'ineligible',
                                          'reason': f'{type(exc).__name__}: {exc}'}
                                journal.event(record)
                                summary['files'].append(record)
                                continue
                            if apply:
                                if selection and selection[1]['device'] not in probed_devices:
                                    proof = capability_probe(selection[0].parent.parent)
                                    journal.event({'status': 'capability', 'proof': proof})
                                    probed_devices.add(proof['device'])
                                record = compress_one(path, root, journal.event)
                            else:
                                record = {'path': str(path), 'status': 'eligible' if selection else 'skipped_format_size_or_already_compressed'}
                            journal.event(record)
                            summary['files'].append(record)
                            if record['status'] == 'compressed':
                                summary['committed_files'] += 1
                        except Exception as exc:
                            journal.event({'path': str(path), 'status': 'failed', 'error': f'{type(exc).__name__}: {exc}'})
                            raise  # Capability/race/evidence failures never become a best-effort commit.
        summary['commit_limit_reached'] = max_files is not None and summary['committed_files'] >= max_files
        summary['complete'] = True
    except BaseException as exc:
        summary['error'] = f'{type(exc).__name__}: {exc}'
        journal.event({'status': 'interrupted_or_failed', 'error': summary['error']})
        raise
    finally:
        journal.finish(summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--roots', nargs='+', required=True)
    parser.add_argument('--report', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--max-files', type=int, help='positive maximum committed replacements (for canary runs)')
    args = parser.parse_args()
    result = run(args.roots, args.report, args.apply, args.max_files)
    print(json.dumps({'complete': result['complete'], 'files': len(result['files'])}))


if __name__ == '__main__':
    main()

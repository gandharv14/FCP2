"""Deterministic, bounded compressed export/import of private verification bytes.

An archive is a transport artifact, not a cleanup capability or financial proof.
Both its compressed SHA and its original manifest SHA must be supplied externally
when importing. Raw package deletion is deliberately outside this module.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import tarfile
import tempfile

from scripts import harbor_verification_package as package

SCHEMA = 'harbor-verification-archive/v1'
MAX_BYTES = 64 * 1024**3
MAX_FILES = 100_000
MAX_MANIFEST_BYTES = 32 * 1024**2
CHUNK = 1024**2


class ArchiveError(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise ArchiveError(message)


def _limits(max_uncompressed_bytes, max_archive_bytes, max_files, max_manifest_bytes):
    for value in (max_uncompressed_bytes, max_archive_bytes, max_files, max_manifest_bytes):
        require(type(value) is int and value > 0, 'positive integer archive limits required')
    require(max_manifest_bytes <= max_uncompressed_bytes, 'manifest limit exceeds total limit')
    require(max_manifest_bytes <= MAX_MANIFEST_BYTES and max_files <= MAX_FILES and
            max_uncompressed_bytes <= MAX_BYTES and max_archive_bytes <= MAX_BYTES,
            'requested limits exceed supported archive format bounds')


def _header(name, size):
    # Fixed short names need no PAX/long-name records. GNU numeric fields support
    # graph objects larger than the 8 GiB limit of classic octal USTAR sizes.
    item = tarfile.TarInfo(name)
    item.size, item.mode, item.mtime = size, 0o444, 0
    item.uid = item.gid = 0
    item.uname = item.gname = ''
    item.type = tarfile.REGTYPE
    return item.tobuf(format=tarfile.GNU_FORMAT)


def _members(value, manifest_size, max_files):
    require(type(value) is dict and value.get('schema_version') == package.SCHEMA,
            'unsupported private package schema')
    objects = value.get('objects')
    require(type(objects) is list and len(objects) + 1 <= max_files, 'object inventory exceeds limit')
    require(type(value.get('files')) is list and len(value['files']) <= max_files,
            'mapped file inventory exceeds limit')
    require(type(value.get('directories')) is list and len(value['directories']) <= max_files,
            'directory inventory exceeds limit')
    seen, rows = set(), [('manifest.json', manifest_size, None)]
    for row in objects:
        require(type(row) is dict and set(row) == {'sha256', 'size'}, 'closed object record required')
        h, size = row['sha256'], row['size']
        require(type(h) is str and package._HEX.fullmatch(h) and h not in seen and
                type(size) is int and size >= 0, 'invalid or duplicate object')
        seen.add(h)
        rows.append(('objects/' + h, size, h))
    return rows[:1] + sorted(rows[1:])


def _total(rows):
    return sum(512 + size + (-size % 512) for _, size, _ in rows) + 1024


class _LimitedWriter:
    def __init__(self, stream, limit):
        self.stream, self.limit, self.size = stream, limit, 0

    def write(self, data):
        require(self.size + len(data) <= self.limit, 'compressed archive exceeds limit')
        written = self.stream.write(data)
        self.size += written
        return written

    def flush(self):
        self.stream.flush()


class _LimitedReader:
    def __init__(self, stream, limit):
        self.stream, self.limit, self.size = stream, limit, 0

    def read(self, size):
        require(0 <= size <= CHUNK, 'unbounded archive read forbidden')
        require(self.size + size <= self.limit, 'expanded archive exceeds limit')
        data = self.stream.read(size)
        self.size += len(data)
        return data

    def exact(self, size):
        out = bytearray()
        while len(out) < size:
            block = self.read(min(CHUNK, size - len(out)))
            require(bool(block), 'truncated archive')
            out.extend(block)
        return bytes(out)


def _publish_file(temporary, destination):
    with temporary.open('rb') as stream:
        os.fsync(stream.fileno())
    # Exclusive publication, with no link to an original evidence file.
    os.link(temporary, destination, follow_symlinks=False)
    temporary.unlink()
    fd = os.open(destination.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def export_package(bundle, expected_manifest_sha256, destination, *,
                   max_uncompressed_bytes=MAX_BYTES, max_archive_bytes=MAX_BYTES,
                   max_files=MAX_FILES, max_manifest_bytes=MAX_MANIFEST_BYTES):
    """Write one deterministic gzip/tar artifact; never remove the raw bundle."""
    _limits(max_uncompressed_bytes, max_archive_bytes, max_files, max_manifest_bytes)
    bundle, destination = package._safe(Path(bundle)), package._safe(Path(destination))
    require(not destination.exists() and destination.parent.is_dir(), 'fresh destination in existing directory required')
    require(not package._inside(destination, bundle), 'archive destination overlaps raw package')
    value = package.inspect_package(bundle, expected_manifest_sha256)
    manifest_size = (bundle/'manifest.json').stat().st_size
    require(manifest_size <= max_manifest_bytes, 'manifest exceeds limit')
    rows = _members(value, manifest_size, max_files)
    total = _total(rows)
    require(total <= max_uncompressed_bytes, 'expanded archive exceeds limit')
    with tempfile.TemporaryDirectory(prefix='.package-export-', dir=destination.parent) as raw:
        temporary = Path(raw)/'package.tar.gz'
        with temporary.open('xb') as file:
            output = _LimitedWriter(file, max_archive_bytes)
            with gzip.GzipFile(fileobj=output, filename='', mode='wb', mtime=0, compresslevel=6) as stream:
                for name, size, expected in rows:
                    stream.write(_header(name, size))
                    h, written = hashlib.sha256(), 0
                    with (bundle/name).open('rb') as source:
                        while block := source.read(CHUNK):
                            written += len(block)
                            require(written <= size, 'package member grew during export')
                            h.update(block); stream.write(block)
                    require(written == size and h.hexdigest() == (expected or expected_manifest_sha256),
                            'package member changed during export')
                    stream.write(b'\0' * (-size % 512))
                stream.write(b'\0' * 1024)
            file.flush(); os.fsync(file.fileno())
        package.inspect_package(bundle, expected_manifest_sha256)
        stored_hash, stored_size = package.digest(temporary), temporary.stat().st_size
        _publish_file(temporary, destination)
    return {'schema_version':SCHEMA, 'path':str(destination), 'sha256':stored_hash,
            'size':stored_size, 'manifest_sha256':expected_manifest_sha256,
            'expanded_tar_bytes':total, 'members':len(rows), 'codec':'gzip-restricted-tar/v1',
            'financial_verification':False, 'cleanup_authorized':False}


def _read_header(reader):
    raw = reader.exact(512)
    require(raw != b'\0'*512, 'unexpected archive end')
    try:
        item = tarfile.TarInfo.frombuf(raw, 'utf-8', 'strict')
    except (tarfile.HeaderError, UnicodeError, ValueError) as error:
        raise ArchiveError('invalid tar header') from error
    require(item.type == tarfile.REGTYPE and item.size >= 0 and
            raw == _header(item.name, item.size), 'noncanonical or nonregular archive member')
    return item


def import_package(archive, expected_archive_sha256, expected_manifest_sha256, destination, *,
                   max_uncompressed_bytes=MAX_BYTES, max_archive_bytes=MAX_BYTES,
                   max_files=MAX_FILES, max_manifest_bytes=MAX_MANIFEST_BYTES):
    """Bounded extraction into a fresh namespace, followed by owning inspection.

    No tar extraction API is used: only exact expected regular-file names are
    written. Path traversal, links, PAX/GNU extension headers, duplicate/extra
    members, nonzero padding, and trailing expanded data are rejected.
    """
    from scripts.harbor_retention import _record
    _limits(max_uncompressed_bytes, max_archive_bytes, max_files, max_manifest_bytes)
    archive, destination = package._safe(Path(archive)), package._safe(Path(destination))
    require(type(expected_archive_sha256) is str and package._HEX.fullmatch(expected_archive_sha256),
            'externally pinned archive hash required')
    require(type(expected_manifest_sha256) is str and package._HEX.fullmatch(expected_manifest_sha256),
            'externally pinned manifest hash required')
    require(not destination.exists() and destination.parent.is_dir(), 'fresh destination in existing directory required')
    require(not package._inside(archive, destination), 'archive overlaps import destination')
    before = _record(archive)
    require(before['identity']['size'] <= max_archive_bytes and before['sha256'] == expected_archive_sha256,
            'archive size or pinned hash differs')
    try:
        with tempfile.TemporaryDirectory(prefix='.package-import-', dir=destination.parent) as raw:
            staging = Path(raw)/'package'; staging.mkdir(); (staging/'objects').mkdir()
            with archive.open('rb') as source, gzip.GzipFile(fileobj=source, mode='rb') as stream:
                reader = _LimitedReader(stream, max_uncompressed_bytes)
                first = _read_header(reader)
                require(first.name == 'manifest.json' and first.size <= max_manifest_bytes, 'manifest must be first and bounded')
                manifest = reader.exact(first.size)
                require(hashlib.sha256(manifest).hexdigest() == expected_manifest_sha256, 'manifest pinned hash differs')
                require(reader.exact(-first.size % 512) == b'\0' * (-first.size % 512), 'nonzero member padding')
                value = json.loads(manifest)
                rows = _members(value, first.size, max_files)
                require(_total(rows) <= max_uncompressed_bytes, 'expanded archive exceeds limit')
                package._atomic(staging/'manifest.json', manifest)
                for name, size, expected in rows[1:]:
                    item = _read_header(reader)
                    require(item.name == name and item.size == size, 'archive member inventory differs')
                    h, remaining = hashlib.sha256(), size
                    with (staging/name).open('xb') as target:
                        while remaining:
                            block = reader.exact(min(CHUNK, remaining))
                            target.write(block); h.update(block); remaining -= len(block)
                        target.flush(); os.fsync(target.fileno())
                    require(h.hexdigest() == expected, 'decoded object hash differs')
                    os.chmod(staging/name, 0o444)
                    require(reader.exact(-size % 512) == b'\0' * (-size % 512), 'nonzero member padding')
                require(reader.exact(1024) == b'\0'*1024, 'archive end markers differ')
                # Read through gzip trailer/CRC and reject all expanded trailing
                # data, including concatenated gzip members. Allow one byte solely
                # for end detection when the exact configured bound was consumed.
                require(stream.read(1) == b'', 'unexpected trailing archive data')
            package.inspect_package(staging, expected_manifest_sha256)
            after = _record(archive)
            require(after['identity'] == before['identity'] and after['sha256'] == before['sha256'],
                    'archive changed during import')
            require(not destination.exists(), 'import destination appeared')
            package._sync_directory(staging/"objects")
            package._sync_directory(staging)
            package._publish_directory(staging, destination)
            fd = os.open(destination.parent, os.O_RDONLY)
            try: os.fsync(fd)
            finally: os.close(fd)
    except (gzip.BadGzipFile, EOFError, tarfile.TarError, UnicodeError, json.JSONDecodeError) as error:
        raise ArchiveError('malformed compressed package') from error
    return {'schema_version':SCHEMA, 'path':str(destination), 'manifest_sha256':expected_manifest_sha256,
            'archive_sha256':expected_archive_sha256, 'financial_verification':False,
            'cleanup_authorized':False}


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest='operation', required=True)
    for operation in ('export', 'import'):
        command = modes.add_parser(operation)
        command.add_argument('source', type=Path)
        command.add_argument('destination', type=Path)
        command.add_argument('--manifest-sha256', required=True)
        if operation == 'import':
            command.add_argument('--archive-sha256', required=True)
        command.add_argument('--max-uncompressed-bytes', type=int, default=MAX_BYTES)
        command.add_argument('--max-archive-bytes', type=int, default=MAX_BYTES)
        command.add_argument('--max-files', type=int, default=MAX_FILES)
        command.add_argument('--max-manifest-bytes', type=int, default=MAX_MANIFEST_BYTES)
    args = parser.parse_args(argv)
    limits = {key:getattr(args,key) for key in ('max_uncompressed_bytes', 'max_archive_bytes',
                                               'max_files', 'max_manifest_bytes')}
    if args.operation == 'export':
        result = export_package(args.source, args.manifest_sha256, args.destination, **limits)
    else:
        result = import_package(args.source, args.archive_sha256, args.manifest_sha256,
                                args.destination, **limits)
    print(json.dumps(result, sort_keys=True))
    return result


if __name__ == '__main__':
    main()

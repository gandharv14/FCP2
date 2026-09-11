"""Compressed transport preserves custody bytes; it never grants cleanup."""
import gzip
import hashlib
import io
from pathlib import Path
import tarfile

import pytest
from scripts import harbor_package_archive as archive
from scripts import harbor_verification_package as package
from test_harbor_verification_package import custody


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def export(c):
    destination = c.root/'package.tar.gz'
    receipt = archive.export_package(c.bundle, c.expected, destination)
    return destination, receipt


def import_one(c, path, **kwargs):
    return archive.import_package(path, sha(path), c.expected, c.root/'imported', **kwargs)


def members(c):
    value = package.inspect_package(c.bundle, c.expected)
    paths = ['manifest.json'] + ['objects/'+row['sha256'] for row in sorted(value['objects'], key=lambda row:row['sha256'])]
    return [(name, (c.bundle/name).read_bytes()) for name in paths]


def write_archive(c, rows, *, trailer=b'\0'*1024):
    path = c.root/'forged.tar.gz'
    with path.open('wb') as out:
        with gzip.GzipFile(fileobj=out, filename='', mode='wb', mtime=0) as stream:
            for name, data in rows:
                stream.write(archive._header(name, len(data)))
                stream.write(data); stream.write(b'\0' * (-len(data)%512))
            stream.write(trailer)
    return path


def test_deterministic_export_roundtrip_preserves_exact_package_and_grants_no_authority(custody):
    c = custody
    first, receipt = export(c)
    second = c.root/'another-name.tar.gz'
    other = archive.export_package(c.bundle, c.expected, second)
    assert receipt['sha256'] == other['sha256'] == sha(first) == sha(second)
    assert receipt['manifest_sha256'] == c.expected
    assert receipt['cleanup_authorized'] is receipt['financial_verification'] is False
    result = import_one(c, first)
    assert result['cleanup_authorized'] is result['financial_verification'] is False
    assert package.inspect_package(c.root/'imported', c.expected) == package.inspect_package(c.bundle,c.expected)
    for original in c.bundle.rglob('*'):
        if original.is_file():
            restored = c.root/'imported'/original.relative_to(c.bundle)
            assert restored.read_bytes() == original.read_bytes()
            assert restored.stat().st_ino != original.stat().st_ino
    assert c.first.exists() and c.bundle.exists() and package._CAPABILITIES == {}
    assert not list(c.root.glob('.package-import-*')) and not list(c.root.glob('.package-export-*'))


def test_repetitive_graph_has_compact_portable_representation(custody):
    # This fixture measures transport compression only, not a corpus yield gain.
    c = custody; path, receipt = export(c)
    assert path.stat().st_size < receipt['expanded_tar_bytes']


@pytest.mark.parametrize('which', ['archive', 'manifest'])
def test_both_external_pins_are_required(custody, which):
    c=custody;path,_=export(c)
    with pytest.raises((archive.ArchiveError, package.PackageError)):
        archive.import_package(path, '0'*64 if which=='archive' else sha(path),
            '0'*64 if which=='manifest' else c.expected, c.root/'imported')
    assert not (c.root/'imported').exists()


@pytest.mark.parametrize('kind', ['traversal', 'absolute', 'duplicate', 'extra', 'missing', 'wrong_object', 'order'])
def test_unexpected_archive_members_rejected_before_publication(custody, kind):
    c = custody; rows = members(c)
    if kind == 'traversal': rows[1] = ('../../escape', rows[1][1])
    elif kind == 'absolute': rows[1] = ('/tmp/escape', rows[1][1])
    elif kind == 'duplicate': rows.insert(2, rows[1])
    elif kind == 'extra': rows.append(('extra.txt', b'not in package'))
    elif kind == 'missing': rows.pop()
    elif kind == 'wrong_object': rows[1] = (rows[1][0], b'x'*len(rows[1][1]))
    else: rows[1], rows[2] = rows[2], rows[1]
    path = write_archive(c, rows)
    with pytest.raises((archive.ArchiveError, package.PackageError)):
        import_one(c, path)
    assert not (c.root/'imported').exists() and not list(c.root.glob('.package-import-*'))
    assert c.first.exists()


@pytest.mark.parametrize('kind', [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE, tarfile.XHDTYPE, tarfile.GNUTYPE_LONGNAME])
def test_links_and_extension_headers_rejected_without_extracting(custody, kind):
    c=custody;path=c.root/'links.tar.gz'; rows=members(c)
    with path.open('wb') as out, gzip.GzipFile(fileobj=out,filename='',mode='wb',mtime=0) as stream:
        name,data=rows[0];stream.write(archive._header(name,len(data)));stream.write(data);stream.write(b'\0'*(-len(data)%512))
        info=tarfile.TarInfo(rows[1][0]);info.type=kind;info.linkname='../../source'
        stream.write(info.tobuf(format=tarfile.GNU_FORMAT));stream.write(b'\0'*1024)
    with pytest.raises(archive.ArchiveError): import_one(c,path)
    assert not (c.root/'imported').exists()


@pytest.mark.parametrize('kind', ['truncate', 'crc', 'trailing', 'concatenated'])
def test_corrupt_or_extra_compressed_data_rejected(custody, kind):
    c=custody;path,_=export(c); raw=path.read_bytes()
    if kind=='truncate': raw=raw[:-5]
    elif kind=='crc': raw=raw[:-8]+bytes([raw[-8]^1])+raw[-7:]
    elif kind=='trailing': raw+=b'not gzip'
    else: raw+=gzip.compress(b'expanded extra member',mtime=0)
    path.write_bytes(raw)
    with pytest.raises(archive.ArchiveError): import_one(c,path)
    assert not (c.root/'imported').exists()


@pytest.mark.parametrize('limit', ['expanded', 'compressed', 'manifest', 'members'])
def test_import_resource_limits_reject_boundedly(custody, limit):
    c=custody;path,receipt=export(c)
    settings={'expanded':{'max_uncompressed_bytes':receipt['expanded_tar_bytes']-1,'max_manifest_bytes':1024},
              'compressed':{'max_archive_bytes':path.stat().st_size-1},
              'manifest':{'max_manifest_bytes':1},'members':{'max_files':1}}[limit]
    with pytest.raises(archive.ArchiveError): import_one(c,path,**settings)
    assert not (c.root/'imported').exists()


def test_oversized_member_header_does_not_allocate_declared_payload(custody):
    c=custody; path=c.root/'bomb.tar.gz'
    path.write_bytes(gzip.compress(archive._header('manifest.json', 1<<40),mtime=0))
    with pytest.raises(archive.ArchiveError,match='bounded'): import_one(c,path)
    assert not (c.root/'imported').exists()


def test_exact_expansion_bound_is_accepted(custody):
    c=custody;path,receipt=export(c)
    result=import_one(c,path,max_uncompressed_bytes=receipt['expanded_tar_bytes'],
                      max_manifest_bytes=(c.bundle/'manifest.json').stat().st_size)
    assert result['manifest_sha256']==c.expected


@pytest.mark.parametrize('operation', ['export', 'import'])
def test_preexisting_destination_is_preserved(custody, operation):
    c=custody;path,_=export(c)
    target=c.root/'existing';target.write_bytes(b'user data')
    with pytest.raises(archive.ArchiveError):
        if operation=='export': archive.export_package(c.bundle,c.expected,target)
        else: archive.import_package(path,sha(path),c.expected,target)
    assert target.read_bytes()==b'user data'


def test_archive_symlink_rejected(custody):
    c=custody;path,_=export(c);link=c.root/'linked.tar.gz';link.symlink_to(path)
    with pytest.raises(package.PackageError,match='symlink'): import_one(c,link)


def test_export_never_writes_inside_private_package(custody):
    c=custody
    with pytest.raises(archive.ArchiveError,match='overlaps'):
        archive.export_package(c.bundle,c.expected,c.bundle/'extra.tar.gz')
    package.inspect_package(c.bundle,c.expected)


def test_nonzero_payload_padding_rejected(custody):
    c=custody;rows=members(c);name,data=rows[0]
    padding=-len(data)%512
    assert padding
    contents=(archive._header(name,len(data))+data+b'x'*padding)
    for name,data in rows[1:]:
        contents+=archive._header(name,len(data))+data+b'\0'*(-len(data)%512)
    contents+=b'\0'*1024
    path=c.root/'padding.tar.gz';path.write_bytes(gzip.compress(contents,mtime=0))
    with pytest.raises(archive.ArchiveError,match='padding'): import_one(c,path)


def test_noncanonical_tar_metadata_rejected(custody):
    c=custody;path=c.root/'timestamp.tar.gz';name,data=members(c)[0]
    info=tarfile.TarInfo(name);info.size=len(data);info.mtime=999
    path.write_bytes(gzip.compress(info.tobuf(format=tarfile.GNU_FORMAT)+data,mtime=0))
    with pytest.raises(archive.ArchiveError,match='noncanonical'): import_one(c,path)


def test_export_limit_failure_leaves_raw_bundle_and_no_output(custody):
    c=custody;destination=c.root/'limited.tar.gz'
    with pytest.raises(archive.ArchiveError):
        archive.export_package(c.bundle,c.expected,destination,max_archive_bytes=1)
    assert not destination.exists()
    assert not list(c.root.glob('.package-export-*'))
    package.inspect_package(c.bundle,c.expected)


def test_export_and_import_cli_require_both_pins_and_preserve_authority_boundary(custody, capsys):
    c=custody;path=c.root/'cli.tar.gz'
    result=archive.main(['export',str(c.bundle),str(path),'--manifest-sha256',c.expected])
    assert result['cleanup_authorized'] is False
    capsys.readouterr()
    with pytest.raises(SystemExit):
        archive.main(['import',str(path),str(c.root/'imported'),'--manifest-sha256',c.expected])
    assert not (c.root/'imported').exists()
    result=archive.main(['import',str(path),str(c.root/'imported'),'--manifest-sha256',c.expected,
                         '--archive-sha256',sha(path)])
    assert result['cleanup_authorized'] is False
    package.inspect_package(c.root/'imported',c.expected)


def test_read_bounds_cannot_be_disabled(custody):
    c=custody;path,_=export(c)
    with pytest.raises(archive.ArchiveError,match='supported'):
        import_one(c,path,max_manifest_bytes=archive.MAX_MANIFEST_BYTES+1)
    assert not (c.root/'imported').exists()


def test_large_graph_streaming_is_compact_and_memory_bounded(custody):
    import tracemalloc
    c=custody
    block=(b'Sheet!A1,formula,=SUM(A2:A30),123.0\n'*40000)[:1024**2]
    with c.first.open('wb') as stream:
        for _ in range(20): stream.write(block)
    # Second original aliases the same logical evidence but remains independent.
    with c.first.open('rb') as source, c.second.open('wb') as target:
        while chunk:=source.read(1024**2): target.write(chunk)
    h=package.digest(c.first)
    for row in c.captured['files']:
        if row['path'] in {str(c.first),str(c.second)}: row['sha256']=h
    bundle=c.root/'large-private-package'
    expected=package.build_package(c.captured['source'],c.tracker,c.key,bundle)
    destination=c.root/'large.tar.gz'
    tracemalloc.start()
    result=archive.export_package(bundle,expected,destination)
    _, export_peak=tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert destination.stat().st_size < c.first.stat().st_size // 20
    assert result['expanded_tar_bytes'] >= c.first.stat().st_size
    tracemalloc.start()
    archive.import_package(destination,sha(destination),expected,c.root/'large-imported')
    _, import_peak=tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert max(export_peak,import_peak) < 10*1024**2
    package.inspect_package(c.root/'large-imported',expected)
    assert package.digest(c.first)==h and package.digest(c.second)==h


def test_directory_publication_never_replaces_concurrent_empty_target(tmp_path):
    source, target = tmp_path/'stage', tmp_path/'published'
    source.mkdir(); target.mkdir()
    (source/'evidence').write_bytes(b'exact')
    identity = target.stat().st_ino
    with pytest.raises(FileExistsError):
        package._publish_directory(source, target)
    assert target.stat().st_ino == identity and list(target.iterdir()) == []
    assert (source/'evidence').read_bytes() == b'exact'

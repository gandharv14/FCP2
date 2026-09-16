import io,json
from pathlib import Path
import pytest
from scripts.fleet import stream_source_custody as S
from scripts.fleet.migration import retain,import_files,checked


def test_streamed_archive_reuses_existing_full_integrity_import(tmp_path):
    src=tmp_path/'original';src.mkdir();p=src/'edges.csv';p.write_bytes(b'row,cell,formula\n'*5000);p.chmod(0o755)
    rows=S.inventory([p]);sink=io.BytesIO();archive=S.stream(rows,sink);f=tmp_path/'archive.tar.gz';f.write_bytes(sink.getvalue())
    m=retain(tmp_path/'manifest.json',S.manifest(rows,archive));receipt=import_files(f,m,tmp_path/'receipt.json',[src],namespace_root=tmp_path/'offline')
    actual=tmp_path/'offline'/str(p).lstrip('/');assert actual.read_bytes()==p.read_bytes() and actual.stat().st_mode&0o777==0o755
    assert checked(receipt)['historical_bytes_rewritten'] is False


def test_changed_source_rejected_before_stream(tmp_path):
    p=tmp_path/'source';p.write_bytes(b'before');rows=S.inventory([p]);p.write_bytes(b'after')
    with pytest.raises(ValueError,match='changed'):S.stream(rows,io.BytesIO())


def test_stream_writer_handles_partial_writes():
    class Partial(io.BytesIO):
        def write(self,data):return super().write(data[:3])
    sink=Partial();w=S.HashWriter(sink);assert w.write(b'0123456789')==10;assert sink.getvalue()==b'0123456789'


def test_credentials_and_symlinks_cannot_enter_custody(tmp_path):
    p=tmp_path/'.env';p.write_text('not a real credential')
    with pytest.raises(ValueError,match='credential'):S.inventory([p])
    alias=tmp_path/'alias';alias.symlink_to(p)
    with pytest.raises(ValueError):S.inventory([alias])

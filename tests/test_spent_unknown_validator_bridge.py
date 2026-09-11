"""The new root selector remains exact and never changes original requests."""
from pathlib import Path
import importlib.util,json,hashlib
import pytest

@pytest.fixture
def bridge(tmp_path,monkeypatch):
    path=Path(__file__).resolve().parents[1]/'diagnostics/requested-fixes-20260910/0274-spent-unknown-bridge/scripts/batch/harbor_normalization_validator_bridge.py'
    spec=importlib.util.spec_from_file_location('spent_bridge_fixture',path);B=importlib.util.module_from_spec(spec);spec.loader.exec_module(B)
    def record(p):return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
    def keep(p,value):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value));return record(p)
    old=tmp_path/'original';new=tmp_path/'successor'
    names={'scripts/batch/harbor_normalization_partitioned.py','scripts/batch/harbor_model_executor.py','scripts/batch/harbor_partition_code_applicability.py','gen_normalizer.py'}
    helper='scripts/batch/harbor_partition_spent_unknown.py'
    for root in [old,new]:
        for name in names:
            p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('same' if name=='gen_normalizer.py' else root.name)
    (new/helper).write_text('approved helper')
    def snapshot(root):return dict(snapshot=dict(root=str(root),files=[dict(relative=str(p.relative_to(root)),sha256=record(p)['sha256']) for p in root.rglob('*') if p.is_file()]))
    old_snapshot=keep(tmp_path/'normalization-snapshot.json',snapshot(old));new_snapshot=keep(tmp_path/'normalization-spent-unknown-snapshot.json',snapshot(new))
    monkeypatch.setattr(B,'APPROVED_SNAPSHOT_SHA256',old_snapshot['sha256']);monkeypatch.setattr(B,'SPENT_SNAPSHOT_SHA256',new_snapshot['sha256'])
    prepared=keep(tmp_path/'partitions/prepared.json',dict(root=str(tmp_path/'partitions'),code={str(old/n):record(old/n)['sha256'] for n in names}))
    monkeypatch.setattr(B,'SPENT_PREPARED_SHA256',prepared['sha256'])
    auth=keep(tmp_path/'authorization.json',dict(schema_version='normalization-spent-unknown/v1',prepared=prepared,index=12,code={str(new/n):record(new/n)['sha256'] for n in (names-{'gen_normalizer.py'})|{helper}}))
    marker=keep(tmp_path/'partitions/spent-unknown-continuation.json',dict(schema_version='normalization-spent-unknown/v1',authorization=auth))
    return B,prepared,old,new,auth,marker,record,keep

@pytest.mark.parametrize('tamper',[None,'extra','old_code','unrelated_new_code','authorization','foreign_prepared','snapshot','selector'])
def test_only_pinned_successor_for_exact_original_plan(bridge,tamper,monkeypatch):
    B,prepared,old,new,auth,marker,record,keep=bridge
    if tamper=='extra':(new/'injected.py').write_text('bad')
    if tamper=='old_code':(old/'gen_normalizer.py').write_text('bad')
    if tamper=='unrelated_new_code':(new/'gen_normalizer.py').write_text('bad')
    if tamper=='authorization':keep(Path(auth['path']),dict(code={}))
    if tamper=='foreign_prepared':monkeypatch.setattr(B,'SPENT_PREPARED_SHA256','0'*64)
    if tamper=='snapshot':(new.parent/'normalization-spent-unknown-snapshot.json').write_text('{}')
    if tamper=='selector':keep(Path(marker['path']),dict(schema_version='wrong',authorization=auth))
    if tamper is not None:
        with pytest.raises((ValueError,KeyError)):B._approved(prepared)
    else:
        root,snapshot,files=B._approved(prepared)
        assert root==new and str(old/'gen_normalizer.py') in files and str(new/'gen_normalizer.py') in files
        Path(marker['path']).unlink()
        assert B._approved(prepared)[0]==old

from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.fleet.disclosure_reserved_round import equivalent_snapshots
from scripts.fleet.migration import binding,retain

@pytest.fixture
def selected(tmp_path):
    root=tmp_path/'code';root.mkdir();p=root/'original.py';p.write_text('original bytes')
    old=retain(tmp_path/'deployment.json',dict(root=str(root),fingerprint='code',bindings={'original.py':binding(p)},parent={'path':'/parent','sha256':'a'*64}))
    native=retain(tmp_path/'native.json',dict(root=str(root),files=[dict(relative='original.py',sha256=binding(p)['sha256'])]))
    from scripts.batch.harbor_disclosure_partition_cache import inventory
    return old,native,SimpleNamespace(inventory=inventory),root


def test_same_complete_code_accepts_native_manifest_without_rewriting(selected):
    a,b,cache,root=selected;before={p:p.read_bytes()for p in root.rglob('*')if p.is_file()}
    equivalent_snapshots([a],[b],cache)
    assert all(p.read_bytes()==raw for p,raw in before.items())


def test_changed_actual_code_fails(selected):
    a,b,cache,root=selected;(root/'original.py').write_text('changed')
    with pytest.raises(ValueError):equivalent_snapshots([a],[b],cache)


def test_undeclared_actual_code_fails(selected):
    a,b,cache,root=selected;(root/'foreign.py').write_text('extra')
    with pytest.raises(ValueError):equivalent_snapshots([a],[b],cache)


def test_missing_inventory_correspondence_fails(selected):
    a,b,cache,_=selected
    with pytest.raises(ValueError):equivalent_snapshots([a],[],cache)


def test_mismatched_manifest_is_not_equivalent(selected,tmp_path):
    a,b,cache,root=selected
    native=retain(tmp_path/'foreign.json',dict(root=str(root),files=[dict(relative='original.py',sha256='f'*64)]))
    with pytest.raises(ValueError,match='equivalent code'):equivalent_snapshots([a],[native],cache)


def test_original_deployment_inventory_without_parent_is_supported(selected,tmp_path):
    from scripts.fleet.migration import checked
    a,b,cache,root=selected;value=checked(a);value.pop('parent')
    old=retain(tmp_path/'original-deployment.json',value)
    equivalent_snapshots([old],[b],cache)


def test_unknown_deployment_metadata_is_rejected(selected,tmp_path):
    from scripts.fleet.migration import checked
    a,b,cache,root=selected;value=checked(a);value['unknown']=True
    old=retain(tmp_path/'unknown-deployment.json',value)
    with pytest.raises(ValueError):equivalent_snapshots([old],[b],cache)

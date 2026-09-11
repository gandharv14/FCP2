"""Real bytes/tree hash fixtures; no financial or runtime gate replacements."""
import importlib.util
from dataclasses import dataclass
from pathlib import Path
import os,sys
import pytest
from scripts.batch import harbor_complete_readiness as live
from scripts.batch.harbor_admission import record
import xl_release_publication as publication

p=live


def selected(binding, **more):
    return p.static.Selection('0245',Path('/unused'), '0'*64, {'mcp_build_a':binding,**more},Path('/unused'),Path('/unused'),None,None)


def fixture(tmp):
    root=tmp/'build';(root/'nested').mkdir(parents=True)
    (root/'one.json').write_text('{"synthetic":true}')
    (root/'nested/two.txt').write_text('exact bytes\n')
    return root,{'path':str(root),'sha256':publication._tree_hash(root)[0]}


def test_reproduces_live_directory_rejection_and_expands_exact_files(tmp_path):
    root,b=fixture(tmp_path)
    expected=sorted([record(root/'one.json'),record(root/'nested/two.txt')],key=lambda x:x['path'])
    assert p.evidence_records(selected(b))==expected
    assert p.evidence_records([selected(b),*expected,selected(b)])==expected
    assert all(Path(x['path']).is_file() for x in expected)


def test_nested_typed_selection_and_overlapping_subtree(tmp_path):
    root,b=fixture(tmp_path)
    @dataclass
    class Context:
        bindings:dict
    inner={'path':str(root/'nested'),'sha256':publication._tree_hash(root/'nested')[0]}
    assert p.evidence_records(Context({'typed':selected(b,mcp_build_b=inner)}))==p.evidence_records(selected(b))


@pytest.mark.parametrize('kind',['content','added','deleted','renamed','size','wrong_hash','file_hash','missing','empty','relative','bad_hash','unknown_entry'])
def test_altered_or_ambiguous_directory_rejected(tmp_path,kind):
    root,b=fixture(tmp_path)
    if kind=='content':(root/'one.json').write_text('different')
    elif kind=='added':(root/'extra').write_text('extra')
    elif kind=='deleted':(root/'one.json').unlink()
    elif kind=='renamed':(root/'one.json').rename(root/'other.json')
    elif kind=='size':(root/'nested/two.txt').write_bytes(b'')
    elif kind=='wrong_hash':b['sha256']='0'*64
    elif kind=='file_hash':b['sha256']=record(root/'one.json')['sha256']
    elif kind=='missing':b['path']=str(tmp_path/'missing')
    elif kind=='empty':
        root=tmp_path/'empty';root.mkdir();b={'path':str(root),'sha256':publication._tree_hash(root)[0]}
    elif kind=='relative':b['path']='build'
    elif kind=='bad_hash':b['sha256']=True
    else:os.mkfifo(root/'pipe')
    with pytest.raises((ValueError,OSError)):p.evidence_records(selected(b))


@pytest.mark.parametrize('kind',['root_link','ancestor_link','child_link','broken_link'])
def test_all_symlink_forms_rejected(tmp_path,kind):
    root,b=fixture(tmp_path)
    if kind=='root_link':
        link=tmp_path/'link';link.symlink_to(root,target_is_directory=True);b['path']=str(link)
    elif kind=='ancestor_link':
        link=tmp_path/'link';link.symlink_to(tmp_path,target_is_directory=True);b['path']=str(link/'build')
    elif kind=='child_link':(root/'link').symlink_to(root/'one.json')
    else:(root/'broken').symlink_to(root/'absent')
    with pytest.raises(ValueError):p.evidence_records(selected(b))


def test_conflicting_file_and_tree_bindings_rejected(tmp_path):
    root,b=fixture(tmp_path);f=record(root/'one.json')
    for inputs in ([selected(b),{**f,'sha256':'0'*64}],[selected({**b,'sha256':'f'*64}),selected(b)]):
        with pytest.raises(ValueError):p.evidence_records(inputs)


@pytest.mark.parametrize('when',[1,2,3])
def test_race_during_or_after_enumeration_rejected(tmp_path,monkeypatch,when):
    root,b=fixture(tmp_path);original=p.static.publication._tree_hash;calls=[]
    def mutate(path):
        result=original(path);calls.append(path)
        if len(calls)==when:(root/'one.json').write_text('raced')
        return result
    monkeypatch.setattr(p.static.publication,'_tree_hash',mutate)
    with pytest.raises(ValueError):p.evidence_records(selected(b))


def test_file_only_contract_remains_strict_and_unchanged(tmp_path):
    root,_=fixture(tmp_path);f=record(root/'one.json')
    assert p.evidence_records([f,f])==[f]
    (root/'one.json').write_text('changed')
    with pytest.raises(ValueError):p.evidence_records(f)


def test_untyped_or_unknown_tree_fields_remain_file_only(tmp_path):
    root,b=fixture(tmp_path)
    for value in (b,{'mcp_build_a':b},selected(b,arbitrary_tree=b),selected(b,source_file=b)):
        with pytest.raises(ValueError):p.evidence_records(value)


def test_file_with_identical_inventory_serialization_is_not_a_tree(tmp_path):
    import shutil
    root,b=fixture(tmp_path)
    _,inventory=publication._tree_hash(root)
    raw=publication._canonical_bytes(inventory)
    shutil.rmtree(root);root.write_bytes(raw)
    assert record(root)['sha256']==b['sha256']  # Same hash input, no collision.
    with pytest.raises(ValueError,match='not a directory'):p.evidence_records(selected(b))
    assert p.evidence_records(b)==[b]  # This location is a valid FILE when explicitly selected as such.


@pytest.mark.parametrize('field',['mcp_build_a','mcp_build_b','mcp_bundle','staged_bundle'])
def test_each_known_tree_field_is_typed_and_complete(tmp_path,field):
    root,b=fixture(tmp_path);s=selected(b);s.bindings.clear();s.bindings[field]=b
    assert len(p.evidence_records(s))==2
    s.bindings[field]={**b,'unrecognized':True}
    with pytest.raises(ValueError):p.evidence_records(s)

import json
from pathlib import Path
import pytest
from scripts.fleet import migration_symlinks as M

def fixture(tmp_path):
    root=tmp_path/'historical';target=root/'generations'/'abc';target.mkdir(parents=True);(target/'bands.csv').write_text('source evidence\n')
    current=root/'current';current.symlink_to('generations/abc',target_is_directory=True)
    alias=root/'bands.csv';alias.symlink_to('current/bands.csv')
    rows=[M.describe(p,root) for p in (alias,current)];alias.unlink();current.unlink()
    manifest=tmp_path/'aliases.json';manifest.write_text(json.dumps(dict(schema_version=M.SCHEMA,root=str(root),aliases=rows)))
    return root,target,manifest,tmp_path/'receipt.json'

def restore(values):
    root,target,m,r=values;return M.restore(m,M.sha(m.read_bytes()),r,root)

def test_exact_directory_then_file_alias_and_replay_receipt(tmp_path):
    v=fixture(tmp_path);assert restore(v)['aliases']==2
    assert (v[0]/'bands.csv').readlink()==Path('current/bands.csv')
    assert (v[0]/'current').readlink()==Path('generations/abc')
    assert (v[0]/'bands.csv').read_bytes()==(v[1]/'bands.csv').read_bytes()
    with pytest.raises(ValueError,match='receipt exists'):restore(v)

def test_target_changed_before_restore_is_rejected(tmp_path):
    v=fixture(tmp_path);(v[1]/'bands.csv').write_text('changed')
    with pytest.raises(ValueError,match='target changed'):restore(v)
    assert not (v[0]/'current').is_symlink() and not v[3].exists()

def test_existing_path_never_overwritten(tmp_path):
    v=fixture(tmp_path);(v[0]/'current').write_text('existing')
    with pytest.raises(ValueError,match='no overwrite'):restore(v)
    assert (v[0]/'current').read_text()=='existing' and not (v[0]/'bands.csv').is_symlink()

@pytest.mark.parametrize('link',['/tmp/outside','../../outside','../other'])
def test_unapproved_absolute_or_parent_traversal_rejected(tmp_path,link):
    v=fixture(tmp_path);value=json.loads(v[2].read_text());value['aliases'][0]['link_text']=link;v[2].write_text(json.dumps(value))
    with pytest.raises(ValueError,match='traversal forbidden'):restore(v)
    assert not (v[0]/'current').is_symlink()

def test_manifest_hash_and_credential_target_rejected(tmp_path):
    v=fixture(tmp_path)
    with pytest.raises(ValueError,match='manifest changed'):M.restore(v[2],'0'*64,v[3],v[0])
    (v[1]/'.env').write_text('fixture secret')
    with pytest.raises(ValueError,match='credential target'):M.target_digest(v[1])

def test_symlink_ancestor_rejected(tmp_path):
    v=fixture(tmp_path);alias=tmp_path/'parent';alias.symlink_to(v[0],target_is_directory=True)
    with pytest.raises(ValueError,match='regular resolved target'):M.target_digest(alias/'generations/abc/bands.csv')

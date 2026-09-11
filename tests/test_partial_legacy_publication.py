import json
from pathlib import Path
import pytest
import xl_release_publication as r
from scripts import qualified_publication as q
from test_release_publication import _fixture,_publish


def pointer(case,kind):
    root=case[kind+'_root'];generation=case[('source' if kind=='source' else 'segmentation')+'_id']
    p=root/'current.json'
    p.write_text(json.dumps({'schema_version':kind+'-current/v1','generation_id':generation,
        'generation_path':'generations/'+generation,
        'manifest_sha256':r._sha256(root/'generations'/generation/'generation-manifest.json')}))
    return p


def freeze(case):
    return r.freeze_partial_legacy_snapshot(case['release_root'],case['workbook'],source_root=case['source_root'],
        source_generation_id=case['source_id'],segmentation_root=case['segmentation_root'],segmentation_generation_id=case['segmentation_id'])


@pytest.mark.parametrize('kind',['source','segmentation'])
def test_partial_tuple_publishes_without_inventing_absent_pointer(tmp_path,monkeypatch,kind):
    case=_fixture(tmp_path,monkeypatch,freeze_legacy=False);p=pointer(case,kind);raw=p.read_bytes()
    directory,snapshot=freeze(case)
    assert r.validate_legacy_snapshot(directory)==snapshot
    assert freeze(case)[1]==snapshot
    out,manifest=_publish(case,legacy_snapshot_hash=snapshot['snapshot_hash'])
    assert out.is_dir() and manifest['legacy_snapshot_hash']==snapshot['snapshot_hash']
    assert p.read_bytes()==raw
    other='source' if kind=='segmentation' else 'segmentation'
    assert not (case[other+'_root']/'current.json').exists()
    assert r.resolve_current_release(case['release_root'],source_root=case['source_root'],
        segmentation_root=case['segmentation_root'],task_root=case['release_root'])[1]==manifest


@pytest.mark.parametrize('fault',['absent_appears','present_changes','archived_changes','manifest_changes','symlink','wrong_id','unknown_schema','extra_files'])
def test_partial_snapshot_rejects_pointer_or_generation_drift(tmp_path,monkeypatch,fault):
    case=_fixture(tmp_path,monkeypatch,freeze_legacy=False);p=pointer(case,'segmentation');directory,_=freeze(case)
    if fault=='absent_appears':pointer(case,'source')
    elif fault=='present_changes':p.write_text('{}')
    elif fault=='archived_changes':(directory/'segmentation-current.json').write_text('{}')
    elif fault=='manifest_changes':(case['segmentation_root']/'generations'/case['segmentation_id']/'generation-manifest.json').write_text('{}')
    elif fault=='symlink':p.unlink();p.symlink_to(directory/'segmentation-current.json')
    elif fault=='extra_files':(directory/'extra').write_text('extra')
    else:
        v=json.loads(p.read_text())
        if fault=='wrong_id':v['generation_id']='f'*64
        else:v['schema_version']='unknown'
        p.write_text(json.dumps(v))
    with pytest.raises((r.ReleasePublicationError,FileNotFoundError)):r.validate_legacy_snapshot(directory)


def test_qualified_adapter_selects_partial_mode_and_preserves_current_manifest(tmp_path,monkeypatch):
    case=_fixture(tmp_path,monkeypatch,freeze_legacy=False);pointer(case,'segmentation')
    directory,manifest=q.publish_or_resume(case['release_root'],case['workbook'],source_root=case['source_root'],
        source_generation_id=case['source_id'],segmentation_root=case['segmentation_root'],
        segmentation_generation_id=case['segmentation_id'],task_generation_id=case['task_id'],
        expected_current_release_id=None,output_path=tmp_path/'output')
    assert manifest['prior_release_id'] is None
    assert (tmp_path/'output').is_dir()
    assert q.publish_or_resume(case['release_root'],case['workbook'],source_root=case['source_root'],
        source_generation_id=case['source_id'],segmentation_root=case['segmentation_root'],
        segmentation_generation_id=case['segmentation_id'],task_generation_id=case['task_id'],
        expected_current_release_id=None,output_path=tmp_path/'output',resuming=True)[1]==manifest

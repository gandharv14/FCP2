import json,sys,tarfile
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import recursion_hardening_image as image
import recursion_hardening_campaign as campaign

def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))

def test_missing_pinned_research_file_is_rejected(tmp_path,monkeypatch):
    monkeypatch.setattr(image,'WORK',tmp_path)
    write(tmp_path/'runtime/overlay-manifest.json',{'files':[],'directories':[]})
    workbook=tmp_path/'task-inputs.xlsx';workbook.write_bytes(b'PK example')
    root=tmp_path/'research';root.mkdir()
    row={'taskId':'task','workbookPath':str(workbook),'workbookSha256':image.sha(workbook),
         'additionalAssumptionsPath':None,'researchRoot':str(root),'researchFiles':{str(root/'missing.md'):'abc'}}
    with pytest.raises(AssertionError,match='Research file set changed'):
        image.make_layer(tmp_path/'build',[row])

def test_only_pinned_solver_assets_enter_layer(tmp_path,monkeypatch):
    monkeypatch.setattr(image,'WORK',tmp_path)
    write(tmp_path/'runtime/overlay-manifest.json',{'files':[],'directories':[]})
    workbook=tmp_path/'task-inputs.xlsx';workbook.write_bytes(b'PK example')
    key=tmp_path/'answer_key.json';key.write_text('{"hidden":123}')
    row={'taskId':'task','workbookPath':str(workbook),'workbookSha256':image.sha(workbook),
         'answerKeyPath':str(key),'additionalAssumptionsPath':None,'researchRoot':None}
    path,_=image.make_layer(tmp_path/'build',[row])
    with tarfile.open(path) as tf:
        assert set(tf.getnames())=={'opt/fcp/tasks/.wh..wh..opq','opt/fcp/tasks/task/task-inputs.xlsx'}

def test_stale_image_asset_fingerprint_blocks_remote_mutations(tmp_path,monkeypatch):
    monkeypatch.setattr(campaign,'WORK',tmp_path)
    write(tmp_path/'final-assets.json',[]);write(tmp_path/'runtime/overlay-manifest.json',{})
    write(tmp_path/'campaign.json',{'status':'validated','tasks':[],
        'assetsSha256':campaign.sha(tmp_path/'final-assets.json'),
        'runtimeManifestSha256':campaign.sha(tmp_path/'runtime/overlay-manifest.json'),'graderSources':{}})
    write(tmp_path/'final-images/opus-image.json',{'assetsManifestSha256':'stale'})
    with pytest.raises(AssertionError):campaign.validate_campaign()

def test_runtime_only_successor_requires_both_exact_asset_fingerprints(tmp_path):
    for model in ('opus','gemini'):
        write(tmp_path/(model+'-image.json'),{'assetsManifestSha256':'selected',
            'verifiedAt':'2026-09-15','image':'registry/'+model+'@sha256:abc'})
    assert set(image.verified_base_images(tmp_path,'selected'))=={'opus','gemini'}
    with pytest.raises(AssertionError,match='Base task assets differ'):
        image.verified_base_images(tmp_path,'changed')
    with pytest.raises(AssertionError,match='requires pinned task assets'):
        image.verified_base_images(tmp_path,None)

import json
from pathlib import Path
import pytest
from scripts.fleet import export_0190 as E


def test_exports_required_code_and_references_without_expanding_survey(tmp_path,monkeypatch):
    q=tmp_path/'q';home=q/'authoring/0190-generated-02';home.mkdir(parents=True)
    monkeypatch.setattr(E.R,'BASE',q);monkeypatch.setattr(E.R,'HOME',home)
    snapshots=[q/'0190-existing-review-snapshot-v5.json',q/'generated-curation-snapshot-v1.json',q/'0190-existing-review-snapshot-v4.json']
    for n,p in enumerate(snapshots):
        root=q/'code'/str(n);root.mkdir(parents=True);(root/'a.py').write_text('pass');(root/'.pipeline-code.json').write_text('{}');p.write_text(json.dumps({'root':str(root)}))
    monkeypatch.setattr(E.R,'SNAPSHOT',{'path':str(snapshots[0])})
    inputs=q/'authority-inputs-v4.json';inputs.write_text('{}');monkeypatch.setattr(E.R,'INPUTS',{'path':str(inputs)})
    (home/'.env').write_text('secret')
    (home/'survey.json').write_text(json.dumps({'unrelated':'/Users/gandharvmahajan/unrelated.json'}))
    found=E.closure()
    assert home/'.env' not in found and home/'survey.json' in found
    assert all(q/'code'/str(n)/'a.py' in found for n in range(3))

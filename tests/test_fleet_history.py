import json
from pathlib import Path
import pytest
from scripts.fleet import history

def certificate(tmp_path, monkeypatch):
    root=tmp_path/'real-history';root.mkdir()
    (root/'0002-outputs').mkdir()
    pause=tmp_path/'pause.json';pause.write_text(json.dumps({'new_dispatch_allowed':False}))
    out=tmp_path/'history.json'
    bound=history.capture([str(root)],{'0001','0002'},['0001'],pause,out)
    for k,v in {'HARBOR_FLEET_HISTORY_PATH':str(out),'HARBOR_FLEET_HISTORY_SHA256':bound['sha256'],
        'HARBOR_FLEET_WORKBOOK':'0001','HARBOR_FLEET_URL':'http://127.0.0.1:8765',
        'HARBOR_FLEET_TOKEN_FILE':'unused','HARBOR_FLEET_OWNER':'worker',
        'HARBOR_FLEET_CLAIM_TOKEN':'private','HARBOR_FLEET_FENCE':'1'}.items():monkeypatch.setenv(k,v)
    return root,out

def test_projection_preserves_positive_history_and_requires_live_claim(tmp_path,monkeypatch):
    root,out=certificate(tmp_path,monkeypatch)
    calls=[]
    class Client:
        def __init__(self,*a):pass
        def call(self,*a,**kw):calls.append((a,kw));return {}
    monkeypatch.setattr('scripts.fleet.coordinator.Client',Client)
    excluded,histories=history.projected_survey([root],{'0001','0002'})
    assert '0002' in excluded and not histories and calls[0][0]==('heartbeat',)
    def fail(*a,**kw):raise RuntimeError('custody lost')
    monkeypatch.setattr(Client,'call',fail)
    with pytest.raises(RuntimeError,match='custody lost'):history.projected_survey([root],{'0001','0002'})

def test_projection_tamper_roots_and_denominator_rejected(tmp_path,monkeypatch):
    root,out=certificate(tmp_path,monkeypatch)
    with pytest.raises(ValueError,match='namespace'):history.projected_survey([root],{'0001'})
    monkeypatch.setenv('HARBOR_FLEET_WORKBOOK','0002')
    with pytest.raises(ValueError,match='fresh'):history.projected_survey([root],{'0001','0002'})
    out.write_text(out.read_text()+' ')
    with pytest.raises(ValueError,match='changed'):history.projected_survey([root],{'0001','0002'})

def test_capture_refuses_known_task(tmp_path):
    root=tmp_path/'history';root.mkdir();(root/'0001-outputs').mkdir()
    pause=tmp_path/'pause.json';pause.write_text(json.dumps({'new_dispatch_allowed':False}))
    with pytest.raises(ValueError,match='has history'):
        history.capture([str(root)],{'0001'},['0001'],pause,tmp_path/'certificate.json')

def test_capture_includes_new_namespaces_without_changing_historical_scope(tmp_path):
    root=tmp_path/'old';root.mkdir();new=tmp_path/'new';new.mkdir()
    (new/'0002-outputs').mkdir()
    pause=tmp_path/'pause.json';pause.write_text(json.dumps({'new_dispatch_allowed':False}))
    path=tmp_path/'certificate.json'
    history.capture([str(root)],{'0001','0002'},['0001'],pause,path,supplemental_roots=[new])
    value=json.loads(path.read_text())
    assert value['roots']==[str(root)] and value['scan_roots']==[str(root),str(new)]
    assert '0002' in value['excluded']

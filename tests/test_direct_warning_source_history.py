import json,os
import pytest
from scripts.fleet import retained_warning_continue as W


def case(tmp_path,monkeypatch):
 for k in ('HARBOR_FLEET_HISTORY_PATH','HARBOR_FLEET_CONTINUATION_HISTORY_PATH','HARBOR_FLEET_SOURCE_HISTORY_PATH','HARBOR_FLEET_LEGACY_RESEARCH_PATH','HARBOR_FLEET_LEGACY_RESEARCH_SHA256'):monkeypatch.delenv(k,raising=False)
 def put(name,value):
  p=tmp_path/name;p.write_text(json.dumps(value));return W.binding(p)
 source={'path':'/source/0055.xlsx','sha256':'a'*64};descriptor=put('descriptor',{'original':'descriptor'});result=put('result',{'descriptor':descriptor});config=put('config',{'original':'config'})
 history=put('history',dict(workbook='0055',original=source));fresh=put('fresh',dict(workbook='0055',legacy_research_history=history))
 prior=put('prior',dict(workbook='0055',result=result,descriptor=descriptor,source=source,fresh_plan=fresh))
 return dict(workbook='0055',source_sha256='a'*64,old_result=result,source_history=prior),dict(selection=dict(workbook='0055',checkpoint=result,config=config)),history


def test_completed_takeover_carries_exact_history_read_only(tmp_path,monkeypatch):
 p,tp,h=case(tmp_path,monkeypatch);before={x.name:x.read_bytes()for x in tmp_path.iterdir()}
 W.select_direct_history(p,tp);W.select_direct_history(p,tp)
 assert os.environ['HARBOR_FLEET_LEGACY_RESEARCH_PATH']==h['path'] and os.environ['HARBOR_FLEET_LEGACY_RESEARCH_SHA256']==h['sha256']
 assert before=={x.name:x.read_bytes()for x in tmp_path.iterdir()}


@pytest.mark.parametrize('key',['workbook','checkpoint'])
def test_foreign_takeover_selection_rejected(tmp_path,monkeypatch,key):
 p,tp,h=case(tmp_path,monkeypatch);tp['selection'][key]='foreign'
 with pytest.raises(ValueError):W.select_direct_history(p,tp)
 assert 'HARBOR_FLEET_LEGACY_RESEARCH_PATH'not in os.environ


@pytest.mark.parametrize('change',['source','history_bytes','foreign_env'])
def test_foreign_source_or_history_rejected(tmp_path,monkeypatch,change):
 p,tp,h=case(tmp_path,monkeypatch)
 if change=='source':p['source_sha256']='b'*64
 elif change=='history_bytes':open(h['path'],'w').write('{}')
 else:monkeypatch.setenv('HARBOR_FLEET_SOURCE_HISTORY_PATH','foreign')
 with pytest.raises(ValueError):W.select_direct_history(p,tp)


def test_old_direct_plan_unchanged():
 W.select_direct_history({}, {})


def test_mismatched_descriptor_rejected(tmp_path,monkeypatch):
 p,tp,h=case(tmp_path,monkeypatch);v=W.read(p['source_history']);v['descriptor']={'path':'foreign','sha256':'b'*64}
 from pathlib import Path
 Path(p['source_history']['path']).write_text(json.dumps(v));p['source_history']=W.binding(p['source_history']['path'])
 with pytest.raises(ValueError):W.select_direct_history(p,tp)

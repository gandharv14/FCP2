import json
import pytest
from scripts.fleet import publish as P

def put(p,v):
 p.write_text(json.dumps(v));return P.binding(p)

@pytest.fixture(params=['live','completed_takeover'])
def selection(tmp_path,monkeypatch,request):
 source={'path':'/source/0570.xlsx','sha256':'a'*64}
 history=put(tmp_path/'history.json',dict(workbook='0570',original=source))
 fresh=put(tmp_path/'fresh.json',dict(workbook='0570',legacy_research_history=history))
 renewal=put(tmp_path/'renewal.json',dict(workbook='0570',source=source,fresh_plan=fresh))
 prior=put(tmp_path/'prior.json',dict(workbook='0570',source_sha256=source['sha256'],**({'live_renewal':dict(plan=renewal)}if request.param=='live'else {'source_history':renewal})))
 plan=dict(workbook='0570',source_sha256=source['sha256'],code_root='/immutable/code',retained_warning=dict(continuation_plan=prior))
 monkeypatch.setattr(P,'history_binding',lambda p:prior)
 return plan,history

def test_cold_environment_carries_actual_legacy_authority_and_no_model_access(selection,monkeypatch):
 plan,history=selection
 for key in ('HARBOR_FLEET_HISTORY_PATH','HARBOR_FLEET_CONTINUATION_HISTORY_PATH','HARBOR_FLEET_MODEL_JOURNAL','OPENAI_API_KEY'):
  monkeypatch.setenv(key,'foreign')
 env=P.publication_environment(plan,dict(workbook='0570',owner='root-publication',token='fixture',fence=5),action='remote',url='http://coordinator',token_file='/private/token')
 assert env['HARBOR_FLEET_LEGACY_RESEARCH_PATH']==history['path'] and env['HARBOR_FLEET_LEGACY_RESEARCH_SHA256']==history['sha256']
 assert not any(k in env for k in ('HARBOR_FLEET_HISTORY_PATH','HARBOR_FLEET_CONTINUATION_HISTORY_PATH','HARBOR_FLEET_MODEL_JOURNAL','OPENAI_API_KEY'))
 assert env['HARBOR_FLEET_PUBLICATION_ONLY']=='1'

@pytest.mark.parametrize('change',['workbook','source','bytes'])
def test_foreign_or_changed_legacy_history_rejected(selection,change):
 plan,history=selection
 if change=='workbook':plan['workbook']='0001'
 elif change=='source':plan['source_sha256']='b'*64
 else:
  with open(history['path'],'a')as f:f.write(' ')
 with pytest.raises(ValueError):P.publication_environment(plan,dict(workbook=plan['workbook'],owner='root',token='fixture',fence=5),action='remote',url='http://coordinator',token_file='/private/token')

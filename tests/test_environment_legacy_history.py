"""Closed original legacy plan selection in parent and cold continuation readers."""
import json,types
import pytest
from scripts.fleet import environment_checkpoint_continue as E
from scripts.batch import harbor_legacy_research_history as L

def put(p,v):
 p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v));return E.binding(p)

@pytest.fixture
def selection(tmp_path,monkeypatch):
 for k in tuple(E.os.environ):
  if k.startswith('HARBOR_FLEET_'):monkeypatch.delenv(k,raising=False)
 # Register keys created directly by the selected production reader so pytest
 # restores absence as well as values inherited at fixture entry.
 for suffix in ('PATH','SHA256'):
  key='HARBOR_FLEET_LEGACY_RESEARCH_'+suffix
  monkeypatch.setenv(key,'');monkeypatch.delenv(key)
 source=put(tmp_path/'0198.json',{});legacy=put(tmp_path/'legacy.json',{'original':source})
 inventory=put(tmp_path/'inventory.json',{'sources':[{'workbook_id':'0198'}]})
 root=tmp_path/'code'/'version';root.mkdir(parents=True);(root/'scripts/fleet').mkdir(parents=True);(root/'scripts/fleet/fresh.py').write_text('# original fixture')
 handoff=put(tmp_path/'handoff.json',{'workbook':'0198','state':'VERIFIED'})
 driver=put(tmp_path/'driver.json',{});snapshot=put(tmp_path/'snapshot.json',{'root':str(root)})
 config=put(tmp_path/'config.json',{'run_root':str(tmp_path/'home/authoring'),'code':snapshot})
 original=put(tmp_path/'original.json',{'driver':driver,'config':config})
 fresh={'workbook':'0198','code_root':str(root),'legacy_research_history':legacy,'inventory':inventory,'history_roots':['/retained'],'reconstruction':{'handoff':handoff},'home':str(tmp_path/'home'),'dispatcher':driver}
 row=put(tmp_path/'plan.json',fresh)
 job={'inputs':[row,legacy],'workbook':'0198','source':source,'version':'version','command':['python','-I','-B',str(root/'scripts/fleet/fresh.py'),'run','--plan',row['path'],'--plan-sha256',row['sha256']]}
 calls=[]
 def validate(row,**kw):
  assert row==legacy and kw['original']==source and kw['workbook']=='0198'
  calls.append('complete_history');return {'retained':7}
 def original_validate(p,survey):
  assert p==fresh and survey(['/retained'],{'0198'})==({'old':'held'},{'0198':['preserved']})
  calls.append('original_fresh');return source
 monkeypatch.setattr(L,'validate',validate)
 monkeypatch.setattr(L,'author_survey',lambda p:({'old':'held'},{'0198':['preserved']}))
 monkeypatch.setattr(L,'source_handoff',lambda l,h:calls.append('source_handoff') if l==legacy and h['state']=='VERIFIED'else pytest.fail('wrong handoff'))
 monkeypatch.setattr(E,'module',lambda n,r:types.SimpleNamespace(_validate=original_validate))
 return {'workbook':'0198','source':source,'original_job':original},job,legacy,calls

def test_live_selection_preserves_original_owning_projection(selection):
 p,job,legacy,calls=selection;original=L.projected_survey
 E.select_history(p,job)
 assert calls==['complete_history','original_fresh','source_handoff']
 assert E.os.environ['HARBOR_FLEET_LEGACY_RESEARCH_PATH']==legacy['path']
 assert L.projected_survey is original

@pytest.mark.parametrize('defect',['plan_command','missing_legacy','workbook','source','version','conflicting_projection','foreign_legacy'])
def test_closed_original_history_rejects_foreign_custody(selection,defect,monkeypatch):
 p,job,legacy,calls=selection
 if defect=='plan_command':job['command'][-1]='0'*64
 elif defect=='missing_legacy':job['inputs'].pop()
 elif defect=='workbook':job['workbook']='0001'
 elif defect=='source':job['source']={**job['source'],'sha256':'0'*64}
 elif defect=='version':job['version']='foreign'
 elif defect=='conflicting_projection':monkeypatch.setenv('HARBOR_FLEET_HISTORY_PATH','foreign')
 else:monkeypatch.setenv('HARBOR_FLEET_LEGACY_RESEARCH_PATH','foreign')
 with pytest.raises(ValueError):E.select_history(p,job)

def test_changed_original_plan_rejected_before_readers(selection):
 p,job,_,calls=selection
 with open(job['inputs'][0]['path'],'a')as f:f.write(' ')
 with pytest.raises(ValueError,match='changed'):E.select_history(p,job)
 assert not calls

from pathlib import Path
import copy,json
import pytest
from scripts.fleet import source_authoring_history as N
from scripts.fleet.migration import binding,checked
ROOT=Path(__file__).resolve().parents[1];H=ROOT/'diagnostics/vm-fleet-20260912/control/source-to-authoring-001'

def actual(monkeypatch,wb):
 row=binding(H/(wb+'-history.json'));capture=checked(binding(H/(wb+'-capture-proof.json')));mapping=capture['read_projection'];values={}
 def read(b):
  if b['path'] in values:return values[b['path']]
  if b['path'] in mapping:
   copy=mapping[b['path']];assert copy['sha256']==b['sha256'];return checked(copy)
  return checked(b)
 monkeypatch.setitem(N.CERTIFICATES,wb,row);monkeypatch.setattr(N,'checked',read)
 return row,read,values

@pytest.mark.parametrize('wb',['0200','0013','0036'])
def test_actual_completed_source_and_retained_preaudit_validate(wb,monkeypatch):
 row,read,values=actual(monkeypatch,wb);p=N.validate_certificate(row,workbook=wb,live=False);assert p['route']=='retained_source_attempt2_then_first_authoring_no_reset'

@pytest.mark.parametrize('case',['extra','wrongcert','priorcall','excluded','history','attempt','generation','source_rebuilt','prior_history','model','publication','result','worker_rc','worker_model_source','old_model_route'])
def test_no_source_or_authoring_budget_reset(case,monkeypatch):
 row,read,values=actual(monkeypatch,'0200');p=copy.deepcopy(read(row));values[row['path']]=p;r=copy.deepcopy(read(p['source_result']));values[p['source_result']['path']]=r;l=copy.deepcopy(read(r['ledger']));values[r['ledger']['path']]=l;o=copy.deepcopy(read(p['source_worker_outcome']));values[p['source_worker_outcome']['path']]=o
 if case=='extra':p['fresh_reset']=True
 if case=='wrongcert':N.CERTIFICATES['0200']={'path':'/foreign','sha256':'b'*64}
 if case=='priorcall':p['central_histories']['0200'].append({'path':'/model/Call.json','sha256':'b'*64})
 if case=='excluded':p['central_excluded']['0200']=['published']
 if case=='history':p['histories']['0200']=[]
 if case=='attempt':l['build']['attempts']=1
 if case=='generation':l['build']['source_generation_id']='wrong'
 if case=='source_rebuilt':l['build']['source_reused']=False
 if case=='prior_history':l['build_history']=[]
 if case=='model':r['models_dispatched']=1
 if case=='publication':r['publication_performed']=True
 if case=='result':r['status']='VERIFIED_BY_SOURCE_VALUES_ONLY'
 if case=='worker_rc':o['returncode']=1
 if case=='worker_model_source':
  i=read(o['intent']);j=copy.deepcopy(read(i['job']));j['source']['sha256']='b'*64;values[i['job']['path']]=j
 if case=='old_model_route':
  old=copy.deepcopy(read(p['pre_audit']));old['model_history_route']='audit_started';values[p['pre_audit']['path']]=old
 with pytest.raises(ValueError):N.validate_certificate(row,workbook='0200',live=False)

@pytest.mark.parametrize('case',['valid','wrong_source','wrong_version','held','wrong_workbook'])
def test_actual_history_requires_live_exact_source_version_custody(case,monkeypatch):
 row,read,values=actual(monkeypatch,'0200');p=read(row);calls=[]
 claim=dict(state='active',source_sha256=p['source']['sha256'],version='new-version')
 if case=='wrong_source':claim['source_sha256']='b'*64
 if case=='wrong_version':claim['version']='old-source-code'
 if case=='held':claim['state']='hold'
 for key,value in dict(HARBOR_FLEET_WORKBOOK='0200' if case!='wrong_workbook' else '0013',HARBOR_FLEET_URL='fixture',HARBOR_FLEET_TOKEN_FILE='fixture-private',HARBOR_FLEET_OWNER='host:slot-1:test',HARBOR_FLEET_CLAIM_TOKEN='fixture-token',HARBOR_FLEET_FENCE='2').items():monkeypatch.setenv(key,value)
 class Client:
  def __init__(self,*args):pass
  def call(self,action,**kwargs):calls.append((action,kwargs));return claim
 import scripts.fleet.coordinator as C
 monkeypatch.setattr(C,'Client',Client)
 if case=='valid':
  assert N.validate_certificate(row,workbook='0200',root=Path('/new-version'))==p
  assert calls==[('heartbeat',dict(workbook='0200',owner='host:slot-1:test',token='fixture-token',fence=2,lease_seconds=180))]
 else:
  with pytest.raises(ValueError):N.validate_certificate(row,workbook='0200',root=Path('/new-version'))

@pytest.mark.parametrize('case',['valid','second_namespace','other_inventory','multiple_ids','wrong_workbook'])
def test_first_authoring_namespace_is_closed(case,monkeypatch):
 from types import SimpleNamespace
 row,read,values=actual(monkeypatch,'0200');p=read(row);monkeypatch.setattr(N,'configured',lambda *args:p)
 policy=SimpleNamespace(history_roots=p['history_roots'],original_inventory=p['inventory'],requested_ids=('0200',),tracker_root=Path(p['authoring_home'])/'authoring/trackers/workbooks')
 if case=='second_namespace':policy.tracker_root=Path('/other/trackers/workbooks')
 if case=='other_inventory':
  policy.original_inventory={'path':'/fixture-inventory','sha256':'b'*64};values['/fixture-inventory']=read(p['inventory'])
 if case=='multiple_ids':policy.requested_ids=('0200','0013')
 if case=='valid':assert N.admission(policy,'0200')==p
 else:
  with pytest.raises(ValueError):N.admission(policy,'0013' if case=='wrong_workbook' else '0200')

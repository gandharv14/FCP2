import copy
from pathlib import Path
import pytest
from scripts.batch import harbor_generated_curation as G
from test_generated_curation_adoption import case,save,validate
from test_continuation_allowance import policy

@pytest.fixture
def completed(case):
 root,p,original,ledger=case
 old=copy.deepcopy(ledger);old.update(state='STOPPED',failure='source_policy_unsupported',reason_codes=['external_links_present']);old['build']={'state':'STOPPED','stage':'normalization','attempts':1,'exit_code':1,'failure':'RemediationError:remediation left unsupported volatile or external references','curation_path':ledger['build']['curation_path'],'bindings':{'curation_sha256':None}}
 prior=save(root/'original/workbooks/0197/ledger.json',old);home=root/'completed';new={**old,'build':copy.deepcopy(ledger['build']),'build_history':[old['build']]};new['build']['attempts']=2;new['build']['bindings'].update(code_sha256='code',source_sha256=original['sha256']);p['ledger']=save(home/'corpus/workbooks/0197/ledger.json',new)
 helper=root/'helper.py';helper.write_text('# retained source helper')
 _,allowance,_=policy(root,source=original['sha256'],workbook='0197')
 plan=dict(schema_version='fleet-pre-source-continuation/v2',workbook='0197',source=old['source'],ledger=prior,helper=G.A.record(helper),previous_attempt=1,next_attempt=2,cumulative_reconstruction_limit=2,models=0,automatic_retry=False,generation=None,retained_curation=None,restriction_inventory=None,inventory_batch_id=None,expected_failure=old['build']['failure'],home=str(home),snapshot=save(root/'snapshot.json',{'fingerprint':'code'}),corpus=str(root/'original'),closure=save(root/'closure.json',{'corpus':str(root/'original'),'files':{'workbooks/0197/ledger.json':prior}}),allowance=allowance,seconds=7200,memory_bytes=8*1024**3)
 pb=save(root/'plan.json',plan);result=dict(plan=pb,ledger=p['ledger'],result=dict(workbook_id='0197',**new['build']),status='RECONSTRUCTION_VERIFIED',models_dispatched=0,publication_performed=False,automatic_retry=False)
 p.update(schema_version='harbor-generated-curation-adoption/v2',completed_source={'plan':pb,'result':save(home/'result.json',result)})
 return case,plan,new,result

def test_completed_second_source_preserves_failed_first_and_all_original_bytes(completed):
 case,plan,ledger,result=completed;paths=[Path(case[1]['ledger']['path']),Path(plan['ledger']['path'])];before=[p.read_bytes()for p in paths]
 assert validate(case)==case[1]
 assert [p.read_bytes()for p in paths]==before

@pytest.mark.parametrize('fault',['old_source','old_normalization','old_curation','old_history','history_rewrite','ordinal_reset','third_attempt','result_mismatch','result_namespace','ledger_namespace','helper_tamper','closure_tamper','model_call','foreign_source','fingerprint','allowance_scope'])
def test_completed_source_adoption_fails_closed(completed,fault):
 case,plan,ledger,result=completed;root,p,original,_=case;old=G.A.read(plan['ledger'])
 if fault=='old_source':old['build']['source_generation_id']='previous-source'
 elif fault=='old_normalization':old['build']['normalization']={'candidate':'old'}
 elif fault=='old_curation':old['build']['bindings']['curation_sha256']='old'
 elif fault=='old_history':old['build_history']=[{}]
 elif fault=='history_rewrite':ledger['build_history'][0]['exit_code']=0
 elif fault=='ordinal_reset':ledger['build']['attempts']=1
 elif fault=='third_attempt':plan['next_attempt']=plan['cumulative_reconstruction_limit']=3
 elif fault=='result_mismatch':result['status']='RECONSTRUCTION_HELD'
 elif fault=='helper_tamper':Path(plan['helper']['path']).write_text('changed')
 elif fault=='closure_tamper':Path(plan['ledger']['path']).write_text('{}')
 elif fault=='model_call':result['models_dispatched']=1
 elif fault=='foreign_source':plan['source']={**plan['source'],'sha256':'a'*64}
 elif fault=='fingerprint':ledger['build']['bindings']['code_sha256']='changed'
 elif fault=='allowance_scope':
  _,plan['allowance'],_=policy(root,source='a'*64,workbook='0197',directory=root/'wrong-policy')
 if fault.startswith('old_'):plan['ledger']=save(root/'original/workbooks/0197/ledger.json',old)
 if fault not in ('helper_tamper','closure_tamper'):
  p['ledger']=save(root/('detached-ledger.json'if fault=='ledger_namespace'else'completed/corpus/workbooks/0197/ledger.json'),ledger)
  p['completed_source']['plan']=save(root/'plan.json',plan);result['plan']=p['completed_source']['plan'];result['ledger']=p['ledger'];result['result']=dict(workbook_id='0197',**ledger['build']);p['completed_source']['result']=save(root/('detached-result.json'if fault=='result_namespace'else'completed/result.json'),result)
 with pytest.raises((ValueError,RuntimeError)):validate(case)

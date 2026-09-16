import json
import pytest
from scripts.fleet import disclosure_review_continue as M

def put(tmp_path,name,value):
 p=tmp_path/name;p.write_text(json.dumps(value));return M.binding(p)

def fixture(tmp_path):
 source={'path':'/source.xlsx','sha256':'a'*64}
 policy=dict(schema_version='fleet-disclosure-initial-renewal-policy/v1',authorization=put(tmp_path,'auth',{'authorized':True}),workbook='0322',source=source,measured_context=put(tmp_path,'proof',dict(returncode=0,seconds=52.8)),required_seconds=600)
 prep=put(tmp_path,'preparation',dict(schema_version='fleet-disclosure-review-preparation/v1',workbook='0322',source=source,build={'exact':'build'},modules={'exact':'modules'}))
 observed=dict(value=dict(status='PREPARED_NOT_RESERVED',models_dispatched=0,task_mutations=0,lease_mutations=0,plan=prep,build={'exact':'build'},inputs={'exact':'inputs'}))
 proof=put(tmp_path,'proof',dict(returncode=0,seconds=52.8,tail=json.dumps(observed)));policy['measured_context']=proof
 p=dict(workbook='0322',source=source,retained=dict(plan=prep),build={'exact':'build'},modules={'exact':'modules'},inputs={'exact':'inputs'},initial_renewal_policy=put(tmp_path,'policy',policy))
 return p,policy

def test_default_and_measured_bounded_policy(tmp_path):
 assert M.initial_renewal_seconds({})==930
 p,_=fixture(tmp_path);assert M.initial_renewal_seconds(p)==600

@pytest.mark.parametrize('key,value',[('workbook','wrong'),('required_seconds',299),('required_seconds',931),('required_seconds',True),('extra',True)])
def test_foreign_or_unbounded_policy_refused(tmp_path,key,value):
 p,policy=fixture(tmp_path);policy[key]=value;p['initial_renewal_policy']=put(tmp_path,'changed',policy)
 with pytest.raises(ValueError):M.initial_renewal_seconds(p)

@pytest.mark.parametrize('proof',[dict(returncode=1,seconds=20),dict(returncode=0,seconds=190),dict(returncode=0,seconds=0)])
def test_failed_or_insufficient_measurement_refused(tmp_path,proof):
 p,policy=fixture(tmp_path);policy['measured_context']=put(tmp_path,'new-proof',proof);p['initial_renewal_policy']=put(tmp_path,'changed',policy)
 with pytest.raises(ValueError):M.initial_renewal_seconds(p)

def test_changed_evidence_refused(tmp_path):
 p,policy=fixture(tmp_path);from pathlib import Path
 Path(policy['measured_context']['path']).write_text('{}')
 with pytest.raises(ValueError):M.initial_renewal_seconds(p)

def test_unrelated_success_measurement_refused(tmp_path):
 p,policy=fixture(tmp_path);proof=json.loads(__import__('pathlib').Path(policy['measured_context']['path']).read_text());v=json.loads(proof['tail']);v['value']['inputs']={'foreign':'inputs'};proof['tail']=json.dumps(v);policy['measured_context']=put(tmp_path,'foreignproof',proof);p['initial_renewal_policy']=put(tmp_path,'foreignpolicy',policy)
 with pytest.raises(ValueError,match='differs'):M.initial_renewal_seconds(p)

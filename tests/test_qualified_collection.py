import copy,json
from pathlib import Path
import pytest
from scripts.fleet import qualified_collection as Q
BASE=Path(__file__).resolve().parents[1]
STAGE=BASE/'diagnostics/vm-fleet-20260912/returned/smoke-0029-qualified-001'
COLLECTOR=BASE/'diagnostics/vm-fleet-20260912/control/0029-publication-003/collector.py'
@pytest.fixture
def actual():
 if not (STAGE/'manifest.json').exists():pytest.skip('retained actual qualified smoke package unavailable')
 return STAGE/'task.tar.gz',json.loads((STAGE/'manifest.json').read_bytes()),Q.binding(COLLECTOR)
def test_actual_qualified_archive_through_generic_verifier(actual):
 a,m,c=actual;r=Q.verify_archive(a,m,'0029',c)
 assert r['passed']is True and r['current_qualification_verified']is True
@pytest.mark.parametrize('field,value',[('workbook','0190'),('model_calls',1),('archive_sha256','0'*64),('schema_version','unknown'),('remote_output','/foreign')])
def test_changed_manifest_rejected(actual,field,value):
 a,m,c=actual;m[field]=value
 with pytest.raises(ValueError):Q.verify_archive(a,m,'0029',c)
def test_collector_hash_checked_before_loading(actual):
 a,m,c=actual;c['sha256']='0'*64
 with pytest.raises(ValueError,match='collector'):Q.verify_archive(a,m,'0029',c)
def test_member_hash_cannot_be_relabelled(actual):
 a,m,c=actual;name=next(iter(m['files']));m['files'][name]['sha256']='0'*64
 with pytest.raises(ValueError,match='hash'):Q.verify_archive(a,m,'0029',c)
def test_foreign_workbook_rejected_even_with_valid_archive(actual):
 a,m,c=actual
 with pytest.raises(ValueError):Q.verify_archive(a,m,'0190',c)

@pytest.fixture
def completion(tmp_path):
 def put(name,value):
  p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value));return Q.binding(p)
 code=put('code.py',{});implementation=put('implementation.json',dict(publisher=code,dependencies={'inventory':code}))
 plan=put('plan.json',dict(schema_version='harbor-fleet-remote-publication/v1',workbook='0141',journal='/remote/original'))
 result=dict(path='/remote/verification/result.json',sha256='a'*64)
 p=dict(schema_version='fleet-retained-publication-verification/v1',helper=code,publisher=code,implementation=implementation,plan=plan,publication=dict(path='/remote/original/owning-publication/result.json',sha256='b'*64),resource={'retained':True},tracker={'retained':True},inventory=code,directory='/remote/verification')
 profile=put('profile.json',p)
 guard=dict(returncode=0,failure=None,seconds=3300,memory_bytes=8589934592,log=json.dumps(dict(status='REMOTE_VERIFIED',result=result,models_dispatched=0,publication_mutations=0)))
 g=put('guard.json',dict(returncode=0,stdout=json.dumps(guard)))
 op=dict(operation_id='consumed',kind='publication',state='abandoned',workbook='0141')
 settled=put('settled.json',dict(automatic_retry=False,result=dict(operation_id='consumed',state='abandoned'),after=dict(operations=[op])))
 c=dict(schema_version='fleet-publication-independent-completion/v1',profile=profile,guard=g,reconciliation=settled)
 return plan,result,p,guard,c,put

def test_separate_verification_retains_original_plan_and_abandoned_operation(completion):
 plan,result,p,g,c,put=completion
 returned=Q.verification_continuation(plan,result,put('certificate.json',c))
 assert returned[1]==p and returned[2]['state']=='abandoned'

@pytest.mark.parametrize('change',['wrong-result','wrong-original','guard-failed','guard-model','reconcile-confirmed','extra-field'])
def test_unproven_verification_completion_cannot_select_collection(completion,change):
 plan,result,p,g,c,put=completion
 if change=='wrong-result':result={**result,'path':'/other/result.json'}
 elif change=='wrong-original':p['publication']['path']='/other/owning-publication/result.json'
 elif change=='guard-failed':g['returncode']=1
 elif change=='guard-model':v=json.loads(g['log']);v['models_dispatched']=1;g['log']=json.dumps(v)
 elif change=='reconcile-confirmed':v=Q.checked(c['reconciliation']);v['result']['state']='confirmed';c['reconciliation']=put('settled.json',v)
 else:p['skip_validation']=True
 c['profile']=put('profile.json',p);c['guard']=put('guard.json',dict(returncode=0,stdout=json.dumps(g)))
 with pytest.raises(ValueError):Q.verification_continuation(plan,result,put('certificate.json',c))

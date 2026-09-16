import ast,copy
from pathlib import Path
import pytest
from test_disclosure_interrupted_reservation import case,keep
from scripts.fleet import disclosure_reserved_round as S
from scripts.batch import harbor_disclosure_partition_review as R
from scripts.batch.harbor_admission import read,record

@pytest.fixture
def derivative(case):
 proof,p,w,l,Q,calls,h,wd,review=case;v=read(proof);old=read(v['plan'])
 newtext=Path(R.__file__).read_text();tree=ast.parse(newtext);fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='task_content');lines=newtext.splitlines(keepends=True);lines[fn.lineno-1:fn.end_lineno]=[]
 oldtext=''.join(lines).replace('    task_content(task,p,before)\n',"    require(record(task.stage/'instruction.md')['sha256']==p['instruction']['sha256'] and record(task.stage/'tests/disclosure.json')['sha256']==p['disclosure']['sha256'],'partition task content changed')\n")
 reader=h.parent/'old_review.py';reader.write_text(oldtext);old['modules']['review']=record(reader);old['source_derivative']=keep(h.parent/'derivative.json',{'proof':'source validation belongs to original Q'})
 inputs=read(old['inputs']);inputs['producer_run']=str(h.parent/'producer');old['inputs']=keep(Path(old['inputs']['path']),inputs)
 v['plan']=keep(Path(v['plan']['path']),old)
 jobpath=Path(read(read(v['worker_outcome'])['intent'])['job']['path']);job=read(record(jobpath));job['command'][-1]=v['plan']['sha256'];jobrow=keep(jobpath,job)
 ip=wd/'intent.json';iv=read(record(ip));iv['job']=jobrow;ir=keep(ip,iv)
 (wd/'worker.log').write_text("request=R.prepare(task,plan,index,_validated=validated)\nrequire(record(task.stage/'instruction.md')['sha256']==p['instruction']['sha256']\nscripts.batch.harbor_admission.AdmissionError: partition task content changed\n")
 outer=read(v['worker_outcome']);outer.update(returncode=1,failure_type=None,intent=ir,log=record(wd/'worker.log'));v['worker_outcome']=keep(wd/'outcome.json',outer);(wd/'finish-uncertain.json').unlink()
 process=read(v['process']);process['job']=jobrow;process['command'][process['command'].index('--job-sha256')+1]=jobrow['sha256'];v['process']=keep(Path(v['process']['path']),process)
 retained=read(v['retained_lease']);retained['authority']=v['plan'];v['retained_lease']=keep(Path(v['retained_lease']['path']),retained)
 keep(h/'intent.json',dict(plan=v['plan'],automatic_retry=False));keep(h/'validation-metrics.json',dict(full_constructor_seconds=20.5,source_rederivations=1))
 authority=read(v['authority']);authority['source_derivative']=old['source_derivative'];v['authority']=keep(Path(v['authority']['path']),authority)
 review=keep(Path(review['path']),dict(inputs,review_round=v['authority']));keep(h/'reserved.json',dict(plan=review,automatic_retry=False))
 v['schema_version']='fleet-disclosure-derivative-request-failure/v1';proof=keep(Path(proof['path']),v)
 p.update(inputs=old['inputs'],source_derivative=old['source_derivative'],reserved_round=proof,modules={**old['modules'],'review':record(R.__file__)})
 def validate(a,i):
  assert a==v['authority']and i==inputs;calls.append('original authority');return authority
 Q.validate=validate
 # Scope structure includes both durable executor and producer request paths.
 from types import SimpleNamespace
 R.scope=lambda p,i:SimpleNamespace(index=i,budget_id='shard-%04d'%i)
 return proof,p,w,l,Q,calls,h,wd,review

def test_sealed_derivative_failure_keeps_reservation_and_all_original_bytes(derivative):
 proof,p,w,l,Q,calls,h,wd,review=derivative;before={x:x.read_bytes()for x in h.parent.rglob('*')if x.is_file()}
 assert S.validate(proof,p,w,None,l,Q)==(Path(review['path']),read(review))
 assert calls==['native process absence','original authority','original executor history']
 assert all(x.read_bytes()==v for x,v in before.items())

@pytest.mark.parametrize('fault',['source','budget','other_module','request','executor','metrics','later_output','uncertain','reader','authority'])
def test_derivative_recovery_rejects_changed_scope_or_any_request(derivative,fault):
 proof,p,w,l,Q,calls,h,wd,review=derivative
 if fault=='source':p['source_derivative']=keep(h.parent/'other.json',{})
 elif fault=='budget':p['round']+=1
 elif fault=='other_module':p['modules']['rounds']=keep(h.parent/'other-rounds',{})
 elif fault=='request':keep(h.parent/'producer/requests/shard-0000/request.json',{})
 elif fault=='executor':(Path(w.campaign.root)/R.E.sha(R.E.canonical({'index':1}))).mkdir(parents=True)
 elif fault=='metrics':keep(h/'validation-metrics.json',dict(full_constructor_seconds=1,source_rederivations=0))
 elif fault=='later_output':keep(h/'batches/0000/request.json',{})
 elif fault=='uncertain':keep(wd/'finish-uncertain.json',{})
 elif fault=='reader':
  f=h.parent/'bad_review.py';f.write_text(Path(R.__file__).read_text().replace("'partition full manifest differs from actual current inputs'","'different unreviewed code'"));p['modules']['review']=record(f)
 elif fault=='authority':Q.validate=lambda *a:(_ for _ in()).throw(ValueError('original authority rejected'))
 with pytest.raises(ValueError):S.validate(proof,p,w,None,l,Q)

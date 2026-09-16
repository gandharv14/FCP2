from pathlib import Path
from types import SimpleNamespace
import copy,json
import pytest
from scripts.fleet import disclosure_review_prepare as P
from scripts.batch import harbor_disclosure_review_round as Q


def save(path,value):
 path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return P.binding(path)


@pytest.mark.parametrize('fault',[None,'checkpoint','selection','lease','action','descriptor','code','guard','status','path','controls','timeout','memory'])
def test_stopped_custody_requires_exact_original_build_guard_and_checkpoint(tmp_path,monkeypatch,fault):
 descriptor=save(tmp_path/'descriptor.json',{});instruction=save(tmp_path/'instruction.py',{})
 build=dict(descriptor=descriptor,selection={'original':'source'},lease={'original':'lease'})
 buildrow=save(tmp_path/'build.json',build)
 blocked=save(tmp_path/'worker/steps/disclosure-review/blocked.json',dict(status='MODEL_RECONCILIATION_REQUIRED',step='disclosure-review',selection=build['selection']))
 value=dict(build,status='MODEL_RECONCILIATION_REQUIRED',next_step='disclosure-review',publication_performed=False,checkpoint=blocked,evidence=[buildrow])
 intent=dict(action='step',arguments=dict(descriptor=descriptor,checkpoint=buildrow),code=instruction,controls={'actual':'controls'},timeout_seconds=3300,memory_limit_bytes=8589934592)
 if fault=='checkpoint':value['evidence']=[descriptor]
 elif fault in ('selection','lease'):value[fault]={'foreign':True}
 elif fault=='action':intent['action']='start'
 elif fault=='descriptor':intent['arguments']['descriptor']=buildrow
 elif fault=='code':intent['code']=descriptor
 elif fault=='controls':intent['controls']={}
 elif fault=='timeout':intent['timeout_seconds']=1
 elif fault=='memory':intent['memory_limit_bytes']=1
 elif fault=='status':value['status']='STEP_COMPLETE'
 elif fault=='path':value['checkpoint']=save(tmp_path/'copied-blocked.json',P.read(blocked))
 guard=save(tmp_path/'resource.json',dict(intent=save(tmp_path/'intent.json',intent),returncode=1 if fault=='guard'else 0,failure=None))
 result=save(tmp_path/'result.json',value);outcome=save(tmp_path/'outcome.json',dict(status='COMPLETED_OPERATION',resource=guard,result=result))
 calls=[];env=(SimpleNamespace(encode=lambda x:x,MEMORY=8589934592),SimpleNamespace(__file__=instruction['path'],guard_seconds=lambda *a:3300,selected=lambda row:(SimpleNamespace(run=tmp_path/'worker'),None,None)),None,None,None,None,None,{'actual':'controls'})
 def successful(row):
  assert row==outcome;calls.append(row);return result,P.read(result)
 monkeypatch.setattr(Q,'bound_module',lambda row:SimpleNamespace(successful=successful))
 p=dict(build=dict(outcome=descriptor,result=buildrow,descriptor=descriptor,instruction=instruction,dispatcher={}),completed_custody=dict(stopped_outcome=outcome))
 if fault:
  with pytest.raises(ValueError):P.completed_custody_origin(p,env,{})
 else:assert P.completed_custody_origin(p,env,{})==(outcome,buildrow)
 assert calls==[outcome]


def test_original_build_origin_is_unchanged():
 p=dict(build=dict(outcome={'old':'outcome'}),completed_custody={})
 assert P.completed_custody_origin(p,None,dict(arguments=dict(checkpoint={'old':'checkpoint'})))==({'old':'outcome'},{'old':'checkpoint'})


@pytest.mark.parametrize('fault',[None,'build_after_first','origin','tracker','latest','cycle'])
def test_chained_custody_keeps_original_lineage_and_full_validation(tmp_path,fault):
 from dataclasses import dataclass
 @dataclass(frozen=True)
 class Lease:
  owner:str
  token:str
  revision:int
  sha256:str
 original=Lease('original','old-token',3,'old-sha');first=Lease('first','first-token',4,'first-sha');latest=Lease('second','second-token',5,'second-sha')
 tracker=str(tmp_path/'tracker.json');rows={};calls=[]
 def step(name,before,after,prior):
  old=dict(revision=before.revision,leases=dict(task_agent=dict(owner=before.owner,token=before.token)))
  new=dict(revision=after.revision)
  stop=save(tmp_path/(name+'-stop.json'),dict(before=dict(path=str(tmp_path/(name+'-before.json')),sha256=before.sha256),selection=dict(tracker=dict(path=tracker),prior_completion=prior)))
  row=save(tmp_path/(name+'.json'),dict(stopped_owner_proof=stop));rows[row['path']]=(old,new,after);return row
 one=step('one',original,first,None);two=step('two',first,latest,one)
 if fault=='build_after_first':original=first
 if fault=='origin':original=Lease('foreign','old-token',3,'old-sha')
 if fault=='tracker':tracker=str(tmp_path/'other.json')
 if fault=='latest':latest=first
 if fault=='cycle':
  value=P.read(P.read(one)['stopped_owner_proof']);value['selection']['prior_completion']=two
  # Cycle is exercised through an authenticated test reader; production binding checks also reject edits.
  normal=P.read
  def read(row):return value if row==normal(one)['stopped_owner_proof']else normal(row)
 else:read=P.read
 def validate(row):calls.append(row);return rows[row['path']]
 chains=[];T=SimpleNamespace(validate_completion=validate,continuation_chain=lambda *a,**k:chains.append((a,k)))
 if fault=='cycle':
  from unittest.mock import patch
  with patch.object(P,'read',read),pytest.raises(ValueError):P.completed_custody_chain(T,two,tracker,original,latest)
 elif fault and fault!='build_after_first':
  with pytest.raises(ValueError):P.completed_custody_chain(T,two,tracker,original,latest)
 else:
  P.completed_custody_chain(T,two,tracker,original,latest)
  assert calls==[two,one]and len(chains)==1
  assert chains[0][1]==dict(takeover=two,tracker_path=Path(tracker))
  assert chains[0][0][1]['owner']==original.owner and chains[0][0][-1]==latest
  anchor=P.read(P.read(two if fault=='build_after_first'else one)['stopped_owner_proof'])['before']
  assert chains[0][0][0]==anchor
 if fault and fault!='build_after_first':assert chains==[]

@pytest.mark.parametrize('fault',[None,'source','loader','helper','build_directory','status','guard','review'])
def test_pre_dispatch_custody_retains_completed_build_and_actual_later_guard(tmp_path,fault):
 instruction=save(tmp_path/'instruction.py',{});driver=save(tmp_path/'driver.py',{});source=dict(path='/original.xlsx',sha256='a'*64)
 config=save(tmp_path/'config.json',dict(workbook='0322'));snapshot=save(tmp_path/'snapshot.json',dict(root=str(tmp_path/'code')))
 original=save(tmp_path/'job.json',dict(driver=driver,config=config,operations=str(tmp_path/'first')));pre=save(tmp_path/'pre.json',dict(original_job=original))
 desc=save(tmp_path/'descriptor.json',{});blocked=save(tmp_path/'blocked.json',dict(status='MODEL_RECONCILIATION_REQUIRED',step='disclosure-review'))
 value=dict(status='MODEL_RECONCILIATION_REQUIRED',next_step='disclosure-review',descriptor=desc,checkpoint=blocked)
 if fault=='status':value['status']='STEP_COMPLETE'
 result=save(tmp_path/'result.json',value);intent=save(tmp_path/'intent.json',dict(action='step',arguments=dict(descriptor=desc),code=instruction));guard=save(tmp_path/'resource.json',dict(returncode=1 if fault=='guard'else 0,failure=None,intent=intent));outcome=save(tmp_path/'outcome.json',dict(status='COMPLETED_OPERATION',result=result,resource=guard))
 helper=save(tmp_path/'helper.py',{});rejected=save(tmp_path/'http.json',{});q=dict(schema_version='fleet-retained-pre-dispatch-review/v1',helper=helper,workbook='0322',source=source,terminal_http=rejected,pre_dispatch=pre,outcome=outcome,result=result,blocked=blocked,descriptor=desc,config=config,snapshot=snapshot)
 if fault=='source':q['source']={'foreign':True}
 if fault=='helper':q['helper']=instruction
 qr=save(tmp_path/'context.json',q);loader=dict(plan=qr,helper=helper,function='cold_context',kwargs=dict(continued=True),mode='contextmanager',environment=3,lease=4)
 if fault=='loader':loader['function']='context'
 build=dict(dispatcher=driver,instruction=instruction,outcome=save(tmp_path/('other'if fault=='build_directory'else'first')/'012/outcome.json',{}))
 p=dict(loader=loader,completed_custody=dict(stopped_pre_dispatch=qr),workbook='0322',source=source,prior_rejections=[]if fault=='review'else[rejected],build=build)
 env=(None,SimpleNamespace(__file__=instruction['path']),None,None,None,P.read(config),tmp_path/'code')
 if fault:
  with pytest.raises(ValueError):P.pre_dispatch_custody_origin(p,env)
 else:assert P.pre_dispatch_custody_origin(p,env)==(outcome,result)

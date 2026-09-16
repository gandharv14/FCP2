import copy
import pytest
from scripts.fleet import publication_launch as L


def fixture():
 def b(path):return dict(path=path,sha256=path)
 certificate=dict(schema_version='fleet-stopped-wrapper-completed-ready/v1',worker_outcome=b('/worker/outcome'),job=b('/job'),guard=b('/ops/000/outcome'),checkpoint=b('/ops/000/result'),ready=b('/ready'),failure_log=b('/worker/log'))
 outcome=dict(intent=b('/worker/intent'),returncode=1,failure_type=None,result=None,log=certificate['failure_log'])
 guard=dict(status='COMPLETED_OPERATION',result=certificate['checkpoint'],resource=b('/resource'))
 resource=dict(returncode=0,failure=None,intent=b('/intent'))
 intent=dict(action='step',models=False,commands=True,containers=True)
 ready=dict(status='PUBLICATION_READY',publication_performed=False,workbook='0245',outcome=certificate['guard'],checkpoint=certificate['checkpoint'],job=b('/authoringjob'))
 checkpoint=dict(status='PUBLICATION_READY')
 data={'/certificate':certificate,'/worker/outcome':outcome,'/worker/intent':dict(job=b('/job')),'/job':{},'/ready':ready,'/ops/000/outcome':guard,'/resource':resource,'/intent':intent,'/ops/000/result':checkpoint}
 p=dict(completed_ready=b('/certificate'),worker_outcome=certificate['worker_outcome']);plan=dict(ready=b('/ready'),workbook='0245')
 return p,plan,lambda row:data[row['path']],b,certificate,outcome,guard,resource,intent,ready,checkpoint


def test_original_ready_can_outlive_failed_outer_wrapper():
 p,plan,read,bind,c,*_=fixture();assert L.completed_ready(p,plan,read,bind)==c


@pytest.mark.parametrize('target,key,value',[
 ('outcome','returncode',None),('outcome','failure_type','timeout'),('outcome','result',{}),
 ('guard','status','RECONCILIATION_REQUIRED'),('guard','result',{}),('resource','returncode',1),('resource','failure',{}),
 ('intent','models',True),('intent','action','publication'),('ready','workbook','9999'),('checkpoint','status','STEP_COMPLETE'),
 ('certificate','job',{}),('certificate','failure_log',{})])
def test_unfinished_or_detached_wrapper_is_not_publication_ready(target,key,value):
 p,plan,read,bind,certificate,outcome,guard,resource,intent,ready,checkpoint=fixture();locals()[target][key]=value
 with pytest.raises((ValueError,KeyError)):L.completed_ready(p,plan,read,bind)


def enqueue_fixture():
 values=fixture();p,plan,read,bind,c,outcome,guard,resource,intent,ready,checkpoint=values
 schema='original-instruction-schema'
 prior=dict(schema_version=schema,status='STEP_COMPLETE',next_step='enqueue',publication_performed=False,descriptor=bind('/descriptor'),checkpoint=bind('/previous-complete'),selection={'exact':'before'})
 step=dict(step='enqueue',previous=prior['checkpoint'],selection=prior['selection'])
 complete=dict(step='enqueue',status='completed',intent=bind('/enqueue/intent.json'),selection={'exact':'after'})
 e=dict(schema_version='fleet-completed-enqueue-permission/v1',intent=resource['intent'],prior=bind('/prior'),descriptor=bind('/descriptor'),completion=bind('/enqueue/complete.json'),step_intent=complete['intent'])
 c['enqueue_permission']=e
 intent.update(models=True,schema_version=schema,arguments=dict(descriptor=e['descriptor'],checkpoint=e['prior']))
 checkpoint.update(schema_version=schema,descriptor=e['descriptor'],checkpoint=e['completion'],next_step=None,publication_performed=False,evidence=[e['prior'],e['completion']],selection=complete['selection'])
 extra={'/prior':prior,'/descriptor':dict(schema_version=schema),'/enqueue/complete.json':complete,'/enqueue/intent.json':step}
 def extended(row):return extra[row['path']]if row['path']in extra else read(row)
 return values,extended,prior,complete,step


def test_original_enqueue_permission_does_not_claim_model_dispatch():
 values,read,*_=enqueue_fixture();p,plan,_,bind,c,*_=values
 assert L.completed_ready(p,plan,read,bind)==c
 assert c['enqueue_permission']['schema_version']=='fleet-completed-enqueue-permission/v1'
 assert 'models_dispatched'not in c


@pytest.mark.parametrize('target,key,value',[
 ('prior','next_step','dialogue-review'),('prior','status','RETRY_READY'),
 ('complete','step','dialogue-review'),('complete','status','MODEL_REQUIRED'),
 ('step','previous',{}),('step','selection',{}),
 ('checkpoint','descriptor',{}),('checkpoint','checkpoint',{}),
 ('checkpoint','evidence',[]),('intent','arguments',{}),('intent','models',False),
 ('permission','intent',{}),('permission','extra',True),
])
def test_enqueue_permission_rejects_foreign_or_unfinished_history(target,key,value):
 values,read,prior,complete,step=enqueue_fixture();p,plan,_,bind,c,_,_,_,intent,_,checkpoint=values
 permission=c['enqueue_permission'];locals()[target][key]=value
 with pytest.raises((ValueError,KeyError)):L.completed_ready(p,plan,read,bind)


def test_completed_version_preserves_original_source_root():
 def b(p):return {'path':p,'sha256':p}
 plan=dict(code_root='/original/old',workbook='1',source_sha256='source',completed_checkpoint=dict(reader=b('/helper'),plan=b('/plan')))
 selected=dict(schema_version='fleet-completed-instruction-continuation/v1',helper=b('/helper'),workbook='1',source=b('/source'),bridge_profile=b('/bridge'),facade=dict(original='/original/old',selected='/selected/new'))
 selected['source']['sha256']='source'
 bridge=dict(schema_version='fleet-completed-checkpoint-takeover/v1',worker_root='/selected/new',source=selected['source'],workbook='1')
 def read(row):return selected if row==b('/plan')else bridge
 assert L.publication_version(plan,read)=='new'
 assert plan['code_root']=='/original/old'
 for obj,key,bad in ((selected,'helper',{}),(selected,'workbook','2'),(bridge,'worker_root','/foreign'),(bridge,'source',{}),(selected['facade'],'original','/other')):
  old=obj[key];obj[key]=bad
  with pytest.raises((ValueError,KeyError)):L.publication_version(plan,read)
  obj[key]=old
 assert L.publication_version({'code_root':'/original/old'},lambda _:None)=='old'

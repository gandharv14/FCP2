"""Read a Fresh completed build and its sealed rejected disclosure request.

The original dispatcher authenticates source, code, controls and checkpoint.
This adapter never renews a lease, claims work, prepares a call or writes a task.
Current custody is supplied through disclosure_review_prepare.completed_custody.
"""
from contextlib import contextmanager
from pathlib import Path
import hashlib,importlib.util,json,sys

SCHEMA='fleet-fresh-completed-disclosure-context/v1'
FIELDS={'schema_version','helper','reader','rounds','fresh_plan','original_job',
        'build','stopped_result','stopped_outcome','prior_rejections','source','workbook'}

def require(ok,message):
    if not ok:raise ValueError(message)

def binding(path):
    p=Path(path);require(p.is_absolute()and p.is_file()and not any(x.is_symlink()for x in(p,*p.parents)),'canonical context input required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def read(row):
    require(set(row)=={'path','sha256'}and binding(row['path'])==row,'context input changed')
    return json.loads(Path(row['path']).read_bytes())

def module(name,row):
    require(binding(row['path'])==row,'context implementation changed')
    s=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

def stopped(p,D,I):
    """Bind the retained failure to the completed build, never manufacture one."""
    _,built=D.successful(p['build']['outcome'])
    result,value=D.successful(p['stopped_outcome'])
    require(result==p['stopped_result']and value==read(result),'original stopped guard differs')
    blocked=read(value['checkpoint'])
    require(value['status']==blocked['status']=='MODEL_RECONCILIATION_REQUIRED'
        and value['next_step']==blocked['step']=='disclosure-review'
        and value['publication_performed']is False
        and value['descriptor']==p['build']['descriptor']==built['descriptor']
        and value['selection']==built['selection']==blocked['selection']
        and value['evidence'][0]==p['build']['result'],
        'sealed failure detached from completed build')
    worker,_,_=I.selected(value['descriptor'])
    require(value['checkpoint']==binding(worker.run/'steps/disclosure-review/blocked.json'),
        'original rejected checkpoint moved')
    return value

@contextmanager
def context(row,*,continued=True):
    p=read(row);require(continued is True and set(p)==FIELDS and p['schema_version']==SCHEMA
        and p['helper']==binding(__file__),'closed read-only Fresh completed context required')
    reader=module('fresh_completed_original_reader',p['reader'])
    fresh,job,D,env=reader.validated_authoring({k:p[k]for k in('workbook','fresh_plan','original_job')})
    require(job['driver']==p['build']['dispatcher']and binding(env[1].__file__)==p['build']['instruction'],
        'original completed dispatcher changed')
    Q=module('fresh_completed_round_reader',p['rounds'])
    w,s,lease=Q.completed_build(p['build'])
    require(s.content.workbook==p['workbook']and s.content.bindings['original_raw_source']==p['source']
        and binding(p['source']['path'])==p['source'],'completed source differs')
    stopped(p,D,env[1])
    prior=p['prior_rejections'];require(isinstance(prior,list)and len(prior)>0,'explicit sealed rejection required')
    scope=Q.semantic_scope(dict(workbook=p['workbook'],original_source=p['source']))
    for i,rejection in enumerate(prior,1):Q.closed_rejection(rejection,scope,i)
    Q._primary_history(w.campaign,scope,len(prior)+1,prior)
    yield p,env,lease

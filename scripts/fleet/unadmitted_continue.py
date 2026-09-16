"""Continue a stopped original admission before its first tracker exists.

Completed source and bootstrap evidence remain immutable. A fresh dependency
observation and distinct bootstrap journal select the original admit/dispatcher.
"""
from pathlib import Path
from dataclasses import replace
import argparse,hashlib,importlib.util,json,os,sys,time

SCHEMA='fleet-unadmitted-continuation/v1'

def require(ok,message):
    if not ok:raise ValueError(message)

def binding(path):
    p=Path(path).absolute();require(p.is_file()and not any(x.is_symlink()for x in(p,*p.parents)),'regular canonical retained file required');return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def read(row):require(binding(row['path'])==row,'unadmitted evidence changed');return json.loads(Path(row['path']).read_bytes())

def retain(path,value):
    raw=(json.dumps(value,sort_keys=True,indent=2)+'\n').encode();fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb')as f:f.write(raw);f.flush();os.fsync(f.fileno())
    return binding(path)

def module(row,name):
    require(binding(row['path'])==row,'selected admission module changed');spec=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def continued_intake(base,intake,dependency,home,evidence,*,dependencies=None):
    """Only refreshed observation, unused bootstrap path and retained ancestry."""
    s=intake.bootstrap;p=replace(s.policy,dependency=dependency,campaign_evidence=(*s.policy.campaign_evidence,*evidence),**({'dependencies':dependencies}if dependencies is not None else {}))
    return replace(intake,bootstrap=replace(s,policy=p,run=Path(home)/'bootstrap'))

def renewed_dependencies(recipe,p):
    require(type(p['old_max_age_ns'])is int and type(p['new_max_age_ns'])is int
        and recipe.max_age_ns==p['old_max_age_ns']==300*10**9
        and p['old_max_age_ns']<p['new_max_age_ns']<=1800*10**9,
        'dependency lifetime outside explicit bounded renewal')
    return replace(recipe,max_age_ns=p['new_max_age_ns'])


def dependency_expiry(p,intake,guard):
    q=read(p['dependency_expiry']);require(set(q)=={'schema_version','workbook','source','original_plan','guard','authorization','old_max_age_ns','new_max_age_ns','created_at_ns','expires_at_ns'}
        and q['schema_version']=='fleet-admission-dependency-renewal/v1'
        and q['workbook']==p['workbook']and q['source']==p['source']and q['original_plan']==p['original_plan'],
        'closed dependency expiry renewal required')
    require(binding(q['authorization']['path'])==q['authorization']
        and q['authorization']['sha256']=='acffe75c8ed401f66484a5bb6c39a15a0a3e209fc976967da0bddd6125126fd0',
        'dependency renewal authorization differs')
    require(type(q['created_at_ns'])is int and type(q['expires_at_ns'])is int
        and 0<q['expires_at_ns']-q['created_at_ns']<=86400*10**9
        and q['created_at_ns']<=time.time_ns()<q['expires_at_ns'],'dependency renewal expired')
    renewed_dependencies(intake.bootstrap.policy.dependencies,q)
    require(q['guard']==binding(guard/'resource.json')
        and {x.name for x in guard.iterdir()}=={'intent.json','step.log','resource.json','outcome.json'},
        'sealed original admission guard required')
    r=read(q['guard']);outcome=read(binding(guard/'outcome.json'))
    require(r['returncode']==1 and r['failure']is None and r['intent']==binding(guard/'intent.json')
        and r['log']==binding(guard/'step.log') and outcome['resource']==q['guard']
        and outcome['status']=='RECONCILIATION_REQUIRED'and outcome['automatic_retry']is False
        and outcome['source_invalidity_claimed']is False and outcome['terminal_verdict']is None
        and (guard/'step.log').read_text().rstrip().endswith('scripts.batch.harbor_admission.AdmissionError: dependency observation expired or future dated'),
        'different sealed admission failure')
    return q


def context(row):
    p=read(row);require(set(p)-{'dependency_expiry'}=={'schema_version','helper','workbook','source','original_job','original_plan','original_config','original_prepared','prior_outcome','custody','home'},'closed unadmitted continuation required')
    require(p['schema_version']==SCHEMA and p['helper']==binding(__file__),'selected unadmitted implementation differs')
    old=read(p['original_job']);fresh=read(p['original_plan']);outcome=read(p['prior_outcome']);intent=read(outcome['intent']);cfg=read(p['original_config'])
    require(old['workbook']==fresh['workbook']==cfg['workbook']==p['workbook']and old['source']==p['source'],'unadmitted source identity differs')
    require(old['inputs'][0]==p['original_plan']and intent['job']==p['original_job']and old['command'][-4:]==['--plan',p['original_plan']['path'],'--plan-sha256',p['original_plan']['sha256']],'original fresh command differs')
    require(outcome['schema_version']=='harbor-fleet-worker-outcome/v1'and outcome['status']=='RECONCILIATION_REQUIRED'and ((outcome['failure_type']is None and outcome['returncode']==1 and outcome['result']==binding(Path(fresh['home'])/'result.json'))if 'dependency_expiry'in p else(outcome['failure_type']=='TimeoutError'and outcome['returncode']is None and outcome['result']is None)) and outcome['automatic_retry']is False,'exact stopped admission outcome required')
    root=Path(fresh['code_root']);require(old['version']==root.name and old['command'][3]==str(root/'scripts/fleet/fresh.py'),'original fresh code identity differs')
    require(read(binding(Path(fresh['home'])/'intent.json'))==dict(plan=p['original_plan'],driver=old['driver'],automatic_retry=False),'original fresh owning intent differs')
    sys.path.insert(0,str(root));F=module(dict(path=old['command'][3],sha256=old['driver']['sha256']),'selected_unadmitted_fresh');author=module(fresh['preparer'],'selected_unadmitted_preparer');base,_,_=author.imports(root)
    require(author.code_snapshot(root)==read(cfg['code']),'original admission code inventory changed')
    intake=base.decode(read(cfg['intake']));s=intake.bootstrap
    require(s.workbook==p['workbook']and Path(p['original_config']['path'])==Path(fresh['home'])/'authoring/config.json'and Path(p['original_prepared']['path'])==s.run/'prepared.json','original completed preparation differs')
    guard=Path(cfg['run_root'])/'admission-001'
    if 'dependency_expiry'in p:dependency_expiry(p,intake,guard)
    else:require({f.name for f in guard.iterdir()}=={'intent.json','step.log'}and (guard/'step.log').read_bytes()==b'','original admission progressed past unsealed empty guard')
    first=read(binding(guard/'intent.json'));require(first['action']=='admit'and first['arguments']=={'intake':cfg['intake']}and first['code']==binding(base.__file__)and first['controls']==cfg['controls']and first['timeout_seconds']==3300 and first['memory_limit_bytes']==8*1024**3 and first['dispatch']is False,'original admit intent differs')
    custody=read(p['custody']);require(custody['matches']==[],'old worker not stopped')
    for item in custody['files']:
        require(binding(item['path'])['sha256']==item['sha256']and Path(item['path']).stat().st_size==item['bytes'],'original stopped bytes changed')
    for path in (s.policy.tracker_root/(p['workbook']+'.json'),intake.environment_run,intake.campaign.root,s.run/'admission.json',guard/'descriptor.json'):
        require(not path.exists(),'original admission has task or model effects; cannot recreate')
    require(Path(p['home']).is_absolute()and Path(p['home'])!=Path(fresh['home'])and Path(fresh['home'])not in Path(p['home']).parents,'distinct continuation journal required')
    saved=base.bootstrap._prepared(s,p['original_prepared'])
    require(saved['model_calls']==0 and saved['lane_approval']is False,'completed source preparation granted unexpected authority')
    return p,F,author,base,intake,cfg

def prepare(row,directory,*,live=True):
    p,F,author,base,intake,cfg=context(row);home=Path(directory);require(not home.exists(),'continuation namespace consumed')
    if live:
        policy=intake.bootstrap.policy;known={x['workbook_id']for x in base.admission.read_inventory(policy.original_inventory['path'])['sources']}
        base.admission.survey_namespaces(policy.history_roots,known)
    home.mkdir(parents=True,mode=0o700)
    dependency=retain(home/'dependency.json',base.admission.dependency_report(intake.bootstrap.policy.dependencies,intake.bootstrap.policy.tracker_root.parent))
    evidence=(p['original_prepared'],cfg['intake'],p['custody'],p['prior_outcome']);recipe=None
    if 'dependency_expiry'in p:
        policy=read(p['dependency_expiry']);recipe=renewed_dependencies(intake.bootstrap.policy.dependencies,policy);evidence=(*evidence,p['dependency_expiry'])
    new=continued_intake(base,intake,dependency,home,evidence,dependencies=recipe)
    ib=retain(home/'intake.json',base.encode(new));cb=retain(home/'config.json',dict(cfg,intake=ib))
    return p,F,author,base,new,cb

def run(row):
    p=read(row)
    if p.get('schema_version')=='fleet-completed-admission-continuation/v1':return completed_run(row)
    p=read(row);p,F,author,base,intake,config=prepare(row,p['home']);home=Path(p['home'])
    retain(home/'intent.json',dict(plan=row,original_prepared=p['original_prepared'],original_outcome=p['prior_outcome'],automatic_retry=False,source_reconstruction=False))
    controls=base.decode(read(config)['controls']);outcome=base.run('admit',{'intake':read(config)['intake']},controls,home/'admission-001')
    fresh=read(p['original_plan']);D=module(fresh['dispatcher'],'selected_unadmitted_dispatcher')
    job=dict(schema_version=D.SCHEMA,driver=fresh['dispatcher'],queue=fresh['queue'],config=config,outcome=outcome,operations=str(Path(read(config)['run_root'])/'dispatcher-v2'/('admission-'+home.name)),maximum_steps=100,models=True,commands=True,containers=True,publish=False)
    jb=retain(home/'authoring-job.json',job);D.successful(outcome,D.setup(job));D.run(jb)
    held=read(binding(Path(job['operations'])/'hold.json'));checkpoint,result=D.successful(held['outcome'],D.setup(job))
    return retain(home/'result.json',dict(status=result['status'],workbook=p['workbook'],plan=row,config=config,job=jb,outcome=held['outcome'],checkpoint=checkpoint,publication_performed=False,automatic_retry=False))

def completed_selection(p,old,job,cfg,*,historical=False):
    require(p['workbook']==old['workbook']==cfg['workbook'],'completed admission workbook differs')
    require(p['authoring_job']['path']==str(Path(old['home'])/'authoring-job.json')
            and job['config']['path']==str(Path(old['home'])/'config.json')
            and job['outcome']['path']==str(Path(old['home'])/'admission-001/outcome.json')
            and job['operations']==str(Path(old['home'])/'dispatcher-v2/first'), 'completed original admission job differs')
    path=Path(p['operations']);base=Path(cfg['run_root'])/'dispatcher-v2'
    require(path.is_absolute()and path.parent==base and path.name not in ('.','..')
            and not any(x.is_symlink()for x in(path,*path.parents)),'original dispatcher namespace required')
    selected=dict(job,operations=str(path))
    if historical:
        require(path.is_dir() and not Path(job['operations']).exists(), 'historical dispatcher location differs')
        output=Path(p['output'])
        require(output.is_absolute() and not any(x.is_symlink()for x in(output,*output.parents)), 'historical admission output is not canonical')
        require(read(binding(output/'authoring-job.json'))==selected, 'actual completed admission authoring job differs')
    else:require(not path.exists()and not Path(job['operations']).exists(),'dispatcher already started; no replay')
    return selected


def completed_context(row):
    p=read(row);require(set(p)=={'schema_version','helper','workbook','original_plan','authoring_job','worker_outcome','operations','output'}
        and p['schema_version']=='fleet-completed-admission-continuation/v1'and p['helper']==binding(__file__),'closed completed admission continuation required')
    old=read(p['original_plan']);require(old['schema_version']==SCHEMA,'original unadmitted plan required');require(binding(old['helper']['path'])==old['helper'],'original helper changed')
    outcome=read(p['worker_outcome']);wi=read(outcome['intent']);wj=read(wi['job'])
    require(outcome['status']=='RECONCILIATION_REQUIRED'and outcome['returncode']==1 and outcome['failure_type']is None and outcome['result']is None and outcome['automatic_retry']is False
            and wj['driver']==old['helper']and wj['inputs'][0]==p['original_plan']and wj['workbook']==p['workbook']and wj['source']==old['source'],'completed admission stopped worker differs')
    log=Path(p['worker_outcome']['path']).parent/'worker.log'
    require(binding(log)==outcome['log']and log.read_text().endswith('ValueError: dispatcher operations must stay in dedicated workbook namespace\n'),'different post-admission failure')
    require(read(binding(Path(old['home'])/'intent.json'))==dict(plan=p['original_plan'],original_prepared=old['original_prepared'],original_outcome=old['prior_outcome'],automatic_retry=False,source_reconstruction=False),'completed admission owning intent differs')
    fresh=read(old['original_plan']);root=Path(fresh['code_root']);sys.path.insert(0,str(root));job=read(p['authoring_job']);cfg=read(job['config'])
    require(job['driver']==fresh['dispatcher']and job['queue']==fresh['queue']and job['maximum_steps']==100 and all(job[k]is True for k in ('models','commands','containers'))and job['publish']is False,'original dispatcher capabilities differ')
    selected=completed_selection(p,old,job,cfg);D=module(fresh['dispatcher'],'selected_completed_admission_dispatcher');env=D.setup(selected);checkpoint,result=D.successful(job['outcome'],env)
    require(result['status']=='ADMITTED','actual completed admission required');env[0]._prior(checkpoint,result['descriptor'])
    D.operation_namespace(cfg,selected['operations'])
    return p,D,env,selected,checkpoint


def completed_history(row):
    """Read the actual completed admission without granting another dispatch."""
    from types import FunctionType,SimpleNamespace
    p=read(row);original=module(p['helper'],'historical_completed_admission_original')
    f=original.completed_context
    require(isinstance(f,FunctionType) and f.__closure__ is None and f.__globals__ is original.__dict__,
            'plain original completed admission reader required')
    selected_jobs=[];namespaces=[]
    def selection(plan,old,job,cfg):
        require(plan==p,'historical reader switched admission plan')
        selected=completed_selection(plan,old,job,cfg,historical=True);selected_jobs.append(selected)
        return selected
    def load(row_,name):
        value=original.module(row_,name)
        if name!='selected_completed_admission_dispatcher':return value
        proxy=SimpleNamespace(**value.__dict__)
        def location(cfg,path):
            require(selected_jobs and row_==selected_jobs[0]['driver']
                    and cfg==read(selected_jobs[0]['config']) and path==selected_jobs[0]['operations'],
                    'historical dispatcher validation escaped original location')
            namespaces.append(path);return Path(path)
        proxy.operation_namespace=location
        return proxy
    reader=FunctionType(f.__code__,{**f.__globals__,'completed_selection':selection,'module':load},f.__name__,f.__defaults__)
    value=reader(row)
    require(len(selected_jobs)==1 and namespaces==[p['operations']], 'original admission reader did not verify its dispatcher')
    require(read(binding(Path(p['output'])/'intent.json'))==dict(plan=row,admission=value[4],automatic_retry=False),
            'actual completed admission owning intent differs')
    return value


def completed_run(row):
    p,D,env,job,checkpoint=completed_context(row);home=Path(p['output']);require(not home.exists(),'completed admission continuation consumed');home.mkdir(parents=True,mode=0o700)
    retain(home/'intent.json',dict(plan=row,admission=checkpoint,automatic_retry=False));jb=retain(home/'authoring-job.json',job);D.run(jb)
    held=read(binding(Path(job['operations'])/'hold.json'));cp,result=D.successful(held['outcome'],env)
    return retain(home/'result.json',dict(status=result['status'],workbook=p['workbook'],plan=row,job=jb,outcome=held['outcome'],checkpoint=cp,publication_performed=False,automatic_retry=False))


if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('action',choices=['run']);a.add_argument('--plan',required=True);a.add_argument('--sha256',required=True);v=a.parse_args();print(json.dumps(run(dict(path=str(Path(v.plan).absolute()),sha256=v.sha256))))

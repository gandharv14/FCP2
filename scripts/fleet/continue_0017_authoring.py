"""Complete the real 0017 deterministic repair guard, then ordinary retained v7.

Requires an actual completed pre-review handoff and a live new central claim.
Every namespace is once-only; no fabricated coordinator outcome or call replay.
"""
import argparse,hashlib,importlib.util,json,os,shutil,sys
from pathlib import Path
ROOT=Path('/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline')
DISPATCHER={'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/authoring-resume-20260910/runner/advance_authoring_v7.py', 'sha256': '7c60e87d211e97f7145b6f05ee9cbd9a6a76a18f26c48d8172f2f57bf308156f'}
TEMPLATE={'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0017/remaining-001/job-template.json', 'sha256': '1481fdf961ad43a687b25ae8a17f6a3115a345555e332bf5ac615950352c2bfe'}
PREP_PLAN={'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0017/scope-repair-003/plan.json', 'sha256': 'b96483b9fca3b39d03900b24ab4ef7b1e38187071625c0f7cadb5428bb24274d'}
PREP_DRIVER={'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0017/scope-repair-003/driver.py', 'sha256': '73f1075ca0cb7ebe3121d941413cdc3691ff7262c4f7570049db5540a110ea3f'}

def require(v,message):
    if not v:raise ValueError(message)
def binding(path):
    p=Path(path).absolute();require(p.is_file() and not p.is_symlink(),'regular 0017 continuation evidence required');return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
def checked(b):
    require(set(b)=={'path','sha256'} and binding(b['path'])==b,'0017 bound continuation changed');return json.loads(Path(b['path']).read_bytes())
def retain(path,value):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True);raw=(json.dumps(value,indent=2,sort_keys=True)+'\n').encode()
    with p.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    return binding(p)
def modules():
    job=checked(TEMPLATE);require(job['driver']==DISPATCHER,'0017 original dispatcher differs')
    require(binding(DISPATCHER['path'])==DISPATCHER,'0017 dispatcher changed')
    sys.path.insert(0,str(Path(DISPATCHER['path']).parent))
    spec=importlib.util.spec_from_file_location('selected0017_remaining',DISPATCHER['path']);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    return m,job

def prepared_context(prepared):
    p=checked(prepared);plan=checked(PREP_PLAN);home=Path(plan['home'])
    require(prepared['path']==str(home/'result.json') and p['plan']==PREP_PLAN and p['status']=='RETRY_READY' and p['model_calls']==0 and p['publication_performed'] is False and p['automatic_retry'] is False,'actual 0017 preparation handoff required')
    require(p['resource']==binding(home/'guard/resource.json') and p['checkpoint']==binding(home/'guard/result.json'),'0017 preparation guard/result detached')
    resource=checked(p['resource']);require(resource['returncode']==0 and resource['failure'] is None and resource['intent']==binding(home/'guard/intent.json') and resource['log']==binding(home/'guard/step.log'),'0017 preparation guard not complete')
    intent=checked(resource['intent']);require(intent==dict(plan=PREP_PLAN,driver=PREP_DRIVER,seconds=3300,memory=8*1024**3) and binding(PREP_DRIVER['path'])==PREP_DRIVER,'0017 preparation implementation/guards changed')
    m,job=modules();env=m.setup(job);C,I,_,W,*_=env;value=checked(p['checkpoint']);worker,controls,recipe=I.selected(value['descriptor']);_,selection,lease=I._prior(p['checkpoint'],value['descriptor'])
    require(value['status']=='RETRY_READY' and value['next_step']=='disclosure-build' and worker.code_repair==value['checkpoint'],'0017 deterministic repair boundary required')
    require(os.environ.get('HARBOR_FLEET_OWNER')==plan['owner'] and sys.executable==plan['native_python'],'0017 selected native owner/interpreter differs')
    from scripts.fleet import disclosure_0017_repair as Q
    _,before,_=W.active(worker,selection,lease);Q.validate(worker,before)
    require(not Q.producer_run(worker).exists(),'0017 deterministic repair already attempted; reconcile instead of replay')
    return p,plan,m,job,env,value,worker,controls

def run(prepared,output):
    require(not os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY'),'0017 read-only environment cannot execute')
    output=Path(output);require(output.is_absolute() and not output.exists(),'0017 remaining namespace used')
    p,plan,m,job,env,value,worker,controls=prepared_context(prepared);C,I=env[:2]
    require(output==Path(job['operations']).parent and not Path(job['operations']).exists(),'0017 bound operation namespace differs or used')
    from scripts.fleet import disclosure_0017_proof as P
    P.require_fleet_custody(worker.root);C._campaign(worker.campaign)
    require(shutil.disk_usage(worker.root).free>=12*1024**3,'0017 launch disk reserve required')
    output.mkdir(parents=True);intent=retain(output/'intent.json',dict(prepared=prepared,dispatcher=DISPATCHER,template=TEMPLATE,driver=binding(__file__),automatic_retry=False));outcome=None
    try:
        # This is the ordinary owning instruction operation, including its
        # resource guard. The next ordinary v7 operation owns review1/1.
        outcome=I.run('step',dict(descriptor=value['descriptor'],checkpoint=p['checkpoint']),controls,output/'deterministic-build',models=False,commands=True,containers=True)
        m.successful(outcome,env)
        actual=dict(job,outcome=outcome);bound=retain(output/'authoring-job.json',actual)
        m.run(bound)
        hold=checked(binding(Path(actual['operations'])/'hold.json'));checkpoint,current=m.successful(hold['outcome'],env)
        require(current['status']=='PUBLICATION_READY','0017 remaining authoring has an owning hold')
        result=dict(status='PUBLICATION_READY',workbook='0017',plan=PREP_PLAN,job=bound,config=actual['config'],outcome=hold['outcome'],checkpoint=checkpoint,publication_performed=False,automatic_retry=False,dispatcher=DISPATCHER)
    except Exception as exc:
        result=dict(status='RECONCILIATION_REQUIRED',workbook='0017',intent=intent,owning_first_build_outcome=outcome,failure_type=type(exc).__name__,publication_performed=False,automatic_retry=False)
    return retain(output/'result.json',result)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--prepared',required=True);p.add_argument('--prepared-sha256',required=True);p.add_argument('--output',required=True);a=p.parse_args();print(json.dumps(run(dict(path=str(Path(a.prepared).absolute()),sha256=a.prepared_sha256),a.output)))

"""Run the real first0029 dialogue-review guard, then ordinary retained v7.

Requires an actual completed pre-review handoff and a live new central claim.
Every namespace is once-only; no fabricated coordinator outcome or call replay.
"""
import argparse,hashlib,importlib.util,json,os,shutil,sys
from pathlib import Path
ROOT=Path('/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline')
NS=ROOT/'diagnostics/vm-fleet-20260912/holds-0029/namespace-001'
MANIFEST={'path':str(NS/'adapter-002/manifest.json'),'sha256':'844653be65e658695762928ae18465bcebbfd0a043bff95c290b85153c7de127'}
TEMPLATE={'path':str(NS/'authoring-job-template.json'),'sha256':'5dbe01ae3bc36cb05e98b9a738627fdfeecc271ed5d9e1ff923b8a3ab9110475'}
PREP_PLAN={'path':str(ROOT/'diagnostics/vm-fleet-20260912/holds-0029/pre-review-001/plan.json'),'sha256':'835ce8237f0c8b1b19d487846b626924911ad004dd3f586799d7597c267e8843'}
PREP_DRIVER={'path':str(ROOT/'diagnostics/vm-fleet-20260912/holds-0029/pre-review-001/driver.py'),'sha256':'52e35506ca06909f04c51b3b45ec13ce8f246d1b9ba66c037b15984dcf8b0cdb'}

def require(v,message):
    if not v:raise ValueError(message)
def binding(path):
    p=Path(path).absolute();require(p.is_file() and not p.is_symlink(),'regular0029 continuation evidence required');return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
def checked(b):
    require(set(b)=={'path','sha256'} and binding(b['path'])==b,'0029 bound continuation changed');return json.loads(Path(b['path']).read_bytes())
def retain(path,value):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True);raw=(json.dumps(value,indent=2,sort_keys=True)+'\n').encode()
    with p.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    return binding(p)
def modules():
    meta=checked(MANIFEST);job=checked(TEMPLATE);require(job['driver']==meta['dispatcher'],'0029 retained dispatcher differs')
    b=meta['dispatcher'];require(binding(b['path'])==b,'0029 adapter changed');s=importlib.util.spec_from_file_location('selected0029_remaining',b['path']);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
    return m,job

def prepared_context(prepared):
    p=checked(prepared);plan=checked(PREP_PLAN);home=Path(plan['home'])
    require(prepared['path']==str(home/'result.json') and p['plan']==PREP_PLAN and p['status']=='RETRY_READY' and p['model_calls']==0 and p['publication_performed'] is False and p['automatic_retry'] is False,'actual0029 preparation handoff required')
    require(p['resource']==binding(home/'guard/resource.json') and p['checkpoint']==binding(home/'guard/result.json'),'0029 preparation guard/result detached')
    resource=checked(p['resource']);require(resource['returncode']==0 and resource['failure'] is None and resource['intent']==binding(home/'guard/intent.json') and resource['log']==binding(home/'guard/step.log'),'0029 preparation guard not complete')
    intent=checked(resource['intent']);require(intent==dict(plan=PREP_PLAN,driver=PREP_DRIVER,seconds=3300,memory=8*1024**3) and binding(PREP_DRIVER['path'])==PREP_DRIVER,'0029 preparation implementation/guards changed')
    m,job=modules();env=m.setup(job);C,I,_,W,*_=env;value=checked(p['checkpoint']);worker,controls,recipe=I.selected(value['descriptor']);_,selection,lease=I._prior(p['checkpoint'],value['descriptor'])
    require(value['status']=='RETRY_READY' and value['next_step']=='dialogue-review' and worker.dialogue_pre_review_repair==value['checkpoint'],'0029 first ordinary review required')
    require(os.environ.get('HARBOR_FLEET_OWNER')==plan['owner'] and sys.executable==plan['native_python'],'0029 selected native owner/interpreter differs')
    from scripts.batch import harbor_dialogue_pre_review_repair as Q
    _,before,_=W.active(worker,selection,lease);Q.validate(worker,before)
    require(not W.step_directory(worker,'dialogue-review').exists(),'0029 review already attempted; reconcile instead of replay')
    return p,plan,m,job,env,value,worker,controls

def run(prepared,output):
    require(not os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY'),'0029 read-only environment cannot execute')
    output=Path(output);require(output.is_absolute() and not output.exists(),'0029 remaining namespace used')
    p,plan,m,job,env,value,worker,controls=prepared_context(prepared);C,I=env[:2]
    from scripts.fleet import dialogue_0029_proof as P
    P.require_fleet_custody(worker.root);C._campaign(worker.campaign)
    require(shutil.disk_usage(worker.root).free>=12*1024**3,'0029 launch disk reserve required')
    output.mkdir(parents=True);intent=retain(output/'intent.json',dict(prepared=prepared,adapter=MANIFEST,template=TEMPLATE,driver=binding(__file__),automatic_retry=False));outcome=None
    try:
        # This is the ordinary owning instruction operation, including its
        # resource guard and independent model-review dispatch semantics.
        outcome=I.run('step',dict(descriptor=value['descriptor'],checkpoint=p['checkpoint']),controls,output/'first-review',models=True,commands=True,containers=True)
        m.successful(outcome,env)
        actual=dict(job,outcome=outcome);bound=retain(output/'authoring-job.json',actual)
        m.run(bound)
        hold=checked(binding(Path(actual['operations'])/'hold.json'));checkpoint,current=m.successful(hold['outcome'],env)
        require(current['status']=='PUBLICATION_READY','0029 remaining authoring has an owning hold')
        result=dict(status='PUBLICATION_READY',workbook='0029',plan=PREP_PLAN,job=bound,config=actual['config'],outcome=hold['outcome'],checkpoint=checkpoint,publication_performed=False,automatic_retry=False,namespace_adapter=MANIFEST)
    except Exception as exc:
        result=dict(status='RECONCILIATION_REQUIRED',workbook='0029',intent=intent,owning_first_review_outcome=outcome,failure_type=type(exc).__name__,publication_performed=False,automatic_retry=False)
    return retain(output/'result.json',result)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--prepared',required=True);p.add_argument('--prepared-sha256',required=True);p.add_argument('--output',required=True);a=p.parse_args();print(json.dumps(run(dict(path=str(Path(a.prepared).absolute()),sha256=a.prepared_sha256),a.output)))

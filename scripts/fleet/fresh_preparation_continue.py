"""Continue original Fresh preparation after a sealed missing-evidence refusal.

The old partial preparation is retained. Only the new native run namespace
changes; source generations, source attempts and all model budgets are original.
"""
from pathlib import Path
import argparse,hashlib,importlib.util,json,os,sys

def require(ok,message):
    if not ok:raise ValueError(message)

def binding(path):
    p=Path(path).absolute();require(p.is_file() and not any(x.is_symlink() for x in (p,*p.parents)),'regular canonical preparation evidence required');return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def read(row):
    require(binding(row['path'])==row,'preparation evidence changed');return json.loads(Path(row['path']).read_bytes())

def module(row,name):
    require(binding(row['path'])==row,'preparation module changed');spec=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

SCHEMA='fleet-fresh-preparation-continuation/v1'

def validate_namespace(old,new,home):
    a,b=Path(old['home']),Path(home)
    require(old['schema_version']=='harbor-fleet-fresh-plan/v1' and old['reconstruction']['mode']=='existing' and new==dict(old,home=home) and b.is_absolute() and b!=a and not b.is_relative_to(a) and not a.is_relative_to(b),'only fresh preparation namespace may change')

def validate_partial(partial):
    expected={'frozen-code.json','survey.json','candidate.json','dependency.json'}
    require({str(f.relative_to(partial))for f in partial.rglob('*')if f.is_file()}==expected and not any(f.is_symlink()for f in partial.rglob('*')),'preparation reached bootstrap, tracker or model effects')
    return expected

def validate_transfer(row):
    transfer=read(row);require(transfer['source_host']!=transfer['destination_host'] and transfer['source_reconstruction']is False,'exact source transfer receipt required');manifest=read(transfer['manifest'])
    require(manifest['files'] and len(manifest['files'])==transfer['files'] and manifest['historical_bytes_rewritten']is False and manifest['qualification_claimed']is False,'unaltered complete transfer required')
    require(len({r['path']for r in manifest['files']})==len(manifest['files']),'duplicate transferred path')
    for r in manifest['files']:require(binding(r['path'])['sha256']==r['sha256'] and Path(r['path']).stat().st_size==r['size'],'transferred source input changed')
    require(binding(transfer['response']['path'])==transfer['response'],'transfer response changed')
    return transfer

def context(row):
    p=read(row);require(set(p)=={'schema_version','helper','workbook','source','original_job','original_plan','continued_plan','prior_outcome','custody','source_transfer','home'} and p['schema_version']==SCHEMA and p['helper']==binding(__file__),'closed original preparation continuation required')
    job=read(p['original_job']);old=read(p['original_plan']);new=read(p['continued_plan']);outcome=read(p['prior_outcome']);intent=read(outcome['intent']);stop=read(p['custody'])
    validate_namespace(old,new,p['home'])
    require(job['workbook']==old['workbook']==p['workbook'] and job['source']==p['source'] and job['inputs'][0]==p['original_plan'] and job['command'][-5:]==['run','--plan',p['original_plan']['path'],'--plan-sha256',p['original_plan']['sha256']] and job['driver']==dict(path=str(Path(old['code_root'])/'scripts/fleet/fresh.py'),sha256=job['driver']['sha256']) and job['version']==Path(old['code_root']).name,'original Fresh worker identity differs')
    require(intent['job']==p['original_job'] and outcome['schema_version']=='harbor-fleet-worker-outcome/v1' and outcome['status']=='RECONCILIATION_REQUIRED' and outcome['returncode']==1 and outcome['failure_type']is None and outcome['automatic_retry']is False and outcome['publication_performed']is False and outcome['result']==binding(Path(old['home'])/'result.json'),'sealed original worker outcome required')
    result=read(outcome['result']);require(result==dict(status='RECONCILIATION_REQUIRED',workbook=p['workbook'],plan=p['original_plan'],failure_type='ValueError',publication_performed=False,automatic_retry=False),'original preparation failure differs')
    require(stop['matches']==[],'original preparation process remains active')
    files={r['path']:r for r in stop['files']};require(len(files)==len(stop['files']),'repeated stopped evidence')
    for path,r in files.items():require(binding(path)['sha256']==r['sha256'] and Path(path).stat().st_size==r['bytes'],'stopped preparation changed')
    log=Path(job['run'])/'worker.log';require(str(log)in files and log.read_text().rstrip().endswith('ValueError: regular cache evidence required') and 'base.bootstrap.validate(selection)'in log.read_text() and "selection(p['scope'],source)"in log.read_text(),'different original failure boundary')
    partial=Path(old['home'])/'authoring';expected=validate_partial(partial)
    require(read(binding(Path(old['home'])/'intent.json'))==dict(plan=p['original_plan'],driver=job['driver'],automatic_retry=False),'original owning intent differs')
    for r in (p['prior_outcome'],outcome['intent'],outcome['result'],binding(log),*[binding(partial/n)for n in expected]):require(files.get(r['path'],{}).get('sha256')==r['sha256'],'missing stopped boundary evidence')
    validate_transfer(p['source_transfer'])
    root=Path(old['code_root']);sys.path.insert(0,str(root));F=module(job['driver'],'continued_original_fresh');author=module(old['preparer'],'continued_original_preparer');base,_,_=author.imports(root)
    require(author.code_snapshot(root)==read(binding(partial/'frozen-code.json')),'original code snapshot changed')
    inventory=base.admission.read_inventory(old['inventory']['path']);candidate=binding(partial/'candidate.json');dependency=binding(partial/'dependency.json')
    recipe=base.admission.DependencyRecipe(tuple(binding(root/x)for x in ('requirements.txt','requirements-harbor.txt')),minimum_disk_bytes=12*1024**3,maximum_workbooks=1,maximum_models=1)
    policy=base.admission.policy_from_candidate_manifest(candidate,batch=old['batch'],tracker_root=partial/'trackers/workbooks',original_inventory=old['inventory'],dependency=dependency,dependencies=recipe,history_roots=tuple(map(Path,old['history_roots'])),expected_original_count=len(inventory['sources']))
    handoff=read(old['reconstruction']['handoff']);selection=base.bootstrap.Selection(candidate,policy,p['workbook'],handoff['source_manifest'],handoff['segmentation_manifest'],partial/'bootstrap',base.bootstrap.Policy('cms6m4urm006n07z8ecxi1oi2'))
    validation=base.bootstrap.validate(selection)
    require(validation['original']==p['source'] and not (partial/'trackers/workbooks'/ (p['workbook']+'.json')).exists(),'source validation or tracker differs')
    return p,F,validation


def run(row):
    p=read(row);old=read(p['original_plan']);sys.path.insert(0,old['code_root'])
    from scripts.fleet.coordinator import Client
    require(os.environ.get('HARBOR_FLEET_WORKBOOK')==p['workbook'],'live original workbook claim required')
    Client(os.environ['HARBOR_FLEET_URL'],os.environ['HARBOR_FLEET_TOKEN_FILE']).call('heartbeat',workbook=p['workbook'],owner=os.environ['HARBOR_FLEET_OWNER'],fence=int(os.environ['HARBOR_FLEET_FENCE']),token=os.environ['HARBOR_FLEET_CLAIM_TOKEN'],lease_seconds=180)
    p,F,validation=context(row)
    return F.run(p['continued_plan'])

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('action',choices=['run','probe']);a.add_argument('--plan',required=True);a.add_argument('--sha256',required=True);v=a.parse_args();row=dict(path=v.plan,sha256=v.sha256)
    if v.action=='run':result=run(row)
    else:
        p,F,validation=context(row);result=dict(status='PASS',models_dispatched=0,result=dict(plan=row,validation=validation),source_reconstruction=False)
    print(json.dumps(result))

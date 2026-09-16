"""Bind an actual 0017 RETRY_READY receipt to the unused deterministic disclosure build.

No synthetic operation result is accepted. Read-only probes prohibit model
execution; run delegates to the preserved owning instruction and v7 bridge.
"""
from __future__ import annotations
import argparse,hashlib,importlib.util,json,os,sys
from pathlib import Path
ROOT=Path('/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline')
BRIDGE={'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0017/rehost3-001/remaining-driver.py', 'sha256': '81bfa035ecb93fae61c2c43674f98a9c63d6cf13e4be363ba42ed3f567bd6fe9'}
PREP_JOB={'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0017/rehost3-001/worker-job-002.json', 'sha256': '434b1d12d03cc7638a48348ca038eb6ca303e851097af938ea699ec559a8374d'}
SCHEMA='harbor-fleet-0017-remaining-authoring-selection/v1'
SOURCE='d54f4c8b06b9655ab8922e8304bbb77036e37b7849c09206fc3314671b77cbf2'
VERSION='74ce86dd50996d836d542247a83c0ed3954d79801bcd416159f80dfd62019371'
OWNER='hhu-server-3:slot-2:0017-scope-repair-host3-001'

def require(ok,message):
    if not ok:raise ValueError(message)

def binding(path):
    p=Path(path);require(p.is_absolute() and p.is_file() and not p.is_symlink(),'bound regular continuation file required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def checked(row):
    require(set(row)=={'path','sha256'} and binding(row['path'])==row,'bound remaining-authoring evidence changed')
    return json.loads(Path(row['path']).read_bytes())

def load(row):
    p=checked(row)
    require(set(p)=={'schema_version','workbook','bridge','prepared','prepared_copy','prior_worker_outcome','prior_worker_outcome_copy','source_sha256','version','owner','prior_fence','output'},'closed remaining-authoring selection required')
    require(p['schema_version']==SCHEMA and p['workbook']=='0017' and p['bridge']==BRIDGE and p['source_sha256']==SOURCE and p['version']==VERSION and p['owner']==OWNER and type(p['prior_fence']) is int and p['prior_fence']==1,'exact stopped 0017 preparation required')
    require(binding(BRIDGE['path'])==BRIDGE,'original review bridge changed')
    require(p['prepared']['path']=='/home/gmahajan_labelbox_com/synthetic-data-fleet/0017-scope-repair-host3-001/result.json' and p['prepared']['sha256']==p['prepared_copy']['sha256'],'actual preparation custody differs')
    require(p['prior_worker_outcome']['sha256']==p['prior_worker_outcome_copy']['sha256'],'actual stopped worker custody differs')
    # Mirrors retain bytes for root inspection; execution reads original paths.
    prepared=checked(p['prepared']);outcome=checked(p['prior_worker_outcome'])
    require(checked(p['prepared_copy'])==prepared and checked(p['prior_worker_outcome_copy'])==outcome,'root custody copies differ from native originals')
    require(prepared['status']=='RETRY_READY' and prepared['model_calls']==0 and prepared['publication_performed'] is False and prepared['automatic_retry'] is False,'preparation did not reach owning RETRY_READY')
    require(outcome['schema_version']=='harbor-fleet-worker-outcome/v1' and outcome['automatic_retry'] is False and outcome['returncode']==0 and outcome['failure_type'] is None,'preparation worker did not stop successfully')
    intent=checked(outcome['intent']);require(intent['job']==PREP_JOB,'original zero-model worker job differs');job=checked(intent['job'])
    require(job['workbook']=='0017' and job['source']['sha256']==SOURCE and job['version']==VERSION and intent['owner']==OWNER,'prior source/code/owner changed')
    require(checked(outcome['result'])==prepared,'preparation outer and native result differ')
    require(p['output']=='/home/gmahajan_labelbox_com/synthetic-data-fleet/0017-scope-repair-003-authoring' and not Path(p['output']).exists(),'remaining authoring namespace already consumed')
    spec=importlib.util.spec_from_file_location('retained_0017_remaining_authoring_bridge',BRIDGE['path']);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    return p,m

def probe(row):
    p,m=load(row)
    require(os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY')=='1','explicit nonexecuting probe required')
    _,_,_,_,_,value,worker,_=m.prepared_context(p['prepared'])
    require(value['next_step']=='disclosure-build','only unused deterministic disclosure build can follow')
    return dict(status='PASS',models_dispatched=0,result=dict(plan=row,next_step='disclosure-build',review_ordinal=1,review_limit=1,passed_lanes_preserved=True,legacy_completed_calls=3),execution_performed=False)

def run(row):
    p,m=load(row)
    require(not os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY'),'read-only selection cannot execute')
    require(os.environ.get('HARBOR_FLEET_OWNER')==OWNER,'review owner differs')
    require(os.environ.get('HARBOR_FLEET_WORKBOOK')=='0017' and os.environ.get('HARBOR_FLEET_FENCE')==str(p['prior_fence']+1),'exact successor review fence required')
    return m.run(p['prepared'],p['output'])

def main():
    a=argparse.ArgumentParser();a.add_argument('action',choices=['probe','run']);a.add_argument('--plan',required=True);a.add_argument('--plan-sha256',required=True);x=a.parse_args();row=dict(path=str(Path(x.plan).absolute()),sha256=x.plan_sha256)
    print(json.dumps((probe if x.action=='probe' else run)(row)))
if __name__=='__main__':main()

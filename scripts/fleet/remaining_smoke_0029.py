"""Bind an actual 0029 RETRY_READY receipt to the unused first dialogue review.

No synthetic operation result is accepted. Read-only probes prohibit model
execution; run delegates to the preserved owning instruction and v7 bridge.
"""
from __future__ import annotations
import argparse,hashlib,importlib.util,json,os,sys
from pathlib import Path
ROOT=Path('/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline')
BRIDGE={'path':str(ROOT/'diagnostics/vm-fleet-20260912/holds-0029/namespace-001/continue-authoring.py'),'sha256':'57704f38bbdc2f1b8ac95f751a32ee87763f0c1c37832e4f019869c9a12c951d'}
PREP_JOB={'path':str(ROOT/'diagnostics/vm-fleet-20260912/holds-0029/pre-review-001/worker-job-002.json'),'sha256':'acc2541686f43134e91470c5dd11b48ff15c95b45ff5652862e3eabe7c52c098'}
SCHEMA='harbor-fleet-0029-first-review-selection/v1'
SOURCE='55029f5834ed624c303b4d7f00adb9bf0ad88c1ff10856e174e44cc7ae1f9509'
VERSION='2d3e15a8e7b500635e6960eac14872beb98adcae75de8f1f53f174eeb42ec98c'
OWNER='hhu-server-3:slot-2:0029-pre-review-001'

def require(ok,message):
    if not ok:raise ValueError(message)

def binding(path):
    p=Path(path);require(p.is_absolute() and p.is_file() and not p.is_symlink(),'bound regular continuation file required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def checked(row):
    require(set(row)=={'path','sha256'} and binding(row['path'])==row,'bound first-review evidence changed')
    return json.loads(Path(row['path']).read_bytes())

def load(row):
    p=checked(row)
    require(set(p)=={'schema_version','workbook','bridge','prepared','prepared_copy','prior_worker_outcome','prior_worker_outcome_copy','source_sha256','version','owner','prior_fence','output'},'closed first-review selection required')
    require(p['schema_version']==SCHEMA and p['workbook']=='0029' and p['bridge']==BRIDGE and p['source_sha256']==SOURCE and p['version']==VERSION and p['owner']==OWNER and type(p['prior_fence']) is int and p['prior_fence']==1,'exact stopped 0029 preparation required')
    require(binding(BRIDGE['path'])==BRIDGE,'original review bridge changed')
    require(p['prepared']['path']=='/home/gmahajan_labelbox_com/synthetic-data-fleet/0029-pre-review-001/result.json' and p['prepared']['sha256']==p['prepared_copy']['sha256'],'actual preparation custody differs')
    require(p['prior_worker_outcome']['sha256']==p['prior_worker_outcome_copy']['sha256'],'actual stopped worker custody differs')
    # Mirrors retain bytes for root inspection; execution reads original paths.
    prepared=checked(p['prepared']);outcome=checked(p['prior_worker_outcome'])
    require(checked(p['prepared_copy'])==prepared and checked(p['prior_worker_outcome_copy'])==outcome,'root custody copies differ from native originals')
    require(prepared['status']=='RETRY_READY' and prepared['model_calls']==0 and prepared['publication_performed'] is False and prepared['automatic_retry'] is False,'preparation did not reach owning RETRY_READY')
    require(outcome['schema_version']=='harbor-fleet-worker-outcome/v1' and outcome['automatic_retry'] is False and outcome['returncode']==0 and outcome['failure_type'] is None,'preparation worker did not stop successfully')
    intent=checked(outcome['intent']);require(intent['job']==PREP_JOB,'original zero-model worker job differs');job=checked(intent['job'])
    require(job['workbook']=='0029' and job['source']['sha256']==SOURCE and job['version']==VERSION and intent['owner']==OWNER,'prior source/code/owner changed')
    require(checked(outcome['result'])==prepared,'preparation outer and native result differ')
    require(p['output']=='/home/gmahajan_labelbox_com/synthetic-data-fleet/0029-remaining-authoring-001' and not Path(p['output']).exists(),'remaining authoring namespace already consumed')
    spec=importlib.util.spec_from_file_location('retained_0029_first_review_bridge',BRIDGE['path']);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    return p,m

def probe(row):
    p,m=load(row)
    require(os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY')=='1','explicit nonexecuting probe required')
    _,_,_,_,_,value,worker,_=m.prepared_context(p['prepared'])
    require(value['next_step']=='dialogue-review','only unused first dialogue review can follow')
    return dict(status='PASS',models_dispatched=0,result=dict(plan=row,next_step='dialogue-review',review_ordinal=1,review_limit=2,passed_lanes_preserved=True,legacy_completed_calls=8),execution_performed=False)

def run(row):
    p,m=load(row)
    require(not os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY'),'read-only selection cannot execute')
    require(os.environ.get('HARBOR_FLEET_OWNER')==OWNER,'review owner differs')
    require(os.environ.get('HARBOR_FLEET_WORKBOOK')=='0029' and os.environ.get('HARBOR_FLEET_FENCE')==str(p['prior_fence']+1),'exact successor review fence required')
    return m.run(p['prepared'],p['output'])

def main():
    a=argparse.ArgumentParser();a.add_argument('action',choices=['probe','run']);a.add_argument('--plan',required=True);a.add_argument('--plan-sha256',required=True);x=a.parse_args();row=dict(path=str(Path(x.plan).absolute()),sha256=x.plan_sha256)
    print(json.dumps((probe if x.action=='probe' else run)(row)))
if __name__=='__main__':main()

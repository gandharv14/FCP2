"""Generate exact future jobs for handed-off size holds, never start them."""
from pathlib import Path
import argparse,json
from scripts.fleet import build_0190_successor as B
from scripts.fleet.build_size_hold_successor import CATALOG
ROOT=B.ROOT

def prepare(workbook,destination):
 B.require(workbook in CATALOG,'unreviewed workbook');source=Path(destination).absolute();built=B.read(B.record(source/'build.json'));out=source/'jobs-001';out.mkdir(exist_ok=False);recipe=B.read(built['inspection_plan']);home=Path(recipe['home'])
 original_helper=ROOT/'scripts/fleet/normalization_0073.py';text=original_helper.read_text()
 # The reviewed source differs only in the explicit workbook/source/budget and
 # full reference denominator. No orchestration or gate branches change.
 report=B.read(B.record(ROOT/'diagnostics/vm-fleet-20260912/remaining-holds-001/report-002.json'));row=next(r for r in report['normalization'] if r['workbook']==workbook)
 changes=[('0073',workbook),('3973add1-8769-4a3c-b1d4-bcbf3564b79f',recipe['budget']['attempt_id']),('cde07456bdefc8b3ae66dcd98040c86c3cf16c9896e9790064380932ed116e39',recipe['source']['sha256']),('len(set(refs))==242','len(set(refs))=='+str(row['unique_candidate_references']))]
 witnesses=[]
 for old,new in changes:
  count=text.count(old);B.require(count>0,'exact helper template changed');text=text.replace(old,new);witnesses.append(dict(before=old,after=new,occurrences=count))
 if workbook=='0173':
  before="home/'dispatcher-v2/first/003/resource.json'";after="home/'dispatcher-v2/after-disk-003-v5/000/resource.json'";B.require(text.count(before)==1,'explicit0173latestguard context changed');text=text.replace(before,after);witnesses.append(dict(before=before,after=after,occurrences=1))
 helper=B.keep(out/('normalization_'+workbook+'-'+B.sha(text.encode())+'.py'),text.encode())
 original_config=B.record(home/'config.json');config=B.read(original_config);config['code']=built['snapshot'];config['controls']['value']['env_file']['value']='/home/gmahajan_labelbox_com/.config/harbor-fleet/model.env';cb=B.keep(out/'config.json',config)
 old_job=B.record(home/'dispatcher-v2/first/job.json');job=B.read(old_job);job.update(schema_version='harbor-queue-dispatch/v6',driver=B.record(ROOT/'diagnostics/authoring-resume-20260910/runner/advance_authoring_v7.py'),config=cb,publish=False);jb=B.keep(out/'dispatcher-job.json',job)
 diag=ROOT/'diagnostics/vm-fleet-20260912';newhome=diag/'workbooks'/workbook/'normalization-continuation-001';plan=dict(schema_version='harbor-fleet-'+workbook+'-normalization-continuation/v1',workbook=workbook,code_root=B.read(built['snapshot'])['root'],home=str(newhome),original_job=jb,tracker=recipe['tracker'],unresolved=B.record(home/('dispatcher-v2/after-disk-003-v5/000/result.json' if workbook=='0173' else 'dispatcher-v2/first/003/result.json')),research=B.record(home/'dispatcher-v2/first/002/result.json'),normalization_plan=recipe['original_plan'],stopped_proof=B.record(diag/'local-custody-handoff-001.json'),snapshot=built['snapshot'],original_config=original_config)
 pb=B.keep(out/'continuation-plan-draft.json',plan)
 r='/home/gmahajan_labelbox_com/synthetic-data-fleet/releases/5d3b09ac15928bcb163405f8fd950a84d7ff3d5f56b5c01eb2a47e8f810e9057'
 worker=dict(schema_version='harbor-fleet-worker-job/v1',workbook=workbook,version=built['fingerprint'],source=recipe['source'],driver=helper,inputs=[pb,built['snapshot'],built['profile']],command=[r+'/venv/bin/python','-I','-B',helper['path'],'run','--plan',pb['path'],'--plan-sha256',pb['sha256']],cwd=r+'/code',run=str(diag/'workers'/recipe['host']/'slot-2'/(workbook+'-normalization-001')),result=str(newhome/'result.json'),memory_limit_bytes=8*1024**3,minimum_disk_bytes=12*1024**3,timeout_seconds=86400)
 wb=B.keep(out/'worker-job-draft.json',worker)
 return B.keep(out/'job-preparation.json',dict(workbook=workbook,host=recipe['host'],builder=B.record(__file__),helper_template=B.record(original_helper),helper=helper,edits=witnesses,original_config=original_config,original_job=old_job,plan=pb,worker_job=wb,root_claim_required=True,smoke_must_pass_first=True,models_dispatched=0,tracker_modified=False,semantic_budget_reset=False,automatic_retry=False))
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--workbook',choices=sorted(CATALOG),required=True);p.add_argument('--destination',required=True);a=p.parse_args();print(json.dumps(prepare(a.workbook,a.destination)))

"""Data preparation for the existing sealed-400 normalization continuation.

Historical requests, source files, budgets and snapshots remain unchanged.
This module creates an immutable code selection and native diagnostic seed;
the existing owning partition/lease/worker controllers perform execution.
"""
from pathlib import Path
import hashlib,json,subprocess
from scripts.fleet.migration import binding,checked,retain

def prepare_launch(root,prepared_row,pool_root,controller):
 """Bind the existing v3 controller to an already proved terminal continuation."""
 import base64,shlex
 root=Path(root).absolute();prepared=checked(prepared_row);h=Path(prepared_row['path']).parent;seed=checked(prepared['seed']);s=checked(checked(prepared['implementation'])['snapshot']);code=Path(s['root']);o=h/'post-takeover-001';planrow=binding(o/'plan.json');plan=checked(planrow);central=checked(binding(h/'takeover-001/central.json'))['workbook'];host,slot,generation=central['owner'].split('/');assert host==prepared['host'];wb=plan['workbook'];L=o/'root-launch-001';L.mkdir()
 prior=checked(prepared['custody'])['files'];native_home='/home/gmahajan_labelbox_com/synthetic-data-fleet/source-pool-opaque-003/'+wb;oldout=next(r for r in prior if r['native']['path']==native_home+'/worker/outcome.json');oldintent=next(r for r in prior if r['native']['path']==native_home+'/worker/intent.json');oldjob=checked(checked(oldintent['copy'])['job'])
 process=Path(pool_root)/host/'generations'/generation/(slot+'.workbook-'+wb+'.result.json');dispatch=Path(pool_root)/host/'dispatches'/(wb+'.json');paths=[str(process),str(dispatch)]
 command='from pathlib import Path\nimport json,base64,hashlib\nprint(json.dumps([dict(path=p,sha256=hashlib.sha256(Path(p).read_bytes()).hexdigest(),data=base64.b64encode(Path(p).read_bytes()).decode())for p in '+repr(paths)+']))'
 r=subprocess.run(['/Users/gandharvmahajan/google-cloud-sdk/bin/gcloud','compute','ssh',host,'--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet','--command',shlex.join(['python3','-I','-B','-c',command])],capture_output=True,timeout=120);(L/'custody.stderr').write_bytes(r.stderr);r.check_returncode();copies=[]
 for i,value in enumerate(json.loads(r.stdout)):
  raw=base64.b64decode(value.pop('data'),validate=True);f=L/('pool-custody-'+str(i)+'.json');f.write_bytes(raw);f.chmod(0o600);assert binding(f)['sha256']==value['sha256'];copies.append(dict(native=value,copy=binding(f)))
 runtime=retain(L/'runtime.json',dict(schema_version='fleet-worker-runtime/v1',root=str(code),python=oldjob['command'][0],files={v['relative']:dict(path=str(code/v['relative']),sha256=v['sha256'])for v in s['files']}))
 runway=retain(L/'native-runway.json',dict(schema_version='fleet-native-job-lease-runway/v1',workbook=wb,source_sha256=oldjob['source']['sha256'],tracker=plan['tracker'],required_seconds=3600))
 helper=checked(prepared['implementation'])['helper'];owner=host+':slot-1:'+wb+'-terminal001';inputs=[planrow,helper,seed['successor'],checked(prepared['implementation'])['snapshot'],runway]
 job=retain(L/'worker-job.json',dict(oldjob,version=code.name,driver=helper,command=[oldjob['command'][0],'-I','-B',helper['path'],'run','--plan',planrow['path'],'--plan-sha256',planrow['sha256']],inputs=inputs,cwd=str(code),run='/home/gmahajan_labelbox_com/synthetic-data-fleet/workers/'+host+'/'+wb+'-terminal001',result=plan['home']+'/result.json',memory_limit_bytes=8589934592))
 # Local absolute-path evidence is read directly; only Linux-native paths may
 # use the authenticated offline projection.
 projected=[r for r in prior+checked(binding(o/'custody.json'))['files']+copies
            if Path(r['native']['path']).is_relative_to('/home/gmahajan_labelbox_com/synthetic-data-fleet')]
 mapping=retain(L/'offline-bindings.json',dict(schema_version='fleet-offline-bindings/v1',copies=projected))
 C=root/'diagnostics/vm-fleet-20260912/control';selected=retain(L/'launch-plan.json',dict(schema_version='harbor-fleet-job-launch/v3',host=host,job=job,root_plan=planrow,probe=binding(o/'proof.json'),prior_outcome=oldout['native'],prior_process=copies[0]['native'],prior_process_copy=copies[0]['copy'],prior_kind='pool-completion',expected_fence=central['fence'],expected_owner=central['owner'],expected_version=central['version'],new_owner=owner,remote_receipt='/home/gmahajan_labelbox_com/synthetic-data-fleet/processes/'+wb+'-terminal001.json',coordinator_receipt=binding(C/'coordinator-process-v4.json'),runtime=runtime,worker=binding(code/'scripts/fleet/worker.py'),offline_bindings=mapping,admission_lock=binding(C/'author-capacity-001/root-helpers-001/admission_lock.py'),native_lease_runway=runway))
 cmd=[str(root/'.venv/bin/python'),'-I','-B',str(controller),'--plan',selected['path'],'--plan-sha256',selected['sha256']];retain(L/'commands.json',dict(inspect=cmd,execute=cmd+['--execute','--journal',str(L/'launch-001')]))
 files=[job,runtime,runway];verify=L/'verify.py';verify.write_text('from pathlib import Path\nimport hashlib\nrows='+repr(files)+'\nfor r in rows:assert hashlib.sha256(Path(r["path"]).read_bytes()).hexdigest()==r["sha256"]\nprint("EXACT_WORKER_INPUTS_VERIFIED")\n');retain(L/'stage-files.json',[r['path']for r in files]+[str(verify)]);return selected

def build(root,workbook,custody,output,implementation=None):
 root=Path(root).absolute();out=Path(output).absolute();out.mkdir(mode=0o700);m=checked(custody);rows=m['files'];native_home='/home/gmahajan_labelbox_com/synthetic-data-fleet/source-pool-opaque-003/'+workbook
 def row(suffix):
  found=[r for r in rows if r['native']['path']==native_home+suffix];assert len(found)==1;return found[0]
 jobrow=row('/authoring/authoring-job.json');configrow=row('/authoring/authoring/config.json');job=checked(jobrow['copy']);config=checked(configrow['copy']);snapshot=next(r for r in rows if r['native']==config['code']);old=checked(snapshot['copy']);oldroot=Path(old['root'])
 changes=['scripts/batch/harbor_normalization_partitioned.py','scripts/batch/harbor_normalization_routing.py','scripts/batch/harbor_normalization_terminal_repair.py']
 if implementation is None:
  imp=out/'implementation-001';imp.mkdir();stage=imp/'stage';subprocess.run(['/bin/cp','-cR',str(oldroot),str(stage)],check=True)
  for name in changes:
   p=stage/name
   if p.exists():p.unlink()
   p.write_bytes((root/name).read_bytes())
  inventory={str(p.relative_to(stage)):binding(p)['sha256']for p in stage.rglob('*')if p.is_file()and p.name!='.pipeline-code.json'};raw=(json.dumps(inventory,sort_keys=True,separators=(',',':'))+'\n').encode();(stage/'.pipeline-code.json').unlink();(stage/'.pipeline-code.json').write_bytes(raw);code=imp/'code'/hashlib.sha256(raw).hexdigest();code.parent.mkdir();stage.rename(code)
  new=retain(imp/'snapshot.json',dict(root=str(code),files=[dict(relative=str(p.relative_to(code)),sha256=binding(p)['sha256'])for p in sorted(code.rglob('*'))if p.is_file()]))
  driver=imp/'continue.py';driver.write_bytes((root/'scripts/fleet/normalization_continue.py').read_bytes());implementation=retain(imp/'implementation.json',dict(snapshot=new,helper=binding(driver),original_snapshot=config['code'],changed_files=changes))
 selected=checked(implementation);assert selected['original_snapshot']['sha256']==config['code']['sha256'];code=Path(checked(selected['snapshot'])['root']);newconfig=retain(out/'config.json',dict(config,code=selected['snapshot']));newjob=retain(out/'job.json',dict(job,config=newconfig,driver=binding(code/Path(job['driver']['path']).relative_to(oldroot))))
 successor=retain(out/'successor.json',dict(schema_version='normalization-terminal-code-successor/v1',original_snapshot=config['code'],snapshot=selected['snapshot'],original_config=configrow['native'],config=newconfig,job=newjob,changed_files=changes))
 execution=[r for r in rows if '/model-campaign/'in r['native']['path']and r['native']['path'].endswith('/plan.json')];assert len(execution)==1
 seed=retain(out/'seed.json',dict(workbook=workbook,code_root=str(code),original_job=jobrow['native'],tracker=row('/authoring/authoring/trackers/workbooks/'+workbook+'.json')['native'],unresolved=row('/authoring/authoring/dispatcher-v2/first/003/result.json')['native'],research=row('/authoring/authoring/dispatcher-v2/first/002/result.json')['native'],normalization_plan=row('/authoring/authoring/environment/normalization/'+workbook+'-variable-sources/production-selection.json')['native'],successor=successor,helper=selected['helper'],execution_plan=execution[0]['native'],authorization=binding(root/'diagnostics/bulk-production/authorization-20260913.md'),fresh_plan=row('/pipeline/transition/author-plan.json')['native']))
 prep=out/'preparation-driver.py';prep.write_bytes(Path(__file__).read_bytes());return retain(out/'prepared.json',dict(seed=seed,implementation=implementation,custody=custody,helper=binding(prep),host=m['host'],models_dispatched=0,claims_changed=0))

"""Prepare data for the existing direct, no-model warning continuation.

Only exact retained style findings are selected. Original reviewers, source,
tracker, snapshots and consumed attempts remain intact; this performs no claim.
"""
from pathlib import Path
import argparse,base64,hashlib,inspect,json,shlex,subprocess,sys,time
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from scripts.fleet.migration import binding,checked,retain

def custody(payload):
 from pathlib import Path
 import json,hashlib,base64,os,time
 home=Path(payload['home']);wb=payload['workbook'];stage=home/'authoring/authoring/dispatcher-v2/first'/payload['step'];rows={}
 def add(p):
  p=Path(p)
  if p.is_file():
   raw=p.read_bytes();assert len(raw)<16*1024**2 and sum(len(x['data'])for x in rows.values())+len(raw)*2<96*1024**2
   rows[str(p)]=dict(path=str(p),sha256=hashlib.sha256(raw).hexdigest(),data=base64.b64encode(raw).decode())
 for p in [home/'worker/outcome.json',home/'worker/intent.json',home/'pipeline/transition/author-plan.json',home/'pipeline/transition/author-history.json',home/'pipeline/transition/handoff.json',home/'authoring/authoring-job.json',home/'authoring/authoring/trackers/workbooks'/(wb+'.json')]:add(p)
 for folder in [home/'authoring',home/'authoring/authoring',stage]:
  for p in folder.glob('*.json'):add(p)
 result=json.loads((stage/'result.json').read_bytes());assert result['status']=='DIALOGUE_REPAIR_REQUIRED';add(result['descriptor']['path']);add(result['checkpoint']['path'])
 for n in ['review.json','paraphraser.filled.md']:add(home/'authoring/authoring/instruction/producer/dialogue/round-01'/n)
 add(home/'authoring/authoring/instruction/producer/dialogue/claims.json')
 processes=[]
 for proc in Path('/proc').iterdir():
  if not proc.name.isdigit()or int(proc.name)==os.getpid():continue
  try:
   fields=(proc/'stat').read_text().rsplit(')',1)[1].split();args=(proc/'cmdline').read_bytes().decode().split(chr(0))
   if fields[0]not in ('Z','X')and any(a==str(home)or a.startswith(str(home)+'/')for a in args):processes.append(dict(pid=int(proc.name),start=fields[19]))
  except(FileNotFoundError,ProcessLookupError):pass
 assert not processes
 return dict(at=time.time(),files=list(rows.values()),processes=processes)

def prepare(root,wb,host,pool,step,output,findings):
 root=Path(root).absolute();out=Path(output).absolute();out.mkdir(mode=0o700);cop=out/'custody-001';cop.mkdir(mode=0o700);control=root/'diagnostics/vm-fleet-20260912/control';original_job=binding(control/pool/'prepared-001/workbooks'/wb/'worker-job.json');job=checked(original_job)
 home='/home/gmahajan_labelbox_com/synthetic-data-fleet/'+pool+'/'+wb;payload=dict(home=home,workbook=wb,step=step)
 command=['/Users/gandharvmahajan/google-cloud-sdk/bin/gcloud','compute','ssh',host,'--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet','--command',shlex.join(['python3','-I','-B','-c',inspect.getsource(custody)+'\nimport json,sys\nprint(json.dumps(custody(json.load(sys.stdin))))'])]
 r=subprocess.run(command,input=json.dumps(payload).encode(),capture_output=True,timeout=120);(cop/'stderr').write_bytes(r.stderr);r.check_returncode();v=json.loads(r.stdout);rows={}
 for i,item in enumerate(v.pop('files')):
  raw=base64.b64decode(item.pop('data'),validate=True);path=cop/(str(i).zfill(3)+'-'+Path(item['path']).name);path.write_bytes(raw);path.chmod(0o600);assert binding(path)['sha256']==item['sha256'];rows[item['path']]=dict(native=item,copy=binding(path))
 mapping=retain(cop/'mapping.json',dict(schema_version='fleet-offline-bindings/v1',files=list(rows.values()),**v))
 return assemble(root,wb,host,pool,step,out,findings,job,rows,mapping)

def preserve_runtime_producer(stage):
 """Install the reviewed retained-root check without unrelated snapshot changes."""
 changes={
 'scripts/batch/harbor_instruction_repair.py':[
  ("def prefix(before,selection,context,*,stop='dialogue'):","def prefix(before,selection,context,*,stop='dialogue',runtime_root=None):"),
  ("else:result=check_completed_gate(view,CompletionEvidence(kind,selection),context)","else:result=check_completed_gate(view,CompletionEvidence(kind,selection),context,\n            **({'runtime_root':runtime_root} if kind=='verification' and runtime_root is not None else {}))")],
 'scripts/batch/harbor_completed_lanes.py':[
  ("def check_completed_gate(before,packet,context,*,runtime_producer=None):","def check_completed_gate(before,packet,context,*,runtime_producer=None,runtime_root=None):"),
  ("        validate_runtime(p.runtime,p.observation,root=static.ROOT, **(","        if runtime_root is not None:\n            from scripts.batch.harbor_runtime_readiness import code_bindings\n            require(p.runtime.code==code_bindings(static.ROOT),\n                    'historical runtime differs from current runtime implementation')\n        validate_runtime(p.runtime,p.observation,root=static.ROOT if runtime_root is None else runtime_root, **(")],
 'scripts/batch/harbor_dialogue_warning_requalification.py':[
  ("    proof=repair.prefix(before,selection,worker.context)","    historical_runtime=(Path(read(old_snapshot)['root'])\n        if selection.runtime==previous.runtime and selection.observation==previous.observation else None)\n    proof=repair.prefix(before,selection,worker.context,\n        **({'runtime_root':historical_runtime} if historical_runtime is not None else {}))")]
 }
 changes['scripts/batch/harbor_dialogue_first_round_warning.py']=[("ALLOWED.add('scripts/batch/harbor_normalization_validator_bridge.py')","ALLOWED.add('scripts/batch/harbor_normalization_validator_bridge.py')\nALLOWED.update({'scripts/batch/harbor_instruction_repair.py','scripts/batch/harbor_completed_lanes.py'})")]
 for name,pairs in changes.items():
  path=stage/name;original=path.read_text();text=original
  for old,new in pairs:
   if new in text:continue
   assert text.count(old)==1,(name,old);text=text.replace(old,new)
  if name.endswith('harbor_dialogue_warning_requalification.py')and "ALLOWED.add('scripts/batch/harbor_completed_lanes.py')"not in text:
   anchor="ALLOWED.add('.pipeline-code.json')";assert text.count(anchor)==1;text=text.replace(anchor,anchor+"\nALLOWED.add('scripts/batch/harbor_completed_lanes.py')")
  if text!=original:path.unlink();path.write_text(text)

def assemble(root,wb,host,pool,step,out,findings,job,rows,mapping):
 control=root/'diagnostics/vm-fleet-20260912/control';original_job=binding(control/pool/'prepared-001/workbooks'/wb/'worker-job.json');home='/home/gmahajan_labelbox_com/synthetic-data-fleet/'+pool+'/'+wb
 def read(path):return checked(rows[str(path)]['copy'])
 def native(path):return rows[str(path)]['native']
 h=Path(home);config_path=h/'authoring/authoring/config.json';config=read(config_path);old=read(config['code']['path']);oldroot=Path(old['root']);old_result=native(h/'authoring/authoring/dispatcher-v2/first'/step/'result.json');result=read(old_result['path']);review=read(h/'authoring/authoring/instruction/producer/dialogue/round-01/review.json')
 assert review['accuracy']['verdict']=='pass'and review['accuracy']['extras']==[]and review['accuracy']['cell_refs_in_senior_turns']==[]and all(c['must_say']=='entailed'and not c['findings']for c in review['accuracy']['claims'])
 assert set(review['naturalness']['findings'])<=set(findings)and review['naturalness']['verdict']=='fail'
 F=out/'implementation-001';F.mkdir();stage=F/'stage';subprocess.run(['/bin/cp','-cR',str(oldroot),str(stage)],check=True);validator='.cursor/skills/additional-assumptions-dialogue/scripts/validate_dialogue.py'
 for item in old['files']:assert binding(oldroot/item['relative'])['sha256']==item['sha256']
 dest=stage/validator;text=dest.read_text();anchor='        retained_template_findings = {';assert text.count(anchor)==1;extra=[f for f in findings if repr(f)not in text];dest.unlink();dest.write_text(text.replace(anchor,anchor+''.join('\n            '+repr(f)+','for f in extra)))
 preserve_runtime_producer(stage)
 inv={str(p.relative_to(stage)):binding(p)['sha256']for p in stage.rglob('*')if p.is_file()and p.name!='.pipeline-code.json'};raw=(json.dumps(inv,sort_keys=True,separators=(',',':'))+'\n').encode();(stage/'.pipeline-code.json').unlink();(stage/'.pipeline-code.json').write_bytes(raw);version=hashlib.sha256(raw).hexdigest();code=F/'code'/version;code.parent.mkdir();stage.rename(code)
 snap=retain(F/'snapshot.json',dict(root=str(code),files=[dict(relative=str(p.relative_to(code)),sha256=binding(p)['sha256'])for p in sorted(code.rglob('*'))if p.is_file()]))
 helpers={}
 for key,name in [('helper','retained_warning_continue.py'),('context','instruction_semantic_continue.py'),('renewal','continuation_lease.py'),('stdlib','stdlib_imports.py'),('capacity','capacity_policy.py'),('prepare','warning_prepare_0190.py')]:
  path=F/name;path.write_bytes((root/'scripts/fleet'/name).read_bytes());helpers[key]=binding(path)
 from scripts.fleet.coordinator import Client
 co=checked(binding(control/'coordinator-process-v4.json'));status=Client(co['url'],str(Path(co['private_directory'])/'admin.token')).call('status');w=next(w for w in status['workbooks']if w['workbook']==wb);assert w['state']=='hold'and w['source_sha256']==job['source']['sha256']and json.loads(w['reason'])==native(h/'worker/outcome.json');assert not any(x['workbook']==wb and x['state']in ('active','hold')for x in status['operations'])
 retain(out/'central.json',dict(at=status['at'],workbook=w));owner=host+':slot-1:'+wb+'-warning-001';fence=w['fence']+1
 config_new=retain(out/'config-001.json',{**config,'code':snap});original=retain(out/'original-context-001.json',dict(schema_version='fleet-instruction-semantic-continuation/v1',workbook=wb,helper=helpers['context'],fresh_plan=native(h/'pipeline/transition/author-plan.json'),original_job=native(h/'authoring/authoring-job.json'),outcome=native(h/'authoring/authoring/dispatcher-v2/first'/step/'outcome.json'),result=old_result,descriptor=result['descriptor'],blocked=result['checkpoint'],tracker=native(h/'authoring/authoring/trackers/workbooks'/(wb+'.json')),source=job['source'],owner=owner,fence=fence,output='/home/gmahajan_labelbox_com/synthetic-data-fleet/'+wb+'-original-warning-context-001',renewal_module=helpers['renewal'],authorization=binding(root/'diagnostics/bulk-production/authorization-20260913.md'),capacity_policy=helpers['capacity']))
 from scripts.batch import harbor_transition_codec as codec
 selection=codec.decode(result['selection']);normal=retain(out/'normalization-profile-001.json',dict(prepared=selection.content.normalization_prepared,snapshot=config['code']))
 plan=retain(out/'plan-001.json',dict(schema_version='harbor-direct-warning-continuation/v1',workbook=wb,helper=helpers['helper'],prepare_helper=helpers['prepare'],dispatcher=binding(code/'scripts/fleet/advance_authoring_v8.py'),snapshot=snap,old_snapshot=config['code'],old_result=old_result,config=config_new,takeover_plan=None,completion=None,capacity_policy=helpers['capacity'],source_sha256=job['source']['sha256'],owner=owner,fence=fence,output='/home/gmahajan_labelbox_com/synthetic-data-fleet/'+wb+'-style-warning-001',maximum_steps=4,stdlib=helpers['stdlib'],live_renewal=dict(context=helpers['context'],plan=original),normalization_profile=normal))
 preparation_source=out/'preparation-driver.py';preparation_source.write_bytes(Path(__file__).read_bytes())
 return retain(out/'prepared.json',dict(plan=plan,mapping=mapping,original_worker_job=original_job,original_native_home=home,host=host,central=binding(out/'central.json'),helper=binding(preparation_source),findings=findings,source_unchanged=True,models_dispatched=0,claims_changed=0))

def stage_and_prove(prepared_row):
 import re,concurrent.futures
 p=checked(prepared_row);out=Path(prepared_row['path']).parent;root=Path(__file__).resolve().parents[2];plan=checked(p['plan']);code=checked(plan['snapshot'])['root'];prior=checked(plan['live_renewal']['plan']);source=checked(p['original_worker_job']);host=p['host']
 for kind,original in [('prefix','native-prefix-002'),('renewal','native-renewal-002')]:
  h=out/('native-'+kind+'-001');h.mkdir();text=(root/'diagnostics/bulk-production/0277-style-warning-001'/original/'probe.py').read_text()
  for name,value in [('ROW',repr(p['plan'])),('ROOT',repr(code)),('HERE',repr(str(h)))]:
   text,n=re.subn('^'+name+'=.*$',lambda _:name+'='+value,text,flags=re.M);assert n==1
  (h/'probe.py').write_text(text)
 from scripts.fleet.migration import export_files
 files=[x for x in(out/'implementation-001').rglob('*')if x.is_file()]+[Path(x['path'])for x in (p['plan'],plan['config'],plan['normalization_profile'],plan['live_renewal']['plan'])]+[out/('native-'+k+'-001/probe.py')for k in ('prefix','renewal')]
 export_files(files,out/'transfer.tar.gz',out/'transfer-manifest.json');stage=out/'stage-001';stage.mkdir();remote='/home/gmahajan_labelbox_com/synthetic-data-fleet/diagnostics/'+out.name;g='/Users/gandharvmahajan/google-cloud-sdk/bin/gcloud'
 def ssh(command):return [g,'compute','ssh',host,'--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet','--command',command]
 with(stage/'transfer.log').open('xb')as log:
  subprocess.run(ssh(shlex.join(['mkdir','-p',remote])),stdout=log,stderr=log,check=True,timeout=120)
  subprocess.run([g,'compute','scp',str(out/'transfer.tar.gz'),str(out/'transfer-manifest.json'),host+':'+remote+'/','--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet'],stdout=log,stderr=log,check=True,timeout=240)
 rows={x['native']['path']:x['copy']for x in checked(p['mapping'])['files']};old=checked(rows[plan['old_snapshot']['path']]);manifest=dict(path=remote+'/transfer-manifest.json',sha256=binding(out/'transfer-manifest.json')['sha256'])
 script='from pathlib import Path\nimport inspect,sys,json\nsys.path.insert(0,'+repr(old['root'])+')\nfrom scripts.fleet.migration import import_files\nprint(json.dumps(import_files(Path('+repr(remote+'/transfer.tar.gz')+'),'+repr(manifest)+',Path('+repr(remote+'/receipt.json')+'),[Path('+repr(str(root))+')])))'
 r=subprocess.run(ssh(shlex.join([source['command'][0],'-I','-B','-c',script])),capture_output=True,timeout=240);(stage/'native.json').write_bytes(r.stdout);(stage/'stderr').write_bytes(r.stderr);r.check_returncode()
 def probe(kind):
  h=out/('native-'+kind+'-001');r=subprocess.run(ssh(shlex.join([source['command'][0],'-I','-B',str(h/'probe.py')])),capture_output=True,timeout=3330);retain(h/'native.json',dict(returncode=r.returncode,stdout=r.stdout.decode(),stderr=r.stderr.decode()));r.check_returncode();value=json.loads(r.stdout);assert value['returncode']==0 and value['failure']is None,value['tail'];return dict(kind=kind,seconds=value['seconds'],summary=json.loads(value['tail'].splitlines()[-1]))
 with concurrent.futures.ThreadPoolExecutor(max_workers=2)as ex:results=list(ex.map(probe,('prefix','renewal')))
 return retain(out/'native-proofs.json',dict(prepared=prepared_row,results=results,models_dispatched=0,claims_changed=0))

def prepare_launch(prepared_row,pool_root,controller):
 root=Path(__file__).resolve().parents[2];control=root/'diagnostics/vm-fleet-20260912/control';p=checked(prepared_row);out=Path(prepared_row['path']).parent;plan=checked(p['plan']);s=checked(plan['snapshot']);code=Path(s['root']);old=checked(p['original_worker_job']);central=checked(p['central'])['workbook'];host,slot,generation=central['owner'].split('/');assert host==p['host'];wb=plan['workbook'];L=out/'root-launch-001';L.mkdir()
 path=Path(pool_root)/host/'generations'/generation/(slot+'.workbook-'+wb+'.result.json');dispatch=Path(pool_root)/host/'dispatches'/(wb+'.json');command='from pathlib import Path\nimport json,base64,hashlib\nprint(json.dumps([dict(path=p,sha256=hashlib.sha256(Path(p).read_bytes()).hexdigest(),data=base64.b64encode(Path(p).read_bytes()).decode())for p in '+repr([str(path),str(dispatch)])+']))'
 r=subprocess.run(['/Users/gandharvmahajan/google-cloud-sdk/bin/gcloud','compute','ssh',host,'--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet','--command',shlex.join(['python3','-I','-B','-c',command])],capture_output=True,timeout=120);(L/'custody.stderr').write_bytes(r.stderr);r.check_returncode();copies=[]
 for i,row in enumerate(json.loads(r.stdout)):
  raw=base64.b64decode(row.pop('data'),validate=True);f=L/('pool-custody-'+str(i)+'.json');f.write_bytes(raw);f.chmod(0o600);assert binding(f)['sha256']==row['sha256'];copies.append(dict(native=row,copy=binding(f)))
 guard=json.loads(checked(binding(out/'native-prefix-001/native.json'))['stdout']);result=json.loads(guard['tail'].splitlines()[-1]);assert guard['returncode']==0 and guard['failure']is None and result['status']=='ORIGINAL_Q_VALIDATED_TO_FIRST_NOTES_WRITE'
 proof=retain(L/'proof.json',dict(status='PASS',models_dispatched=0,result={**result,'plan':p['plan']},original_guard=guard,evidence=binding(out/'native-prefix-001/native.json')))
 runtime=retain(L/'runtime.json',dict(schema_version='fleet-worker-runtime/v1',root=str(code),python=old['command'][0],files={r['relative']:dict(path=str(code/r['relative']),sha256=r['sha256'])for r in s['files']}))
 runway=retain(L/'native-runway.json',dict(schema_version='fleet-native-job-lease-runway/v1',workbook=wb,source_sha256=plan['source_sha256'],tracker=checked(plan['live_renewal']['plan'])['tracker'],required_seconds=600))
 inputs=[p['plan'],plan['helper'],plan['prepare_helper'],plan['snapshot'],plan['config'],plan['stdlib'],plan['capacity_policy'],plan['normalization_profile'],plan['live_renewal']['plan'],plan['live_renewal']['context'],runway]
 job=retain(L/'worker-job.json',dict(old,version=code.name,driver=plan['helper'],command=[old['command'][0],'-I','-B',plan['helper']['path'],'run','--plan',p['plan']['path'],'--sha256',p['plan']['sha256']],inputs=inputs,cwd=str(code),run='/home/gmahajan_labelbox_com/synthetic-data-fleet/workers/'+host+'/slot-1/'+wb+'-warning-001',result=plan['output']+'/result.json',memory_limit_bytes=8589934592))
 mapping=checked(p['mapping'])['files'];offline=retain(L/'offline-bindings.json',dict(schema_version='fleet-offline-bindings/v1',copies=mapping+copies));original=next(r['native']for r in mapping if r['native']['path']==p['original_native_home']+'/worker/outcome.json')
 selected=retain(L/'launch-plan.json',dict(schema_version='harbor-fleet-job-launch/v3',host=host,job=job,root_plan=p['plan'],probe=proof,prior_outcome=original,prior_process=copies[0]['native'],prior_process_copy=copies[0]['copy'],prior_kind='pool-completion',expected_fence=central['fence'],expected_owner=central['owner'],expected_version=central['version'],new_owner=plan['owner'],remote_receipt='/home/gmahajan_labelbox_com/synthetic-data-fleet/processes/'+wb+'-warning-001.json',coordinator_receipt=binding(control/'coordinator-process-v4.json'),runtime=runtime,worker=binding(code/'scripts/fleet/worker.py'),offline_bindings=offline,admission_lock=binding(control/'author-capacity-001/root-helpers-001/admission_lock.py'),native_lease_runway=runway))
 cmd=[str(root/'.venv/bin/python'),'-I','-B',str(controller),'--plan',selected['path'],'--plan-sha256',selected['sha256']];retain(L/'commands.json',dict(inspect=cmd,execute=cmd+['--execute','--journal',str(L/'launch-001')]))
 files=[job,runtime,runway];verify=L/'verify.py';verify.write_text('from pathlib import Path\nimport json,hashlib\nrows='+repr(files)+'\nfor r in rows:assert hashlib.sha256(Path(r["path"]).read_bytes()).hexdigest()==r["sha256"]\nprint(json.dumps(dict(status="WORKER_INPUT_BYTES_VERIFIED")))\n');retain(L/'stage-files.json',[r['path']for r in files]+[str(verify)]);return selected

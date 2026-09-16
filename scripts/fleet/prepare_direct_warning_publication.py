"""Prepare existing publication controls for a stopped direct warning continuation.

This creates data and a read-only proof invocation. It does not claim, renew,
publish, dispatch a model, or change the selected owning implementation.
"""
from pathlib import Path
import base64,inspect,json,re,shlex,subprocess,sys
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from scripts.fleet.migration import binding,checked,retain


def resume_admission_archive(prior,launch,job):
 """Use the exact root-retained input bytes of a completed resume admission."""
 if not prior.get('resume'):return None
 previous=checked(prior['resume']['prior_plan'])
 assert previous['live_renewal']==prior['live_renewal'] and previous['workbook']==prior['workbook']
 runway=checked(launch['native_lease_runway']);row=runway['tracker']
 assert runway['workbook']==prior['workbook'] and runway['source_sha256']==prior['source_sha256']
 assert job['inputs'].count(row)==1
 copies=checked(launch['offline_bindings'])['copies'];found=[x for x in copies if x['native']==row]
 assert len(found)==1 and found[0]['copy']['sha256']==row['sha256']
 archive=found[0]['copy'];assert binding(archive['path'])==archive
 return dict(input=row,archive=archive)


def native(payload):
 from pathlib import Path
 import base64,hashlib,json,os,time
 files={}
 def read(row):
  p=Path(row['path']);provided=payload.get('admission_archive')
  raw=(base64.b64decode(provided['data'],validate=True) if provided is not None and row==provided['archive'] else p.read_bytes())
  assert hashlib.sha256(raw).hexdigest()==row['sha256'],'changed native binding'
  files[str(p)]=raw;return json.loads(raw)
 def bind(p):
  p=Path(p);assert p.is_absolute() and not any(x.is_symlink()for x in(p,*p.parents))
  return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
 launch=payload['launch'];job=read(launch['job']);prior=read(payload['continuation']);wb=prior['workbook']
 process=read(bind(launch['remote_receipt']))
 assert process==payload['process'] and process['job']==launch['job']
 assert job['workbook']==wb and job['inputs'][0]==payload['continuation'] and job['result']==str(Path(prior['output'])/'result.json')
 for entry in Path('/proc').iterdir():
  if not entry.name.isdigit()or int(entry.name)==os.getpid():continue
  try:
   fields=(entry/'stat').read_text().rsplit(')',1)[1].split();args=(entry/'cmdline').read_bytes().decode(errors='replace').split(chr(0))
  except(FileNotFoundError,PermissionError):continue
  if fields[0]=='Z':continue
  assert not(int(entry.name)==process['pid']and fields[19]==str(process['start_ticks'])),'author still active'
  assert not any(a==job['run']or a.startswith(job['run']+'/')or a==payload['continuation']['path']for a in args),'author-related process active'
 outrow=bind(Path(job['run'])/'outcome.json');out=read(outrow);intent=read(out['intent']);ready=read(out['result'])
 assert out['returncode']==0 and out['failure_type']is None and intent['job']==launch['job']and intent['owner']==launch['new_owner']
 assert ready['status']=='PUBLICATION_READY'and ready['publication_performed']is False and ready['plan']==payload['continuation'] and ready['models_dispatched']==0
 historical=prior.get('live_renewal',{}).get('completed_semantic',{}).get('tracker')
 admission_history=payload.get('admission_archive')
 if admission_history is not None:
  assert prior.get('resume') and set(admission_history)=={'input','archive','data'}
  runway=read(launch['native_lease_runway'])
  assert admission_history['input']==runway['tracker'] and job['inputs'].count(admission_history['input'])==1
  assert admission_history['archive']['sha256']==admission_history['input']['sha256']
  archived=read(admission_history['archive']);assert archived['workbook_id']==wb
 elif historical is not None:
  archive=bind(Path(prior['output'])/'semantic-live-before.json')
  assert archive['sha256']==historical['sha256'],'admission tracker archive changed'
  read(archive)
 for row in ready['outcomes']:
  outcome=read(row);result=read(outcome['result']);read(outcome['resource'])
 tracker=bind(payload['tracker']);value=read(tracker)
 assert value['workbook_id']==wb and value['execution_state']=='publication_queued'and value['terminal_verdict']=='PUBLICATION_READY'
 return dict(status='STOPPED_DIRECT_WARNING_CUSTODY',at=time.time(),outcome=outrow,ready=out['result'],tracker=tracker,process=bind(launch['remote_receipt']),processes=[],**({'admission_history':{k:admission_history[k]for k in('input','archive')}}if admission_history is not None else {}),files=[dict(native=dict(path=p,sha256=hashlib.sha256(raw).hexdigest()),data=base64.b64encode(raw).decode())for p,raw in sorted(files.items())])


def prepare(repository,continuation,launched,tracker,implementation,output):
 root=Path(repository).absolute();out=Path(output).absolute();prior=checked(continuation);actual=checked(launched);intent=checked(actual['intent']);launch=checked(intent['plan']);job=checked(launch['job']);m=checked(implementation)
 assert prior['schema_version']=='harbor-direct-warning-continuation/v1'and actual['remote']['root_intent']==actual['intent']and launch['root_plan']==continuation
 assert job['driver']==prior['helper']and job['source']['sha256']==prior['source_sha256']
 out.mkdir(mode=0o700)
 if 'publication_wait' not in m['dependencies']:
  from scripts.fleet.build_publication_wait import build as bounded_publisher
  implementation=bounded_publisher(out/'bounded-publisher',implementation,
   root/'diagnostics/vm-fleet-20260912/control/0029-publication-003/collector.py')
  m=checked(implementation)
 payload=dict(launch=launch,process=actual['remote'],continuation=continuation,tracker=str(tracker))
 history=resume_admission_archive(prior,launch,job)
 if history is not None:payload['admission_archive']=dict(history,data=base64.b64encode(Path(history['archive']['path']).read_bytes()).decode())
 command=['/Users/gandharvmahajan/google-cloud-sdk/bin/gcloud','compute','ssh',launch['host'],'--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet','--command',shlex.join([job['command'][0],'-I','-B','-c',inspect.getsource(native)+'\nimport json,sys\nprint(json.dumps(native(json.load(sys.stdin))))'])]
 r=subprocess.run(command,input=json.dumps(payload).encode(),capture_output=True,timeout=120);(out/'custody.stderr').write_bytes(r.stderr);r.check_returncode();v=json.loads(r.stdout);copies=[];cust=out/'custody';cust.mkdir(mode=0o700)
 for i,row in enumerate(v.pop('files')):
  raw=base64.b64decode(row['data'],validate=True);p=cust/(str(i).zfill(3)+'-'+Path(row['native']['path']).name);p.write_bytes(raw);p.chmod(0o600);assert binding(p)['sha256']==row['native']['sha256'];copies.append(dict(native=row['native'],copy=binding(p)))
 v['copies']=copies;custody=retain(out/'custody.json',v)
 wb=prior['workbook'];policy=retain(out/'wait-policy.json',dict(schema_version='harbor-fleet-publication-wait/v1',workbook=wb,source_sha256=prior['source_sha256'],lease_tracker=v['tracker'],maximum_wait_seconds=120,authorization=binding(root/'diagnostics/bulk-production/authorization-20260913.md')))
 base=json.loads((root/'diagnostics/bulk-production/0320-publication-001/plan-001.json').read_text())
 plan=retain(out/'plan.json',dict(base,code_root=checked(prior['snapshot'])['root'],workbook=wb,source_sha256=prior['source_sha256'],journal='/home/gmahajan_labelbox_com/synthetic-data-fleet/'+wb+'-warning-publication-001',ready=v['ready'],publication_wait=policy,retained_warning=dict(continuation_helper=prior['helper'],continuation_plan=continuation,job=launch['job'],root_launch=launched)))
 return finish(root,out,prior,actual,intent,launch,job,m,continuation,launched,implementation,custody,plan,policy)


def write_bytes(path,raw):
 with Path(path).open("xb")as stream:stream.write(raw)
 return binding(path)


def admission_tracker_history(profile, archive, output):
 """Select the existing completed-tracker admission contract after qualification.

 The archive is a verified copy of the continuation's original admission
 tracker. The original native controller still checks full tracker ancestry,
 every other job input, current custody, and the independent qualification.
 """
 from scripts.fleet import publication_launch as L
 p=checked(profile);plan=checked(p['plan']);prior=checked(plan['retained_warning']['continuation_plan'])
 old=prior['live_renewal']['completed_semantic']['tracker'];before=checked(archive)
 if ('input_history'in p or archive['sha256']!=old['sha256']
     or before['workbook_id']!=plan['workbook']
     or checked(p['proof'])['tracker']['path']!=old['path']):
  raise ValueError('completed admission tracker archive differs')
 out=Path(output).absolute();out.mkdir()
 history=retain(out/'input-history.json',dict(schema_version='fleet-completed-tracker-input/v1',
  input=old,archive=archive,proof=p['proof'],ready=plan['ready']))
 result=retain(out/'profile.json',dict(p,input_history=history));L.load(result)
 from scripts.fleet.publication_staging import prepare as stage_publication
 stage_publication(result,out)
 return result


def finish(root,out,prior,actual,intent,launch,job,m,continuation,launched,implementation,custody,plan,policy):
 proof=out/'native-ready-001';proof.mkdir(exist_ok=True);q=write_bytes(out/'queued_qualification.py',(root/'scripts/fleet/queued_qualification.py').read_bytes())
 text=(root/'diagnostics/bulk-production/0031-publication-001/native-ready-001/probe.py').read_text()
 for name,value in [('QUALIFICATION',repr(q)),('PUBLISHER',repr(m['publisher'])),('PLAN','Path('+repr(plan['path'])+')')]:
  text,n=re.subn('^'+name+'=.*$',lambda _:name+'='+value,text,flags=re.M);assert n==1
 from scripts.fleet.publication_staging import direct_child_selection_probe
 text=direct_child_selection_probe(text)
 probe=write_bytes(proof/'probe.py',text.encode());text=(root/'diagnostics/bulk-production/0031-publication-001/native-ready-001/run.py').read_text();text,n=re.subn('^PROBE=.*$',lambda _:'PROBE='+repr(probe),text,flags=re.M);assert n==1;runner=write_bytes(proof/'run.py',text.encode())
 files=[row['path']for row in (implementation,m['publisher'],*m['dependencies'].values(),policy,plan,q,intent['plan'],actual['intent'],launched,probe,runner)]
 history=resume_admission_archive(prior,launch,job)
 if history is not None:
  files[-1:-1]=[launch['native_lease_runway']['path'],launch['offline_bindings']['path'],history['archive']['path']]
 retain(out/'native-files.json',files)
 preparation_source=write_bytes(out/'preparation-driver.py',Path(__file__).read_bytes())
 return retain(out/'prepared.json',dict(plan=plan,implementation=implementation,custody=custody,continuation=continuation,launched=launched,helper=preparation_source,models_dispatched=0,publication_mutations=0))


def prepare_root(repository,prepared_row,output,controller):
 from scripts.fleet import publication_launch as L
 root=Path(repository).absolute();prepared=checked(prepared_row);home=Path(prepared_row['path']).parent;actual=checked(prepared['launched']);launch=checked(checked(actual['intent'])['plan']);job=checked(launch['job']);custody=checked(prepared['custody']);rows={x['native']['path']:x for x in custody['copies']}
 native=home/'native-ready-001';guard=json.loads(json.loads((native/'native.json').read_text())['stdout']);assert guard['returncode']==0 and guard['failure']is None
 captured=native/'captured';captured.mkdir(exist_ok=True)
 files=[str(captured/name)for name in ('publication-proof.json','proof.json','qualification.json')]
 code='from pathlib import Path\nimport json,base64,hashlib\nprint(json.dumps([dict(path=p,data=base64.b64encode(Path(p).read_bytes()).decode(),sha256=hashlib.sha256(Path(p).read_bytes()).hexdigest())for p in '+repr(files)+']))'
 c=['/Users/gandharvmahajan/google-cloud-sdk/bin/gcloud','compute','ssh',launch['host'],'--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet','--command',shlex.join([job['command'][0],'-I','-B','-c',code])]
 r=subprocess.run(c,capture_output=True,timeout=120);r.check_returncode()
 for row in json.loads(r.stdout):
  p=Path(row['path']);raw=base64.b64decode(row['data'],validate=True);assert p.parent==captured
  write_bytes(p,raw);p.chmod(0o600);assert binding(p)['sha256']==row['sha256']
 proof=binding(captured/'publication-proof.json');assert json.loads(guard['log'])['proof']==proof
 out=Path(output).absolute();out.mkdir();gr=retain(out/'guard.json',guard);capture=retain(out/'qualification-capture.json',dict(schema_version='fleet-guarded-publication-qualification-capture/v1',helper=binding(home/'queued_qualification.py'),capture=binding(captured/'proof.json'),qualification=binding(captured/'qualification.json')))
 cp=root/'diagnostics/vm-fleet-20260912/control';process=rows[launch['remote_receipt']];m=checked(prepared['implementation'])
 p=dict(schema_version=L.SCHEMA,host=launch['host'],plan=prepared['plan'],publisher=m['publisher'],implementation=prepared['implementation'],proof=proof,guard=gr,worker_outcome=custody['outcome'],process=process['native'],process_copy=process['copy'],expected_fence=launch['expected_fence']+1,expected_owner=launch['new_owner'],coordinator_receipt=launch['coordinator_receipt'],owner=launch['host']+':publication:'+job['workbook']+':direct001',python=job['command'][0],capacity_policy=binding(cp/'publication-control-018/implementation/scripts/fleet/capacity_policy.py'),qualification_capture=capture,admission_lock=(launch['admission_lock'] if 'admission_lock' in launch else binding(cp/'author-capacity-001/root-helpers-001/admission_lock.py')))
 prior=checked(prepared['continuation']);historical=prior.get('live_renewal',{}).get('completed_semantic',{}).get('tracker')
 if custody.get('admission_history') is not None:
  history=custody['admission_history'];assert history==resume_admission_archive(prior,launch,job)
  historical=history['input'];archive=rows[history['archive']['path']]
 else:archive=rows.get(str(Path(prior['output'])/'semantic-live-before.json'))
 if historical is not None and archive is not None:
  assert archive['copy']['sha256']==historical['sha256']
  p['input_history']=retain(out/'input-history.json',dict(schema_version='fleet-completed-tracker-input/v1',
   input=historical,archive=archive['copy'],proof=proof,ready=checked(prepared['plan'])['ready']))
 profile=retain(out/'profile.json',p);L.load(profile);cmd=[str(root/'.venv/bin/python'),'-I','-B',str(controller),'--profile',profile['path'],'--sha256',profile['sha256']]
 retain(out/'commands.json',dict(inspect=cmd,execute=cmd+['--execute','--journal',str(out/'launch-001')]))
 from scripts.fleet.publication_staging import prepare as stage_publication
 stage_publication(profile,out);return profile

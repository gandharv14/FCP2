"""Prepare original publication proof from an exact completed continuation.

This read-only preparation accepts a stopped model tail, aggregate adoption, or
source-revision author and records its actual guarded READY, original source
context, queued tracker and process custody.
"""
from pathlib import Path
import base64,inspect,json,shlex,subprocess
from scripts.fleet.migration import binding,checked,retain
from scripts.fleet import build_fresh_publication as build,build_publication_wait as wait


def completed_probe(template,publisher,plan,*,aggregate=False,source_revision=False,semantic=False,derivative=False):
 """Render the native-proved completed-history ordering and cleanup."""
 start=template.index('from scripts.fleet import history as H\n');end=template.index('cp,v=D.successful',start)
 opening="""prefix=pub.fresh_publication.history_prefix(plan)
os.environ.update({prefix+'PATH':history['path'],prefix+'SHA256':history['sha256'],'HARBOR_FLEET_WORKBOOK':plan['workbook'],'HARBOR_FLEET_URL':'http://127.0.0.1:8765'})
pub.fresh_publication.history_survey(pub,plan,history,pub.fresh_publication.selected_history_survey(plan))
ready=pub.fresh_publication.ready_value(pub,plan);job=pub.checked(ready['job']);D=pub.fresh_publication.dispatcher(pub,pub.pinned_dispatcher,plan,ready,job);pub.publication_only();env=D.setup(job)
"""
 text=template[:start]+opening+template[end:]
 text=text.replace('request=D.publication_request(env,v)','request=pub.fresh_publication.projected_request(pub,env[2],D.publication_request(env,v),plan)')
 from scripts.fleet.publication_staging import child_selection_probe
 text=child_selection_probe(text)
 if sum((aggregate,source_revision,semantic,derivative))>1:raise ValueError("one completed publication context required")
 close="D._aggregate_publication_scope.__exit__(None,None,None)\nD._aggregate_publication_manager.__exit__(None,None,None)"if aggregate else"D._model_publication_manager.__exit__(None,None,None)"
 if semantic:close="D._semantic_publication_scope.__exit__(None,None,None)\nD._semantic_publication_manager.__exit__(None,None,None)"
 if derivative:close="D._source_revision_publication_scope.__exit__(None,None,None)\nD._derivative_context_manager.__exit__(None,None,None)"
 if source_revision:close="D._disclosure_publication_scope.__exit__(None,None,None)\nD._disclosure_publication_manager.__exit__(None,None,None)"
 text=text.replace("print(json.dumps(dict(status='ACTUAL_READY_FULL_OWNING_PUBLICATION_PROOF_PASSED'", close+"\nprint(json.dumps(dict(status='ACTUAL_READY_FULL_OWNING_PUBLICATION_PROOF_PASSED'")
 return '\n'.join('PUBLISHER='+repr(publisher)if line.startswith('PUBLISHER=')else 'PLAN='+repr(plan)if line.startswith('PLAN=')else line for line in text.splitlines())+'\n'


def native_custody(payload):
 import os,hashlib,base64
 from pathlib import Path
 import json
 files={}
 def record(path):
  p=Path(path)
  if not p.is_absolute()or any(x.is_symlink()for x in(p,*p.parents)):raise ValueError('unsafe completed author artifact')
  raw=p.read_bytes();files[str(p)]=raw;return dict(path=str(p),sha256=hashlib.sha256(raw).hexdigest())
 def read(row):
  if record(row['path'])!=row:raise ValueError('completed author binding changed')
  return json.loads(files[row['path']])
 p=payload['launch'];job=read(p['job']);task=read(p['root_plan']);process=read(payload['process'])
 derivative=task['schema_version']=='fleet-dialogue-source-revision-adoption/v1'
 if job!=payload['job']or task!=payload['task']or process!=payload['process_value']:raise ValueError('completed author selection differs')
 if not (derivative or (task['schema_version']=='fleet-instruction-unacquired-model-continuation/v1'and 'completed_tail'in task)
         or task['schema_version']=='fleet-disclosure-aggregate-adoption/v1'
         or(task['schema_version']=='fleet-completed-instruction-continuation/v1'and task.get('completed_review')is True and 'bridge'in task)
         or(task['schema_version']=='fleet-completed-instruction-continuation/v1'and ('checkpoint_lease'in task or 'completed_enqueue'in task or ('checkpoint_takeover'in task and 'interrupted_commit'in task))and 'semantic_context'in task)
         or(task['schema_version']=='fleet-ordinary-mechanical-dialogue/v1'and 'semantic_context'in task)
         or(task['schema_version']=='fleet-instruction-semantic-continuation/v1'and task.get('retained_context',{}).get('kind')in {'completed_disclosure','completed_pre_dispatch'})
         or(task['schema_version']=='fleet-disclosure-command-continuation/v1'and
            ((task.get('source_revision')is True and 'completed_takeover'in task)
             or('source_revision'not in task and 'completed_takeover'not in task
                and task.get('context',{}).get('function')=='completed_context')))):raise ValueError('unsupported completed context')
 if job['inputs'][0]!=p['root_plan']or job['driver']!=task['helper']or job['source']!=task['source']or job['workbook']!=task['workbook']:raise ValueError('completed task detached from launch')
 for f in Path('/proc').glob('[0-9]*/stat'):
  if int(f.parent.name)==os.getpid():continue
  try:
   fields=f.read_text().rsplit(')',1)[1].split();argv=[x.decode(errors='replace')for x in(f.parent/'cmdline').read_bytes().split(b'\0')if x]
  except(FileNotFoundError,PermissionError):continue
  if fields[0]=='Z':continue
  if int(f.parent.name)==process['pid']and fields[19]==str(process['start_ticks']):raise ValueError('completed author process remains active')
  if any(x in (p['job']['path'],task['helper']['path'])for x in argv):raise ValueError('completed author child remains active')
 outrow=record(Path(job['run'])/'outcome.json');outer=read(outrow);intent=read(outer['intent'])
 stopped=task['schema_version']=='fleet-completed-instruction-continuation/v1'and ('completed_enqueue'in task or ('checkpoint_takeover'in task and 'interrupted_commit'in task)) and outer['returncode']==1 and outer['failure_type']is None and outer['result']is None
 if intent['job']!=p['job']or intent['owner']!=p['new_owner']:raise ValueError('author identity changed')
 projection=None
 if stopped:
  home=Path(task['output']);commit_tail='checkpoint_takeover'in task and 'interrupted_commit'in task
  folder=Path(task['operations'])/'000'if commit_tail else home/'first-step'
  guardrow=record(folder/'outcome.json');guard=read(guardrow);resource=read(guard['resource']);command=read(resource['intent']);checkpoint=read(guard['result']);complete=read(checkpoint['checkpoint']);read(complete['intent']);read(command['arguments']['checkpoint']);read(command['arguments']['descriptor']);read(record(home/'intent.json'))
  if guard['status']!='COMPLETED_OPERATION'or resource['returncode']!=0 or resource['failure']is not None or checkpoint['status']!='PUBLICATION_READY'or checkpoint['next_step']is not None or checkpoint['publication_performed']is not False:raise ValueError('stopped wrapper lacks completed original READY')
  if (home/'result.json').exists():raise ValueError('stopped wrapper has a conflicting final result')
  if commit_tail:
   if [x.name for x in sorted(Path(task['operations']).iterdir())if x.is_dir()]!=['000']:raise ValueError('completed commit tail has extra dispatcher work')
   first=record(home/'first-step/outcome.json');first_value=read(read(first)['result'])
   if first_value['status']!='STEP_COMPLETE'or first_value['next_step']!='enqueue'or read(command['arguments']['checkpoint'])!=first_value:raise ValueError('enqueue lost completed commit predecessor')
   held=read(record(Path(task['operations'])/'hold.json'))
   if held!=dict(automatic_retry=False,error_type='AdmissionError',outcome=guardrow,reason='prior evidence inspection failed'):raise ValueError('completed tail stopped at another boundary')
  elif(home/'authoring-job.json').exists()or Path(task['operations']).exists():raise ValueError('stopped wrapper has later owning work')
  log=record(outer['log']['path'])
  if log!=outer['log']or not files[log['path']].decode().rstrip().endswith('AdmissionError: queued worker code changed'):raise ValueError('unknown stopped wrapper failure')
  original=read(task['original_job']);author=dict(original,outcome=first if commit_tail else guardrow,operations=task['operations'])
  if commit_tail and read(record(home/'authoring-job.json'))!=author:raise ValueError('completed tail actual author job differs')
  projection=dict(author=author,guard=guardrow,checkpoint=guard['result'],intent=record(home/'intent.json'),failure_log=log,permission=None if commit_tail and command['models']is False else dict(schema_version='fleet-completed-enqueue-permission/v1',intent=resource['intent'],prior=command['arguments']['checkpoint'],descriptor=command['arguments']['descriptor'],completion=checkpoint['checkpoint'],step_intent=complete['intent']))
  ready=dict(status='PUBLICATION_READY',publication_performed=False,continuation=p['root_plan'],job=task['original_job'],outcome=guardrow,checkpoint=guard['result'])
 else:
  if outer['returncode']!=0 or outer['failure_type']is not None:raise ValueError('author did not stop successfully')
  ready=read(outer['result'])
 if (not stopped and outer['result']['path']!=job['result'])or ready['status']!='PUBLICATION_READY'or ready['publication_performed']is not False or (ready.get('plan')if derivative or task['schema_version']=='fleet-ordinary-mechanical-dialogue/v1'else ready.get('continuation'))!=p['root_plan']:raise ValueError('actual completed READY required')
 if derivative:
  count=1 if task.get('continuation',{}).get('completed_outcomes') else 3
  expected=[record(Path(task['operations'])/('%03d'%i)/'outcome.json')for i in range(count)]
  if ready['operations']!=expected or ready['models_dispatched']!=0 or ready['automatic_retry']is not False:raise ValueError('actual derivative guard differs')
  for i,row in enumerate(expected):
   operation=read(row);value=read(operation['result'])
   if operation['status']!='COMPLETED_OPERATION'or (i==count-1 and operation['result']!=ready['checkpoint']):raise ValueError('derivative guard did not qualify')
   if i<count-1 and (value['status']!='STEP_COMPLETE'or value['next_step']!=('dialogue-commit','enqueue')[i]):raise ValueError('derivative phase order differs')
  ready=dict(ready,job=task['authoring_job'],outcome=ready['operations'][-1])
 author=read(ready['job']);config=read(author['config']);snapshot=read(config['code']);read(ready['outcome']);read(ready['checkpoint'])
 tr=record(Path(config['run_root'])/'trackers/workbooks'/(job['workbook']+'.json'));tracker=read(tr)
 if tracker['workbook_id']!=job['workbook']or tracker['execution_state']!='publication_queued'or tracker['terminal_verdict']!='PUBLICATION_READY'or tracker['bindings']['original_raw_source']!=job['source']:raise ValueError('queued source tracker differs')
 return dict(status='STOPPED_COMPLETED_AUTHOR_CUSTODY',projection=projection,ready=outer['result'],outcome=outrow,tracker=tr,config=config,original_code_root=job['cwd']if derivative else snapshot['root'],author=author,processes=[],files=[dict(original=dict(path=p,sha256=hashlib.sha256(raw).hexdigest()),data=base64.b64encode(raw).decode())for p,raw in sorted(files.items())])


def prepare(repository,launch_row,output,*,completed_reader=None):
 root=Path(repository).absolute();out=Path(output).absolute();launched=checked(launch_row);intent=checked(launched['intent']);p=checked(intent['plan']);job=checked(p['job']);task=checked(p['root_plan'])
 derivative=task['schema_version']=='fleet-dialogue-source-revision-adoption/v1'
 aggregate=task['schema_version']=='fleet-disclosure-aggregate-adoption/v1'
 completed_review=task['schema_version']=='fleet-completed-instruction-continuation/v1'and task.get('completed_review')is True and 'bridge'in task
 ordinary_disclosure=task['schema_version']=='fleet-disclosure-command-continuation/v1'and 'source_revision'not in task and 'completed_takeover'not in task and task.get('context',{}).get('function')=='completed_context'
 source_revision=completed_review or(task['schema_version']=='fleet-disclosure-command-continuation/v1'and ((task.get('source_revision')is True and 'completed_takeover'in task)or ordinary_disclosure))
 semantic=(task['schema_version']=='fleet-ordinary-mechanical-dialogue/v1'and 'semantic_context'in task)or(task['schema_version']=='fleet-instruction-semantic-continuation/v1'and task.get('retained_context',{}).get('kind')in {'completed_disclosure','completed_pre_dispatch'})or(task['schema_version']=='fleet-completed-instruction-continuation/v1'and ('checkpoint_lease'in task or 'completed_enqueue'in task or ('checkpoint_takeover'in task and 'interrupted_commit'in task))and 'semantic_context'in task)
 if aggregate or source_revision or semantic or derivative:
  if completed_reader is None or binding(completed_reader['path'])!=completed_reader:raise ValueError('explicit bound completed context reader required')
 elif completed_reader is not None or task['schema_version']!='fleet-instruction-unacquired-model-continuation/v1'or 'completed_tail'not in task:raise ValueError('explicit completed model tail required')
 process=launched['remote']
 if process['root_intent']!=launched['intent']or process['job']!=p['job']:raise ValueError('process launch authority differs')
 raw=(json.dumps(process,sort_keys=True,indent=2)+'\n').encode()
 import hashlib
 payload=dict(launch=p,job=job,task=task,process_value=process,process=dict(path=p['remote_receipt'],sha256=hashlib.sha256(raw).hexdigest()))
 out.mkdir(parents=True,mode=0o700);retain(out/'selection.json',dict(launch=launch_row,completed=p['root_plan'],helper=binding(__file__)))
 command=['/Users/gandharvmahajan/google-cloud-sdk/bin/gcloud','compute','ssh',p['host'],'--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet','--command',shlex.join([job['command'][0],'-I','-B','-c',inspect.getsource(native_custody)+'\nimport json,sys\nprint(json.dumps(native_custody(json.load(sys.stdin))))'])]
 r=subprocess.run(command,input=json.dumps(payload).encode(),capture_output=True,timeout=120);(out/'custody.stderr').write_bytes(r.stderr)
 if r.returncode:raise RuntimeError('completed native custody failed; inspect stderr')
 custody=json.loads(r.stdout);mapping=[];directory=out/'custody';directory.mkdir(mode=0o700)
 for i,row in enumerate(custody.pop('files')):
  raw=base64.b64decode(row['data'],validate=True)
  if hashlib.sha256(raw).hexdigest()!=row['original']['sha256']:raise ValueError('custody transfer changed')
  copy=directory/(f'{i:03d}-'+Path(row['original']['path']).name);copy.write_bytes(raw);copy.chmod(0o600);mapping.append(dict(original=row['original'],local=binding(copy)))
 if custody.get('projection')is not None:
  projected=custody['projection'];author=retain(out/'authoring-job.json',projected['author'])
  ready=retain(out/'ready.json',dict(schema_version='fleet-stopped-wrapper-ready-projection/v1',status='PUBLICATION_READY',workbook=task['workbook'],plan=task['fresh_plan'],job=author,config=projected['author']['config'],outcome=projected['guard'],checkpoint=projected['checkpoint'],continuation=p['root_plan'],intent=projected['intent'],publication_performed=False,automatic_retry=False,projection_path=str(out/'ready.json')))
  custody['ready']=ready;custody['completed_ready']=retain(out/'completed-ready.json',dict(schema_version='fleet-stopped-wrapper-completed-ready/v1',worker_outcome=custody['outcome'],job=p['job'],guard=projected['guard'],checkpoint=projected['checkpoint'],ready=ready,failure_log=projected['failure_log'],**({'enqueue_permission':projected['permission']}if projected['permission']is not None else{})))
 retain(out/'custody.json',dict(custody,mapping=mapping));control=root/'diagnostics/vm-fleet-20260912/control'
 manifest=build.build(out/'implementation-001',control/'0029-publication-003/collector.py');manifest=wait.build(out/'implementation-002',manifest,control/'0029-publication-003/collector.py');m=checked(manifest)
 base=checked(binding(control/'source-first/0010/publication-001/plan-base.json'));base={k:v for k,v in base.items()if k not in('fresh_authoring','history')}
 config=custody['config'];code_root=custody['original_code_root']if aggregate or source_revision or semantic or derivative else config.get('code_root',job['cwd'])
 # Original source config is independently checked by the completed model reader.
 policy=retain(out/'wait-policy.json',dict(schema_version='harbor-fleet-publication-wait/v1',workbook=job['workbook'],source_sha256=job['source']['sha256'],lease_tracker=custody['tracker'],maximum_wait_seconds=120,authorization=binding(root/'diagnostics/bulk-production/authorization-20260913.md')))
 if derivative:
  actual=next(x['local']for x in mapping if x['original']==custody['ready']);finished=checked(actual)
  branch=dict(source_revision=dict(reader=task['helper'],plan=p['root_plan'],outcome=finished['operations'][-1],completed_reader=completed_reader))
 elif semantic:
  branch=dict(semantic_continuation=dict(reader=task['helper'],plan=p['root_plan'],completed_reader=completed_reader),fresh_authoring=task['fresh_plan'])
  if task.get('retained_context',{}).get('kind')=='completed_pre_dispatch':
   rows=checked(p['offline_bindings'])['copies'];found=[r for r in rows if r['native']==task['tracker']]
   if len(found)!=1 or binding(found[0]['copy']['path'])!=found[0]['copy']or found[0]['copy']['sha256']!=task['tracker']['sha256']:raise ValueError('exact pre-renewal admission archive required')
   branch['semantic_continuation']['renewal_before']=found[0]['copy']
  if custody.get('completed_ready')is not None:
   wrapper=out/'stopped_tail_ready.py';__import__('shutil').copyfile(root/'scripts/fleet/stopped_tail_ready.py',wrapper)
   branch['semantic_continuation']['stopped_ready']=dict(reader=binding(wrapper),certificate=custody['completed_ready'])
 elif completed_review:
  branch=dict(disclosure_continuation=dict(reader=task['helper'],plan=p['root_plan'],completed_reader=completed_reader),fresh_authoring=task['previous'])
 elif source_revision:
  origin=checked(task['context']['plan'])
  if ordinary_disclosure:
   if origin['schema_version']!='fleet-completed-environment-continuation/v1'or origin['workbook']!=task['workbook']or origin['original_job']!=task['original_job']or origin['source']!=task['source']:raise ValueError('ordinary disclosure original environment differs')
   authority=task['context']['plan']
  else:
   if origin!=dict(workbook=task['workbook'],original_job=task['original_job'],fresh_plan=origin.get('fresh_plan')):raise ValueError('source revision original context differs')
   authority=origin['fresh_plan']
  branch=dict(disclosure_continuation=dict(reader=task['helper'],plan=p['root_plan'],completed_reader=completed_reader),fresh_authoring=authority)
 elif aggregate:
  review=checked(task['context']['plan']);preparation=checked(review['retained']['plan']);command=checked(preparation['loader']['plan']);origin=checked(command['context']['plan'])
  if origin['original_job']!=task['original_job']or origin['workbook']!=task['workbook']:raise ValueError('aggregate source context differs')
  branch=dict(disclosure_continuation=dict(reader=task['helper'],plan=p['root_plan'],completed_reader=completed_reader),fresh_authoring=origin['fresh_plan'])
 else:branch=dict(model_tail=dict(reader=task['helper'],plan=p['root_plan']))
 plan=retain(out/'plan.json',dict(base,**branch,workbook=job['workbook'],code_root=code_root,source_sha256=job['source']['sha256'],ready=custody['ready'],journal='/home/gmahajan_labelbox_com/synthetic-data-fleet/'+job['workbook']+'-publication-completed-001',publication_wait=policy))
 native=out/'native-ready-001';native.mkdir();text=completed_probe((control/'source-first/0010/publication-001/native-ready-base-001/probe.py').read_text(),m['publisher'],plan,aggregate=aggregate,source_revision=source_revision,semantic=semantic,derivative=derivative);(native/'probe.py').write_text(text)
 runner=(root/'diagnostics/bulk-production/0303-continuation/publication-001/native-ready-004/run.py').read_text();runner='\n'.join('PROBE='+repr(binding(native/'probe.py'))if l.startswith('PROBE=')else l for l in runner.splitlines())+'\n';(native/'run.py').write_text(runner)
 files=([str(out/f)for f in ('authoring-job.json','ready.json','completed-ready.json','stopped_tail_ready.py')]if custody.get('completed_ready')is not None else[])+[str(f)for d in(out/'implementation-001',out/'implementation-002')for f in d.iterdir()if f.is_file()]+([completed_reader['path']]if aggregate or source_revision or semantic or derivative else[])+[policy['path'],plan['path'],str(native/'probe.py'),str(native/'run.py')]
 if semantic and task.get('retained_context',{}).get('kind')=='completed_pre_dispatch':
  # Original pre-renewal bytes are a declared immutable publication input.
  files.insert(-2,branch['semantic_continuation']['renewal_before']['path'])
 retain(out/'native-files.json',files)
 return retain(out/'prepared.json',dict(plan=plan,implementation=manifest,custody=binding(out/'custody.json'),models_dispatched=0,publication_mutations=0))


def prepare_root(repository,prepared_row,publication_plan,guard_result,output,controller):
 """Bind stopped continuation custody to the existing publication controller."""
 from scripts.fleet import publication_launch as L
 root=Path(repository).absolute();prepared=checked(prepared_row);home=Path(prepared_row['path']).parent
 selected=checked(binding(home/'selection.json'));launched=checked(selected['launch']);intent=checked(launched['intent']);launch=checked(intent['plan']);job=checked(launch['job'])
 if launch['root_plan']!=selected['completed']or prepared['plan']!=publication_plan:raise ValueError('completed publication selection changed')
 observed=json.loads(checked(guard_result)['stdout']);proof=json.loads(observed['log'])
 if observed['returncode']!=0 or observed['failure']is not None or proof['plan']!=publication_plan:raise ValueError('current original full proof required')
 custody=checked(prepared['custody']);mapping={x['original']['path']:x for x in custody['mapping']};process=mapping[launch['remote_receipt']]
 out=Path(output).absolute();out.mkdir();pr=retain(out/'proof.json',proof);gr=retain(out/'guard.json',observed)
 control=root/'diagnostics/vm-fleet-20260912/control';implementation=Path(controller).parent/'implementation/scripts/fleet'
 p=dict(schema_version=L.SCHEMA,host=launch['host'],plan=publication_plan,publisher=proof['publisher'],implementation=prepared['implementation'],proof=pr,guard=gr,worker_outcome=custody['outcome'],process=process['original'],process_copy=process['local'],expected_fence=launch['expected_fence']+1,expected_owner=launch['new_owner'],coordinator_receipt=launch['coordinator_receipt'],owner=launch['host']+':publication:'+job['workbook']+':completed001',python=job['command'][0],capacity_policy=binding(implementation/'capacity_policy.py'),admission_lock=binding(control/'author-capacity-001/root-helpers-001/admission_lock.py'))
 p['version_custody']=retain(out/'version-custody.json',dict(schema_version='fleet-publication-version-custody/v1',files=custody['mapping']))
 if custody.get('completed_ready')is not None:p['completed_ready']=custody['completed_ready']
 profile=retain(out/'profile.json',p);L.load(profile)
 cmd=[str(root/'.venv/bin/python'),'-I','-B',str(controller),'--profile',profile['path'],'--sha256',profile['sha256']]
 retain(out/'commands.json',dict(preflight=cmd,execute=[*cmd,'--execute','--journal',str(out/'launch-001')]))
 from scripts.fleet.publication_staging import prepare as prepare_staging
 prepare_staging(profile,out);return profile

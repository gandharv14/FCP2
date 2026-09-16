"""Exact successor after a stopped collector failed its disk gate before claim."""
from pathlib import Path
import json,os,shlex,subprocess,time
from scripts.fleet.migration import binding,checked,safe

def require(ok,message):
 if not ok:raise ValueError(message)

def collector_source(original):
 """Only a collection-specific plan and disk gate change; all other bytes stay."""
 replacements=[
 ("require(plan.get('schema_version') == ('harbor-fleet-remote-publication/v1' if action == 'remote' else 'harbor-fleet-collection-plan/v1'), 'publication action/plan schema differs')","require(plan.get('schema_version') in ({'harbor-fleet-remote-publication/v1'} if action == 'remote' else {'harbor-fleet-collection-plan/v1','harbor-fleet-collection-plan/v2'}), 'publication action/plan schema differs')"),
 ("    require(shutil.disk_usage(safe(plan['code_root'])).free >= 12*1024**3, 'publication disk reserve unavailable before claim')","    disk = None; disk_helper = None\n    if action == 'collect' and plan['schema_version'] == 'harbor-fleet-collection-plan/v2':\n        disk_helper = load('selected_collection_disk_policy', checked(plan['disk_policy'])['helper'])\n        disk = disk_helper.requirements(plan); disk_helper.check(disk)\n    else: require(shutil.disk_usage(safe(plan['code_root'])).free >= 12*1024**3, 'publication disk reserve unavailable before claim')"),
 ("                    require(shutil.disk_usage(plan['code_root']).free >= 8*1024**3, 'publication disk reserve exhausted')","                    if disk is not None: disk_helper.check(disk, during=True)\n                    else: require(shutil.disk_usage(plan['code_root']).free >= 8*1024**3, 'publication disk reserve exhausted')"),
 ('require(plan["schema_version"] == "harbor-fleet-collection-plan/v1", "collection plan required")','require(plan["schema_version"] in {"harbor-fleet-collection-plan/v1","harbor-fleet-collection-plan/v2"}, "collection plan required")')]
 for before,after in replacements:
  require(original.count(before)==1,'unsupported original collector transformation');original=original.replace(before,after)
 return original

def successor(p,s,old_plan):
 d=checked(p['collector_derivation']);require(set(d)=={'schema_version','original','collector','disk_helper'}and d['schema_version']=='harbor-collection-only-successor/v1'and d['original']==s['collector']and d['collector']==p['collector'],'closed collector derivation required')
 require(binding(d['original']['path'])==d['original']and binding(d['collector']['path'])==d['collector'],'collector source changed')
 require(Path(d['collector']['path']).read_text()==collector_source(Path(d['original']['path']).read_text()),'collector changed beyond collection disk policy')
 plan=checked(p['collection_plan']);require(set(plan)==set(old_plan)|{'disk_policy'}and plan['schema_version']=='harbor-fleet-collection-plan/v2'and {k:v for k,v in plan.items()if k not in {'schema_version','disk_policy'}}=={k:v for k,v in old_plan.items()if k!='schema_version'},'collection successor changed archive, output or original journal')
 policy=checked(plan['disk_policy']);require(policy['helper']==d['disk_helper']and binding(d['disk_helper']['path'])==d['disk_helper'],'selected disk policy implementation differs')
 return plan

def local_stopped(needles):
 r=subprocess.run(['ps','-axo','pid=,command='],capture_output=True,text=True,check=True)
 for line in r.stdout.splitlines():
  try:pid,command=line.strip().split(None,1);args=shlex.split(command)
  except ValueError:continue
  if int(pid)==os.getpid():continue
  require(not any(a==n or a.startswith(n+'/')for n in needles for a in args),'prior local collection process still running')
 return dict(status='PRIOR_LOCAL_COLLECTION_STOPPED',observed_at=time.time())

def preclaim(p,s,plan,*,check_process=True):
 c=checked(p['preclaim_recovery']);require(set(c)=={'schema_version','profile','journal','files'}and c['schema_version']=='harbor-collection-preclaim-disk-failure/v1','closed preclaim recovery required')
 old=checked(c['profile']);require(old['schema_version']=='harbor-fleet-collection-launch/v1'and all(old[k]==p[k]for k in old if k!='schema_version'),'prior collection selection changed')
 root=safe(c['journal']);names={'intent.json','ready.json','command.json','response.log','uncertain.json'}
 require(set(c['files'])==names and {x.name for x in root.iterdir()}==names,'original failed root journal changed or advanced')
 for name,row in c['files'].items():require(row==binding(root/name),'failed collection evidence changed')
 intent=checked(c['files']['intent.json']);ready=checked(c['files']['ready.json']);uncertain=checked(c['files']['uncertain.json']);command=checked(c['files']['command.json'])
 receipt=checked(p['coordinator_receipt'])
 expected=[p['python'],'-I','-B',s['collector']['path'],'collect','--plan',s['plan']['path'],'--plan-sha256',s['plan']['sha256'],'--url',receipt['url'],'--token-file',str(Path(receipt['private_directory'])/'worker.token'),'--owner',p['owner'],'--version',s['remote_version']]
 require(command==expected and intent['profile']==c['profile']and intent['automatic_retry']is False and binding(intent['driver']['path'])==intent['driver'],'original collection command/intent detached')
 require(uncertain==dict(intent=c['files']['intent.json'],status='RECONCILIATION_REQUIRED',failure_type='ValueError',automatic_retry=False),'prior root failure differs')
 log=Path(c['files']['response.log']['path']).read_text();require(log.startswith('Traceback (most recent call last):\n')and log.endswith('ValueError: publication disk reserve unavailable before claim\n')and s['collector']['path']in log,'actual original pre-claim disk failure required')
 before=intent['before'];require(before['state']=='hold'and before['fence']==p['expected_fence']and before['owner']==p['expected_owner']and before['source_sha256']==plan['source_sha256']and before['version']==s['remote_version'],'original publication custody differs')
 require(ready['state']=='ready'and ready['fence']==before['fence']and ready['owner']is None and ready['claim_id']is None and ready['expires']is None and ready['version']==before['version']and ready['source_sha256']==before['source_sha256']and ready['workbook']==plan['workbook']and ready['reason']==json.dumps(c['files']['intent.json'],sort_keys=True)and ready['checkpoint']==json.dumps({'qualified_remote_result':s['remote_result']},sort_keys=True,separators=(',',':')),'original collection ready transition differs')
 require(not Path(plan['journal']).exists()and not(Path(plan['output_root'])/(plan['workbook']+'-outputs')).exists(),'collector crossed first-write boundary; no preclaim recovery')
 if check_process:local_stopped({s['collector']['path'],s['plan']['path'],c['profile']['path'],str(root),plan['journal']})
 return ready

def ready_central(status,p,s,plan,capacity,*,now=None):
 ready=preclaim(p,s,plan);capacity.validate_occupancy(status);now=time.time()if now is None else now
 require(type(status.get('at'))in(int,float)and abs(now-status['at'])<=60,'fresh ready custody observation required')
 rows=[w for w in status['workbooks']if w['workbook']==plan['workbook']];require(len(rows)==1 and rows[0]==ready,'ready collection custody changed; do not reconstruct a hold')
 for op in status['operations']:
  if op['state']in {'active','hold'}:
   require(op['kind']=='model'and op['workbook']!=plan['workbook'],'publication or target model barrier')
   owners=[w for w in status['workbooks']if w['workbook']==op['workbook']]
   require(len(owners)==1 and owners[0]['state']==op['state']and owners[0]['owner']==op['owner']and owners[0]['fence']==op['fence'],'unknown operation owner')
   if op['state']=='active':require(owners[0]['expires']>now,'expired active model owner')
 ops=[o for o in status['operations']if o['kind']=='publication'and o['workbook']==plan['workbook']and o['fence']==p['expected_fence']]
 require(len(ops)==1 and ops[0]['state']=='confirmed'and ops[0]['owner']==p['expected_owner']and json.loads(ops[0]['evidence'])==s['remote_result'],'original verified publication operation differs')
 return rows[0]

def prepare(profile,output,*,collector,disk_helper,authorization,failed_journal=None,small_archive=False):
 """Select the measured collector using new data, preserving original staging."""
 from scripts.fleet.pool import persist
 from scripts.fleet import collection_disk_policy as D
 require(type(small_archive)is bool,'explicit bounded collection policy selection required')
 old=checked(profile);require(old['schema_version']=='harbor-fleet-collection-launch/v1','original collection selection required')
 staged=checked(old['staged']);plan=checked(staged['plan'])
 require(plan['schema_version']=='harbor-fleet-collection-plan/v1'and staged['status']=='STAGED_VERIFIED_NOT_COLLECTED','original verified collection staging required')
 require(binding(collector['path'])==collector and Path(collector['path']).read_text()==collector_source(Path(staged['collector']['path']).read_text()),'selected collector is not the closed original derivative')
 require(binding(disk_helper['path'])==disk_helper and binding(authorization['path'])==authorization and authorization['sha256']==D.AUTH_SHA,'selected implementation or authorization changed')
 require(not Path(plan['journal']).exists()and not(Path(plan['output_root'])/(plan['workbook']+'-outputs')).exists(),'collection already consumed')
 out=safe(Path(output).absolute());require(not out.exists(),'collection selection already exists');out.mkdir(parents=True,mode=0o700);now=time.time_ns()
 policy=persist(out/'disk-policy.json',dict(schema_version=D.SMALL_SCHEMA if small_archive else D.SCHEMA,helper=disk_helper,authorization=authorization,workbook=plan['workbook'],source_sha256=plan['source_sha256'],manifest=plan['manifest'],archive=binding(plan['archive']),reserve_bytes=768*1024**2 if small_archive else 2*1024**3,allocation_factor=3,metadata_bytes=64*1024**2,created_at_ns=now,expires_at_ns=now+(3600 if small_archive else 86400)*10**9),immutable=True)
 selected_plan=persist(out/'collection-plan.json',{**plan,'schema_version':'harbor-fleet-collection-plan/v2','disk_policy':policy},immutable=True)
 derivation=persist(out/'collector-derivation.json',dict(schema_version='harbor-collection-only-successor/v1',original=staged['collector'],collector=collector,disk_helper=disk_helper),immutable=True)
 result={**old,'schema_version':'harbor-fleet-collection-launch/v2','collector':collector,'collection_plan':selected_plan,'collector_derivation':derivation}
 if failed_journal is not None:
  prior=safe(Path(failed_journal).absolute())
  result['preclaim_recovery']=persist(out/'preclaim-recovery.json',dict(schema_version='harbor-collection-preclaim-disk-failure/v1',profile=profile,journal=str(prior),files={p.name:binding(p)for p in prior.iterdir()}),immutable=True)
 successor(result,staged,plan)
 if failed_journal is not None:preclaim(result,staged,checked(selected_plan))
 return persist(out/'profile.json',result,immutable=True)

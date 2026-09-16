"""Once-only local collection of an actual independently verified remote result."""
from pathlib import Path
import argparse,json,subprocess,sys,time
if __package__ in(None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from scripts.fleet import publication_launch as P,qualified_collection as Q
from scripts.fleet.migration import binding,checked,safe
from scripts.fleet.pool import lock,persist
from scripts.fleet.coordinator import Client
SCHEMA='harbor-fleet-collection-launch/v1'
SCHEMA2='harbor-fleet-collection-launch/v2'
FIELDS={'schema_version','staged','coordinator_receipt','capacity_policy','expected_fence','expected_owner','owner','host','publisher','python','native_python'}
def require(ok,message):
 if not ok:raise ValueError(message)
def load(row):
 p=checked(row);extra={'collector','collection_plan','collector_derivation'}if p.get('schema_version')==SCHEMA2 else set()
 require(set(p)==FIELDS|extra|({'preclaim_recovery'}if 'preclaim_recovery'in p else set())and p['schema_version']in {SCHEMA,SCHEMA2}and ('preclaim_recovery'not in p or bool(extra))and p['host']in P.HOSTS,'closed collection selection required')
 s=checked(p['staged']);plan=checked(s['plan']);remote=checked(s['remote_plan'])
 require(s['status']=='STAGED_VERIFIED_NOT_COLLECTED' and s['claims_changed']==s['models_dispatched']==0 and s['automatic_retry']is False,'verified staged package required')
 require(plan['schema_version']=='harbor-fleet-collection-plan/v1' and plan['workbook']==remote['workbook'] and plan['source_sha256']==remote['source_sha256'] and s['remote_version']==Q.remote_version(remote,s.get('version_custody')),'local collection detached from remote source/version')
 if 'verification_continuation'in s:Q.verification_continuation(s['remote_plan'],s['remote_result'],s['verification_continuation'])
 else:require(s['remote_result']['path']==remote['journal']+'/result.json','collection result differs')
 require(not Path(plan['journal']).exists() and not(Path(plan['output_root'])/(plan['workbook']+'-outputs')).exists(),'collection output consumed')
 Q.verify_archive(Path(plan['archive']),checked(plan['manifest']),plan['workbook'],s['collector'])
 require(binding(p['publisher']['path'])==p['publisher'],'selected stopped publisher changed')
 require(p['owner']!=p['expected_owner'] and p['owner'].startswith('local:collection:') and type(p['expected_fence'])is int and p['expected_fence']>0,'new local collector owner required')
 if extra:
  from scripts.fleet import collection_recovery as R
  plan=R.successor(p,s,plan)
  helper=P.module('selected_collection_disk',checked(plan['disk_policy'])['helper']);measurement=helper.requirements(plan);helper.check(measurement)
  if 'preclaim_recovery'in p:R.preclaim(p,s,plan)
 return p,s,plan,remote

def central(status,p,s,plan,remote,capacity,now=None):
 if 'preclaim_recovery'in p:
  from scripts.fleet.collection_recovery import ready_central
  return ready_central(status,p,s,plan,capacity,now=now)
 proxy=dict(expected_fence=p['expected_fence'],expected_owner=p['expected_owner'],worker_outcome=s['remote_result'])
 if s.get('version_custody')is not None:proxy['version_custody']=s['version_custody']
 retained=None
 if 'verification_continuation'in s:
  certificate,verification,abandoned=Q.verification_continuation(s['remote_plan'],s['remote_result'],s['verification_continuation'])
  settled=checked(certificate['reconciliation'])
  prior=[x for x in settled['after']['workbooks'] if x['workbook']==plan['workbook']]
  require(len(prior)==1,'unique retained publication custody required');retained=prior[0]
 w=P.central(status,proxy,remote,capacity,now=now,**({'held_reason':retained['reason']}if retained is not None else {}))
 ops=[o for o in status['operations']if o['kind']=='publication'and o['workbook']==plan['workbook']and o['fence']==p['expected_fence']]
 if 'verification_continuation'in s:
  require(len(ops)==1 and ops[0]==abandoned and ops[0]['owner']==p['expected_owner']
   and retained==w,'independent completion changed original abandoned operation or custody')
 else:require(len(ops)==1 and ops[0]['state']=='confirmed' and ops[0]['owner']==p['expected_owner'] and json.loads(ops[0]['evidence'])==s['remote_result'],'exact verified remote publication confirmation required')
 return w

def native(payload):
 from pathlib import Path
 import json,hashlib,os,time
 r=payload['result'];path=Path(r['path'])
 if hashlib.sha256(path.read_bytes()).hexdigest()!=r['sha256']:raise ValueError('remote result changed')
 v=json.loads(path.read_bytes())
 if v['status']!='REMOTE_VERIFIED'or v['plan']!=payload['plan']or v['model_calls']!=0:raise ValueError('remote publication not verified')
 for key in('publication','verification'):
  row=v[key]
  if hashlib.sha256(Path(row['path']).read_bytes()).hexdigest()!=row['sha256']:raise ValueError('remote proof changed')
 if 'verification_continuation'in payload:
  profile=payload['verification_continuation']
  if v.get('verification_continuation')!=profile:raise ValueError('verification profile changed')
  if hashlib.sha256(Path(profile['path']).read_bytes()).hexdigest()!=profile['sha256']:raise ValueError('verification profile bytes changed')
  selected=json.loads(Path(profile['path']).read_bytes())
  if selected['plan']!=payload['plan']or selected['publication']!=v['publication']:raise ValueError('original publication changed')
  for key in('tracker','publication','resource'):
   item=selected[key]
   if hashlib.sha256(Path(item['path']).read_bytes()).hexdigest()!=item['sha256']:raise ValueError('released promotion evidence changed')
  tracker=json.loads(Path(selected['tracker']['path']).read_bytes())
  if tracker['execution_state']!='PROMOTED'or any(x is not None for x in tracker['leases'].values()):raise ValueError('released publication required')
 needles=[str(path.parent),payload['publisher']['path']]
 for d in Path('/proc').iterdir():
  if not d.name.isdigit()or int(d.name)==os.getpid():continue
  try:
   if(d/'stat').read_text().rsplit(')',1)[1].split()[0]=='Z':continue
   args=[x.decode(errors='replace')for x in(d/'cmdline').read_bytes().split(b'\0')if x]
   if any(any(a==n or a.startswith(n+'/')for n in needles)for a in args):raise ValueError('remote publication still running')
  except(FileNotFoundError,ProcessLookupError):continue
 return dict(status='VERIFIED_PUBLICATION_STOPPED',result=r,at=time.time())

def launch(row,journal,execute=False):
 import inspect
 p,s,plan,remote=load(row);capacity=P.module('collection_capacity',p['capacity_policy']);receipt=checked(p['coordinator_receipt']);client=Client(receipt['url'],Path(receipt['private_directory'])/'admin.token')
 intent=None;out=None
 try:
  with lock(Path(p['coordinator_receipt']['path']).parent/'job-launch.lock',blocking=False):
   before=central(client.call('status'),p,s,plan,remote,capacity)
   payload=dict(result=s['remote_result'],plan=s['remote_plan'],publisher=p['publisher'])
   if 'verification_continuation'in s:payload['verification_continuation']=checked(s['verification_continuation'])['profile']
   probe=P.ssh({**p,'python':p['native_python']},inspect.getsource(native)+'\nprint(__import__("json").dumps(native('+repr(payload)+')))')
   if not execute:return dict(status='COLLECTION_VALIDATED_NOT_STARTED',profile=row,native=probe,claims_changed=0)
   out=safe(Path(journal).absolute());require(not out.exists(),'root collection journal consumed; reconcile without replay');out.mkdir(parents=True,mode=0o700)
   intent=persist(out/'intent.json',dict(profile=row,driver=binding(Path(__file__).resolve()),before=before,native=probe,automatic_retry=False),immutable=True)
   central(client.call('status'),p,s,plan,remote,capacity)
   ready=before if 'preclaim_recovery'in p else client.call('reconcile',workbook=plan['workbook'],disposition='ready',evidence=json.dumps(intent,sort_keys=True),checkpoint={'qualified_remote_result':s['remote_result']})
   require(ready['state']=='ready' and ready['fence']==p['expected_fence'],'unexpected collection ready response');persist(out/'ready.json',ready,immutable=True)
   collector=p.get('collector',s['collector']);selected_plan=p.get('collection_plan',s['plan'])
   command=[p['python'],'-I','-B',collector['path'],'collect','--plan',selected_plan['path'],'--plan-sha256',selected_plan['sha256'],'--url',receipt['url'],'--token-file',str(Path(receipt['private_directory'])/'worker.token'),'--owner',p['owner'],'--version',s['remote_version']]
   persist(out/'command.json',command,immutable=True)
  with(out/'response.log').open('xb')as log:r=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT)
  require(r.returncode==0,'collection response uncertain; reconcile')
  result=binding(Path(plan['journal'])/'collection.json');v=checked(result)
  require(v['status']=='COLLECTED'and v['workbook']==plan['workbook']and v['local_byte_integrity_verified']is True,'verified collection receipt missing')
  collector=P.module('verified_final_collector',p.get('collector',s['collector']));require(collector.files(Path(v['output']))==v['files'],'local collected bytes changed')
  status=client.call('status');capacity.validate_occupancy(status);rows=[w for w in status['workbooks']if w['workbook']==plan['workbook']]
  require(len(rows)==1 and rows[0]['state']=='complete'and rows[0]['fence']==p['expected_fence']+1 and rows[0]['owner']==p['owner']and rows[0]['version']==s['remote_version']and rows[0]['source_sha256']==plan['source_sha256'],'central completed collection differs')
  return persist(out/'completed.json',dict(status='COLLECTED',intent=intent,collection=result,coordinator=status,automatic_retry=False),immutable=True)
 except BaseException as exc:
  if intent is not None:persist(out/'uncertain.json',dict(intent=intent,status='RECONCILIATION_REQUIRED',failure_type=type(exc).__name__,automatic_retry=False),immutable=True)
  raise

def main():
 a=argparse.ArgumentParser();a.add_argument('--profile',required=True);a.add_argument('--sha256',required=True);a.add_argument('--execute',action='store_true');a.add_argument('--journal');x=a.parse_args()
 if x.execute and not x.journal:a.error('--journal required for once execution')
 print(json.dumps(launch(dict(path=str(Path(x.profile).absolute()),sha256=x.sha256),x.journal,x.execute)))
if __name__=='__main__':main()

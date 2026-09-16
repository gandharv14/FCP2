"""Once-only root custody for the generic queued publication lease primitive."""
from pathlib import Path
import argparse,hashlib,importlib.util,json,os,shlex,subprocess,sys,time
SCHEMA='fleet-root-queued-renewal/v1'
FIELDS={'schema_version','helper','runtime','coordinator','capacity','plan','runner','host','python','guard','guardian','expected_fence','expected_owner','source_sha256','version','outcome'}
def require(ok,msg):
 if not ok:raise ValueError(msg)
def binding(path):
 p=Path(path);require(p.is_absolute() and not any(q.is_symlink()for q in(p,*p.parents)),'unsafe root input');return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
def read(row):require(binding(row['path'])==row,'root input changed');return json.loads(Path(row['path']).read_bytes())
def load(row):
 p=read(row);require(set(p)-{'publication_recovery','version_custody'}==FIELDS and p['schema_version']==SCHEMA and p['helper']==binding(__file__),'closed root queued renewal required');m=read(p['runtime']);root=Path(m['root'])
 require({str(x)for x in root.rglob('*')if x.is_file()}=={x['path']for x in m['files']},'frozen runtime inventory changed')
 for x in m['files']:require(binding(x['path'])==x,'frozen runtime changed')
 require(not any(n=='scripts'or n.startswith('scripts.')for n in sys.modules),'isolated root imports required');sys.path.insert(0,str(root))
 from scripts.fleet import publication_launch as L
 plan=read(p['plan']);require(plan['implementation']==p['runner'] and binding(p['runner']['path'])==p['runner'],'selected renewal runner changed')
 pub=read(plan['publication_plan']);validate_version(p,pub,L)
 require(p['host']in L.HOSTS and p['expected_fence']>0 and p['expected_owner'],'selected stopped owner required')
 if 'publication_recovery'in p:
  selected,publication,_=L.load(p['publication_recovery'])
  require('preowning_recovery'in selected and 'owning_recovery'not in selected
   and selected['plan']==plan['publication_plan'] and publication==pub
   and selected['host']==p['host'] and selected['expected_fence']==p['expected_fence']
   and selected['expected_owner']==p['expected_owner'] and selected['worker_outcome']==p['outcome']
   and selected['publisher']==plan['publisher'] and selected['coordinator_receipt']==p['coordinator'],
   'queued recovery detached from settled publication')
 return p,plan,pub,L

def validate_version(p,pub,L):
 copies={}
 if 'version_custody'in p:
  c=read(p['version_custody']);require(set(c)=={'schema_version','files'} and c['schema_version']=='fleet-publication-version-custody/v1','closed version custody required')
  for item in c['files']:
   require(set(item)=={'original','local'} and item['original']['sha256']==item['local']['sha256'] and item['original']['path']not in copies,'version custody differs')
   require(binding(item['local']['path'])==item['local'],'version custody bytes changed');copies[item['original']['path']]=item
 def version_read(row):
  if row['path']not in copies:return read(row)
  item=copies[row['path']];require(item['original']==row,'version custody native binding changed');return read(item['local'])
 require(pub['source_sha256']==p['source_sha256'] and L.publication_version(pub,version_read if copies else read)==p['version'],'queued source/version differs')

def central(status,p,wb,capacity):
 if 'publication_recovery'in p:
  from scripts.fleet import publication_launch as L
  selected,publication,_=L.load(p['publication_recovery'])
  require(publication['workbook']==wb and selected['expected_owner']==p['expected_owner']
   and selected['expected_fence']==p['expected_fence'] and selected['worker_outcome']==p['outcome'],
   'queued settled custody differs')
  return L.central(status,selected,publication,capacity)
 capacity.validate_occupancy(status);require(abs(time.time()-status['at'])<60,'stale central state');rows=[x for x in status['workbooks']if x['workbook']==wb];require(len(rows)==1,'unique stopped workbook required');w=rows[0]
 require(w['state']=='hold' and w['fence']==p['expected_fence'] and w['owner']==p['expected_owner'] and w['source_sha256']==p['source_sha256'] and w['version']==p['version'] and json.loads(w['reason'])==p['outcome'],'queued stopped custody differs')
 require(not any(o['workbook']==wb and o['state']in('active','hold')for o in status['operations']),'target operation unresolved')
 return w

def native_code(p,renew,directory):
 # Original guarded process owns all descendants; the selected native runner
 # revalidates source, actual READY, stopped identities and Q immediately at CAS.
 return """from pathlib import Path
import hashlib,importlib.util,json,os,sys,time
p=__P__;out=Path(__OUT__);out.mkdir();rows=[p['plan'],p['runner'],p['guard'],p['guardian']]
assert all(hashlib.sha256(Path(r['path']).read_bytes()).hexdigest()==r['sha256']for r in rows)
spec=importlib.util.spec_from_file_location('selected_queued_guard',p['guard']['path']);guard=importlib.util.module_from_spec(spec);sys.modules[spec.name]=guard;spec.loader.exec_module(guard)
_run_in_process_group=guard._run_in_process_group
command=[p['python'],'-I','-B',p['runner']['path'],'--plan',p['plan']['path'],'--sha256',p['plan']['sha256']]+(['--renew']if __RENEW__ else [])
metrics={};rc=None;failure=None;start=time.monotonic()
try:
 with(out/'run.log').open('xb')as log:rc=_run_in_process_group(command,stdout=log,timeout_seconds=3300,grace_seconds=3,memory_limit_bytes=8589934592,metrics=metrics,cwd=out,env={**os.environ,'PATH':str(Path(p['python']).parent)+':'+os.environ.get('PATH',''),'HARBOR_FLEET_URL':'http://127.0.0.1:8765','PYTHONDONTWRITEBYTECODE':'1'})
except BaseException as e:failure=type(e).__name__
result=dict(returncode=rc,failure=failure,seconds=3300,memory_bytes=8589934592,metrics=metrics,elapsed_seconds=time.monotonic()-start,models_dispatched=0,automatic_retry=False)
(out/'resource.json').write_text(json.dumps(result,sort_keys=True));print(json.dumps(result));sys.exit(0 if rc==0 and failure is None else 1)
""".replace('__P__',repr(p)).replace('__OUT__',repr(directory)).replace('__RENEW__',repr(renew))

def launch(row,journal,*,execute=False):
 p,plan,pub,L=load(row);cap=L.module('queued_capacity',p['capacity']);r=read(p['coordinator']);client=L.Client(r['url'],Path(r['private_directory'])/'admin.token');out=Path(journal).absolute();require(not out.exists(),'root attempt retained; no replay')
 native_dir='/home/gmahajan_labelbox_com/synthetic-data-fleet/'+pub['workbook']+'-queued-'+out.name
 command=['gcloud','compute','ssh',p['host'],'--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet','--command',shlex.join([p['python'],'-I','-B','-c',native_code(p,execute,native_dir)])]
 with L.lock(Path(p['coordinator']['path']).parent/'job-launch.lock',blocking=False):
  before=central(client.call('status'),p,pub['workbook'],cap);out.mkdir(parents=True,mode=0o700)
  intent=L.persist(out/'intent.json',dict(profile=row,central=before,command=command,renewal_requested=execute,automatic_retry=False),immutable=True)
  central(client.call('status'),p,pub['workbook'],cap)
  with(out/'response.log').open('xb')as log:q=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT)
  if q.returncode!=0:
   L.persist(out/'uncertain.json',dict(intent=intent,returncode=q.returncode,automatic_retry=False),immutable=True);raise ValueError('native renewal boundary requires reconciliation')
  return L.persist(out/'complete.json',dict(status='RESPONSE_RECEIVED_REQUIRES_VERIFIED_CUSTODY',intent=intent,native_directory=native_dir,renewal_requested=execute,automatic_retry=False),immutable=True)
if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('--profile',required=True);a.add_argument('--sha256',required=True);a.add_argument('--journal',required=True);a.add_argument('--execute',action='store_true');x=a.parse_args();print(json.dumps(launch(dict(path=str(Path(x.profile).absolute()),sha256=x.sha256),x.journal,execute=x.execute)))

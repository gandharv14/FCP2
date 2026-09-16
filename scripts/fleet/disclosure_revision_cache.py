"""Retain a consumed source-revision permit after a sealed cache collision.

Only a byte-identical copy of the original producer history is moved aside. The
original permit, budget, candidate, failed step, and model history stay intact.
"""
from pathlib import Path
from contextlib import contextmanager
import hashlib,json,os,shutil

SCHEMA='fleet-disclosure-revision-cache/v1'

def require(ok,message):
 if not ok:raise ValueError(message)
def binding(path):
 p=Path(path);require(p.is_absolute()and p.is_file()and not any(x.is_symlink()for x in(p,*p.parents)),'canonical cache evidence required')
 return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
def read(row):
 require(set(row)=={'path','sha256'}and binding(row['path'])==row,'cache evidence changed')
 return json.loads(Path(row['path']).read_bytes())
def tree(path):
 p=Path(path);require(p.is_absolute()and p.is_dir()and not any(x.is_symlink()for x in(p,*p.parents)),'canonical cache directory required')
 out={}
 for f in sorted(p.rglob('*')):
  require(not f.is_symlink()and(f.is_file()or f.is_dir()),'unsupported cache entry')
  if f.is_file():out[str(f.relative_to(p))]=binding(f)['sha256']
 return out

def selection(p):
 a=read(p['source_cache_resume']);require(set(a)=={'schema_version','helper','plan','checkpoint','outcome','worker_outcome','step_intent','exception','producer_tree','stage_tree'}and a['schema_version']==SCHEMA,'closed cache continuation proof required')
 require(a['helper']==binding(__file__),'selected cache reader differs')
 old=read(a['plan']);changed={'helper','owner','fence','output'}
 require(old.get('source_revision')is True and 'source_cache_resume'not in old
  and {k:v for k,v in p.items()if k not in changed|{'source_cache_resume'}}=={k:v for k,v in old.items()if k not in changed}
  and p['owner']!=old['owner']and p['fence']==old['fence']+1 and p['output']!=old['output'], 'cache continuation changed source, code, permit, or allowance')
 return a,old

def cache_state(p,a,producer,original,*,continued):
 expected=a['producer_tree'];build={k.removeprefix('disclosure-build/'):v for k,v in expected.items()if k.startswith('disclosure-build/')}
 require(build and 'disclosure/sentences.json'in expected and tree(original)==expected,'producer is not the exact copied original review history')
 archive=Path(p['output'])/'retained-producer-copy';receipt=Path(p['output'])/'cache-move.json'
 if continued:
  moved=read(binding(receipt));require(moved==dict(schema_version=SCHEMA,proof=p['source_cache_resume'],source=str(producer),archive=str(archive),tree=expected,semantic_budget_reset=False)
   and tree(archive)==expected,'authenticated retained producer copy is missing or changed')
  require(tree(producer/'disclosure-build')==build,'retained disclosure build changed')
 else:
  require(not archive.exists()and not receipt.exists()and tree(producer)==expected,'producer changed before cache recovery')
 return archive,expected

def validate(p,env,worker,K,*,continued):
 a,old=selection(p);C,I=env[:2];home=Path(old['output']);cp=read(a['checkpoint'])
 require(a['checkpoint']==binding(home/'permit/result.json')and cp['status']=='RETRY_READY'
  and cp['next_step']=='disclosure-review'and cp['checkpoint']==binding(home/'permit/permit.json'),'original consumed review permit required')
 _,s,l=I._prior(a['checkpoint'],cp['descriptor']);w=I.worker_decode(read(cp['descriptor'])['worker'])
 require(w.root==worker.root and w.run==worker.run and w.disclosure_revision==cp['checkpoint'],'retained revision worker differs')
 permit=read(w.disclosure_revision);K.validate(w,read(binding(p['tracker']['path'])))
 out=read(a['outcome']);g=read(out['resource']);intent=read(g['intent'])
 require(a['outcome']==binding(home/'first-step/outcome.json')and out['status']=='RECONCILIATION_REQUIRED'
  and g['returncode']==1 and g['failure']is None and intent['code']==binding(I.__file__)
  and intent['action']=='step'and intent['arguments']==dict(descriptor=cp['descriptor'],checkpoint=a['checkpoint'])
  and intent['controls']==C.encode(env[7])and intent['timeout_seconds']==3300 and intent['memory_limit_bytes']==8589934592
  and intent['models']is True and not(home/'first-step/result.json').exists(),'exact original pre-request guarded failure required')
 log=Path(g['log']['path']);require(binding(log)==g['log']and log.read_text().rstrip().endswith('scripts.batch.harbor_admission.AdmissionError: existing producer artifact changed')
  and 'prepare_disclosure_review'in log.read_text()and "disclosure/sentences.json"in log.read_text(),'failure was not the exact prepared-manifest collision')
 outer=read(a['worker_outcome']);wi=read(outer['intent']);job=read(wi['job'])
 require(outer['returncode']==1 and outer['failure_type']is None and outer['result']is None
  and wi['owner']==old['owner']and job['driver']==old['helper']and job['result']==str(home/'result.json'),'stopped original worker differs')
 step=home/'permit/steps/disclosure-review';si=read(a['step_intent']);exception=read(a['exception'])
 require(a['step_intent']==binding(step/'intent.json')and a['exception']==binding(step/'exception.json')
  and tree(step)=={'intent.json':a['step_intent']['sha256'],'exception.json':a['exception']['sha256']}
  and exception==dict(type='AdmissionError',message_sha256=hashlib.sha256(b'existing producer artifact changed').hexdigest(),automatic_replay=False)
  and si==dict(code=K.W.code(w),initial=permit['initial'],lease=permit['lease'],previous=w.disclosure_revision,selection=I.codec.encode(s),step='disclosure-review'), 'a request or unrecognized step effect exists')
 producer=K.producer_run(w);require(producer==home/'permit/producer','original permit producer location differs')
 cache_state(p,a,producer,w.run/'producer',continued=continued)
 if not continued:require(tree(s.content.stage)==a['stage_tree'],'candidate stage changed before recovery')
 return a,w,s,l

def move(p,a,worker,K,keep):
 producer=K.producer_run(worker);archive,expected=cache_state(p,a,producer,worker.run/'producer',continued=False)
 # Same-filesystem rename retains every original byte and is atomic. An
 # interruption before the receipt remains an explicit reconciliation boundary.
 os.rename(producer,archive)
 shutil.copytree(archive/'disclosure-build',producer/'disclosure-build')
 require(tree(archive)==expected,'moved cache differs')
 for directory in (producer,archive.parent):
  fd=os.open(directory,os.O_RDONLY)
  try:os.fsync(fd)
  finally:os.close(fd)
 return keep(archive.parent/'cache-move.json',dict(schema_version=SCHEMA,proof=p['source_cache_resume'],source=str(producer),archive=str(archive),tree=expected,semantic_budget_reset=False))

@contextmanager
def step_scope(p,W,worker):
 original=W.step_directory
 def selected(other,phase):
  if other.run==worker.run and other.root==worker.root and other.disclosure_revision==worker.disclosure_revision and phase=='disclosure-review':return Path(p['output'])/'steps/disclosure-review'
  return original(other,phase)
 W.step_directory=selected
 try:yield
 finally:W.step_directory=original

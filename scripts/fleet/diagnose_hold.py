"""Read a held worker's authenticated outcome and last dispatcher boundary.

No retry, claim, or task mutation is performed. Full diagnostic contents are
retained privately; the console reports only stage, error type and process IDs.
"""
from pathlib import Path
import argparse,base64,hashlib,inspect,json,shlex,subprocess,sys
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from scripts.fleet.migration import binding,checked,retain
from scripts.fleet.coordinator import Client
from scripts.fleet.prepare_queue import HOSTS

def native(payload):
 from pathlib import Path
 import json,hashlib,base64,os,time
 records={}
 def add(path,expected=None):
  p=Path(path);assert p.is_absolute()and not any(x.is_symlink()for x in(p,*p.parents));raw=p.read_bytes();assert len(raw)<=16*1024**2;row=dict(path=str(p),sha256=hashlib.sha256(raw).hexdigest());assert expected is None or expected==row;records[str(p)]=dict(native=row,data=base64.b64encode(raw).decode());return json.loads(raw)if p.suffix=='.json'else None
 outcome=add(payload['outcome']['path'],payload['outcome']if payload['outcome'].get('sha256')else None);intent=add(outcome['intent']['path'],outcome['intent']);job=add(intent['job']['path'],intent['job']);assert job['workbook']==payload['workbook']and job['source']['sha256']==payload['source_sha256'];home=Path(job['result']).parent
 if payload.get('launch_job'):assert intent['job']==payload['launch_job']and intent['owner']==payload['owner']
 for name in('worker.log','finish-uncertain.json'):
  path=Path(job['run'])/name
  if path.is_file():add(path)
 if Path(job['result']).exists():add(job['result'])
 for pattern in('*.log','outcome.json','resource.json','hold.json'):
  for q in home.rglob(pattern):
   if str(q)not in records and q.is_file():add(q)
 for ops in home.rglob('dispatcher-v2'):
  for run in ops.iterdir():
   if not run.is_dir():continue
   steps=sorted(p for p in run.iterdir()if p.is_dir()and len(p.name)==3 and p.name.isdigit())
   for step in steps[-2:]:
    for name in('result.json','outcome.json','resource.json','step.log'):
     p=step/name
     if p.is_file():add(p)
 for record in list(records.values()):
  if not record['native']['path'].endswith('.json'):continue
  v=json.loads(base64.b64decode(record['data']))
  for k in('checkpoint','result','log'):
   r=v.get(k)
   if isinstance(r,dict)and set(r)=={'path','sha256'}and r['path']not in records:add(r['path'],r)
 processes=[]
 for d in Path('/proc').iterdir():
  if not d.name.isdigit()or int(d.name)==os.getpid():continue
  try:
   st=(d/'stat').read_text().rsplit(')',1)[1].split();argv=(d/'cmdline').read_bytes().decode(errors='replace').split(chr(0))
   if st[0]!='Z'and any(a==job['run']or a.startswith(job['run']+'/')or a==str(home)or a.startswith(str(home)+'/')or a==intent['job']['path']for a in argv):processes.append(dict(pid=int(d.name),start=st[19]))
  except(FileNotFoundError,PermissionError,ProcessLookupError):pass
 return dict(at=time.time(),files=list(records.values()),processes=processes,models=0,claims=0)

def run(workbook,output,coordinator,launch=None):
 c=checked(coordinator);s=Client(c['url'],str(Path(c['private_directory'])/'admin.token')).call('status');w=next(w for w in s['workbooks']if w['workbook']==workbook);assert w['state']=='hold';host=w['owner'].split('/')[0].split(':')[0];assert host in HOSTS;out=Path(output).absolute();out.mkdir(mode=0o700);retain(out/'central.json',{k:w[k]for k in('workbook','state','owner','fence','source_sha256','version','reason')});payload=dict(workbook=workbook,source_sha256=w['source_sha256'],owner=w['owner'])
 if launch is None:payload['outcome']=json.loads(w['reason'])
 else:
  v=checked(launch);i=checked(v['intent']);j=checked(v['remote']['job']);assert i['remote']['job']==v['remote']['job']and checked(i['plan'])['job']==v['remote']['job']and v['remote']['root_intent']==v['intent']and j['workbook']==workbook and j['source']['sha256']==w['source_sha256'];assert v['remote']['command'][-1]==w['owner'];payload.update(launch_job=v['remote']['job'],outcome=dict(path=str(Path(j['run'])/'outcome.json')));retain(out/'selected-launch.json',launch)
 code=inspect.getsource(native)+'\nimport json,sys\nprint(json.dumps(native(json.load(sys.stdin))))';command=['/Users/gandharvmahajan/google-cloud-sdk/bin/gcloud','compute','ssh',host,'--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet','--command',shlex.join(['python3','-I','-B','-c',code])];r=subprocess.run(command,input=json.dumps(payload).encode(),capture_output=True,timeout=180);(out/'stderr').write_bytes(r.stderr);r.check_returncode();v=json.loads(r.stdout);copies=[];summary=[]
 for i,row in enumerate(v.pop('files')):
  raw=base64.b64decode(row['data'],validate=True);p=out/(str(i).zfill(3)+Path(row['native']['path']).suffix);p.write_bytes(raw);p.chmod(0o600);assert binding(p)['sha256']==row['native']['sha256'];copies.append(dict(native=row['native'],copy=binding(p)))
  if p.suffix=='.json':
   value=json.loads(raw);summary.append(dict(path=row['native']['path'],**{k:value[k]for k in('status','step','next_step','failure_type','returncode')if k in value}))
 receipt=retain(out/'mapping.json',dict(**v,files=copies));print(json.dumps(dict(workbook=workbook,host=host,processes=v['processes'],stages=summary,evidence=receipt)))

if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('workbook');a.add_argument('--output',required=True);a.add_argument('--launch');q=a.parse_args();root=Path(__file__).resolve().parents[2];run(q.workbook,q.output,binding(root/'diagnostics/vm-fleet-20260912/control/coordinator-process-v4.json'),binding(q.launch)if q.launch else None)

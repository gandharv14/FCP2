"""Provision coordinator worker auth over SSH stdin; verify read-only remote status."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shlex
import subprocess
import sys

HOSTS=['hhu-server']+['hhu-server-'+str(i) for i in range(2,9)]
REMOTE='''import json,os,sys,urllib.request
from pathlib import Path
p=Path.home()/'.config/harbor-fleet/worker.token'
p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
data=sys.stdin.buffer.read()
if p.exists():
    if p.read_bytes()!=data:raise RuntimeError('existing worker token differs; reconcile')
else:
    fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
req=urllib.request.Request('http://127.0.0.1:8765/v1',data=b'{"action":"status"}',headers={'Authorization':'Bearer '+data.decode().strip(),'Content-Type':'application/json'})
with urllib.request.urlopen(req,timeout=15) as response:value=json.load(response)
print(json.dumps(dict(schema_version=value['schema_version'],model_limit=value['model_limit'],publication_limit=value['publication_limit'],workbook_count=len(value['workbooks']),token_path=str(p),token_mode=oct(p.stat().st_mode&0o777))))
'''

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--process-record',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    record=json.loads(Path(a.process_record).read_text());token=(Path(record['private_directory'])/'worker.token').read_bytes()
    def run(host):
        command=['gcloud','compute','ssh',host,'--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet','--command','python3 -c '+shlex.quote(REMOTE)]
        r=subprocess.run(command,input=token,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120)
        value=dict(host=host,returncode=r.returncode)
        if r.returncode==0:value['status']=json.loads(r.stdout)
        else:value['error']='remote provisioning/status failed; credentials not logged'
        return value
    with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(run,HOSTS))
    Path(a.output).write_text(json.dumps(results,indent=2)+'\n');print(json.dumps(results,indent=2))
    if any(r['returncode'] for r in results):sys.exit(1)

if __name__=='__main__':main()

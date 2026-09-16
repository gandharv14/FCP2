"""Provision only the configured model key by SSH stdin, outside artifacts."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import shlex
import subprocess
import sys
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

def main():
    from xl_task_build import read_env_key
    root=Path(__file__).resolve().parents[1]
    key=read_env_key(root.parent/'.env')
    if not key or '\n' in key:raise RuntimeError('Configured model credential unavailable')
    data=('lbx_labelbox_api_key='+key+'\n').encode()
    command="""from pathlib import Path
import os,sys
p=Path('/home/gmahajan_labelbox_com/.config/harbor-fleet/model.env')
p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
data=sys.stdin.buffer.read()
if p.exists():
 assert not p.is_symlink() and p.read_bytes()==data, 'Existing credential differs; reconcile explicitly'
 assert p.stat().st_mode & 0o077 == 0, 'Existing credential permissions unsafe'
else:
 fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 os.write(fd,data);os.fsync(fd);os.close(fd)
print('PRIVATE_CREDENTIAL_READY')
"""
    hosts=['hhu-server',*[f'hhu-server-{n}' for n in range(2,9)]]
    def provision(host):
        result=subprocess.run(['gcloud','compute','ssh',host,'--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet','--command',shlex.join(['python3','-c',command])],input=data,capture_output=True,timeout=90)
        return {'host':host,'ready':result.returncode==0 and result.stdout.strip()==b'PRIVATE_CREDENTIAL_READY'}
    with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(provision,hosts))
    out=root/'diagnostics/vm-fleet-20260912/control/credential-provisioning-001.json'
    with out.open('x') as f:json.dump(results,f,indent=2)
    print(json.dumps(results))
    if not all(r['ready'] for r in results):raise SystemExit(1)

if __name__=='__main__':main()

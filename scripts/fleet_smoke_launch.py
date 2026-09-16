"""Provision private model credential and launch the hash-selected 0270 smoke."""
from pathlib import Path
import hashlib
import json
import shlex
import subprocess
import sys
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

def main():
    root=Path(__file__).resolve().parents[1];base=root/'diagnostics/vm-fleet-20260912'
    bundle=json.loads((base/'deploy/bundle-03/bundle.json').read_text())
    remote=Path('/home/gmahajan_labelbox_com/synthetic-data-fleet');release=remote/'releases'/bundle['release']
    prefix=['gcloud','compute','ssh','hhu-server','--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet']
    log=base/'smoke-launch-001.log'
    if log.exists():raise SystemExit('Existing smoke launch: inspect/reconcile before retry')
    with log.open('xb') as output:
        def ssh(command,input=None):
            return subprocess.run(prefix+['--command',command],input=input,stdout=output,stderr=subprocess.STDOUT,check=True)
        plans=base/'plans'
        subprocess.run(['gcloud','compute','scp',str(plans/'0270-plans.tar.gz'),str(plans/'0270-manifest.json'),f'hhu-server:{remote}/','--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet'],stdout=output,stderr=subprocess.STDOUT,check=True)
        args=[str(release/'venv/bin/python'),'-B',str(release/'code/scripts/fleet/migration.py'),'import',
              '--archive',str(remote/'0270-plans.tar.gz'),'--manifest',str(remote/'0270-manifest.json'),
              '--manifest-sha256',hashlib.sha256((plans/'0270-manifest.json').read_bytes()).hexdigest(),
              '--receipt',str(remote/'0270-plan-import-receipt.json'),'--allowed-root',str(root.parent)]
        ssh(shlex.join(args))
        from xl_task_build import read_env_key
        credential=read_env_key(root.parent/'.env')
        if not credential or '\n' in credential:raise RuntimeError('Configured model credential unavailable')
        payload=('lbx_labelbox_api_key='+credential+'\n').encode()
        writer="from pathlib import Path; import os,sys; p=Path('/home/gmahajan_labelbox_com/.config/harbor-fleet/model.env'); fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600); os.write(fd,sys.stdin.buffer.read()); os.fsync(fd); os.close(fd)"
        ssh(shlex.join(['python3','-c',writer]),payload)
        job=plans/'0270-job-001.json'
        command=[str(release/'venv/bin/python'),'-B',str(release/'code/scripts/fleet/worker.py'),
            '--job',str(job),'--job-sha256',hashlib.sha256(job.read_bytes()).hexdigest(),
            '--url','http://127.0.0.1:8765','--token-file','/home/gmahajan_labelbox_com/.config/harbor-fleet/worker.token',
            '--owner','hhu-server:slot-1:0270-smoke']
        launch="import json,os,subprocess; from pathlib import Path; r=Path('/home/gmahajan_labelbox_com/synthetic-data-fleet/0270-process.json'); assert not r.exists(); log=r.with_suffix('.log').open('xb'); env={**os.environ,'PATH':"+repr(str(release/'venv/bin')+':')+"+os.environ['PATH'],'PYTHONDONTWRITEBYTECODE':'1'}; p=subprocess.Popen("+repr(command)+",stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env=env); r.write_text(json.dumps({'pid':p.pid,'command':"+repr(command)+"})); print(json.dumps({'pid':p.pid,'receipt':str(r)}))"
        ssh(shlex.join(['python3','-c',launch]))
    print(str(log))

if __name__=='__main__':main()

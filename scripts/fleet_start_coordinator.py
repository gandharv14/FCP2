"""Start one local durable fleet authority, preserving legacy lock inodes."""
from pathlib import Path
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile

def main():
    root=Path(__file__).resolve().parents[1]
    control=root/'diagnostics/vm-fleet-20260912/control'
    control.mkdir(parents=True,exist_ok=True)
    receipt=control/'coordinator-process.json'
    if receipt.exists():raise SystemExit('Existing coordinator receipt; inspect before restarting')
    private=Path(tempfile.mkdtemp(prefix='harbor-fleet-',dir='/private/tmp'))
    for name in ('worker','admin'):
        p=private/(name+'.token');p.write_text(secrets.token_hex(32));p.chmod(0o600)
    source=root/'scripts/fleet/coordinator.py'
    frozen=control/'coordinator-v1.py';shutil.copyfile(source,frozen)
    legacy=root/'diagnostics/authoring-resume-20260910/root'
    locks=[legacy/'authoring-fleet-slots'/f'{n}.lock' for n in range(2)]
    locks.append(legacy/'model-slots/slot-0.lock')
    args=[sys.executable,'-B',str(frozen),'serve','--database',str(control/'fleet.sqlite3'),
        '--port','8765','--worker-token-file',str(private/'worker.token'),
        '--admin-token-file',str(private/'admin.token')]
    for p in locks:args.extend(['--legacy-lock',str(p)])
    with (control/'coordinator.log').open('xb') as log:
        child=subprocess.Popen(args,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,cwd=root)
    value=dict(pid=child.pid,command=args,private_directory=str(private),
        code_sha256=hashlib.sha256(frozen.read_bytes()).hexdigest(),legacy_locks=list(map(str,locks)),
        url='http://127.0.0.1:8765',database=str(control/'fleet.sqlite3'))
    with receipt.open('x') as f:json.dump(value,f,indent=2)
    print(json.dumps({'pid':child.pid,'receipt':str(receipt)}))

if __name__=='__main__':main()

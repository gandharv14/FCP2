"""Run real Linux isolation or fleet transaction checks on every installed host."""
from concurrent.futures import ThreadPoolExecutor
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import time

from scripts.fleet_deploy import HOSTS, ssh

SUITES = {
    'guards': ['tests/test_process_parent_death.py', 'tests/test_fleet_guardian_forwarding.py'],
    'fleet': ['tests/test_fleet_deploy.py', 'tests/test_fleet_adversarial.py', 'tests/test_fleet_coordinator.py', 'tests/test_fleet_model_gate.py'],
}


def check(host, args):
    release = args.remote_root+'/releases/'+args.release
    command = 'cd '+shlex.quote(release+'/code')+' && env PYTHONDONTWRITEBYTECODE=1 PATH='+shlex.quote(release+'/venv/bin')+':"$PATH" '+shlex.quote(release+'/venv/bin/python')+' -B -m pytest -p no:cacheprovider -q '+shlex.join(SUITES[args.suite])
    start = time.monotonic()
    try:
        result = subprocess.run(ssh(host,command,args.project,args.zone), text=True,capture_output=True,timeout=300)
        stdout,stderr,rc = result.stdout,result.stderr,result.returncode
    except subprocess.TimeoutExpired:
        stdout,stderr,rc = '', 'Check timed out; reconcile temporary pytest processes before repeating.\n', None
    (args.output/(host+'.stdout.txt')).write_text(stdout)
    (args.output/(host+'.stderr.txt')).write_text(stderr)
    return dict(host=host,returncode=rc,passed=rc==0,elapsed_seconds=time.monotonic()-start,summary=stdout.splitlines()[-1:] or None)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--release',required=True)
    p.add_argument('--suite',choices=SUITES,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--hosts',nargs='+',choices=HOSTS,default=list(HOSTS))
    p.add_argument('--remote-root',default='/home/gmahajan_labelbox_com/synthetic-data-fleet')
    p.add_argument('--project',default='saleseng');p.add_argument('--zone',default='us-central1-a')
    args=p.parse_args()
    if len(args.release)!=64 or any(c not in '0123456789abcdef' for c in args.release):raise ValueError('release SHA256 required')
    args.output.mkdir(parents=True,exist_ok=False)
    with ThreadPoolExecutor(max_workers=len(args.hosts)) as pool:rows=list(pool.map(lambda host:check(host,args),args.hosts))
    report=dict(release=args.release,suite=args.suite,hosts=rows,workbook_execution=False)
    (args.output/'validation.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(dict(output=str(args.output),passed=sum(row['passed'] for row in rows),requested=len(rows))))
    return 0 if all(row['passed'] for row in rows) else 1

if __name__=='__main__':raise SystemExit(main())

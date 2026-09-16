"""Once-only launch of the explicitly reconciled 0270 size-bound continuation."""
from pathlib import Path
import argparse
import json
import shlex
import subprocess
import sys
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.fleet.migration import binding, checked, retain
from scripts.fleet.coordinator import Client


def main():
    root = Path(__file__).resolve().parents[1]
    base = root/'diagnostics/vm-fleet-20260912'
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--journal', type=Path, default=base/'control/0270-normalization-launch-001')
    parser.add_argument('--resume-unstarted', action='store_true')
    args = parser.parse_args()
    job_binding = binding(base/'plans/0270-normalization-job-002.json')
    job = checked(job_binding)
    assert job['driver']['sha256'] == '6a8354c667a041e3f2bde4fa59e8b4656292b6ff4e9ec7c3de48e8f14ee4bcd6'
    assert job['version'] == '5d3b09ac15928bcb163405f8fd950a84d7ff3d5f56b5c01eb2a47e8f810e9057'
    proof = binding(base/'control/0270-context-hold-reconciliation-001.json')
    assert proof['sha256'] == '1ddde2c09d1c8f3dcabe59dfbe7938c711eed728133e3a4268defbac8b567712'
    probe = binding(base/'control/0270-normalization-probe-001.json')
    assert probe['sha256'] == 'da88cd2f69f19ec2d40e127e292df1a85835fe5b87e549893afc8a734d1db7db'
    process = json.loads((base/'control/coordinator-process.json').read_text())
    client = Client(process['url'], Path(process['private_directory'])/'admin.token')
    journal = args.journal.absolute()
    assert journal.parent == base/'control'
    journal.mkdir(exist_ok=False)
    common = ['gcloud','compute','ssh','hhu-server','--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet']
    def ssh(code, value):
        with (journal/('ssh-'+str(len(list(journal.glob('ssh-*.log'))))+'.log')).open('xb') as log:
            result = subprocess.run([*common,'--command',shlex.join([job['command'][0],'-B','-c',code])], input=json.dumps(value).encode(),stdout=subprocess.PIPE,stderr=log,check=True,timeout=180)
        return json.loads(result.stdout)
    validate = """
import json,sys,hashlib,os
from pathlib import Path
d=json.load(sys.stdin);job=d['job'];sys.path.insert(0,job['cwd'])
from scripts.fleet.worker import validate
from scripts.fleet.migration import binding,checked
validate(job)
assert not Path(job['run']).exists() and not Path(job['result']).parent.exists()
plan=checked(job['inputs'][0]);assert binding(plan['tracker']['path'])==plan['tracker']
assert not Path('/proc/337286').exists(), 'original worker PID still present; reconcile'
assert not Path('/home/gmahajan_labelbox_com/synthetic-data-fleet/0270-normalization-process-001.json').exists()
assert not Path('/home/gmahajan_labelbox_com/synthetic-data-fleet/0270-normalization-process-001.log').exists()
p=Path(d['binding']['path']);raw=d['raw'].encode()
assert hashlib.sha256(raw).hexdigest()==d['binding']['sha256']
if p.exists():assert p.read_bytes()==raw
else:
    with p.open('xb') as f:f.write(raw)
print(json.dumps({'status':'VALIDATED_NOT_STARTED','job':binding(p),'tracker':plan['tracker']}))
"""
    remote = ssh(validate, {'job':job,'binding':job_binding,'raw':Path(job_binding['path']).read_text()})
    status = client.call('status')
    row = next(w for w in status['workbooks'] if w['workbook']=='0270')
    assert row['fence']==2
    prior = None
    if args.resume_unstarted:
        prior = binding(base/'control/0270-normalization-launch-002/intent.json')
        assert checked(prior)['job'] == job_binding
        assert checked(binding(base/'control/0270-normalization-launch-002/launch-failure.json'))['status']=='LAUNCH_EXCEPTION_BEFORE_SUBPROCESS'
        assert row['state']=='ready' and json.loads(row['reason'])==prior
    else:
        assert row['state']=='hold' and json.loads(row['reason'])==checked(proof)['worker_outcome']
    assert status['model_limit']==status['publication_limit']==1
    assert not any(o['state'] in {'hold','active'} for o in status['operations'])
    intent = retain(journal/'intent.json', {'job':job_binding,'proof':proof,'probe':probe,'remote':remote,'expected_fence':2,'automatic_retry':False,'launcher':binding(__file__),'prior_unstarted_launch':prior})
    if not args.resume_unstarted:
        client.call('reconcile',workbook='0270',disposition='ready',evidence=json.dumps(intent),checkpoint={'continuation':job['inputs'][0]})
    python = job['command'][0]
    command = [python,'-I','-B',str(Path(job['cwd'])/'scripts/fleet/worker.py'),'--job',job_binding['path'],'--job-sha256',job_binding['sha256'],'--url','http://127.0.0.1:8765','--token-file','/home/gmahajan_labelbox_com/.config/harbor-fleet/worker.token','--owner','hhu-server:slot-1:0270-normalization-001']
    launch = """
import json,os,subprocess,sys
from pathlib import Path
d=json.load(sys.stdin);p=Path(d['receipt']);assert not p.exists()
with p.with_suffix('.log').open('xb') as log:
    env={**os.environ,'PATH':str(Path(d['command'][0]).parent)+':'+os.environ['PATH'],'PYTHONDONTWRITEBYTECODE':'1'}
    process=subprocess.Popen(d['command'],stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env=env)
    value={'pid':process.pid,'command':d['command'],'automatic_retry':False}
    with p.open('x') as f:json.dump(value,f);f.flush();os.fsync(f.fileno())
print(json.dumps(value))
"""
    remote = ssh(launch, {'command':command,'receipt':'/home/gmahajan_labelbox_com/synthetic-data-fleet/0270-normalization-process-001.json'})
    print(json.dumps(retain(journal/'launched.json', {'intent':intent,'remote':remote,'automatic_retry':False})))


if __name__=='__main__':main()

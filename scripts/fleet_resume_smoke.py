"""Explicit pre-admission infrastructure continuation; never a model replay."""
from pathlib import Path
import argparse
import json
import shlex
import subprocess
import sys
import tarfile
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.fleet.migration import binding,checked,retain,export_files
from scripts.fleet.coordinator import Client

def main():
    p=argparse.ArgumentParser();p.add_argument('--bundle',required=True);p.add_argument('--attempt',required=True);a=p.parse_args()
    if a.attempt!='002':p.error('This reviewed continuation covers only retained attempt001 to attempt002')
    root=Path(__file__).resolve().parents[1];base=root/'diagnostics/vm-fleet-20260912';plans=base/'plans'
    prior_job=binding(plans/'0270-job-001.json');old=checked(prior_job)
    prior_plan=binding(plans/'0270-plan-001.json');plan=checked(prior_plan)
    outcome=binding(Path(old['run'])/'outcome.json');value=checked(outcome)
    process=binding(base/'control/0270-process-reconciliation-001.json');observed=checked(process)
    assert observed['process_state']=='absent' and observed['tracker_files']==[] and not observed['model_campaign_exists'] and not observed['admission_exists'] and not observed['config_exists']
    assert value['status']=='RECONCILIATION_REQUIRED' and value['returncode']==1 and observed['outcome_sha256']==outcome['sha256']
    bundle=json.loads(Path(a.bundle).read_text());remote=Path('/home/gmahajan_labelbox_com/synthetic-data-fleet');release=remote/'releases'/bundle['release'];code=release/'code'
    with tarfile.open(bundle['archive']) as t:manifest=json.load(t.extractfile('FLEET_CODE_MANIFEST.json'))
    proof=retain(plans/f'0270-infra-continuation-{a.attempt}.json',dict(schema_version='fleet-preadmission-infrastructure-continuation/v1',previous_job=prior_job,previous_plan=prior_plan,previous_outcome=outcome,process_reconciliation=process,new_release=bundle['release'],new_archive_sha256=bundle['sha256'],semantic_attempts_consumed=0,source_reconstruction_replayed=False,previous_artifacts_preserved=True,reason='Restore exact historical source approval and include required pinned runtime registry'))
    plan['code_root']=str(code);plan['home']=str(base/f'workbooks/0270/attempt-{a.attempt}')
    pb=retain(plans/f'0270-plan-{a.attempt}.json',plan)
    job=dict(old);job.update(version=bundle['release'],driver={'path':str(code/'scripts/fleet/fresh.py'),'sha256':manifest['files']['scripts/fleet/fresh.py']['sha256']},cwd=str(code),run=str(base/f'workers/hhu-server/slot-1/0270-{a.attempt}'),result=plan['home']+'/result.json')
    job['inputs']=[pb,*old['inputs'][1:],proof]
    job['command']=[str(release/'venv/bin/python'),'-B',job['driver']['path'],'run','--plan',pb['path'],'--plan-sha256',pb['sha256']]
    jb=retain(plans/f'0270-job-{a.attempt}.json',job)
    archive=plans/f'0270-plans-{a.attempt}.tar.gz';mb=export_files([pb['path'],jb['path'],proof['path']],archive,plans/f'0270-manifest-{a.attempt}.json')
    common=['--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet']
    with (base/f'smoke-launch-{a.attempt}.log').open('xb') as log:
        def ssh(command):return subprocess.run(['gcloud','compute','ssh','hhu-server',*common,'--command',command],stdout=log,stderr=subprocess.STDOUT,check=True)
        subprocess.run(['gcloud','compute','scp',str(archive),mb['path'],'hhu-server:'+str(remote)+'/',*common],stdout=log,stderr=subprocess.STDOUT,check=True)
        ssh(shlex.join([str(release/'venv/bin/python'),'-B',str(code/'scripts/fleet/migration.py'),'import','--archive',str(remote/archive.name),'--manifest',str(remote/Path(mb['path']).name),'--manifest-sha256',mb['sha256'],'--receipt',str(remote/f'0270-plan-import-{a.attempt}.json'),'--allowed-root',str(root.parent)]))
        config=checked(binding(base/'control/coordinator-process.json'));client=Client(config['url'],Path(config['private_directory'])/'admin.token')
        prior_claim=checked(binding(Path(old['run'])/'claim.json'))
        current=next(w for w in client.call('status')['workbooks'] if w['workbook']=='0270')
        assert current['state']=='hold' and current['fence']==prior_claim['fence'] and current['claim_id']==prior_claim['claim_id'], 'A newer claim needs its own reconciliation'
        assert json.loads(current['reason'])==outcome, 'Hold must bind the exact reconciled outcome'
        client.call('reconcile',workbook='0270',evidence=json.dumps(proof,sort_keys=True),disposition='ready')
        command=[str(release/'venv/bin/python'),'-B',str(code/'scripts/fleet/worker.py'),'--job',jb['path'],'--job-sha256',jb['sha256'],'--url',config['url'],'--token-file','/home/gmahajan_labelbox_com/.config/harbor-fleet/worker.token','--owner',f'hhu-server:slot-1:0270-smoke-{a.attempt}']
        script="import os,json,subprocess; from pathlib import Path; r=Path("+repr(str(remote/f'0270-process-{a.attempt}.json'))+"); assert not r.exists(); log=r.with_suffix('.log').open('xb'); env={**os.environ,'PATH':"+repr(str(release/'venv/bin')+':')+"+os.environ['PATH'],'PYTHONDONTWRITEBYTECODE':'1'}; cmd="+repr(command)+"; p=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env=env); r.write_text(json.dumps({'pid':p.pid,'command':cmd})); print(json.dumps({'pid':p.pid,'receipt':str(r)}))"
        ssh(shlex.join(['python3','-c',script]))
    print(json.dumps({'job':jb,'recovery':proof,'release':bundle['release']}))

if __name__=='__main__':main()

"""Root-side fleet start/status/drain/resume with an explicit real-smoke gate.

Check is read-only. Mutations require --execute and a fresh local journal.
Never retry a lost SSH/claim response; reconcile recorded remote PIDs first.
This controller leaves the coordinator and IAP tunnels running during drain.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
import time

if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from scripts.fleet.publish import checked, binding, retain, files, require
from scripts.fleet.coordinator import Client
from scripts.fleet.prepare_queue import HOSTS, WORKBOOKS

SCHEMA='harbor-fleet-launch/v1'
REMOTE_BASE='/home/gmahajan_labelbox_com/synthetic-data-fleet'


def load_plan(row):
    p=checked(row)
    required={'schema_version','validation','custody','smoke_verification','smoke_collection',
              'coordinator_receipt','supervisor','remote_supervisor','remote_run_root'}
    require(set(p)==required and p['schema_version']==SCHEMA,'closed fleet launch plan required')
    require(binding(p['supervisor']['path'])==p['supervisor'],'supervisor bytes changed')
    for key in ('remote_supervisor','remote_run_root'):
        target=Path(p[key]);require(target.is_absolute() and '..' not in target.parts
            and target.is_relative_to(REMOTE_BASE),'isolated remote fleet control paths required')
    validation=checked(p['validation']);preparation=checked(validation['preparation'])
    require(validation.get('claims')==0 and validation.get('workers_started')==0,'validation already mutated work')
    rows=validation['hosts'];require(len(rows)==8 and {r['host'] for r in rows}==set(HOSTS),'exact eight-machine validation required')
    assignments={};seen=[]
    for host in rows:
        require(host['status']=='PLAN_AND_INPUT_HASHES_VERIFIED','all VM plan validation must pass')
        config=checked(host['pool_config']);require(config['host']==host['host'],'VM/config mismatch')
        require(config['jobs']==preparation['assignments'][host['host']],'assignment differs from validated preparation')
        jobs=[]
        for selected in config['jobs']:
            job=checked(selected);seen.append(job['workbook']);jobs.append(job)
            require(job['version']==preparation['release'] and job['history']==preparation['history'],'job release/history changed')
            require(job['memory_limit_bytes']==8*1024**3 and job['minimum_disk_bytes']>=12*1024**3,'resource guards changed')
        assignments[host['host']]={'config':host['pool_config'],'value':config,'jobs':jobs}
    require(sorted(seen)==sorted(WORKBOOKS) and len(set(seen))==9,'exact exclusive fresh-nine allowlist required')
    history=checked(preparation['history']);handoff=checked(preparation['handoff'])
    handoffs={r['workbook_id']:r for r in handoff['workbooks']}
    for assignment in assignments.values():
        for job in assignment['jobs']:
            wb=job['workbook'];require(wb in history['fresh_ids'] and wb not in history['excluded'] and not history['histories'].get(wb),'fresh history authority absent')
            recipe=next(checked(r) for r in job['inputs'] if checked(r).get('schema_version')=='harbor-fleet-fresh-plan/v1')
            prior=checked(recipe['reconstruction']['prior_preparation'])
            require(recipe['reconstruction']['mode']=='fresh' and prior['new_local_attempt_limit']==1 and prior['models'] is False and prior['automatic_retry'] is False,'preparation budget changed')
            hand=handoffs[wb]
            require(hand['status']=='unstarted_reconstruction' and hand['preparation']==recipe['reconstruction']['prior_preparation'],'unused handoff/preparation mismatch')
            assignment.setdefault('recipes',[]).append(recipe)
    custody=checked(p['custody'])
    require(custody['manifest']==preparation['plan_manifest'] and custody['release']==preparation['release'],'wrong input custody campaign')
    require(len(custody['hosts'])==8 and {r['host'] for r in custody['hosts']}==set(HOSTS)
            and all(r['status']=='INPUTS_VERIFIED' for r in custody['hosts']),'eight verified input transfers required')
    return p,assignments,preparation


def smoke_gate(p,central):
    verification=checked(p['smoke_verification']);collection=checked(p['smoke_collection'])
    require(verification.get('schema_version')=='independent-published-task-copy-check/v1'
        and verification.get('workbook')=='0270' and all(verification.get(k) is True for k in
        ['passed','byte_identical_to_immutable_generation','current_qualification_verified'])
        and verification.get('model_calls')==0 and verification.get('runtime_operations')==0,'real independent smoke qualification required')
    require(collection.get('schema_version')=='harbor-fleet-collection/v1' and collection.get('status')=='COLLECTED'
        and collection.get('workbook')=='0270' and collection.get('local_byte_integrity_verified') is True
        and collection['independent_remote_verification']['sha256']==p['smoke_verification']['sha256'],'qualified local smoke collection required')
    require(files(Path(collection['output']))==collection['files'],'local smoke output changed after collection')
    require(collection['files']['generation-manifest.json']['sha256']==verification['task_manifest']['sha256'],'smoke immutable generation differs')
    require(next((w for w in central['workbooks'] if w['workbook']=='0270'),{}).get('state')=='complete','central smoke workbook is not complete')
    require(not any(o['state'] in ('active','hold') for o in central['operations']),'global model/publication operation unresolved')


def fresh_gate(assignments,central):
    states={w['workbook']:w for w in central['workbooks']}
    for assignment in assignments.values():
        for job,recipe in zip(assignment['jobs'],assignment['recipes']):
            w=states.get(job['workbook'],{})
            require(w.get('state')=='hold' and w.get('fence')==0 and w.get('owner') is None
                and w.get('source_sha256')==job['source']['sha256'],'fresh workbook not untouched centrally held ownership')
            prior=Path(recipe['reconstruction']['prior_preparation']['path'])
            legacy=prior.parents[1]/('reconstruction-'+job['workbook']+'-attempt-001')
            require(not Path(job['run']).exists() and not Path(recipe['home']).exists() and not legacy.exists(),'local retained attempt forbids fresh launch')


def client_for(p):
    receipt=checked(p['coordinator_receipt'])
    return Client(receipt['url'],Path(receipt['private_directory'])/'admin.token')


def ssh(host,script,*,payload=None,timeout=180,interpreter='python3'):
    command=['gcloud','compute','ssh',host,'--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet',
             '--command',shlex.join([interpreter,'-c',script])]
    result=subprocess.run(command,input=payload,capture_output=True,timeout=timeout)
    if result.returncode:raise RuntimeError('remote operation failed; inspect/reconcile before retry')
    return json.loads(result.stdout)


def probe(host,assignment,p,*,fresh):
    # Source/plan/runtime verification occurs under the actual installed runtime.
    script="import json,sys;from pathlib import Path;sys.path.insert(0,"+repr(assignment['value']['code_root'])+")\n"
    script+="from scripts.fleet.pool import config,resources,process_identity\nfrom scripts.fleet.worker import validate\nfrom scripts.fleet.migration import checked\n"
    script+="cfg,jobs=config("+repr(assignment['config'])+")\n"
    script+="for bound,job in jobs:\n validate(job)\n"
    if fresh:
        script+=" recipe=next(checked(r) for r in job['inputs'] if checked(r).get('schema_version')=='harbor-fleet-fresh-plan/v1')\n legacy=Path(recipe['reconstruction']['prior_preparation']['path']).parents[1]/('reconstruction-'+job['workbook']+'-attempt-001')\n if Path(job['run']).exists() or Path(job['result']).parent.exists() or legacy.exists():raise ValueError('remote attempt exists; no replay')\n"
    run=str(Path(p['remote_run_root'])/host)
    script+="run=Path("+repr(run)+");pool=None\nif (run/'current.json').exists():\n from scripts.fleet.pool import status\n pool=status(run)\n"
    script+="if pool:\n for slot in pool['slots']:\n  identity=slot.get('worker_identity');slot['recorded_worker_alive']=bool(identity and process_identity(identity['pid'])==identity)\n"
    if fresh:script+="if pool is not None:raise ValueError('existing pool needs explicit reconciliation')\n"
    script+="print(json.dumps(dict(host="+repr(host)+",status='LIVE_INPUTS_VERIFIED',resources=resources(cfg['code_root']),pool=pool)))"
    return ssh(host,script,interpreter=assignment['value']['python'])


def stage_and_operate(host,assignment,p,action,*,force=False):
    source=Path(p['supervisor']['path']).read_bytes()
    require(hashlib.sha256(source).hexdigest()==p['supervisor']['sha256'],'supervisor changed during transfer')
    remote=p['remote_supervisor'];run=str(Path(p['remote_run_root'])/host)
    script="import os,sys,json,hashlib,subprocess;from pathlib import Path\n"
    script+="p=Path("+repr(remote)+");data=sys.stdin.buffer.read();expected="+repr(p['supervisor']['sha256'])+"\n"
    script+="if hashlib.sha256(data).hexdigest()!=expected:raise ValueError('supervisor transfer changed')\n"
    script+="if p.exists():\n if p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest()!=expected:raise ValueError('existing supervisor changed')\n"
    script+="else:\n p.parent.mkdir(parents=True,exist_ok=True);fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o700)\n with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())\n"
    cmd=[assignment['value']['python'],'-I','-B',remote,action,'--run',run]
    if action in ('start','resume'):cmd+=['--config',assignment['config']['path'],'--config-sha256',assignment['config']['sha256']]
    if force:cmd+=['--force']
    script+="r=subprocess.run("+repr(cmd)+",capture_output=True,text=True)\nif r.returncode:raise ValueError('supervisor operation failed; do not retry')\nprint(r.stdout)"
    return ssh(host,script,payload=source)


def resume_gate(reconciliation,prior_intent,hosts):
    value=checked(reconciliation)
    require(value.get('schema_version')=='harbor-fleet-resume-reconciliation/v1'
        and value.get('prior_intent')==prior_intent,'exact prior control intent reconciliation required')
    require(len(value['hosts'])==8 and {r['host'] for r in value['hosts']}==set(hosts),'exact host reconciliation required')
    modes={}
    for row in value['hosts']:
        require(row.get('processes_stopped') is True and row.get('retained_dispatches_checked') is True
            and row.get('no_uncertain_claims') is True and row.get('action') in ['start','resume'],'stopped process/claim/intent evidence required')
        checked(row['evidence']);modes[row['host']]=row['action']
    return modes


def operate(selected,action,journal,*,execute=False,force=False,reconciliation=None,prior_intent=None):
    p,assignments,preparation=load_plan(selected);client=None
    if action in ['start','resume']:
        client=client_for(p)
        central=client.call('status');smoke_gate(p,central)
        if action=='start':fresh_gate(assignments,central)
        else:
            require(reconciliation is not None and prior_intent is not None,'resume needs retained reconciliation and prior intent')
            modes=resume_gate(reconciliation,prior_intent,HOSTS)
            require(not any(w['workbook'] in WORKBOOKS and w['state']=='active' for w in central['workbooks']), 'active workbook claims must be reconciled before resume')
    with ThreadPoolExecutor(max_workers=8) as executor:
        observations=list(executor.map(lambda h:probe(h,assignments[h],p,fresh=action=='start'),HOSTS))
    if action=='resume':
        for observation in observations:
            state=observation['pool']
            require(not state or all(not s['supervisor_alive'] and not s.get('recorded_worker_alive') for s in state['slots']),
                    'pool or recorded worker still active; reconcile before resume')
    if not execute:return {'action':action,'preflight_only':True,'hosts':observations,'claims_changed':0,'workers_started':0}
    require(action in ['start','resume','stop'],'status cannot mutate')
    journal=Path(journal).absolute();require(not journal.exists(),'control journal exists; reconcile uncertain operation before retry')
    journal.mkdir(parents=True)
    intent=retain(journal/'intent.json',{'plan':selected,'action':action,'force':force,'reconciliation':reconciliation,
        'prior_intent':prior_intent,'preflight':observations,'driver':binding(Path(__file__).resolve()),'automatic_retry':False})
    if action=='start':
        for host in HOSTS:
            for job in assignments[host]['jobs']:
                # Explicitly attest the retained source/handoff/history/process
                # preflight; no elapsed-lease shortcut or consumed-attempt reset.
                value=client.call('reconcile',workbook=job['workbook'],disposition='ready',evidence=json.dumps(intent,sort_keys=True))
                retain(journal/(job['workbook']+'-ready.json'),value)
    by_host={row['host']:row for row in observations}
    def run(host):
        selected_action=modes[host] if action=='resume' else action
        if action=='stop' and by_host[host].get('pool') is None:
            result={'host':host,'action':'stop','status':'NOT_LAUNCHED','response_received':True,'slots':[]}
            retain(journal/(host+'-response.json'),result);return result
        try:
            response=stage_and_operate(host,assignments[host],p,selected_action,force=force)
            retain(journal/(host+'-response.json'),response)
            actual=probe(host,assignments[host],p,fresh=False)
            observation=retain(journal/(host+'-observed.json'),actual)
            state=actual.get('pool')
            return {'host':host,'action':selected_action,'response_received':True,'observation':observation,
                    'slots':[{k:s.get(k) for k in ('slot','state','supervisor_alive','workbook','recorded_worker_alive')} for s in state['slots']] if state else []}
        except Exception as exc:
            result={'host':host,'action':selected_action,'response_received':False,'failure_type':type(exc).__name__,
                'status':'RECONCILIATION_REQUIRED','automatic_retry':False}
            retain(journal/(host+'-uncertain.json'),result);return result
    with ThreadPoolExecutor(max_workers=8) as executor:results=list(executor.map(run,HOSTS))
    return retain(journal/'result.json',{'intent':intent,'hosts':results,'coordinator_stopped':False,'tunnels_stopped':False,
        'publication_or_qualification_claimed':False,'automatic_retry':False})


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['start','status','stop','resume'])
    for name in ['plan','plan-sha256']:p.add_argument('--'+name,required=True)
    p.add_argument('--journal');p.add_argument('--execute',action='store_true');p.add_argument('--force',action='store_true')
    for name in ['reconciliation','reconciliation-sha256','prior-intent','prior-intent-sha256']:p.add_argument('--'+name)
    a=p.parse_args()
    if a.execute and not a.journal:p.error('--execute requires a fresh --journal')
    rec={'path':a.reconciliation,'sha256':a.reconciliation_sha256} if a.reconciliation else None
    prior={'path':a.prior_intent,'sha256':a.prior_intent_sha256} if a.prior_intent else None
    result=operate({'path':str(Path(a.plan).absolute()),'sha256':a.plan_sha256},a.action,a.journal,
        execute=a.execute,force=a.force,reconciliation=rec,prior_intent=prior)
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()

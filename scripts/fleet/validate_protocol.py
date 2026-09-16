"""Exercise 16 actual VM clients against an isolated coordinator via IAP tunnels.

Synthetic IDs only: no source workbooks, model endpoint calls or publication.
The production coordinator on 8765 is never mutated by these checks.
"""
from concurrent.futures import ThreadPoolExecutor
import argparse
import json
from pathlib import Path
import secrets
import shlex
import subprocess
import threading
import time

from scripts.fleet.coordinator import Coordinator, Conflict, serve
from scripts.fleet_tunnels import HOSTS, start_one, stop_one

REMOTE = '''import concurrent.futures,json,sys,time,urllib.request,urllib.error,uuid
from pathlib import Path
host=sys.argv[1]
token=(Path.home()/'.config/harbor-fleet/worker.token').read_text().strip()
def call(action,**arguments):
    req=urllib.request.Request('http://127.0.0.1:8766/v1',data=json.dumps(dict(action=action,arguments=arguments)).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=30) as r:return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code==409:
            e.close();return {'conflict':True}
        raise

def work(index):
    owner=host+'/'+str(index)
    duplicate=call('claim',workbook='probe-duplicate',owner=owner,claim_id='duplicate-'+owner,version='protocol-validation',lease_seconds=30)
    won='conflict' not in duplicate
    row=call('claim',workbook='probe-'+owner,owner=owner,claim_id='own-'+owner,version='protocol-validation',lease_seconds=180)
    auth={k:row[k] for k in ['workbook','owner','token','fence']}
    waits={}
    for kind in ['model','publication']:
        op=kind+'-'+owner;start=time.monotonic()
        while True:
            result=call('begin',kind=kind,operation_id=op,request_sha256='b'*64,**auth)
            if result.get('execute'):break
            if not result.get('busy'):raise RuntimeError('unexpected operation state')
            time.sleep(0.1)
        waits[kind]=time.monotonic()-start
        time.sleep(0.15)
        call('end',kind=kind,operation_id=op,evidence='synthetic-protocol-only; no actual side effect',**auth)
        repeated=call('begin',kind=kind,operation_id=op,request_sha256='b'*64,**auth)
        if repeated.get('execute') is not False:raise RuntimeError('duplicate execution grant')
    call('finish',evidence='synthetic validation finished',**auth)
    return dict(owner=owner,duplicate_winner=won,wait_seconds=waits)
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(work,[0,1]))
print(json.dumps(results))
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',required=True);parser.add_argument('--process-record',required=True)
    args=parser.parse_args();root=Path(args.run).resolve();root.mkdir(parents=True,exist_ok=False,mode=0o700)
    record=json.loads(Path(args.process_record).read_text())
    token=(Path(record['private_directory'])/'worker.token').read_text().strip()
    coordinator=Coordinator(root/'validation.sqlite')
    for workbook in ['probe-duplicate']+['probe-'+h+'/'+str(i) for h in HOSTS for i in [0,1]]+['probe-interruption','probe-uncertain']:
        coordinator.enroll(workbook,'a'*64,'synthetic protocol fixture')
        coordinator.reconcile(workbook,'synthetic fixture; no real workbook or previous owner')
    server=serve(coordinator,'127.0.0.1',8766,token,secrets.token_hex(32))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    tunnel_root=root/'tunnels';tunnel_root.mkdir()
    tunnels=[]
    started=time.monotonic();report={}
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:tunnels=list(pool.map(lambda h:start_one(h,tunnel_root,8766),HOSTS))
        def remote(host):
            cmd=['gcloud','compute','ssh',host,'--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet','--command','python3 -c '+shlex.quote(REMOTE)+' '+shlex.quote(host)]
            result=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=150)
            if result.returncode:
                (root/(host+'.stderr.log')).write_bytes(result.stderr)
                raise RuntimeError(host+' protocol client failed')
            rows=json.loads(result.stdout)
            (root/(host+'.json')).write_text(json.dumps(rows,indent=2)+'\n')
            return rows
        # SSH setup typically takes longer than tunnel setup; a bounded readiness
        # probe never mutates a claim or replays a side effect.
        time.sleep(8)
        with ThreadPoolExecutor(max_workers=8) as pool:rows=[row for host_rows in pool.map(remote,HOSTS) for row in host_rows]
        assert len(rows)==16 and sum(r['duplicate_winner'] for r in rows)==1
        state=coordinator.status()
        maxima={}
        for kind in ['model','publication']:
            events=[]
            for op in state['operations']:
                if op['kind']==kind:
                    assert op['state']=='confirmed'
                    events.extend([(op['started'],1),(op['ended'],-1)])
            active=maximum=0
            for _,change in sorted(events,key=lambda x:(x[0],x[1])):
                active+=change;maximum=max(maximum,active)
            assert maximum==1 and active==0
            maxima[kind]=maximum
        # Lost mutation response: a committed begin is never an execution grant twice.
        row=coordinator.claim('probe-uncertain','synthetic-root','lost-claim','protocol',lease_seconds=30)
        auth={k:row[k] for k in ['workbook','owner','token','fence']}
        assert coordinator.begin('model','lost-response',request_sha256='c'*64,**auth)['execute']
        assert not coordinator.begin('model','lost-response',request_sha256='c'*64,**auth)['execute']
        coordinator.end('model','lost-response',evidence='synthetic partial response',outcome='uncertain',**auth)
        row2=coordinator.claim('probe-interruption','synthetic-interrupted','interrupted','protocol',lease_seconds=10)
        auth2={k:row2[k] for k in ['workbook','owner','token','fence']}
        assert coordinator.begin('model','blocked',request_sha256='d'*64,**auth2)['state']=='hold'
        coordinator.reconcile_operation('model','lost-response','synthetic provider completion settled; attempt consumed','abandoned')
        assert coordinator.begin('model','interrupted-op',request_sha256='d'*64,**auth2)['execute']
        time.sleep(11)
        state=coordinator.status()
        assert next(w for w in state['workbooks'] if w['workbook']=='probe-interruption')['state']=='hold'
        try:coordinator.claim('probe-interruption','replacement','new-claim','protocol')
        except Conflict:pass
        else:raise AssertionError('expired claim silently replayed')
        report=dict(passed=True,scope='distributed coordinator protocol only; not actual pipeline qualification',
                    actual_vm_count=8,concurrent_clients_per_vm=2,total_clients=16,duplicate_claim_winners=1,
                    max_simultaneous_operations=maxima,publication_idempotency=True,
                    lost_response_no_replay=True,uncertain_operation_blocks_global_slot=True,
                    expired_lease_requires_reconciliation=True,clients=rows,
                    elapsed_seconds=time.monotonic()-started)
        (root/'final-state.json').write_text(json.dumps(state,indent=2)+'\n')
    finally:
        for tunnel in tunnels:
            try:stop_one(tunnel)
            except Exception:pass
        server.shutdown();server.server_close();thread.join()
        (root/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()

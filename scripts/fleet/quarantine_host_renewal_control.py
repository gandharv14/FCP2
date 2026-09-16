"""Recorded bounded renewal through the original live ledger, without restart."""
from pathlib import Path
import argparse, fcntl, importlib.util, json, os, sqlite3, subprocess, time
from scripts.fleet.physical_capacity_control import binding, read, keep, module, require, AUTH

SCHEMA = 'fleet-quarantine-host-renewal-control/v1'


def load(row):
    p = read(row)
    require(set(p) == {'schema_version','helper','primitive','support_coordinator','support_control','coordinator','process_identity','reader','authorization','arguments','support_quarantine'} and p['schema_version'] == SCHEMA and p['helper'] == binding(str(Path(__file__).absolute())), 'closed renewal control required')
    import scripts.fleet.coordinator as C
    import scripts.fleet.physical_capacity_control as S
    require(binding(C.__file__) == p['support_coordinator'] and binding(S.__file__) == p['support_control'], 'selected support changed')
    auth = p['authorization']; args = p['arguments']
    require(auth['sha256'] == AUTH and binding(auth['path']) == auth and args['authorization'] == auth, 'explicit renewal authorization differs')
    evidence = read(args['evidence'])
    require(set(evidence)=={'schema_version','prior_evidence','current_status','unknown_operations_released','author_limit','physical_model_limit','maximum_seconds'} and evidence['schema_version']=='fleet-quarantine-host-renewal-evidence/v1' and evidence['unknown_operations_released'] is False and (evidence['author_limit'],evidence['physical_model_limit'],evidence['maximum_seconds'])==(2,2,86400),'bounded paired renewal declaration differs')
    read(evidence['prior_evidence'])
    status=read(evidence['current_status']);now=time.time()
    require(type(status['at']) in (int,float) and 0<=now-status['at']<=300,'fresh original status required')
    selected=[x for x in status['quarantines']if C.digest(x)==args['previous_quarantine_sha256']]
    caps=[x for x in status['quarantine_host_capacity']if C.digest(x)==args['previous_capacity_sha256']]
    require(len(selected)==len(caps)==1 and selected[0]['host']==caps[0]['host'] and selected[0]['evidence']==evidence['prior_evidence'],'current paired authority differs')
    import scripts.fleet.quarantine_renewal as Q
    require(binding(Q.__file__)==p['support_quarantine'],'selected quarantine history helper changed')
    old = read(p['coordinator'])
    require(old['url'] == 'http://127.0.0.1:8765' and old['command'][3] == 'serve' and old['command'][old['command'].index('--database')+1] == old['database'], 'canonical listener differs')
    code = dict(path=old['command'][2],sha256=old['code_sha256']); O = module(code,'physical_renewal_original_listener')
    observed = subprocess.run(['/bin/ps','-p',str(old['pid']),'-o','lstart=','-o','command='],capture_output=True,text=True,timeout=3)
    require(observed.returncode == 0 and observed.stdout.strip() == p['process_identity'] and code['path'] in observed.stdout, 'running listener changed')
    R = module(p['primitive'],'scripts.fleet.selected_physical_renewal'); P = module(p['reader'],'physical_renewal_original_reader')
    return p,old,O,R,P


def copy_proof(data,home):
    p,old,O,R,P = data; home.mkdir(mode=0o700); copy = home/'ledger.sqlite3'
    with sqlite3.connect('file:'+old['database']+'?mode=ro',uri=True) as source, sqlite3.connect(copy) as destination: source.backup(destination)
    copy.chmod(0o600); c = O.Coordinator(copy); before = c.status(); result = R.renew(c,**p['arguments']); after = c.status()
    require(all(after[k] == v for k,v in before.items() if k not in ('at','events','quarantines','quarantine_host_capacity')) and after['events'] == before['events']+1, 'copy changed original authority')
    require(result['effective'] in P.validate_host_capacities(after) and result['quarantine_renewal']['effective'] in P.validate_quarantines(after), 'frozen reader rejects copied renewal')
    with c.connect() as db: originals,history,effective = R.history(db)
    keep(home/'before.json',before); keep(home/'after.json',after); keep(home/'originals.json',originals); keep(home/'renewals.json',history)
    return keep(home/'proof.json',dict(status='FULL_DATABASE_COPY_PAIRED_RENEWAL_PASS',record=result,provider_calls=0,live_mutations=0,listener_restarts=0))


def run(row,directory,*,execute=False):
    data = load(row); p,old,O,R,P = data; home = Path(directory)
    require(home.is_absolute() and not home.exists() and not any(x.is_symlink() for x in home.parents), 'fresh canonical journal required'); home.mkdir(mode=0o700)
    proof = copy_proof(data,home/'copy-proof')
    if not execute: return keep(home/'result.json',dict(status='PAIRED_RENEWAL_VALIDATED_NOT_STARTED',plan=row,proof=proof))
    locks = []
    try:
        for name in ('coordinator-recovery.lock','job-launch.lock'):
            fd = os.open(Path(old['database']).parent/name,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600); locks.append(fd); fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        p,old,O,R,P = load(row); c = O.Coordinator(old['database'])
        keep(home/'intent.json',dict(plan=row,proof=proof,at=time.time(),automatic_retry=False))
        result = R.renew(c,**p['arguments']); receipt = keep(home/'renewal.json',result)
        with c.connect() as db: originals,history,effective = R.history(db)
        keep(home/'originals.json',originals); keep(home/'renewals.json',history)
        after = O.Client(old['url'],Path(old['private_directory'])/'worker.token').call('status'); keep(home/'after.json',after)
        require(result['effective'] in P.validate_host_capacities(after) and result['quarantine_renewal']['effective'] in P.validate_quarantines(after), 'unchanged live listener or frozen reader did not observe renewal')
        return keep(home/'result.json',dict(status='QUARANTINE_HOST_RENEWED',plan=row,receipt=receipt,proof=proof,provider_calls=0,listener_restarts=0,unknown_operations_released=False))
    finally:
        for fd in locks: os.close(fd)


if __name__ == '__main__':
    a=argparse.ArgumentParser();a.add_argument('--plan',required=True);a.add_argument('--sha256',required=True);a.add_argument('--journal',required=True);a.add_argument('--execute',action='store_true');v=a.parse_args();print(json.dumps(run(dict(path=v.plan,sha256=v.sha256),v.journal,execute=v.execute)))

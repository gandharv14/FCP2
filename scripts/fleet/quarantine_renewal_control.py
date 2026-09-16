"""Recorded local administrator transaction for append-only quarantine renewal."""
from pathlib import Path
import argparse,fcntl,hashlib,importlib.util,json,os,sqlite3,subprocess,sys,time

SCHEMA='fleet-quarantine-renewal-control/v1'
AUTH='acffe75c8ed401f66484a5bb6c39a15a0a3e209fc976967da0bddd6125126fd0'

def require(ok,message):
    if not ok:raise ValueError(message)

def binding(path):
    p=Path(path).absolute();require(p.is_file()and not any(q.is_symlink()for q in(p,*p.parents)),'canonical quarantine control evidence required');return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def read(row):require(set(row)=={'path','sha256'}and binding(row['path'])==row,'quarantine control binding changed');return json.loads(Path(row['path']).read_bytes())

def retain(path,value):
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w')as f:json.dump(value,f,sort_keys=True,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    return binding(path)

def module(row,name):
    require(binding(row['path'])==row,'selected quarantine implementation changed');spec=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def state(db):
    result={}
    for name in ('workbooks','claims','operations','model_policy','model_policy_changes','quarantine_host_capacity','author_capacity_policy','author_reservations'):
        rows=[tuple(r)for r in db.execute('SELECT * FROM '+name+' ORDER BY 1,2')];result[name]=dict(rows=len(rows),sha256=hashlib.sha256(json.dumps(rows,separators=(',',':')).encode()).hexdigest())
    return result

def scope_evidence(row,args):
    e=read(row);require(e['schema_version']in('fleet-sealed-unknown-model-scope/v1','fleet-stopped-submitted-model-scope/v1')and e['workbook']==args['workbook']and e['operation_id']==args['operation_id'],'scope evidence identity differs')
    mapping=read(e['custody'])
    for item in mapping:require(binding(item['copy'])['sha256']==item['native']['sha256'],'original unknown custody changed')
    if e['schema_version']=='fleet-stopped-submitted-model-scope/v1':unsealed_evidence(e,args,mapping)
    else:
        for name in ('execution_plan','submitted','outcome'):
            require(any(binding(item['copy'])==e[name]for item in mapping),'scope execution evidence outside custody')
        plan=read(e['execution_plan']);submitted=read(e['submitted']);outcome=read(e['outcome'])
        require(plan['attempt_id']==submitted['attempt_id']==args['operation_id']and plan['request']['sha256']==submitted['request']['sha256']and outcome['status']=='incomplete'and outcome['details'] in [dict(automatic_replay=False,reason_type=reason)for reason in ('RemoteDisconnected','TimeoutError')]and outcome['semantic_approval']is False,'exact sealed unknown transport required')
        require(outcome['evidence']['plan.json']==e['execution_plan']['sha256']and outcome['evidence']['submitted.json']==e['submitted']['sha256'],'sealed executor provenance differs')
        if outcome['details']['reason_type']=='TimeoutError':timeout_evidence(e,plan,submitted,outcome,mapping)
    stopped=read(e['stopped']);prior=read(e['prior_observation'])
    if prior.get('schema_version')=='fleet-oneoff-quarantine-prior/v1':
        oneoff_stopped(prior,stopped,args);return e
    host=next(x for x in prior['hosts']if x['host']==args['host']);slot=next(x for x in host['slots']if x.get('workbook')==args['workbook']);identity=slot['worker_identity']
    require(stopped['matches']==[]and stopped['boot_id']==identity['boot_id']and stopped['at']>=host['at']and not any(x['pid']==identity['pid']and x['start']==identity['start']for x in stopped['processes']),'unknown worker process is not proven stopped')
    for item in stopped['copies']:require(binding(item['copy']['path'])==item['copy']and item['copy']['sha256']==item['native']['sha256'],'stopped custody changed')
    require(any(item['native']==slot['dispatch']for item in stopped['copies']),'stopped process dispatch differs')
    return e

def oneoff_stopped(prior,stopped,args):
    require(set(prior)=={'schema_version','launch'},'closed oneoff quarantine prior required')
    launch=read(prior['launch']);remote=launch['remote'];intent=read(launch['intent']);plan=read(intent['plan']);job=read(remote['job'])
    require(launch['automatic_retry']is False and remote['automatic_retry']is False and remote['root_intent']==launch['intent']and intent['automatic_retry']is False,'original oneoff launch custody differs')
    require(remote['job']==plan['job']==intent['remote']['job']and plan['host']==args['host']and plan['new_owner']==args['owner']and plan['expected_fence']+1==args['fence'],'original oneoff owner/fence differs')
    require(job['workbook']==args['workbook']and job['source']['sha256']==args['source_sha256']and job['version']==args['version'],'oneoff job source/version differs')
    require(remote['command'][-2:]==['--owner',args['owner']]and '--job' in remote['command']and remote['command'][remote['command'].index('--job')+1]==remote['job']['path'],'original oneoff command differs')
    require(type(stopped['boot_time'])is int and 0<stopped['boot_time']*10**9<remote['created_at_ns']<=stopped['at']*10**9 and stopped['matches']==[],'oneoff same-boot complete stopped census required')
    require(not any(x['pid']==remote['pid']and x['start']==remote['start_ticks']for x in stopped['processes']),'original oneoff process remains alive')
    for row in stopped['copies']:require(binding(row['copy']['path'])==row['copy']and row['copy']['sha256']==row['native']['sha256'],'oneoff stopped custody changed')
    require(any(row['native']==remote['job']for row in stopped['copies']),'oneoff native job missing')
    original=next((row for row in stopped['copies']if row['native']['path']==str(Path(job['run'])/'intent.json')),None)
    require(original is not None and read(original['copy'])['job']==remote['job']and read(original['copy'])['owner']==args['owner'],'oneoff original worker intent differs')

def unsealed_evidence(e,args,mapping):
    """Retain a stopped submitted attempt as unknown without inventing a seal."""
    require(set(e)=={'schema_version','workbook','operation_id','custody','attempt_inventory','execution_plan','submitted','transport_started','request','worker_outcome','stopped','prior_observation','current_status'},'closed stopped submitted evidence required')
    for name in ('execution_plan','submitted','transport_started','request','worker_outcome'):
        require(any(binding(item['copy'])==e[name]for item in mapping),'submitted evidence outside original custody')
    plan=read(e['execution_plan']);submitted=read(e['submitted']);started=read(e['transport_started']);outer=read(e['worker_outcome']);inventory=read(e['attempt_inventory']);root=Path(inventory['root'])
    require(plan['attempt_id']==submitted['attempt_id']==started['attempt_id']==args['operation_id']and started['index']==0,'submitted attempt identity differs')
    require(plan['request']['sha256']==submitted['request']['sha256']==started['request_sha256']==e['request']['sha256'],'submitted physical request differs')
    require(plan['scope']['workbook_id']==submitted['scope']['workbook_id']==args['workbook']and plan['scope']['original_sha256']==submitted['scope']['original_sha256']==args['source_sha256'],'submitted source identity differs')
    require(plan['max_transport_retries']==0 and plan['uncertain_completion_policy']=='retain_incomplete_never_replay'and plan['budget_evidence']==submitted['budget_evidence'],'submitted replay policy or budget differs')
    require(type(started['started_at_ns'])is int and type(submitted['submitted_at_ns'])is int and submitted['submitted_at_ns']<=started['started_at_ns']<inventory['at']*10**9,'submitted chronology differs')
    rows=inventory['files'];files={Path(r['native']['path']).name:r for r in rows};expected={'plan.json','submitted.json','identity.json','prior-attempts.json','request.json','transport-00.started.json'}|{f'budget-{i:02d}.snapshot'for i in range(len(submitted['budget_evidence']))}
    require(len(files)==len(rows)and set(files)==expected and all(Path(r['native']['path']).parent==root for r in rows),'attempt inventory contains missing, foreign or terminal transport evidence')
    for row in rows:
        require(binding(row['copy']['path'])==row['copy']and row['copy']['sha256']==row['native']['sha256']and Path(row['copy']['path']).stat().st_size==row['bytes'],'submitted complete inventory bytes differ')
        require(any(item['native']==row['native']and binding(item['copy'])==row['copy']for item in mapping),'submitted inventory outside original custody')
    for name,filename in [('execution_plan','plan.json'),('submitted','submitted.json'),('transport_started','transport-00.started.json'),('request','request.json')]:
        require(files[filename]['copy']==e[name],'selected submitted files differ from complete inventory')
    require(submitted['request']==files['request.json']['native'],'submitted request not retained in its actual attempt')
    for i,row in enumerate(submitted['budget_evidence']):require(files[f'budget-{i:02d}.snapshot']['native']['sha256']==row['sha256'],'submitted budget archive differs')
    require(outer['schema_version']=='harbor-fleet-worker-outcome/v1'and outer['status']=='RECONCILIATION_REQUIRED'and outer['failure_type']=='TimeoutError'and outer['returncode']is None and outer['result']is None and outer['automatic_retry']is False,'original stopped worker outcome differs')
    stopped=read(e['stopped']);require(stopped['at']>=inventory['at'],'complete attempt inventory is newer than stopped proof')
    require(any(row['copy']==e['worker_outcome']for row in stopped['copies']),'worker outcome absent from stopped custody')
    require(any(row['native']==outer['intent']for row in stopped['copies']),'original worker intent absent from stopped custody')
    intent=read(next(row['copy']for row in stopped['copies']if row['native']==outer['intent']))
    require(intent['owner']==args['owner']and intent['automatic_retry']is False,'stopped worker owner or replay policy differs')

def timeout_evidence(e,plan,submitted,outcome,mapping):
    """A sealed submitted physical timeout remains unknown, never replayable."""
    for name,filename in [('transport_started','transport-00.started.json'),('connection_error','transport-00.connection-error.json')]:
        require(any(binding(item['copy'])==e[name]for item in mapping),'timeout evidence outside original custody')
        require(outcome['evidence'][filename]==e[name]['sha256'],'timeout evidence not sealed by original executor')
    started=read(e['transport_started']);error=read(e['connection_error'])
    require(started['attempt_id']==plan['attempt_id']==submitted['attempt_id']and started['index']==0 and started['request_sha256']==submitted['request']['sha256'],'timeout physical request differs')
    require(error==dict(category='timeout',completed_at_ns=error.get('completed_at_ns'),delay_seconds=None,response_body_present=False,retry=False,stage='open')and type(error['completed_at_ns'])is int and type(started['started_at_ns'])is int and submitted['submitted_at_ns']<=started['started_at_ns']<error['completed_at_ns']<=outcome['sealed_at_ns'],'timeout is not a sealed response-free transport')
    require(plan['max_transport_retries']==0 and plan['uncertain_completion_policy']=='retain_incomplete_never_replay'and not any(n in outcome['evidence']for n in ('response.raw.json','transport-00.body.raw','completed.json')),'timeout has response or replay authority')


def load(row):
    p=read(row);require(set(p)-{'scope_operation'}=={'schema_version','helper','renewal','support_coordinator','coordinator','authorization','declaration','arguments','process_identity'}and p['schema_version']==SCHEMA and p['helper']==binding(__file__),'closed quarantine control required')
    require(p['authorization']['sha256']==AUTH and binding(p['authorization']['path'])==p['authorization'],'explicit retained authorization differs')
    declaration=read(p['declaration']);args=p['arguments'];scope=p.get('scope_operation')
    require(scope in(None,'fleet-active-host-quarantine/v1','fleet-stopped-host-quarantine/v1'),'unknown isolation operation')
    expected=dict(schema_version='fleet-quarantine-renewal-authorization/v1',authorization=p['authorization'],arguments=args,original_operation_remains_unknown=True,original_workbook_and_host_remain_blocked=True,maximum_exception_seconds=86400,provider_calls=0,claims=0,listener_restarts=0)
    if scope:
        expected.pop('original_workbook_and_host_remain_blocked');expected.update(schema_version='fleet-active-host-quarantine-authorization/v1',unknown_workbook_remains_blocked=True,physical_model_limit=2,unknown_slots_consumed=1,healthy_claims_preserved=True)
    if scope=='fleet-stopped-host-quarantine/v1':
        expected.update(schema_version='fleet-stopped-host-quarantine-authorization/v1',unknown_slots_consumed=2,existing_host_capacity_unchanged=True)
    require(declaration==expected,'bounded isolation declaration differs')
    require(args['authorization']==p['authorization'],'renewal authorization changed');read(args['evidence'])
    if scope:scope_evidence(args['evidence'],args)
    old=read(p['coordinator']);code=dict(path=old['command'][2],sha256=old['code_sha256']);O=module(code,'selected_original_quarantine_coordinator')
    require(old['url']=='http://127.0.0.1:8765'and old['command'][3]=='serve'and str(Path(old['database']).absolute())==old['database'],'original authority differs')
    observed=subprocess.run(['/bin/ps','-p',str(old['pid']),'-o','lstart=','-o','command='],capture_output=True,text=True,timeout=3)
    require(observed.returncode==0 and observed.stdout.strip()==p['process_identity']and code['path']in observed.stdout,'original live coordinator process changed')
    import scripts.fleet.coordinator as C
    require(binding(C.__file__)==p['support_coordinator'],'renewal primitive dependency differs')
    R=module(p['renewal'],'scripts.fleet.selected_quarantine_renewal')
    status=O.Client(old['url'],Path(old['private_directory'])/'worker.token').call('status')
    require(status['author_capacity_policy']['authors_per_host']==2,'original capacity/isolation policy changed')
    return p,old,O,R,status

def apply_operation(p,authority,R):
    if p.get('scope_operation')=='fleet-stopped-host-quarantine/v1':
        from scripts.fleet.capacity_policy import validate_quarantines,validate_host_capacities
        status=authority.status();args=p['arguments'];host=args['host']
        prior=[q for q in validate_quarantines(status)if q['host']==host]
        capacities=[c for c in validate_host_capacities(status)if c['host']==host]
        occupied=[o for o in status['operations']if o['kind']=='model'and o['state']in('active','hold')and authority.host(o['owner'])==host]
        require(len(prior)==len(capacities)==1 and len(occupied)==2 and all(o['state']=='hold'for o in occupied),'exact two-unknown stopped host required')
        require({o['operation_id']for o in occupied}=={prior[0]['operation_id'],args['operation_id']}and args['expires_at']<=min(prior[0]['expires_at'],capacities[0]['expires_at']),'second unknown differs or extends existing host authority')
        return authority.quarantine_model(**p['arguments'])
    return (R.isolate if p.get('scope_operation') else R.renew)(authority,**p['arguments'])

def effective_record(p,result):
    return result if p.get('scope_operation')=='fleet-stopped-host-quarantine/v1'else(result['quarantine']if p.get('scope_operation')else result['effective'])

def copied_proof(data,home):
    p,old,O,R,status=data;home=Path(home);home.mkdir(mode=0o700);copy=home/'ledger.sqlite3'
    with sqlite3.connect('file:'+old['database']+'?mode=ro',uri=True)as src,sqlite3.connect(copy)as dst:src.backup(dst)
    copy.chmod(0o600);authority=O.Coordinator(copy);before=authority.status()
    with authority.connect()as db:before_state=state(db);archived=db.execute("SELECT 1 FROM sqlite_master WHERE type='table'AND name='model_quarantine_originals'").fetchone();old_rows=[tuple(r)for r in db.execute('SELECT * FROM '+('model_quarantine_originals'if archived else'model_quarantines')+' ORDER BY revision')]
    scope=bool(p.get('scope_operation'));result=apply_operation(p,authority,R);after=authority.status();effective=effective_record(p,result)
    from scripts.fleet.capacity_policy import validate_quarantines
    require(effective in validate_quarantines(after),'original capacity validation refused new effective authority')
    with authority.connect()as db:
        after_state=state(db);require(all(after_state[k]==v for k,v in before_state.items()if not(scope and k=='quarantine_host_capacity')),'copy isolation changed original work')
        archived=db.execute("SELECT 1 FROM sqlite_master WHERE type='table'AND name='model_quarantine_originals'").fetchone();actual_rows=[tuple(r)for r in db.execute('SELECT * FROM '+('model_quarantine_originals'if archived else'model_quarantines')+' ORDER BY revision')]
        require((actual_rows[:-1]==old_rows and len(actual_rows)==len(old_rows)+1)if scope else actual_rows==old_rows,'copied original quarantine rows changed')
        originals=[json.loads(r[-1])for r in actual_rows]
        if scope:
            from scripts.fleet.capacity_policy import validate_host_capacities
            require(after['quarantines']==before['quarantines']+[effective],'original quarantine append differs')
            if p['scope_operation']=='fleet-stopped-host-quarantine/v1':
                require(after['quarantine_host_capacity']==before['quarantine_host_capacity']and validate_host_capacities(after)==validate_host_capacities(before),'original host capacity changed')
            else:require(after['quarantine_host_capacity']==before['quarantine_host_capacity']+[result['host_capacity']]and result['host_capacity']in validate_host_capacities(after),'original frozen host capacity refused atomic isolation')
        else:R.history(db)
    require(before['operations']==after['operations']and before['workbooks']==after['workbooks']and after['events']==before['events']+1,'copy changed unrelated state')
    return read(retain(home/'proof.json',dict(status='FULL_DATABASE_COPY_SCOPE_PASS'if scope else'FULL_DATABASE_COPY_RENEWAL_PASS',renewal=result,original_state=before_state,original_quarantines=originals,claims=0,provider_calls=0,listener_restarts=0)))

def run(row,directory,*,execute=False):
    data=load(row);p,old,O,R,status=data;home=Path(directory).absolute();require(not home.exists(),'quarantine renewal journal consumed; no replay');home.mkdir(mode=0o700);proof=copied_proof(data,home/'copy-proof')
    if not execute:return read(retain(home/'result.json',dict(status='QUARANTINE_RENEWAL_VALIDATED_NOT_STARTED',plan=row,proof=binding(home/'copy-proof/proof.json'))))
    locks=[]
    try:
        for name in ('coordinator-recovery.lock','job-launch.lock'):
            fd=os.open(Path(old['database']).parent/name,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600);locks.append(fd);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        data=load(row);p,old,O,R,status=data
        backup=home/'before.sqlite3'
        with sqlite3.connect('file:'+old['database']+'?mode=ro',uri=True)as src,sqlite3.connect(backup)as dst:src.backup(dst)
        backup.chmod(0o600);retain(home/'intent.json',dict(plan=row,backup=binding(backup),proof=binding(home/'copy-proof/proof.json'),automatic_retry=False,at=time.time()))
        # Original live object's implementation sees the transactional view on its
        # next query. No process, socket, worker heartbeat or provider is paused.
        authority=O.Coordinator(old['database']);scope=bool(p.get('scope_operation'));result=apply_operation(p,authority,R);rr=retain(home/'renewal.json',result);effective=effective_record(p,result)
        after=O.Client(old['url'],Path(old['private_directory'])/'worker.token').call('status');retain(home/'after.json',after)
        require(effective in after['quarantines'],'original live HTTP did not expose renewed authority')
        with authority.connect()as db:
            if scope:
                names={r['name']for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")};original_table='model_quarantine_originals'if'model_quarantine_originals'in names else'model_quarantines'
                original_rows=[json.loads(r['record'])for r in db.execute('SELECT record FROM '+original_table+' ORDER BY revision')]
                renewal_rows=[json.loads(r['record'])for r in db.execute('SELECT record FROM model_quarantine_renewals ORDER BY revision')]if'model_quarantine_renewals'in names else[]
                history=(original_rows,renewal_rows,authority.quarantines(db))
            else:history=R.history(db)
        retain(home/'history.json',dict(originals=history[0],renewals=history[1],effective=history[2]))
        return read(retain(home/'result.json',dict(status='QUARANTINE_SCOPE_RECORDED'if scope else'QUARANTINE_RENEWAL_RECORDED',plan=row,renewal=rr,history=binding(home/'history.json'),original_coordinator=p['coordinator'],listener_restarts=0,claims=0,provider_calls=0,automatic_retry=False)))
    finally:
        for fd in locks:os.close(fd)

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--plan',required=True);a.add_argument('--sha256',required=True);a.add_argument('--directory',required=True);a.add_argument('--execute',action='store_true');v=a.parse_args();print(json.dumps(run(dict(path=str(Path(v.plan).absolute()),sha256=v.sha256),v.directory,execute=v.execute),indent=2))

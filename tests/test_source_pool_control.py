from pathlib import Path
from types import SimpleNamespace
import json,fcntl,os
import pytest
from scripts.fleet import source_pool_control as C


def fixture(tmp_path,monkeypatch):
    hosts=['hhu-server']+['hhu-server-'+str(i)for i in range(2,9)]
    p=dict(schema_version='fleet-source-pool-start/v1',configs={h:{}for h in hosts},coordinator={'path':str(tmp_path/'coordinator.json')},probes={h:{}for h in hosts})
    rows=[dict(workbook='0188',source={'sha256':'s'},host=hosts[0],job={})]
    calls=[]
    class Client:
        def call(self,action,**kw):calls.append((action,kw));return dict(workbook=kw['workbook'],state='hold'if action=='enroll'else'ready')
    monkeypatch.setattr(C,'load',lambda selected:(p,dict(rows=rows),{}, {},Client(),{}))
    starts=[]
    def remote(p,prepared,runtime,host,execute=False):
        if execute:starts.append(host)
        return SimpleNamespace(returncode=0,stdout=json.dumps(dict(status='PASS',host=host)).encode(),stderr=b'')
    monkeypatch.setattr(C,'remote',remote)
    return p,calls,starts


def test_readonly_preflight_never_enrolls_or_starts(tmp_path,monkeypatch):
    p,calls,starts=fixture(tmp_path,monkeypatch)
    value=C.run({})
    assert value['status']=='PASS'and len(value['hosts'])==8
    assert calls==starts==[]


def test_shared_lock_refuses_before_intent_and_admin_calls(tmp_path,monkeypatch):
    p,calls,starts=fixture(tmp_path,monkeypatch)
    fd=os.open(tmp_path/'job-launch.lock',os.O_CREAT|os.O_RDWR,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):C.run({},tmp_path/'launch')
        assert calls==starts==[]and not(tmp_path/'launch').exists()
    finally:os.close(fd)


def test_one_start_preserves_every_host_outcome_and_cannot_replay(tmp_path,monkeypatch):
    p,calls,starts=fixture(tmp_path,monkeypatch);journal=tmp_path/'launch'
    result=C.run({},journal)
    assert result['status']=='START_RECORDED'and len(starts)==8
    assert [x[0]for x in calls]==['enroll','reconcile']
    assert (journal/'intent.json').is_file()and len((journal/'events.jsonl').read_text().splitlines())==4
    with pytest.raises(FileExistsError):C.run({},journal)
    assert len(starts)==8 and len(calls)==2


def test_lost_remote_reply_retains_uncertainty_without_second_start(tmp_path,monkeypatch):
    p,calls,starts=fixture(tmp_path,monkeypatch)
    def remote(p,prepared,runtime,host,execute=False):
        if execute:
            starts.append(host)
            if host=='hhu-server':raise TimeoutError('ambiguous after spawn')
        return SimpleNamespace(returncode=0,stdout=b'{}',stderr=b'')
    monkeypatch.setattr(C,'remote',remote)
    result=C.run({},tmp_path/'launch')
    assert result['status']=='RECONCILIATION_REQUIRED'and len(starts)==8 and len(set(starts))==8
    assert (tmp_path/'launch/hhu-server.intent.json').is_file()
    assert next(r for r in result['hosts']if r['host']=='hhu-server')['failure_type']=='TimeoutError'


def prior_fixture(tmp_path):
    def put(name,v):return C.retain(tmp_path/name,v)
    wb='0188';source={'path':'/original.xlsx','sha256':'s'};source_plan={'path':'/source-plan.json','sha256':'p'}
    oldplan=put('old-plan.json',dict(reconstruction=dict(plan=source_plan)))
    oldjob=put('old-job.json',dict(workbook=wb,source=source,version='v',inputs=[oldplan]))
    intent=dict(owner='hhu-server/slot-0/uuid',job=oldjob);intentcopy=put('intent.json',intent);intentnative=dict(intentcopy,path='/native/intent.json')
    ledger=dict(workbook_id=wb,build=dict(state='VERIFIED',attempts=1));lc=put('ledger.json',ledger);ln=dict(lc,path='/native/ledger.json')
    values=[dict(status='RECONSTRUCTION_VERIFIED',plan=source_plan,ledger=ln,models_dispatched=0),ledger,dict(status='RECONCILIATION_REQUIRED',intent=intentnative),intent,{}, {},{}]
    records=[]
    for i,value in enumerate(values):
        copy=put(str(i)+'.json',value)
        native=dict(copy,path='/native/'+('ledger.json'if i==1 else'intent.json'if i==3 else str(i)+'.json'))
        records.append(dict(native=native,copy=copy))
    handoff=put('handoff.json',dict(workbook=wb,state='VERIFIED',ledger=records[1]['native'],source_manifest=records[4]['native'],segmentation_manifest=records[5]['native']))
    plan=put('new-plan.json',dict(reconstruction=dict(mode='existing',handoff=handoff,generated_curation=records[6]['native'])))
    job=dict(workbook=wb,source=source,version='v',inputs=[plan]);prior=dict(job=oldjob,records=records)
    current=dict(state='hold',fence=1,owner=intent['owner'],version='v',source_sha256='s')
    return prior,job,current


def test_actual_retained_source_identity_allows_author_only_handoff(tmp_path):
    prior,job,current=prior_fixture(tmp_path)
    assert C.retained_authority(prior,job,current)['reconstruction']


@pytest.mark.parametrize('fault',['fence','owner','source','version','active','counter','projection'])
def test_retained_source_recovery_rejects_changed_custody_or_counter(tmp_path,fault):
    prior,job,current=prior_fixture(tmp_path)
    if fault=='fence':current['fence']=2
    elif fault=='owner':current['owner']='other'
    elif fault=='source':current['source_sha256']='other'
    elif fault=='version':job['version']='other'
    elif fault=='active':current['state']='active'
    elif fault=='projection':prior['records'][0]['native']['sha256']='other'
    else:
        record=prior['records'][1];value=C.read(record['copy']);value['build']['attempts']=2
        record['copy']=C.retain(tmp_path/'ledger2.json',value);record['native']['sha256']=record['copy']['sha256']
    with pytest.raises(ValueError):C.retained_authority(prior,job,current)


def test_queued_unused_move_retains_ready_without_admin_reconciliation(tmp_path,monkeypatch):
    p,calls,starts=fixture(tmp_path,monkeypatch);p['schema_version']='fleet-source-pool-start/v3'
    assert C.run({},tmp_path/'launch')['status']=='START_RECORDED'
    assert calls==[] and len(starts)==8


@pytest.mark.parametrize('fault',[None,'active','fence','job','source'])
def test_ready_unused_job_is_exact_prior_unchanged_author_selection(tmp_path,fault):
    original,job,current=prior_fixture(tmp_path)
    selected=C.retain(tmp_path/'selected-author-job.json',job)
    prior=dict(job=selected,records=original['records'])
    previous=dict(prior={job['workbook']:original})
    current.update(state='ready',owner=None)
    if fault=='active':current['state']='active'
    elif fault=='fence':current['fence']=2
    elif fault=='job':job=dict(job,version='changed')
    elif fault=='source':current['source_sha256']='changed'
    if fault:
        with pytest.raises(ValueError):C.queued_authority(prior,job,current,previous)
    else:assert C.queued_authority(prior,job,current,previous)


def test_unused_initial_job_retains_fence_zero_without_admin_mutation(tmp_path,monkeypatch):
    p,calls,starts=fixture(tmp_path,monkeypatch);p['schema_version']='fleet-source-pool-start/v4'
    result=C.run({},tmp_path/'new-queue')
    assert result['status']=='START_RECORDED' and len(starts)==8 and calls==[]
    assert 'retain-ready' in (tmp_path/'new-queue/events.jsonl').read_text()


def test_sealed_preclaim_recovery_retains_ready_without_reenrollment(tmp_path,monkeypatch):
    p,calls,starts=fixture(tmp_path,monkeypatch);p['schema_version']='fleet-source-pool-start/v7'
    result=C.run({},tmp_path/'preclaim-recovery')
    assert result['status']=='START_RECORDED' and len(starts)==8 and calls==[]
    assert 'retain-ready' in (tmp_path/'preclaim-recovery/events.jsonl').read_text()
    with pytest.raises(FileExistsError):C.run({},tmp_path/'preclaim-recovery')
    assert len(starts)==8 and calls==[]


def unused_fixture(tmp_path):
    plan=C.retain(tmp_path/'initial-plan.json',dict(reconstruction=dict(mode='source-first')))
    job=dict(workbook='0125',source={'sha256':'s'},inputs=[plan]);prior=dict(host='hhu-server-5',job=C.retain(tmp_path/'job.json',job))
    current=dict(state='ready',fence=0,owner=None,version=None,source_sha256='s')
    return prior,job,current,dict(schema_version='fleet-source-pool-start/v1')


def test_unused_initial_job_exact_fence_zero(tmp_path):
    assert C.unused_authority(*unused_fixture(tmp_path))['reconstruction']['mode']=='source-first'


@pytest.mark.parametrize('fault',['claimed','held','owner','version','source','job','previous_kind','extra_prior','author_only'])
def test_unused_initial_queue_rejects_consumed_or_changed_selection(tmp_path,fault):
    prior,job,current,previous=unused_fixture(tmp_path)
    if fault=='claimed':current['fence']=1
    elif fault=='held':current['state']='hold'
    elif fault=='owner':current['owner']='some-owner'
    elif fault=='version':current['version']='selected'
    elif fault=='source':current['source_sha256']='other'
    elif fault=='job':job['workbook']='9999'
    elif fault=='previous_kind':previous['schema_version']='fleet-source-pool-start/v2'
    elif fault=='extra_prior':prior['records']=[]
    else:
        plan=C.retain(tmp_path/'author-plan.json',dict(reconstruction=dict(mode='existing')));job['inputs']=[plan];prior['job']=C.retain(tmp_path/'author-job.json',job)
    with pytest.raises(ValueError):C.unused_authority(prior,job,current,previous)


def test_v5_start_enrolls_once_and_never_replays(tmp_path,monkeypatch):
    p,calls,starts=fixture(tmp_path,monkeypatch);p['schema_version']='fleet-source-pool-start/v5'
    assert C.run({},tmp_path/'launch')['status']=='START_RECORDED'
    assert [x[0]for x in calls]==['enroll','reconcile'] and len(starts)==8
    with pytest.raises(FileExistsError):C.run({},tmp_path/'launch')
    assert len(calls)==2 and len(starts)==8


def test_selected_v5_policy_bytes_cannot_change(tmp_path):
    selected=C.binding(Path('scripts/fleet/capacity_policy.py').absolute())
    assert C.selected_admission(dict(schema_version='fleet-source-pool-start/v5',admission_policy=selected)).SCHEMA=='fleet-concurrency-policy/v1'
    with pytest.raises(ValueError):C.selected_admission(dict(schema_version='fleet-source-pool-start/v5',admission_policy={**selected,'sha256':'0'*64}))
    assert C.selected_admission(dict(schema_version='fleet-source-pool-start/v4')) is None


def test_v5_actual_quarantine_preserves_unknown_and_publication_holds(tmp_path):
    from test_fleet_quarantine import setup,retained
    c,now,claims,args=setup(tmp_path);c.quarantine_model(**args)
    admission=C.selected_admission(dict(schema_version='fleet-source-pool-start/v5',admission_policy=C.binding(Path('scripts/fleet/capacity_policy.py').absolute())))
    c.begin('publication','unrelated-publication',request_sha256='f'*64,**claims['0141'])
    c.end('publication','unrelated-publication',evidence='uncertain publication',outcome='uncertain',**claims['0141'])
    before=retained(c);C.task_admission(admission,c.status(),'hhu-server-8',dict(workbook='0001'));assert retained(c)==before
    with pytest.raises(ValueError):C.task_admission(admission,c.status(),'hhu-server',dict(workbook='0001'))
    with pytest.raises(ValueError):C.task_admission(admission,c.status(),'hhu-server-7',dict(workbook='0141'))
    c.begin('model','other-unknown',request_sha256='e'*64,**claims['0140']);c.end('model','other-unknown',evidence='unknown',outcome='uncertain',**claims['0140'])
    with pytest.raises(ValueError):C.task_admission(admission,c.status(),'hhu-server-8',dict(workbook='0001'))


def test_v5_native_script_uses_selected_policy_and_target_gate(monkeypatch):
    captures=[]
    monkeypatch.setattr(C.subprocess,'run',lambda argv,**kw:captures.append(argv)or SimpleNamespace(returncode=0))
    C.remote({}, {},dict(python='/selected/python'),'hhu-server-8')
    import shlex
    code=shlex.split(captures[0][-1])[-1]
    assert "pool.admission_module(p['admission_policy'])"in code
    assert "op['workbook']==job['workbook']"in code
    assert "else:assert not any(op['state']=='hold'"in code


def initial_hold_fixture(tmp_path):
    row=dict(workbook='0289',state='hold',fence=0,owner=None,claim_id=None,version=None,
             checkpoint=None,source_sha256='source',reason='initial ownership reconciliation required')
    status=dict(workbooks=[row],operations=[],author_reservations=[])
    p=dict(schema_version='fleet-source-pool-start/v5',coordinator={'path':'/coordinator','sha256':'c'},authorization={'path':'/authorization','sha256':'a'})
    proof=dict(schema_version='fleet-initial-unclaimed-holds/v1',coordinator=p['coordinator'],
               authorization=p['authorization'],status=C.retain(tmp_path/'status.json',status),workbooks=['0289'])
    p['initial_holds']=C.retain(tmp_path/'initial.json',proof)
    return p,dict(rows=[dict(workbook='0289',source={'sha256':'source'})]),status


def test_initial_hold_is_enrollment_not_a_consumed_attempt(tmp_path):
    p,prepared,status=initial_hold_fixture(tmp_path)
    assert C.initial_hold_authority(p,prepared,status)['workbooks']==['0289']


@pytest.mark.parametrize('fault',['owner','fence','claim_id','version','checkpoint','source','ready','reason','operation','reservation','changed','duplicate'])
def test_initial_hold_reconciliation_rejects_consumption_and_drift(tmp_path,fault):
    p,prepared,status=initial_hold_fixture(tmp_path);row=status['workbooks'][0]
    if fault in ('owner','claim_id','version','checkpoint'):row[fault]='occupied'
    elif fault=='fence':row['fence']=1
    elif fault=='source':row['source_sha256']='foreign'
    elif fault=='ready':row['state']='ready'
    elif fault=='reason':row['reason']='previous worker failed'
    elif fault=='operation':status['operations']=[dict(workbook='0289',state='confirmed')]
    elif fault=='reservation':status['author_reservations']=[dict(workbook='0289')]
    elif fault=='changed':row['updated_at']=123
    elif fault=='duplicate':status['workbooks'].append(dict(row))
    with pytest.raises(ValueError):C.initial_hold_authority(p,prepared,status)


def test_initial_hold_original_observation_cannot_omit_prior_model(tmp_path):
    p,prepared,status=initial_hold_fixture(tmp_path);proof=C.read(p['initial_holds'])
    old=C.read(proof['status']);old['operations']=[dict(workbook='0289',state='confirmed')]
    proof['status']=C.retain(tmp_path/'bad-status.json',old);p['initial_holds']=C.retain(tmp_path/'bad-proof.json',proof)
    with pytest.raises(ValueError,match='prior operations'):C.initial_hold_authority(p,prepared,status)


def test_initial_hold_start_only_reconciles_and_never_reenrolls(tmp_path,monkeypatch):
    p,calls,starts=fixture(tmp_path,monkeypatch);p['schema_version']='fleet-source-pool-start/v5';p['initial_holds']={'selected':True}
    assert C.run({},tmp_path/'launch')['status']=='START_RECORDED'
    assert [action for action,_ in calls]==['reconcile']and len(starts)==8
    with pytest.raises(FileExistsError):C.run({},tmp_path/'launch')
    assert len(calls)==1


def unstarted_fixture(tmp_path,fault=None):
    p,prepared,status=initial_hold_fixture(tmp_path)
    p['configs']={h:{}for h in ['hhu-server']+['hhu-server-'+str(i)for i in range(2,9)]}
    helper=tmp_path/'old.py';helper.write_text('# retained original helper\n');p['helper']=C.binding(helper)
    prior=C.retain(tmp_path/'plan.json',p);root=tmp_path/'start';root.mkdir()
    intent=C.retain(root/'intent.json',dict(plan=prior))
    current=dict(status['workbooks'][0],state='ready',reason='selected native proof',expires=None)
    status['workbooks']=[current]
    events=[dict(action='reconcile',workbook='0289',state='INTENT'),dict(action='reconcile',workbook='0289',state='RECORDED',result=dict(current))]
    if fault=='event':events[1]['action']='enroll'
    ep=root/'events.jsonl';ep.write_text(''.join(json.dumps(v)+'\n'for v in events))
    hosts=[]
    for h in p['configs']:
        stdout=root/(h+'.stdout');stdout.write_text('possibly started'if fault=='stdout'else'')
        stderr=root/(h+'.stderr');stderr.write_text('ERROR: (gcloud.compute.ssh) There was a problem refreshing your current auth tokens: Reauthentication failed. cannot prompt during non-interactive execution.\n')
        hosts.append(dict(host=h,returncode=1,automatic_retry=False,stdout=C.binding(stdout),stderr=C.binding(stderr)))
    if fault=='timeout':hosts[0]=dict(host=hosts[0]['host'],failure_type='TimeoutError',automatic_retry=False)
    if fault=='success':hosts[0]['returncode']=0
    if fault=='partial':hosts.pop()
    result=C.retain(root/'result.json',dict(status='RECONCILIATION_REQUIRED',automatic_retry=False,plan=prior,hosts=hosts))
    p=dict(p,helper={'path':'/new.py','sha256':'new'},start_recovery=dict(result=result,intent=intent,events=C.binding(ep)))
    if fault=='scope':p['run_root']='/another'
    if fault=='claimed':current['fence']=1
    if fault=='operation':status['operations']=[dict(workbook='0289',state='confirmed')]
    if fault=='reservation':status['author_reservations']=[dict(workbook='0289')]
    if fault=='altered_error':(root/'hhu-server.stderr').write_text('changed')
    return p,prepared,status


def test_authentication_failure_can_resume_only_original_unclaimed_queue(tmp_path):
    p,prepared,status=unstarted_fixture(tmp_path)
    assert C.unstarted_pool_authority(p,prepared,status)['status']=='RECONCILIATION_REQUIRED'


@pytest.mark.parametrize('fault',['event','stdout','timeout','success','partial','scope','claimed','operation','reservation','altered_error'])
def test_start_recovery_rejects_uncertain_or_consumed_work(tmp_path,fault):
    with pytest.raises(ValueError):C.unstarted_pool_authority(*unstarted_fixture(tmp_path,fault))


def test_recovered_start_never_reconciles_or_reenrolls(tmp_path,monkeypatch):
    p,calls,starts=fixture(tmp_path,monkeypatch);p.update(schema_version='fleet-source-pool-start/v5',initial_holds={},start_recovery={})
    assert C.run({},tmp_path/'resumed')['status']=='START_RECORDED'
    assert calls==[] and len(starts)==8

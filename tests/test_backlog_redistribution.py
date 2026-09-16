from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import copy,json,time
import pytest
from scripts.fleet import backlog_redistribution as R,backlog_enrollment as E,source_pool_control as S
from scripts.fleet.coordinator import Coordinator,Conflict,digest


def put(tmp,name,value):return S.retain(tmp/name,value)


def jobs(tmp):
    source=put(tmp,'source.json',dict(schema_version='fleet-source-first/v3',native_history_roots=['/old'],source={'sha256':'a'*64},next_attempt=4,legacy_research={'path':'/legacy','sha256':'h'}))
    pipeline=put(tmp,'pipeline.json',dict(schema_version='harbor-fleet-staged-pipeline/v1',source_plan=source,home='/home'))
    old=dict(workbook='0028',source={'sha256':'a'*64},version='v1',inputs=[pipeline,source],command=['python','driver','run','--plan',pipeline['path'],'--sha256',pipeline['sha256']],run='/same-original-run')
    oldrow=put(tmp,'old-job.json',old);newsource=put(tmp,'new-source.json',dict(R.read(source),native_history_roots=['/new']))
    newpipe=put(tmp,'new-pipeline.json',dict(R.read(pipeline),source_plan=newsource))
    new=dict(old,inputs=[newpipe,newsource],command=old['command'][:-5]+['run','--plan',newpipe['path'],'--sha256',newpipe['sha256']])
    return oldrow,put(tmp,'new-job.json',new)


def test_only_native_history_roots_and_containing_bindings_change(tmp_path):
    old,new=jobs(tmp_path);a,b,sp=R.job_delta(old,new,['/new'])
    assert a['run']==b['run']and a['source']==b['source']and sp['next_attempt']==4

@pytest.mark.parametrize('fault',['ordinal','source','history','home','run','command','other_input','roots'])
def test_job_delta_rejects_scope_or_authority_changes(tmp_path,fault):
    old,new=jobs(tmp_path);j=R.read(new);p=R.read(j['inputs'][0]);s=R.read(p['source_plan'])
    if fault in {'ordinal','source','history','roots'}:
        if fault=='ordinal':s['next_attempt']=5
        if fault=='source':s['source']['sha256']='b'*64
        if fault=='history':s['legacy_research']={}
        if fault=='roots':s['native_history_roots']=['/unobserved']
        p['source_plan']=put(tmp_path,'bad-source.json',s);j['inputs'][1]=p['source_plan']
    if fault=='home':p['home']='/changed'
    if fault in {'ordinal','source','history','roots','home'}:
        j['inputs'][0]=put(tmp_path,'bad-pipeline.json',p);j['command'][-3]=j['inputs'][0]['path'];j['command'][-1]=j['inputs'][0]['sha256']
    if fault=='run':j['run']='/new-run'
    if fault=='command':j['command'].append('--repeat')
    if fault=='other_input':j['inputs'].append(put(tmp_path,'extra.json',{}))
    with pytest.raises(ValueError):R.job_delta(old,put(tmp_path,'bad-job.json',j),['/new'])


def queued(tmp):
    c=Coordinator(tmp/'fleet.sqlite3');c.enroll('0028','a'*64,'original enrollment');c.reconcile('0028','ready')
    c.set_author_capacity(2,dict(path='/auth',sha256='a'*64),dict(path='/census',sha256='b'*64),'policy',digest(c.status()['workbooks']))
    return c

@pytest.mark.parametrize('fault',['active','fence','owner','version','source','claim_id','operation','reservation'])
def test_only_unclaimed_ready_without_any_prior_operations(tmp_path,fault):
    c=queued(tmp_path);status=c.status();w=status['workbooks'][0]
    assert R.ready(status,'0028',{'sha256':'a'*64})==w
    if fault=='active':w['state']='active'
    elif fault=='fence':w['fence']=1
    elif fault=='owner':w['owner']='another'
    elif fault=='version':w['version']='v1'
    elif fault=='source':w['source_sha256']='b'*64
    elif fault=='claim_id':w['claim_id']='uuid'
    elif fault=='operation':status['operations'].append(dict(workbook='0028',state='finished'))
    else:status['author_reservations'].append(dict(workbook='0028',state='released'))
    with pytest.raises(ValueError):R.ready(status,'0028',{'sha256':'a'*64})


def test_original_atomic_claim_across_both_queues_selects_one_worker(tmp_path):
    c=queued(tmp_path)
    def run(host):
        other=Coordinator(c.database)
        try:return other.claim('0028',host+'/slot-0/queued','claim-'+host,'v1',180)
        except Conflict:return None
    with ThreadPoolExecutor(max_workers=2)as e:winners=[x for x in e.map(run,['hhu-server-2','hhu-server-8'])if x]
    assert len(winners)==1 and winners[0]['fence']==1
    assert len(c.status()['author_reservations'])==1
    assert c.status()['operations']==[]


def test_existing_backlog_execute_skips_all_enroll_reconcile_calls(tmp_path,monkeypatch):
    c=queued(tmp_path);before=c.status()['workbooks'];calls=[]
    class Client:
        def call(self,action,**kwargs):calls.append(action);raise AssertionError('no admin mutation allowed')
    a=dict(schema_version='fleet-source-pool-start/v6');prepared=dict(rows=[dict(workbook='0028')],supervisor={});selection={'hhu-server-8':{}}
    monkeypatch.setattr(E,'load',lambda row:({},S,a,prepared,{},dict(database=c.database),Client(),c.status(),selection))
    monkeypatch.setattr(S,'remote',lambda *args,**kw:SimpleNamespace(returncode=0,stdout=b'{"status":"PASS"}',stderr=b''))
    monkeypatch.setattr(E,'native',lambda *args,**kw:dict(status='BACKLOG_APPENDED'if args[-1]is True else'APPEND_VALIDATED_NOT_STARTED'))
    result=E.run({},tmp_path/'once');assert result['status']=='BACKLOG_APPEND_RECORDED'
    assert calls==[]and c.status()['workbooks']==before and not c.status()['author_reservations']
    with pytest.raises(ValueError):E.run({},tmp_path/'once')

@pytest.mark.parametrize('fault',[None,'stale','future','nonfinite','missing_root','exclusion','process','consumed','history'])
def test_native_survey_is_complete_fresh_and_unused(tmp_path,fault):
    v=dict(schema_version='fleet-ready-queue-native-survey/v1',host='hhu-server-8',observed_at=time.time(),infrastructure_excluded=['releases','staging','tools'],native_root='/native',task_root='/tasks',top_level_entries=['control','releases'],roots=['/native/control','/tasks'],excluded={},histories={},matching_processes=[],consumed_paths=[])
    if fault=='stale':v['observed_at']-=901
    if fault=='future':v['observed_at']+=60
    if fault=='nonfinite':v['observed_at']=float('inf')
    if fault=='missing_root':v['roots'].pop()
    if fault=='exclusion':v['infrastructure_excluded'].append('control')
    if fault=='process':v['matching_processes']=[dict(pid=12)]
    if fault=='consumed':v['consumed_paths']=['/native/task']
    if fault=='history':v['histories']['0028']=[{}]
    row=put(tmp_path,'survey.json',v)
    if fault:
        with pytest.raises(ValueError):R.survey(row,'hhu-server-8','0028')
    else:assert R.survey(row,'hhu-server-8','0028')==v


def test_actual_prepared_0028_data_closure_and_deep_event_tamper(tmp_path,monkeypatch):
    # Real prepared data tests the multi-file authority shape, not a mirrored mock.
    base=Path(__file__).resolve().parents[1]/'diagnostics/vm-fleet-20260912/control/queued-redistribution-001/selected-002'
    if not(base/'redistribution.json').exists():pytest.skip('retained integration fixture unavailable')
    selected=R.read(S.binding(base/'redistribution.json'));selected['helper']=S.binding(Path(R.__file__).resolve())
    prepared=R.read(S.binding(base/'prepared.json'));c=Coordinator(tmp_path/'actual-fixture.sqlite3');c.enroll('0028',prepared['rows'][0]['source']['sha256'],'original fixture');c.reconcile('0028','fixture ready');p=dict(redistribution=put(tmp_path,'selection.json',selected),authorization=prepared['authorization'])
    # Historical fixture timestamps are frozen; inspect under their actual observation instant.
    stamp=R.read(selected['rows']['0028']['destination_survey'])['observed_at'];monkeypatch.setattr(R.time,'time',lambda:stamp+1)
    assert R.validate(p,prepared,c.status())==selected
    events=[json.loads(x)for x in Path(selected['enrollment_events']['path']).read_text().splitlines()]
    for e in events:
        if e.get('workbook')=='0028'and e.get('action')=='reconcile'and e.get('state')=='RECORDED':e['result']['fence']=1
    file=tmp_path/'bad-events.jsonl';file.write_text(''.join(json.dumps(e)+'\n'for e in events));selected['enrollment_events']=S.binding(file);p['redistribution']=put(tmp_path,'bad-selection.json',selected)
    with pytest.raises(ValueError,match='ready result'):R.validate(p,prepared,c.status())


def test_future_pipeline_intent_does_not_become_prior_history(tmp_path):
    from scripts.batch.harbor_admission import survey_namespaces
    pool=tmp_path/'pool';pool.mkdir();old=pool/'0001';old.mkdir();(old/'instruction.md').write_text('authentic old task')
    own=pool/'0028';v=dict(roots=[str(pool)],partitions=[dict(path=str(pool),directories=['0001'],files=[],symlinks=[])])
    roots=R.history_roots(v,dict(home=str(own/'source')))
    assert roots==[str(old)]
    own.mkdir();pipeline=own/'pipeline';pipeline.mkdir();(pipeline/'intent.json').write_text('{}')
    # The real original reader observes the sibling and excludes only the current job.
    excluded,histories=survey_namespaces(tuple(map(Path,roots)),{'0001','0028'})
    assert '0001'in excluded and '0028'not in excluded and not histories.get('0028')

@pytest.mark.parametrize('fault',['files','links','present','missing','foreign'])
def test_namespace_partition_cannot_drop_existing_evidence(tmp_path,fault):
    v=dict(roots=['/pool'],partitions=[dict(path='/pool',directories=['0001'],files=[],symlinks=[])])
    if fault=='files':v['partitions'][0]['files']=['intent.json']
    if fault=='links':v['partitions'][0]['symlinks']=['alias']
    if fault=='present':v['partitions'][0]['directories'].append('0028')
    if fault=='missing':v['partitions']=[]
    if fault=='foreign':v['partitions'][0]['path']='/another'
    with pytest.raises(ValueError):R.history_roots(v,dict(home='/pool/0028/source'))

def fresh_job(tmp):
    root=tmp/'code';(root/'scripts/fleet').mkdir(parents=True);f=root/'scripts/fleet/fresh.py';f.write_text('# original Fresh\n');driver=R.binding(f)
    snap=put(tmp,'snapshot.json',dict(root=str(root),fingerprint='v1',bindings={'scripts/fleet/fresh.py':driver}))
    hand=put(tmp,'handoff.json',dict(workbook='0028',state='VERIFIED',source_attempts=1,automatic_retry=False))
    plan=put(tmp,'fresh.json',dict(schema_version='harbor-fleet-fresh-plan/v1',workbook='0028',code_root=str(root),home='/native/unchanged',owner='original-VM4-owner',reconstruction=dict(mode='existing',handoff=hand,generated_curation=None)))
    history=put(tmp,'history.json',{})
    return put(tmp,'fresh-job.json',dict(workbook='0028',source={'sha256':'a'*64},version='v1',cwd=str(root),driver=driver,inputs=[plan,snap,hand],history=history,command=['python','-I','-B',str(f),'run','--plan',plan['path'],'--plan-sha256',plan['sha256']],run='/native/workers/old-host/0028'))

def test_existing_fresh_redistribution_preserves_exact_job_and_original_owner(tmp_path):
    job=fresh_job(tmp_path);old,new,p=R.job_delta(job,job,None)
    assert old==new and p['owner']=='original-VM4-owner' and new['run']=='/native/workers/old-host/0028'

@pytest.mark.parametrize('field,value',[('run','/changed'),('version','new'),('history',{}),('command',['repeat'])])
def test_fresh_redistribution_rejects_any_changed_job(tmp_path,field,value):
    job=fresh_job(tmp_path);new=dict(R.read(job),**{field:value})
    with pytest.raises(ValueError):R.job_delta(job,put(tmp_path,'altered.json',new),None)

def test_fresh_identical_bytes_at_another_path_and_root_projection_rejected(tmp_path):
    job=fresh_job(tmp_path)
    with pytest.raises(ValueError):R.job_delta(job,put(tmp_path,'alias.json',R.read(job)),None)
    with pytest.raises(ValueError):R.job_delta(job,job,['/changed'])

from pathlib import Path
from types import SimpleNamespace
import json
import pytest
from scripts.fleet import backlog_enrollment as E,coordinator as C,source_pool_control as S


def save(p,v):return S.retain(p,v)


def fixture(tmp_path,monkeypatch):
    db=C.Coordinator(tmp_path/'authority/fleet.sqlite3');events=[]
    receipt=dict(database=db.database,private_directory=str(tmp_path/'private'),url='http://fixture')
    jobs=[dict(workbook='0033',host='hhu-server-2',source=dict(sha256='a'*64),job=dict(path='/job',sha256='b'*64))]
    selection=dict(root='/originalpool',batch={},config={},expected=None,policy={})
    selected={'hhu-server-2':selection}
    prepared=dict(rows=jobs,supervisor={});p=dict();a=dict(probes={'hhu-server-2':{}})
    class Client:
        def call(self,action,**kwargs):
            events.append(action);return getattr(db,action)(**kwargs)
    def loaded(row):
        if db.status()['workbooks']:raise ValueError('already enrolled')
        return p,S,a,prepared,{},receipt,Client(),db.status(),selected
    monkeypatch.setattr(E,'load',loaded)
    monkeypatch.setattr(S,'remote',lambda *args,**kwargs:SimpleNamespace(returncode=0,stdout=b'{"status":"PASS"}',stderr=b''))
    def native(*args,**kwargs):
        execute=bool(args[-1]is True)
        if execute:
            w=db.status()['workbooks'][0];assert w['state']=='ready'and w['fence']==0
            events.append('append');return dict(status='BACKLOG_APPENDED')
        return dict(status='APPEND_VALIDATED_NOT_STARTED')
    monkeypatch.setattr(E,'native',native)
    return db,events,receipt


def test_original_enroll_ready_finishes_before_any_append_and_journal_retained(tmp_path,monkeypatch):
    db,events,receipt=fixture(tmp_path,monkeypatch);row={'path':'/selected','sha256':'a'*64}
    result=E.run(row,tmp_path/'once')
    assert result['status']=='BACKLOG_APPEND_RECORDED'and events==['enroll','reconcile','append']
    assert db.status()['workbooks'][0]['fence']==0
    assert not db.status()['operations']and not db.status()['author_reservations']
    assert (Path(receipt['database']).parent/'job-launch.lock').exists()
    assert (tmp_path/'once/intent.json').exists()
    with pytest.raises(ValueError):E.run(row,tmp_path/'once')
    assert events==['enroll','reconcile','append']


def test_readonly_preflight_never_enrolls_or_claims(tmp_path,monkeypatch):
    db,events,_=fixture(tmp_path,monkeypatch)
    result=E.run({'path':'/selected','sha256':'a'*64})
    assert result['status']=='BACKLOG_ENROLLMENT_VALIDATED_NOT_STARTED'
    assert not events and not db.status()['workbooks']


def test_uncertain_head_publication_retains_enrollments_and_never_retries(tmp_path,monkeypatch):
    db,events,_=fixture(tmp_path,monkeypatch)
    def native(*args,**kwargs):
        if args[-1]is True:events.append('append-response-lost');raise TimeoutError('response lost')
        return {'status':'APPEND_VALIDATED_NOT_STARTED'}
    monkeypatch.setattr(E,'native',native)
    result=E.run({'path':'/selected','sha256':'a'*64},tmp_path/'once')
    assert result['status']=='RECONCILIATION_REQUIRED'and events==['enroll','reconcile','append-response-lost']
    assert db.status()['workbooks'][0]['state']=='ready'
    assert (tmp_path/'once/hhu-server-2-intent.json').exists()
    with pytest.raises(ValueError):E.run({'path':'/selected','sha256':'a'*64},tmp_path/'once')


def load_fixture(tmp_path,monkeypatch):
    host='hhu-server-2';source=save(tmp_path/'source.json',{})
    oldjob=save(tmp_path/'oldjob.json',{'workbook':'0001'});newjob=save(tmp_path/'newjob.json',{'workbook':'0002'})
    prior=dict(jobs=[oldjob],backlog=source,host=host,worker=source,code_root='/code')
    cfg=save(tmp_path/'newcfg.json',dict(prior,jobs=[newjob]));oldcfg=save(tmp_path/'oldcfg.json',prior)
    receipt=dict(database='/authority/db',private_directory='/private',url='http://authority')
    r=save(tmp_path/'receipt.json',receipt)
    prepared=dict(snapshot=source,supervisor=source,assignments={host:[newjob]})
    oldprepared=save(tmp_path/'oldprepared.json',prepared)
    old=save(tmp_path/'oldplan.json',dict(schema_version='fleet-source-pool-start/v5',prepared=oldprepared,coordinator=r,
        configs={host:oldcfg},authorization=source,admission_policy=source,run_root='/original'))
    started=save(tmp_path/'started.json',dict(status='START_RECORDED',automatic_retry=False,plan=old))
    controller=E.binding(Path(S.__file__).resolve())
    a=dict(schema_version='fleet-source-pool-start/v5',helper=controller,authorization=source,admission_policy=source,
        configs={host:cfg},run_root='/newcheck')
    admission=save(tmp_path/'admission.json',a)
    p=dict(schema_version=E.SCHEMA,helper=E.binding(Path(E.__file__).resolve()),source_controller=controller,
        admission=admission,started=started,heads={host:None},batches={host:save(tmp_path/'batch.json',[newjob])})
    monkeypatch.setattr(E,'module',lambda row:SimpleNamespace(load=lambda row:(a,prepared,{},receipt,None,
        dict(author_capacity_policy=dict(authors_per_host=2,schema_version='fleet-author-capacity/v1')))))
    return p,a,prior,host


def test_existing_selected_generation_and_unchanged_config_pass(tmp_path,monkeypatch):
    p,a,prior,h=load_fixture(tmp_path,monkeypatch)
    assert E.load(save(tmp_path/'plan.json',p))[-1][h]['root']=='/original/'+h


@pytest.mark.parametrize('kind',['initial_duplicate','policy','runtime','batch','host','root','old_uncertain'])
def test_foreign_or_consumed_scope_rejected_before_any_enrollment(tmp_path,monkeypatch,kind):
    p,a,prior,h=load_fixture(tmp_path,monkeypatch)
    if kind in {'initial_duplicate','policy','runtime','batch'}:
        cfg=E.read(a['configs'][h])
        if kind=='initial_duplicate':cfg['jobs']=prior['jobs'];p['batches'][h]=save(tmp_path/'badbatch.json',cfg['jobs'])
        if kind=='policy':cfg['backlog']={}
        if kind=='runtime':cfg['code_root']='/foreign'
        if kind=='batch':p['batches'][h]=save(tmp_path/'badbatch.json',[])
        a['configs'][h]=save(tmp_path/'badcfg.json',cfg)
    if kind=='host':p['heads']={}
    if kind=='root':a['run_root']='/original'
    if kind=='old_uncertain':v=E.read(p['started']);v['status']='RECONCILIATION_REQUIRED';p['started']=save(tmp_path/'badstart.json',v)
    with pytest.raises(ValueError):E.load(save(tmp_path/'plan.json',p))

@pytest.mark.parametrize('kind',['nonzero','malformed','timeout'])
def test_native_retains_entire_private_response_before_failure(tmp_path,monkeypatch,kind):
    raw=b'actual-partial-append-receipt:'+b'x'*4000;err=b'actual-error:'+b'y'*4000
    def run(*args,**kwargs):
        if kind=='timeout':raise E.subprocess.TimeoutExpired(args[0],90,output=raw,stderr=err)
        return SimpleNamespace(returncode=1 if kind=='nonzero'else 0,stdout=raw,stderr=err)
    monkeypatch.setattr(E.subprocess,'run',run);home=tmp_path/'response'
    with pytest.raises(Exception):E.native({}, {}, {'python':'/python'},'hhu-server-2',True,evidence_directory=home)
    assert (home/'stdout').read_bytes()==raw and (home/'stderr').read_bytes()==err
    assert (home/'stdout').stat().st_mode&0o777==0o600
    assert json.loads((home/'transport.json').read_bytes())['uncertain']==(kind=='timeout')

@pytest.mark.parametrize('execute',[False,True])
def test_actual_remote_empty_batch_needs_no_coordinator_import_or_append(tmp_path,monkeypatch,execute):
    import sys,io,importlib.util,builtins
    cfg=dict(backlog={'path':'/policy','sha256':'p'})
    config=save(tmp_path/'cfg.json',cfg);batch=save(tmp_path/'batch.json',[]);supervisor=save(tmp_path/'supervisor.py',{})
    identity={'pid':123,'start':456};root=tmp_path/'pool';root.mkdir();save(root/'current.json',dict(config=config,supervisor=supervisor,slots=[dict(identity=identity)]))
    class Backlog:
        def load(self,*args):return [],None
        def append(self,*args,**kwargs):raise AssertionError('empty append must not run')
    pool=SimpleNamespace(config=lambda row:(cfg,None),backlog_module=lambda row:Backlog(),process_identity=lambda pid:identity,validated_jobs=lambda *args:None)
    cfg['jobs']=[];config=save(tmp_path/'cfg2.json',cfg);save(root/'current2.json',{})
    (root/'current.json').write_text(json.dumps(dict(config=config,supervisor=supervisor,slots=[dict(identity=identity)])))
    class Loader:
        def create_module(self,spec):return pool
        def exec_module(self,module):pass
    from importlib.machinery import ModuleSpec
    monkeypatch.setattr(importlib.util,'spec_from_file_location',lambda *args:ModuleSpec('empty_pool',Loader()))
    original=builtins.__import__
    def guarded(name,*args,**kwargs):
        if name=='scripts.fleet.coordinator':raise AssertionError('empty batch imported coordinator')
        return original(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',guarded)
    value=dict(selection=dict(config=config,root=str(root),policy=cfg['backlog'],expected=None,batch=batch),supervisor=supervisor,execute=execute)
    monkeypatch.setattr(sys,'stdin',io.StringIO(json.dumps(value)));out=io.StringIO();monkeypatch.setattr(sys,'stdout',out)
    exec(E.REMOTE,{})
    assert json.loads(out.getvalue())['status']==('EMPTY_BATCH'if execute else'APPEND_VALIDATED_NOT_STARTED')

@pytest.mark.parametrize('fault',[None,'ordinary','append_origin','foreign_start','unlisted','same_host'])
def test_initial_ready_redistribution_uses_exact_start_and_different_destination(tmp_path,monkeypatch,fault):
    p,a,prior,h=load_fixture(tmp_path,monkeypatch);other='hhu-server-6'
    old_result=E.read(p['started']);old=E.read(old_result['plan']);old_prepared=E.read(old['prepared'])
    original=prior['jobs'][0];new_cfg=E.read(a['configs'][h]);new_cfg['jobs']=[]
    a['configs'][h]=save(tmp_path/'empty-new.json',new_cfg);p['batches'][h]=save(tmp_path/'empty-batch.json',[])
    destination=dict(prior,host=other,jobs=[])
    old['configs'][other]=save(tmp_path/'destination-old.json',destination)
    a['configs'][other]=save(tmp_path/'destination-new.json',dict(destination,jobs=[original]))
    p['batches'][other]=save(tmp_path/'destination-batch.json',[original]);p['heads'][other]=None
    old_prepared['assignments']={h:[],other:[original]}
    old['prepared']=save(tmp_path/'oldprepared-two.json',old_prepared)
    original_start=save(tmp_path/'oldplan-two.json',old);old_result['plan']=original_start
    p['started']=save(tmp_path/'started-two.json',old_result)
    a['schema_version']='fleet-source-pool-start/v6'
    relocation=dict(enrollment_kind='initial-start',original_admission=original_start,rows={'0001':{}})
    if fault=='ordinary':a['schema_version']='fleet-source-pool-start/v5'
    if fault=='append_origin':relocation['enrollment_kind']='append'
    if fault=='foreign_start':relocation['original_admission']={}
    if fault=='unlisted':relocation['rows']={}
    if fault=='same_host':
        old['configs'][other]=save(tmp_path/'bad-destination-old.json',dict(destination,jobs=[original]))
        changed=save(tmp_path/'changed-start.json',old);old_result['plan']=changed;p['started']=save(tmp_path/'changed-started.json',old_result);relocation['original_admission']=changed
    a['redistribution']=save(tmp_path/'relocation.json',relocation)
    receipt=E.read(old['coordinator'])
    monkeypatch.setattr(E,'module',lambda row:SimpleNamespace(load=lambda row:(a,old_prepared,{},receipt,None,dict(author_capacity_policy=dict(authors_per_host=2,schema_version='fleet-author-capacity/v1')))))
    row=save(tmp_path/'two-host-plan.json',p)
    if fault:
        with pytest.raises(ValueError):E.load(row)
    else:assert E.load(row)[-1][other]['root']=='/original/'+other

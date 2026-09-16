import copy,json,time
from pathlib import Path
import pytest
from scripts.fleet import reconstruction_custody as C


def central():
    return dict(schema_version='fleet-coordinator/v1',at=100.,model_limit=1,publication_limit=1,operations=[],workbooks=[])

def host_fixture():
    s=central();w=dict(workbook='0491',state='active',owner='hhu-server-4:slot-1:0491',fence=1,claim_id='uuid',version='v',source_sha256='a'*64,expires=130.)
    s['workbooks']=[w];bound={'path':'/job/0491.json','sha256':'jobsha'};job={'workbook':'0491','source':{'sha256':'a'*64},'version':'v'}
    known={bound['path']:(bound,job,w.copy())}
    proc=dict(pid=10,ppid=1,argv=['python','/release/scripts/fleet/worker.py','--job',bound['path'],'--job-sha256','jobsha','--owner',w['owner']],task_related=True,pool=None)
    return s,[proc],known


def test_known_single_worker_leaves_one_slot_and_descendants_do_not_count():
    s,ps,k=host_fixture();ps.append(dict(pid=11,ppid=10,argv=['python','-m','scripts.corpus_build'],task_related=True,pool=None))
    result=C.reconcile_host('hhu-server-4',s,ps,k,[],now=101.)
    assert result['worker_count']==1 and result['maximum_workers']==2

@pytest.mark.parametrize('case',['two','orphan','unknown_job','missing_claim','missing_process','bad_fence','bad_owner','expired','stale','duplicate'])
def test_host_reconciliation_fails_closed(case):
    s,ps,k=host_fixture()
    if case=='two':s['workbooks'].append({**s['workbooks'][0],'workbook':'0173','owner':'hhu-server-4:slot-2:0173'})
    if case=='orphan':ps.append(dict(pid=11,ppid=1,argv=['python','old_task.py'],task_related=True,pool=None))
    if case=='unknown_job':k={}
    if case=='missing_claim':s['workbooks']=[]
    if case=='missing_process':ps=[]
    if case=='bad_fence':k['/job/0491.json'][2]['fence']=2
    if case=='bad_owner':ps[0]['argv'][-1]='another-owner'
    if case=='expired':s['workbooks'][0]['expires']=99.
    if case=='stale':s['at']=0.
    if case=='duplicate':ps.append({**ps[0],'pid':12})
    with pytest.raises(ValueError):C.reconcile_host('hhu-server-4',s,ps,k,[],now=101.)


def test_live_pool_requires_explicit_drain_and_matching_known_root():
    s,ps,k=host_fixture();pool=dict(pid=20,ppid=1,argv=['supervisor.py'],pool='/pool',pool_stopped=False,task_related=True);ps.append(pool)
    with pytest.raises(ValueError,match='drained'):C.reconcile_host('hhu-server-4',s,ps,k,['/pool'],now=101.)
    pool['pool_stopped']=True
    assert C.reconcile_host('hhu-server-4',s,ps,k,['/pool'],now=101.)['worker_count']==1
    with pytest.raises(ValueError):C.reconcile_host('hhu-server-4',s,ps,k,[],now=101.)


def test_embedded_probe_script_does_not_become_workbook_process():
    assert not C.owned_argv(['python','-c',"print('/Users/task')"],['/Users/task'])
    assert C.owned_argv(['python','--plan=/Users/task/plan.json'],['/Users/task'])


def active_operation():
    s,_,_=host_fixture();w=s['workbooks'][0]
    s['operations']=[dict(kind='model',state='active',operation_id='call1',workbook=w['workbook'],owner=w['owner'],fence=w['fence'],identity='b'*64,started=90.,expires=130.,ended=None,evidence=None)]
    return s


def test_known_other_model_may_coexist_with_zero_model_reconstruction():
    s=active_operation();assert len(C.operation_gate(s,'0200'))==1

@pytest.mark.parametrize('case',['held','unknown','publication','target','expired','mismatched_owner','mismatched_fence','two','limit','incomplete_identity','terminal'])
def test_uncertain_global_operations_block(case):
    s=active_operation();o=s['operations'][0]
    if case=='held':o['state']='hold'
    if case=='unknown':o['state']='unknown'
    if case=='publication':o['kind']='publication'
    if case=='target':o['workbook']='0200'
    if case=='expired':o['expires']=99.
    if case=='mismatched_owner':o['owner']='other'
    if case=='mismatched_fence':o['fence']=2
    if case=='two':s['operations'].append({**o,'operation_id':'another'})
    if case=='limit':s['model_limit']=2
    if case=='incomplete_identity':o['identity']='unknown'
    if case=='terminal':o['evidence']='response'
    with pytest.raises(ValueError):C.operation_gate(s,'0200')


def test_initial_fence_only_no_prior_operation_or_retry():
    s=central();job={'workbook':'0200','source':{'sha256':'a'*64}};p={'expected_reason':'initial'}
    w=dict(workbook='0200',state='hold',fence=0,owner=None,version=None,claim_id=None,source_sha256='a'*64,reason='initial');s['workbooks']=[w]
    assert C.central_gate(s,p,job)==w
    for change in [{'fence':1},{'state':'ready'},{'claim_id':'consumed'},{'owner':'old'}]:
        old=w.copy();w.update(change)
        with pytest.raises(ValueError):C.central_gate(s,p,job)
        w.clear();w.update(old)


def test_actual0200_profile_preserves_exact_native_source_authority():
    h=Path(C.PROFILES['0200']['path']).parent
    p=json.loads((h/'0200-launch-template.json').read_bytes());job=C.checked(p['job'])
    profile,source=C.source_authority(p,job)
    assert profile['output_count']==27 and source['generation'].startswith('864f9ecc')
    for field,value in [('timeout_seconds',86400),('memory_limit_bytes',16*1024**3),('version','old-code')]:
        changed=copy.deepcopy(job);changed[field]=value
        with pytest.raises(ValueError):C.source_authority(p,changed)


def test_readonly_launch_never_reconciles_or_spawns(monkeypatch):
    p={'coordinator_receipt':{'path':'/tmp/fake-coordinator.json'},'job':{},'host':'hhu-server-4'};job={'workbook':'0200'};calls=[]
    class Client:
        def __init__(self,*args):pass
        def call(self,action,**kwargs):calls.append(action);assert action=='status';return central()
    from contextlib import nullcontext
    monkeypatch.setattr(C,'load',lambda row:(p,job,{},{}));monkeypatch.setattr(C,'checked',lambda row:{'url':'test','private_directory':'/tmp/private'})
    monkeypatch.setattr(C,'Client',Client);monkeypatch.setattr(C,'lock',lambda *a,**k:nullcontext())
    monkeypatch.setattr(C,'central_gate',lambda *a:{});monkeypatch.setattr(C,'smoke_gate',lambda *a:None);monkeypatch.setattr(C,'local_custody',lambda *a:{})
    monkeypatch.setattr(C,'ssh',lambda *a,**k:{'status':'PASS'})
    assert C.launch({})['workers_started']==0 and calls==['status']


def test_uncertain_spawn_retains_intent_and_never_retries_claim(monkeypatch,tmp_path):
    from contextlib import nullcontext
    p={'coordinator_receipt':{'path':str(tmp_path/'coordinator.json')},'job':{'path':'job','sha256':'j'},'host':'hhu-server-4','worker':{'path':'worker'},'new_owner':'hhu-server-4:slot-1:0200-source-reconstruction-001'}
    job={'workbook':'0200','command':['python']};calls=[]
    class Client:
        def __init__(self,*a):pass
        def call(self,action,**kw):
            calls.append(action)
            return {'state':'ready','fence':0,'owner':None} if action=='reconcile' else central()
    monkeypatch.setattr(C,'load',lambda row:(p,job,{},{}));monkeypatch.setattr(C,'checked',lambda row:{'url':'test','private_directory':str(tmp_path)})
    monkeypatch.setattr(C,'Client',Client);monkeypatch.setattr(C,'lock',lambda *a,**k:nullcontext())
    monkeypatch.setattr(C,'central_gate',lambda *a:{});monkeypatch.setattr(C,'smoke_gate',lambda *a:None);monkeypatch.setattr(C,'local_custody',lambda *a:{})
    def ssh(*a,**kw):
        if kw.get('execute'):raise RuntimeError('lost response')
        return {'status':'PASS'}
    monkeypatch.setattr(C,'ssh',ssh);p['profile']={'path':'profile','sha256':'p'}
    journal=tmp_path/'launch'
    with pytest.raises(RuntimeError,match='lost response'):C.launch({},journal,execute=True)
    uncertainty=json.loads((journal/'uncertain.json').read_bytes())
    assert uncertainty['phase']=='launch' and uncertainty['automatic_retry'] is False and calls.count('reconcile')==1
    with pytest.raises(ValueError,match='already attempted'):C.launch({},journal,execute=True)
    assert calls.count('reconcile')==1


def test_smoke_must_have_real_verified_collection_and_complete_claim(monkeypatch,tmp_path):
    verification={'schema_version':'independent-published-task-copy-check/v1','workbook':'0270','passed':True,'byte_identical_to_immutable_generation':True,'current_qualification_verified':True,'model_calls':0,'runtime_operations':0,'task_manifest':{'sha256':'manifest'}}
    collection={'schema_version':'harbor-fleet-collection/v1','status':'COLLECTED','workbook':'0270','local_byte_integrity_verified':True,'independent_remote_verification':{'sha256':'verified'},'output':str(tmp_path),'files':{'generation-manifest.json':{'sha256':'manifest'}}}
    p={'job':{'path':'job'},'smoke_verification':{'path':'verification','sha256':'verified'},'smoke_collection':{'path':'collection'}}
    values={'job':{'workbook':'0200'},'verification':verification,'collection':collection}
    monkeypatch.setattr(C,'checked',lambda b:values[b['path']]);monkeypatch.setattr(C.F,'files',lambda p:collection['files'])
    s=central();s['workbooks']=[{'workbook':'0270','state':'complete'}];C.smoke_gate(p,s)
    for key in ['passed','byte_identical_to_immutable_generation','current_qualification_verified']:
        verification[key]=False
        with pytest.raises(ValueError):C.smoke_gate(p,s)
        verification[key]=True
    s['workbooks'][0]['state']='hold'
    with pytest.raises(ValueError,match='incomplete'):C.smoke_gate(p,s)
    s['workbooks'][0]['state']='complete';collection['local_byte_integrity_verified']=False
    with pytest.raises(ValueError,match='collection'):C.smoke_gate(p,s)


def test_unreadable_root_service_cwd_does_not_hide_argv_or_orphans():
    class Denied:
        def __truediv__(self,key):return self
        def resolve(self):raise PermissionError('system process')
    assert C.process_cwd(Denied(),0,1000) is None
    with pytest.raises(ValueError,match='pipeline account'):C.process_cwd(Denied(),1000,1000)
    assert C.owned_argv(['/usr/bin/python','/Users/task/orphan.py'],['/Users/task'])
    s,ps,k=host_fixture();ps.append(dict(pid=22,ppid=1,argv=['python','/Users/task/orphan.py'],task_related=True,pool=None,uid=0,cwd_visible=False))
    with pytest.raises(ValueError,match='orphan'):C.reconcile_host('hhu-server-4',s,ps,k,[],now=101.)

@pytest.mark.parametrize('workbook',['0200','0013','0064','0298','0051','0062'])
def test_all_closed_profiles_preserve_actual_counters_and_validation_scope(workbook):
    h=Path(C.PROFILES[workbook]['path']).parent;p=json.loads((h/(workbook+'-launch-template.json')).read_text());job=C.checked(p['job'])
    profile,plan=C.source_authority(p,job)
    assert profile['next_attempt']==profile['current_attempt']+1
    assert profile['validation_kind']==('source_identity' if workbook in {'0298','0051','0062'} else 'numerical_source')
    if profile['validation_kind']=='source_identity':assert C.checked(profile['native_probe'])['numerical_segmentation_validated'] is False
    assert plan['guards']['inner_seconds']==900 and job['timeout_seconds']==1350


def test_protected_same_uid_service_cwd_uses_actual_privileged_read_not_argv_whitelist(monkeypatch):
    from types import SimpleNamespace as S
    path=Path('/proc/123/cwd');entry=path.parent;old=Path.resolve;exists=Path.exists;calls=[]
    monkeypatch.setattr(Path,'resolve',lambda p,*a,**k:(_ for _ in ()).throw(PermissionError()) if p==path else old(p,*a,**k))
    monkeypatch.setattr(Path,'exists',lambda p:True if p==entry else exists(p))
    def run(args,**kwargs):calls.append(args);return S(returncode=0,stdout='/actual/service/cwd\n')
    monkeypatch.setattr(C.subprocess,'run',run)
    assert C.process_cwd(entry,1000,1000)=='/actual/service/cwd'
    assert calls==[['sudo','-n','readlink','--','/proc/123/cwd']]
    monkeypatch.setattr(C.subprocess,'run',lambda *a,**k:S(returncode=1,stdout=''))
    with pytest.raises(ValueError,match='pipeline account'):C.process_cwd(entry,1000,1000)

@pytest.mark.parametrize('workbook',['0013','0064','0298','0051','0062'])
def test_typed_source_proof_cannot_reset_attempt_or_claim_numerical_pass(workbook,monkeypatch):
    h=Path(C.PROFILES[workbook]['path']).parent;p=json.loads((h/(workbook+'-launch-template.json')).read_text());job=C.checked(p['job']);profile=C.checked(p['profile'])
    original=C.checked;native=copy.deepcopy(original(profile['native_probe']))
    def read(b):return native if b==profile['native_probe'] else original(b)
    monkeypatch.setattr(C,'checked',read)
    native['next_attempt']=1
    with pytest.raises(ValueError,match='preflight'):C.source_authority(p,job)
    native['next_attempt']=profile['next_attempt']
    if profile['validation_kind']=='source_identity':native['numerical_segmentation_validated']=True
    else:native['source_reuse']['source_rebuilt']=True
    with pytest.raises(ValueError):C.source_authority(p,job)

@pytest.mark.parametrize('pool',[False,True])
def test_selected_logical_slot_is_exclusive_for_existing_oneoff_and_pool_owner(pool):
    s,ps,known=host_fixture()
    if pool:
        owner='hhu-server-4/slot-0/generation';s['workbooks'][0]['owner']=owner;ps[0]['argv'][-1]=owner;known['/job/0491.json'][2]['owner']=owner
    with pytest.raises(ValueError,match='logical slot'):C.reconcile_host('hhu-server-4',s,ps,known,[],now=101.,slot=1)
    assert C.reconcile_host('hhu-server-4',s,ps,known,[],now=101.,slot=2)['worker_count']==1

def test_selected_smoke_v1_unchanged_and_v2_closed():
    assert C.selected_smoke({})=='0270'
    assert C.selected_smoke({'schema_version':C.SCHEMA})=='0270'
    for wb in ('0029','0270'):assert C.selected_smoke({'schema_version':C.SCHEMA_V2,'smoke_workbook':wb})==wb
    for p in ({'schema_version':C.SCHEMA,'smoke_workbook':'0029'},{'schema_version':C.SCHEMA_V2},{'schema_version':C.SCHEMA_V2,'smoke_workbook':'0013'},{'schema_version':'unknown'}):
        with pytest.raises(ValueError):C.selected_smoke(p)

@pytest.mark.parametrize('smoke',['0029','0270'])
def test_selected_smoke_requires_its_actual_independent_collection_and_unique_complete_claim(smoke,monkeypatch,tmp_path):
    verification={'schema_version':'independent-published-task-copy-check/v1','workbook':smoke,'passed':True,'byte_identical_to_immutable_generation':True,'current_qualification_verified':True,'model_calls':0,'runtime_operations':0,'task_manifest':{'sha256':'manifest'}}
    collection={'schema_version':'harbor-fleet-collection/v1','status':'COLLECTED','workbook':smoke,'local_byte_integrity_verified':True,'independent_remote_verification':{'sha256':'verified'},'output':str(tmp_path),'files':{'generation-manifest.json':{'sha256':'manifest'}}}
    p={'schema_version':C.SCHEMA_V2,'smoke_workbook':smoke,'job':{'path':'job'},'smoke_verification':{'path':'verification','sha256':'verified'},'smoke_collection':{'path':'collection'}}
    values={'job':{'workbook':'0200'},'verification':verification,'collection':collection}
    monkeypatch.setattr(C,'checked',lambda b:values[b['path']]);monkeypatch.setattr(C.F,'files',lambda p:collection['files'])
    s=active_operation();s['workbooks'].append({'workbook':smoke,'state':'complete'})
    manifest=tmp_path/'generation-manifest.json';manifest.write_text(json.dumps({'identity':{'workbook_id':smoke}}))
    C.smoke_gate(p,s) # Known other live model remains allowed, as in004.
    other='0270' if smoke=='0029' else '0029'
    verification['workbook']=other
    with pytest.raises(ValueError):C.smoke_gate(p,s)
    verification['workbook']=smoke;collection['workbook']=other
    with pytest.raises(ValueError):C.smoke_gate(p,s)
    collection['workbook']=smoke
    manifest.write_text(json.dumps({'identity':{'workbook_id':other}}))
    with pytest.raises(ValueError,match='generation workbook'):C.smoke_gate(p,s)
    manifest.write_text(json.dumps({'identity':{'workbook_id':smoke}}))
    s['workbooks'][-1]['state']='hold'
    with pytest.raises(ValueError):C.smoke_gate(p,s)
    s['workbooks'][-1]['state']='complete';s['workbooks'].append({'workbook':smoke,'state':'complete'})
    with pytest.raises(ValueError):C.smoke_gate(p,s)

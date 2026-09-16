import copy
from types import SimpleNamespace
import pytest
from scripts.fleet import fresh_publication as F

def fixture():
    helper=dict(path='/original/author_workbook.py',sha256='414a278fe2bc52858788e1253af18d4989f0275869e86fbab6b071a13b241666')
    driver=dict(path='/code/dispatcher.py',sha256='d');preparer=dict(path='/original/preparer.py',sha256='p');row=dict(path='/fresh',sha256='f');config=dict(path='/config',sha256='c')
    selected=dict(schema_version='harbor-fleet-fresh-plan/v1',workbook='0010',code_root='/code',dispatcher=driver,preparer=preparer)
    plan=dict(fresh_authoring=row,workbook='0010',code_root='/code',author_helper=helper)
    ready=dict(plan=row,workbook='0010',config=config);job=dict(driver=driver,config=config);calls=[]
    def require(ok,message):
        if not ok:raise ValueError(message)
    pub=SimpleNamespace(checked=lambda r:selected,binding=lambda p:preparer,require=require)
    pinned=SimpleNamespace(load=lambda *a,**k:calls.append((a,k))or'actual-pinned-dispatcher')
    return pub,pinned,plan,ready,job,selected,calls

def test_exact_original_helper_dispatcher_selected():
    pub,pinned,plan,ready,job,selected,calls=fixture()
    assert F.dispatcher(pub,pinned,plan,ready,job)=='actual-pinned-dispatcher'
    assert calls[0][0][1:]==(job['driver'],plan['author_helper'])

@pytest.mark.parametrize('target,key,value',[('selected','workbook','foreign'),('selected','code_root','other'),('selected','schema_version','other'),('ready','plan',{}),('job','driver',{}),('job','config',{}),('plan','author_helper',dict(path='/unknown',sha256='x'))])
def test_detached_import_authority_rejected_before_dispatcher(target,key,value):
    pub,pinned,plan,ready,job,selected,calls=fixture();locals()[target][key]=value
    with pytest.raises(ValueError):F.dispatcher(pub,pinned,plan,ready,job)
    assert not calls

@pytest.fixture
def normalized_publication(tmp_path):
    pub,pinned,plan,ready,job,selected,calls=fixture()
    row=dict(path='/normalization-plan',sha256='n');helper=dict(path='/reader.py',sha256='r')
    plan['completed_normalization']=dict(reader=helper,normalization_context=dict(helper=dict(path='/normal.py',sha256='m'),plan=row))
    ready.update(status='PUBLICATION_READY',plan=row,job=dict(path='/actual-job',sha256='j'),outcome=dict(path='/final-outcome',sha256='o'),checkpoint=dict(path='/final-result',sha256='f'),publication_performed=False,automatic_retry=False)
    root=__import__('pathlib').Path('/code');events=[]
    env=(SimpleNamespace(SECONDS=3300,MEMORY=8589934592),None,None,None,None,dict(workbook='0010'),root,None)
    D=SimpleNamespace(setup=lambda j:env,successful=lambda row,env: (ready['checkpoint'],dict(status='PUBLICATION_READY'))if row==ready['outcome']else(None,dict(status='STEP_COMPLETE',publication_ready=False)))
    M=SimpleNamespace(authoring_context=lambda p:(selected,job,root,job['config'],('refresh','bridge')),completed_refresh=lambda *a:events.append('original-refresh')or dict(status='IMPORTED'))
    pub.load=lambda name,row:M;pinned.load=lambda *a,**k:D
    return pub,pinned,plan,ready,job,M,D,events


def test_normalized_publication_reads_original_refresh_and_final_ready(normalized_publication):
    pub,pinned,plan,ready,job,M,D,events=normalized_publication
    assert F.dispatcher(pub,pinned,plan,ready,job)is D
    assert events==['original-refresh']

@pytest.mark.parametrize('kind',['unready','plan','config','root','final-result','refresh'])
def test_normalized_publication_rejects_detached_completed_history(normalized_publication,kind):
    pub,pinned,plan,ready,job,M,D,events=normalized_publication
    if kind=='unready':ready['status']='RECONCILIATION_REQUIRED'
    elif kind=='plan':ready['plan']={}
    elif kind=='config':ready['config']={}
    elif kind=='root':plan['code_root']='/foreign'
    elif kind=='final-result':D.successful=lambda *a:(None,dict(status='STEP_COMPLETE',publication_ready=False))
    else:M.completed_refresh=lambda *a:dict(status='UNRESOLVED')
    with pytest.raises(ValueError):F.dispatcher(pub,pinned,plan,ready,job)

@pytest.fixture
def semantic_publication(tmp_path):
    from pathlib import Path
    pub,pinned,plan,ready,old,_,events=fixture()
    def b(p):return dict(path=str(p),sha256=str(p))
    row=b('/semantic-plan');reader=b('/semantic-reader');home=Path('/semantic-output')
    selected=dict(helper=reader,workbook='0010',source=dict(sha256='source'),
        retained_context={},completed_takeover={},fresh_plan=plan['fresh_authoring'],output=str(home))
    plan.update(semantic_continuation=dict(reader=reader,plan=row),source_sha256='source')
    first=b(home/'first-step/outcome.json');guardrow=b(home/'permit-resource.json');intent=b(home/'intent.json')
    ready.update(status='PUBLICATION_READY',continuation=row,outcome=b('/final-outcome'),checkpoint=b('/final-result'),
        permit_guard=guardrow,publication_performed=False,automatic_retry=False)
    job={**old,'outcome':first,'operations':'/run/dispatcher-v2/semantic-output'}
    env=(None,None,None,None,None,dict(run_root='/run'),Path('/code'),None)
    manager=SimpleNamespace(__exit__=lambda *args:events.append('restore'))
    D=SimpleNamespace(successful=lambda r,e:(ready['checkpoint'],dict(status='PUBLICATION_READY'))
        if r==ready['outcome']else(None,dict(status='STEP_COMPLETE',next_step='dialogue-paraphraser')))
    M=SimpleNamespace(context=lambda *args,**kwargs:(selected,{},old,D,env,None,None,None,None,None,SimpleNamespace(manager=manager)))
    guard=dict(returncode=0,failure=None,models_dispatched=0,seconds=3300,memory_bytes=8589934592,intent=intent)
    values={row['path']:selected,guardrow['path']:guard,intent['path']:dict(plan=row,helper=reader,automatic_retry=False)}
    pub.checked=lambda r:values[r['path']];pub.binding=b;pub.load=lambda *args:M
    return pub,pinned,plan,ready,job,selected,D,guard,events,manager


def test_actual_semantic_remaining_job_and_original_ready_keep_history_installed(semantic_publication):
    pub,pinned,p,ready,job,selected,D,guard,events,manager=semantic_publication
    assert F.dispatcher(pub,pinned,p,ready,job)is D
    assert D._semantic_publication_manager is manager and 'restore'not in events


@pytest.mark.parametrize('defect',['source','continuation','guard','intent','first','ready','job','root'])
def test_semantic_publication_rejects_detached_receipts_and_restores_on_failure(semantic_publication,defect):
    pub,pinned,p,ready,job,selected,D,guard,events,manager=semantic_publication
    if defect=='source':p['source_sha256']='foreign'
    elif defect=='continuation':ready['continuation']={}
    elif defect=='guard':guard['returncode']=1
    elif defect=='intent':guard['models_dispatched']=1
    elif defect=='first':D.successful=lambda r,e:(ready['checkpoint'],dict(status='PUBLICATION_READY'))
    elif defect=='ready':D.successful=lambda r,e:(None,dict(status='STEP_COMPLETE',next_step='dialogue-paraphraser'))
    elif defect=='job':job['outcome']={}
    else:p['code_root']='/foreign'
    with pytest.raises(ValueError):F.dispatcher(pub,pinned,p,ready,job)
    if defect not in ('source','continuation'):assert events[-1]=='restore'
    assert not hasattr(D,'_semantic_publication_manager')


def test_semantic_history_is_separate_and_never_projects_fresh_ids(semantic_publication):
    pub,_,p,_,_,_,_,_,_,_=semantic_publication
    events=[]
    row=F.semantic_history_binding(pub,p)
    assert row==p['semantic_continuation']['plan']
    assert F.history_prefix(p)=='HARBOR_FLEET_RETAINED_HISTORY_'
    F.history_survey(pub,p,row,lambda *a:events.append(a))
    assert events==[]

@pytest.mark.parametrize('key',['history','continuation_history','source_continuation'])
def test_semantic_history_rejects_mixed_fresh_authority(semantic_publication,key):
    pub,_,p,*_=semantic_publication;p[key]={}
    with pytest.raises(ValueError):F.semantic_history_binding(pub,p)


def test_default_fresh_survey_preserves_exact_roots_and_known_ids():
    from pathlib import Path
    calls=[];row=dict(path='/history',sha256='h')
    pub=SimpleNamespace(checked=lambda r:dict(roots=['/one','/two'],known_ids=['0010']))
    F.history_survey(pub,{},row,lambda *a:calls.append(a))
    assert calls==[((Path('/one'),Path('/two')),{'0010'})]
    assert F.history_prefix({})=='HARBOR_FLEET_HISTORY_'
    assert F.history_prefix({'continuation_history':row})=='HARBOR_FLEET_CONTINUATION_HISTORY_'


def test_frozen_builder_changes_only_declared_history_and_child_functions(tmp_path,monkeypatch):
    import ast,importlib.util,json,os,sys
    from pathlib import Path
    from scripts.fleet import build_fresh_publication as B
    original=Path('diagnostics/vm-fleet-20260912/control/0029-publication-003/collector.py').absolute()
    row=B.build(tmp_path/'frozen',original);manifest=json.loads(Path(row['path']).read_bytes())
    path=Path(manifest['publisher']['path']);spec=importlib.util.spec_from_file_location('test_frozen_semantic_publisher',path);m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
    def f(p):return {n.name:ast.dump(n,include_attributes=False)for n in ast.parse(p.read_text()).body if isinstance(n,ast.FunctionDef)}
    before,after=f(original),f(path)
    assert [k for k in before if before[k]!=after[k]]==['history_binding','publication_environment','remote_child']
    history=dict(path='/semantic',sha256='s');p=dict(workbook='0010',code_root='/code',semantic_continuation={})
    m.fresh_publication.semantic_history_binding=lambda pub,plan:history
    monkeypatch.setenv('OPENAI_API_KEY','do-not-forward');monkeypatch.setenv('HARBOR_FLEET_HISTORY_PATH','stale')
    env=m.publication_environment(p,dict(workbook='0010',owner='owner',token='token',fence=1),action='remote',url='http://localhost',token_file='/token')
    assert env['HARBOR_FLEET_RETAINED_HISTORY_PATH']=='/semantic'
    assert 'HARBOR_FLEET_HISTORY_PATH'not in env and 'OPENAI_API_KEY'not in env
    assert env['HARBOR_FLEET_PUBLICATION_ONLY']=='1'


def test_retained_history_does_not_import_absent_fresh_module(monkeypatch):
    import builtins
    original=builtins.__import__
    def guarded(name,*args,**kwargs):
        if name in ('scripts.fleet.history','scripts.fleet.continuation_history'):
            raise AssertionError('retained context has no fresh-history module')
        return original(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',guarded)
    assert F.selected_history_survey({'semantic_continuation':{}})is None
    with pytest.raises(AssertionError):F.selected_history_survey({})
    with pytest.raises(AssertionError):F.selected_history_survey({'continuation_history':{}})


@pytest.fixture
def legacy_publication():
    history=dict(path='/legacy',sha256='legacy');fresh=dict(path='/fresh',sha256='fresh')
    certificate=dict(schema_version='harbor-legacy-completed-research/v1',workbook='0565',original=dict(sha256='source'),home='/retained/0565',history_roots=['/old'],known_ids=['0565'])
    selected=dict(schema_version='harbor-fleet-fresh-plan/v1',workbook='0565',code_root='/code',home='/retained/0565/authoring',legacy_research_history=history,reconstruction=dict(mode='existing',generated_curation=None,handoff=dict(path='/handoff',sha256='h')))
    plan=dict(legacy_research_history=history,fresh_authoring=fresh,workbook='0565',code_root='/code',source_sha256='source')
    values={'/legacy':certificate,'/fresh':selected,'/handoff':{'workbook':'0565'}}
    def require(ok,message):
        if not ok:raise ValueError(message)
    return SimpleNamespace(require=require,checked=lambda r:values[r['path']]),plan,selected,certificate


def test_legacy_publication_runs_original_history_and_completed_source_proof(legacy_publication,monkeypatch):
    from pathlib import Path
    from scripts.batch import harbor_legacy_research_history as L
    pub,p,selected,certificate=legacy_publication;events=[]
    monkeypatch.setattr(L,'validate',lambda row,**kw:events.append(('history',row,kw))or certificate)
    monkeypatch.setattr(L,'source_handoff',lambda *a:events.append(('source',a)))
    row=F.legacy_history_binding(pub,p)
    F.history_survey(pub,p,row,lambda *a:events.append(('live',a)))
    assert [x[0]for x in events]==['history','source','live']
    assert events[-1][1]==((Path('/old'),),{'0565'})
    assert F.history_prefix(p)=='HARBOR_FLEET_LEGACY_RESEARCH_'
    assert F.selected_history_survey(p)is L.projected_survey


@pytest.mark.parametrize('defect',['mixed','source','workbook','code','authority','home','curation','mode'])
def test_legacy_publication_rejects_detached_history(legacy_publication,defect):
    pub,p,s,c=legacy_publication
    if defect=='mixed':p['history']={}
    elif defect=='source':p['source_sha256']='foreign'
    elif defect=='workbook':s['workbook']='0570'
    elif defect=='code':s['code_root']='/foreign'
    elif defect=='authority':s['legacy_research_history']={}
    elif defect=='home':s['home']='/foreign'
    elif defect=='curation':s['reconstruction']['generated_curation']={}
    else:s['reconstruction']['mode']='new'
    with pytest.raises(ValueError):F.legacy_history_binding(pub,p)

@pytest.mark.parametrize('kind',['valid','source_only','snapshot'])
def test_package_publication_requires_explicit_author_successor(legacy_publication,kind):
    pub,p,selected,certificate=legacy_publication;old=pub.checked
    certificate['schema_version']='harbor-legacy-package-authoring/v1'
    certificate['authoring']={'snapshot':{'path':'/snapshot','sha256':'s'}}
    pub.checked=lambda r:({'root':'/foreign'if kind=='snapshot'else'/code'}if r['path']=='/snapshot'else old(r))
    if kind=='source_only':certificate['schema_version']='harbor-legacy-completed-package/v1'
    if kind=='valid':assert F.legacy_history_binding(pub,p)==p['legacy_research_history']
    else:
        with pytest.raises(ValueError):F.legacy_history_binding(pub,p)


@pytest.mark.parametrize('kind',['valid','source_only','mcp_source_only','snapshot'])
def test_staged_publication_retains_author_snapshot_and_original_history(legacy_publication,monkeypatch,kind):
    from scripts.batch import harbor_legacy_research_history as L
    pub,p,selected,certificate=legacy_publication;old=pub.checked;events=[]
    certificate['schema_version']='harbor-legacy-staged-authoring/v1'
    certificate['authoring']={'snapshot':{'path':'/snapshot','sha256':'s'}}
    pub.checked=lambda r:({'root':'/foreign'if kind=='snapshot'else'/code'}if r['path']=='/snapshot'else old(r))
    if kind=='source_only':certificate['schema_version']='harbor-legacy-staged-source/v1'
    if kind=='mcp_source_only':certificate['schema_version']='harbor-legacy-staged-mcp-source/v1'
    if kind!='valid':
        with pytest.raises(ValueError):F.legacy_history_binding(pub,p)
        return
    monkeypatch.setattr(L,'validate',lambda *a,**k:events.append('original_history')or certificate)
    monkeypatch.setattr(L,'source_handoff',lambda *a:events.append('original_source'))
    row=F.legacy_history_binding(pub,p)
    F.history_survey(pub,p,row,lambda *a:events.append('original_survey'))
    assert events==['original_history','original_source','original_survey']


def test_ordinary_semantic_publication_keeps_exact_fresh_context(semantic_publication):
    pub,pinned,p,ready,job,selected,D,guard,events,manager=semantic_publication
    del selected['retained_context']
    selected['original_job']=dict(path='/original-job',sha256='j')
    p['history']=dict(path='/actual-fresh-history',sha256='h')
    M=pub.load(None,None)
    expected=dict(function='load',args=[{k:selected[k]for k in ('workbook','fresh_plan','original_job')}],kwargs={})
    calls=[]
    M.lease_context=lambda value:calls.append(value)or(selected['helper'],expected)
    assert F.dispatcher(pub,pinned,p,ready,job)is D
    assert calls==[selected] and D._semantic_publication_manager is manager
    assert F.history_prefix(p)=='HARBOR_FLEET_HISTORY_'


@pytest.mark.parametrize('defect',['missing-history','retained-mixed','foreign-call','foreign-helper'])
def test_ordinary_semantic_publication_rejects_unbound_history_context(semantic_publication,defect):
    pub,pinned,p,ready,job,selected,D,guard,events,manager=semantic_publication
    selected['original_job']=dict(path='/original-job',sha256='j')
    if defect!='retained-mixed':del selected['retained_context']
    if defect!='missing-history':p['history']=dict(path='/history',sha256='h')
    M=pub.load(None,None)
    entry={}if defect=='foreign-helper'else selected['helper']
    call=dict(function='load',args=[{k:selected[k]for k in ('workbook','fresh_plan','original_job')}],kwargs={})
    if defect=='foreign-call':call['kwargs']={'projection':'foreign'}
    M.lease_context=lambda value:(entry,call)
    with pytest.raises(ValueError):F.dispatcher(pub,pinned,p,ready,job)
    assert not hasattr(D,'_semantic_publication_manager')


def test_ordinary_semantic_builder_uses_original_fresh_survey(tmp_path):
    import importlib.util,json,sys
    from pathlib import Path
    from scripts.fleet import build_fresh_publication as B
    original=Path('diagnostics/vm-fleet-20260912/control/0029-publication-003/collector.py').absolute()
    row=B.build(tmp_path/'ordinary-semantic',original);manifest=json.loads(Path(row['path']).read_bytes())
    path=Path(manifest['publisher']['path']);spec=importlib.util.spec_from_file_location('test_ordinary_semantic_publisher',path);m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
    history=dict(path='/original-history',sha256='h');p=dict(workbook='0415',history=history,semantic_continuation={})
    cert=dict(schema_version='fleet-central-history/v1',fresh_ids=['0415'],known_ids=['0415'],roots=['/original'],scan_roots=['/original'],excluded={},histories={})
    m.checked=lambda r:cert
    m.fresh_publication.semantic_history_binding=lambda *a:pytest.fail('ordinary fresh history must use original validator')
    assert m.history_binding(p)==history
    calls=[];F.history_survey(m,p,history,lambda *a:calls.append(a))
    assert calls==[((Path('/original'),),{'0415'})]
    cert['histories']['0415']=['unknown-history']
    with pytest.raises(ValueError):m.history_binding(p)


def test_completed_semantic_tail_qualifies_original_writer_and_exact_review_handoff(semantic_publication):
    from pathlib import Path
    pub,pinned,p,ready,job,selected,D,guard,events,manager=semantic_publication
    del selected['retained_context'];selected['original_job']=dict(path='/original-job',sha256='j')
    p['history']=dict(path='/fresh-history',sha256='h')
    M=pub.load(None,None)
    M.lease_context=lambda value:(selected['helper'],dict(function='load',args=[{k:selected[k]for k in ('workbook','fresh_plan','original_job')}],kwargs={}))
    b=pub.binding;a=p['semantic_continuation'];tail=dict(helper=b('/tail-reader'),plan=b('/tail-plan'));a['tail']=tail
    prior_job={k:v for k,v in job.items()if k not in ('outcome','operations')}
    tail_selected=dict(helper=tail['helper'],semantic_context=dict(helper=a['reader'],plan=a['plan']),output='/tail-output',outcome=b('/completed-review'))
    env=(None,None,None,None,None,dict(run_root='/run'),Path('/code'),None)
    D._semantic_tail_manager=manager
    Q=SimpleNamespace(tail_context=lambda *args,**kw:(tail_selected,{},prior_job,D,env,None,None))
    pub.load=lambda name,row:Q if name=='publication_completed_semantic_tail'else M
    old_read=pub.checked;tail_intent=b('/tail-output/intent.json')
    pub.checked=lambda row:tail_selected if row==tail['plan']else(dict(plan=tail['plan'],prior_completed=tail_selected['outcome'],automatic_retry=False)if row==tail_intent else old_read(row))
    ready.update(continuation=tail['plan'],intent=tail_intent);ready.pop('permit_guard')
    job.update(outcome=tail_selected['outcome'],operations='/run/dispatcher-v2/tail-output')
    assert F.dispatcher(pub,pinned,p,ready,job)is D
    assert D._semantic_publication_manager is manager and 'restore'not in events
    ready['intent']={}
    with pytest.raises(ValueError):F.dispatcher(pub,pinned,p,ready,job)
    assert events[-1]=='restore'

@pytest.mark.parametrize('route',['source_revision','semantic_continuation','disclosure_continuation'])
def test_nested_publication_loads_retained_context_before_model_guard(monkeypatch,route):
    """The real nested entry must not pre-import a foreign historical snapshot."""
    import sys
    events=[];plan={'code_root':'/selected/code','workbook':'0187',route:{'retained':True}}
    row={'path':'/plan','sha256':'p'};history={'path':'/history','sha256':'h'}
    ready={'job':{'path':'/job','sha256':'j'},'outcome':'actual-outcome','checkpoint':'actual-ready'}
    request={'original':'request'};intent={'path':'/intent','sha256':'i'}
    values={'/plan':plan,'/job':{},'/intent':{'request':request}}
    def require(ok,message):
        if not ok:raise ValueError(message)
    def guard():
        assert events==['retained-context']
        events.append('models-forbidden')
    def execute(bound):
        assert bound==intent and events==['retained-context','models-forbidden','adapter-validated']
        events.append('original-execute');return {'status':'original-result'}
    P=SimpleNamespace(encode=lambda x:x,execute=execute)
    env=(None,None,P)
    D=SimpleNamespace(setup=lambda job:env,successful=lambda out,e:('actual-ready',{'status':'PUBLICATION_READY'}),publication_request=lambda e,v:request)
    def dispatcher(*args):events.append('retained-context');return D
    pub=SimpleNamespace(checked=lambda r:values[r['path']],history_binding=lambda p:history,require=require,publication_only=guard,pinned_dispatcher=None,binding=lambda p:dict(path=str(p),sha256='x'),__file__='/wrapper')
    monkeypatch.setattr(F,'history_prefix',lambda p:'TEST_HISTORY_')
    monkeypatch.setattr(F,'history_survey',lambda *a:None)
    monkeypatch.setattr(F,'selected_history_survey',lambda p:None)
    monkeypatch.setattr(F,'ready_value',lambda *a:ready)
    monkeypatch.setattr(F,'dispatcher',dispatcher)
    monkeypatch.setattr(F,'install_validation',lambda *a:None)
    monkeypatch.setattr(F,'projected_request',lambda pub,p,r,plan:r)
    monkeypatch.setattr(F,'reader_evidence',lambda *a:[])
    monkeypatch.setenv('TEST_HISTORY_PATH','/history');monkeypatch.setenv('TEST_HISTORY_SHA256','h')
    monkeypatch.setenv('HARBOR_FLEET_WORKBOOK','0187');monkeypatch.delenv('HARBOR_FLEET_CONTINUATION_READ_ONLY',raising=False)
    monkeypatch.setattr(sys,'argv',['wrapper','publication-child','--plan','/plan','--plan-sha256','p','--intent','/intent','--intent-sha256','i','--adapter','/sidecar'])
    monkeypatch.setattr(sys,'path',list(sys.path))
    F.publication_child(pub,SimpleNamespace(validate=lambda *a,**k:events.append('adapter-validated')))
    assert events[-1]=='original-execute'

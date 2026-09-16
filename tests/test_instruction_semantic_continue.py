import copy
import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from scripts.fleet import instruction_semantic_continue as M


@dataclasses.dataclass
class Lease:
    owner: str='original'
    token: str='lease-token'
    revision: int=16
    sha256: str='tracker-sha'


@pytest.fixture
def failure(tmp_path):
    def put(name,value):
        p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text(json.dumps(value));return M.binding(p)
    code=put('instruction.py',{});controls={'control':'original'}; prior=put('prior.json',{})
    descriptor=put('descriptor.json',{});selection={'selected':'unchanged'};lease=Lease()
    failed=dict(status='DIALOGUE_REPAIR_REQUIRED',step='dialogue-review',selection=selection,lease=dataclasses.asdict(lease))
    blocked=put('blocked.json',failed)
    value=dict(status=failed['status'],next_step=failed['step'],publication_performed=False,checkpoint=blocked,descriptor=descriptor,selection=selection,lease=dataclasses.asdict(lease),evidence=[prior])
    result=put('guard/result.json',value)
    intent=put('guard/intent.json',dict(schema_version='original-instruction',code=code,controls=controls,action='step',arguments=dict(descriptor=descriptor,checkpoint=prior),timeout_seconds=3300,memory_limit_bytes=8589934592))
    log=put('guard/step.log',{})
    resource=put('guard/resource.json',dict(returncode=0,failure=None,intent=intent,log=log))
    outcome=put('guard/outcome.json',dict(status='COMPLETED_OPERATION',automatic_retry=False,result=result,resource=resource))
    p=dict(outcome=outcome,result=result,descriptor=descriptor,blocked=blocked)
    I=NS(SCHEMA='original-instruction',__file__=code['path'],base=NS(encode=lambda x:x,decode=lambda x:Lease(**x),MEMORY=8589934592),codec=NS(decode=lambda x:x),guard_seconds=lambda *a:3300)
    return p,I,controls,put


def test_original_completed_rejection_is_read_without_mutation(failure,tmp_path):
    p,I,controls,_=failure
    before={x:M.binding(x) for x in tmp_path.rglob('*') if x.is_file()}
    value,selection,lease=M.completed_failure(p,I,controls)
    assert value['status']=='DIALOGUE_REPAIR_REQUIRED' and lease.revision==16
    assert before=={x:M.binding(x) for x in tmp_path.rglob('*') if x.is_file()}


@pytest.mark.parametrize('change',['uncertain','nonzero','missing-result','foreign-descriptor','wrong-step','different-lease','relaxed-guard','different-code','changed-predecessor'])
def test_non_equivalent_or_uncertain_review_cannot_authorize_retry(failure,change):
    p,I,controls,put=failure
    out=M.read(p['outcome']);g=M.read(out['resource']);intent=M.read(g['intent']);v=M.read(p['result']);failed=M.read(p['blocked'])
    if change=='uncertain':g['failure']='UnknownOutcome'
    elif change=='nonzero':g['returncode']=1
    elif change=='missing-result':out['result']=put('foreign-result.json',v)
    elif change=='foreign-descriptor':v['descriptor']=put('foreign-descriptor.json',{})
    elif change=='wrong-step':failed['step']=v['next_step']='dialogue-paraphraser'
    elif change=='different-lease':failed['lease']['revision']+=1
    elif change=='relaxed-guard':intent['timeout_seconds']=4000
    elif change=='different-code':intent['code']=put('foreign-code.py',{})
    elif change=='changed-predecessor':v['evidence']=[put('foreign-prior.json',{})]
    p['blocked']=put('blocked.json',failed);v['checkpoint']=p['blocked'];p['result']=put('guard/result.json',v)
    if change!='missing-result':out['result']=p['result']
    g['intent']=put('guard/intent.json',intent);out['resource']=put('guard/resource.json',g);p['outcome']=put('guard/outcome.json',out)
    with pytest.raises(ValueError):M.completed_failure(p,I,controls)


def test_changed_bytes_never_rebound_implicitly(failure):
    p,I,controls,_=failure
    Path(p['blocked']['path']).write_text('{}')
    with pytest.raises(ValueError,match='evidence changed'):M.completed_failure(p,I,controls)


def test_original_guard_without_optional_retry_field_is_supported(failure):
    p,I,controls,put=failure
    out=M.read(p['outcome']);del out['automatic_retry'];p['outcome']=put('guard/outcome.json',out)
    assert M.completed_failure(p,I,controls)[0]['status']=='DIALOGUE_REPAIR_REQUIRED'


def test_once_only_journal_preserves_previous_attempt(tmp_path):
    p=tmp_path/'intent.json';first=M.keep(p,{'attempt':1})
    with pytest.raises(FileExistsError):M.keep(p,{'attempt':2})
    assert M.read(first)=={'attempt':1}


def test_dispatcher_namespace_is_preflighted_under_workbook_not_outer_worker(tmp_path):
    from scripts.fleet import advance_authoring_v8 as D
    config={'run_root':str(tmp_path/'original-authoring')}
    outer=tmp_path/'fleet'/'semantic-continuation'
    selected=M.operations_for(D,config,outer)
    assert selected==tmp_path/'original-authoring/dispatcher-v2/semantic-continuation'
    assert not selected.exists()
    with pytest.raises(ValueError,match='dedicated workbook namespace'):
        D.operation_namespace(config,str(outer/'operations'))
    selected.mkdir(parents=True)
    with pytest.raises(ValueError,match='no replay'):
        M.operations_for(D,config,outer)


def test_tail_uses_completed_guard_without_rerunning_writer_or_allowance(tmp_path,monkeypatch):
    output=tmp_path/'tail';operations=tmp_path/'authoring/dispatcher-v2/tail'
    prior=tmp_path/'first-outcome.json';prior.write_text('{}');old=M.binding(prior)
    old_job={'outcome':{'path':'old'},'config':{'path':'config'},'operations':'old','maximum_steps':64,'models':True,'commands':True,'containers':True,'publish':False}
    p={'output':str(output),'operations':str(operations),'outcome':old,'fresh_plan':{'path':'fresh'},'workbook':'1234'}
    calls=[]
    def run(job):
        current=M.read(job);calls.append(current)
        assert current=={**old_job,'outcome':old,'operations':str(operations)}
        operations.mkdir(parents=True);M.keep(operations/'hold.json',{'outcome':old})
    D=NS(run=run,successful=lambda *a:(old,{'status':'PUBLICATION_READY'}))
    def context(row):
        if output.exists():raise ValueError('fresh continuation output required')
        return p,{},old_job,D,(None,None,None,None,None,None,tmp_path,None),old,{'next_step':'dialogue-paraphraser'}
    monkeypatch.setattr(M,'tail_context',context);monkeypatch.setattr(M,'custody',lambda *a:None)
    monkeypatch.setattr(M,'permit_child',lambda *a:pytest.fail('must not authorize or repeat writer'))
    row={'path':'selected'};result=M.tail_run(row)
    assert M.read(result)['status']=='PUBLICATION_READY' and len(calls)==1
    with pytest.raises(ValueError,match='fresh continuation'):M.tail_run(row)
    assert len(calls)==1 and prior.read_text()=='{}'

@pytest.fixture
def normalized_context(tmp_path,monkeypatch):
    def put(name,value):
        p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value));return M.binding(p)
    olddriver=put('old/driver.py',{});newdriver=put('new/driver.py',{})
    oldconfig=put('fresh/authoring/config.json',{'run_root':str(tmp_path/'authoring')})
    newconfig=put('selected-config.json',{'run_root':str(tmp_path/'authoring'),'code':{'selected':True}})
    original=dict(driver=olddriver,config=oldconfig,publish=False,models=True,commands=True,containers=True,outcome={'old':True},operations='first')
    oldjob=put('fresh/authoring-job.json',original);selected={**original,'driver':newdriver,'config':newconfig}
    home=tmp_path/'normalization';home.mkdir();helper=put('continue.py',{})
    n=dict(schema_version='terminal-v1',workbook='0078',home=str(home),code_root=str(tmp_path/'new'),original_job=oldjob,terminal_rejection={'selected':True})
    plan=put('normalization-plan.json',n)
    put('normalization/intent.json',dict(plan=plan,driver=helper));put('normalization/result.json',dict(plan=plan,status='RECONCILIATION_REQUIRED',publication_performed=False,automatic_retry=False))
    bridge=put('normalization/bridge-step/outcome.json',{});put('normalization/refresh/outcome.json',{})
    job={**selected,'outcome':bridge,'operations':str(tmp_path/'authoring/dispatcher-v2/normalization')}
    jobrow=put('normalization/authoring-job.json',job)
    fresh=put('fresh-plan.json',dict(schema_version='harbor-fleet-fresh-plan/v1',workbook='0078',home=str(tmp_path/'fresh'),code_root=str(tmp_path/'old'),dispatcher=olddriver))
    calls=[]
    def successor(value,old):
        assert value==n and old==original;calls.append(True);return selected
    monkeypatch.setattr(M,'module',lambda name,row:NS(SCHEMA='terminal-v1',successor=successor))
    p=dict(workbook='0078',fresh_plan=fresh,original_job=jobrow,normalization_context=dict(helper=helper,plan=plan))
    return p,put,calls


def test_retained_normalization_uses_exact_completed_handoff(normalized_context):
    p,put,calls=normalized_context
    fresh,job,root,config,handoff=M.authoring_context(p)
    assert root.name=='new' and len(handoff)==2 and calls==[True]
    assert job['publish']is False and job['models']is True

@pytest.mark.parametrize('change',['job-policy','job-outcome','job-operations','old-config','plan-identity','helper-identity','extra-field'])
def test_retained_normalization_rejects_unbound_or_modified_history(normalized_context,change):
    p,put,calls=normalized_context;job=M.read(p['original_job']);context=p['normalization_context'];n=M.read(context['plan'])
    if change=='job-policy':job['models']=False
    elif change=='job-outcome':job['outcome']=put('foreign-outcome.json',{})
    elif change=='job-operations':job['operations']='wrong'
    elif change=='old-config':
        fresh=M.read(p['fresh_plan']);fresh['home']+='/wrong';p['fresh_plan']=put('fresh-plan.json',fresh)
    elif change=='plan-identity':put('normalization/intent.json',dict(plan=put('foreign-plan.json',n),driver=context['helper']))
    elif change=='helper-identity':put('normalization/intent.json',dict(plan=context['plan'],driver=put('foreign-helper.py',{})))
    else:context['unknown']=True
    if change.startswith('job-'):p['original_job']=put('normalization/authoring-job.json',job)
    with pytest.raises((ValueError,FileNotFoundError)):M.authoring_context(p)

@pytest.fixture
def refresh(tmp_path):
    def put(name,value):
        f=tmp_path/name;f.parent.mkdir(parents=True,exist_ok=True);f.write_text(json.dumps(value,sort_keys=True));return M.binding(f)
    home=tmp_path/'normal'; code=put('campaign.py',{});exe=put('python',{})
    n=dict(home=str(home),research=put('research.json',{}));p={'normalization_context':{'plan':put('normal-plan.json',n)}}
    args=dict(legacy=put('normal/legacy.json',{}),checkpoint=n['research'],current=put('normal/current.json',{}),lease=put('normal/lease.json',{}))
    intent=dict(schema_version='harbor-coordinator-step/v1',action='refresh',arguments=args,dispatch=False,code=code,controls={},timeout_seconds=3300,memory_limit_bytes=8589934592,executable=exe)
    ib=put('normal/refresh/intent.json',intent);arguments=list(M.read(ib)['arguments'].values());proof=put('proof.json',{});commit=put('commit.json',{})
    descriptor=put('normal/refresh/descriptor.json',dict(schema_version='harbor-environment-coordinator-refresh/v1',refresh=proof,origin=arguments+[proof,commit],initial_lease={'lease':'original'}))
    value=dict(status='IMPORTED',publication_ready=False,descriptor=descriptor,evidence=[proof,commit,*arguments],lease={'lease':'original'})
    checkpoint=put('normal/refresh/result.json',value);outcome=put('normal/refresh/outcome.json',{});calls=[]
    C=NS(__file__=code['path'],SECONDS=3300,MEMORY=8589934592,encode=lambda x:x,selected=lambda d:calls.append(('selected',d)),_prior=lambda c,d:calls.append(('prior',c,d)))
    D=NS(successful=lambda o:(checkpoint,value))
    return p,D,(C,None,None,None,None,None,None,{}),outcome,put,intent,value,calls


def test_completed_refresh_uses_original_readers(refresh):
    p,D,env,outcome,put,intent,value,calls=refresh
    assert M.completed_refresh(p,D,env,outcome)==value
    assert [c[0]for c in calls]==['selected','prior']

@pytest.mark.parametrize('change',['action','dispatch','timeout_seconds','code','arguments','evidence','descriptor','lease'])
def test_completed_refresh_rejects_changed_identity(refresh,change):
    p,D,env,outcome,put,intent,value,calls=refresh
    if change in ('evidence','descriptor','lease'):
        if change=='evidence':value['evidence']=list(reversed(value['evidence']))
        elif change=='descriptor':value[change]=put('foreign.json',{})
        else:value[change]={'lease':'foreign'}
    else:
        intent[change]={'action':'step','dispatch':True,'timeout_seconds':7200,'code':put('foreign.py',{}),'arguments':{}}[change]
        put('normal/refresh/intent.json',intent)
    with pytest.raises(ValueError):M.completed_refresh(p,D,env,outcome)
    assert calls==[]

@pytest.fixture
def stopped_preparation(tmp_path):
    def put(name,value):
        q=tmp_path/name;q.parent.mkdir(parents=True,exist_ok=True);q.write_text(json.dumps(value));return M.binding(q)
    tracker=put('tracker.json',{'revision':12});helper=put('old.py',{})
    old=dict(helper=helper,renewal_module=put('lease.py',{}),output=str(tmp_path/'old-home'),owner='old',fence=4,tracker=tracker,source='same-source')
    plan=put('old-plan.json',old);intent=put('old-home/intent.json',dict(plan=plan,helper=helper,automatic_retry=False));log=put('old-home/permit.log',{})
    guard=dict(intent=intent,log=log,returncode=1,failure=None,models_dispatched=0,seconds=3300,memory_bytes=8589934592,automatic_retry=False,command=[M.sys.executable,'-I','-B',helper['path'],'permit-child','--plan',plan['path'],'--sha256',plan['sha256']])
    put('old-home/permit-resource.json',guard);put('old-home/renewal/intent.json',dict(schema_version='fleet-validated-continuation-lease/v1',authority=plan,tracker=tracker,duration_ns=7200*10**9))
    proof=dict(schema_version='fleet-semantic-renewal-preparation-failure/v1',plan=plan,files={str(x.relative_to(tmp_path/'old-home')):M.binding(x)for x in (tmp_path/'old-home').rglob('*')if x.is_file()},tracker=tracker,process=put('process.json',dict(pid=999999999)))
    p={**old,'helper':put('new.py',{}),'owner':'new','fence':5,'output':str(tmp_path/'new-home'),'preparation_failure':put('proof.json',proof)}
    return p,proof,put,guard

def test_sealed_pre_cas_preparation_can_continue_without_reset(stopped_preparation):
    p,proof,put,guard=stopped_preparation;M.stopped_renewal_preparation(p)

@pytest.mark.parametrize('kind',['extra-task-file','guard-success','guard-uncertain','changed-source','changed-tracker','live-process'])
def test_preparation_recovery_rejects_owning_or_uncertain_boundary(stopped_preparation,kind):
    p,proof,put,guard=stopped_preparation
    if kind=='extra-task-file':put('old-home/permit/complete.json',{})
    elif kind.startswith('guard-'):
        guard['returncode']=0 if kind=='guard-success' else 1;guard['failure']='lost'if kind=='guard-uncertain'else None
        proof['files']['permit-resource.json']=put('old-home/permit-resource.json',guard)
    elif kind=='changed-source':p['source']='foreign'
    elif kind=='changed-tracker':p['tracker']=put('new-tracker.json',{'revision':13})
    else:proof['process']=put('process.json',dict(pid=M.os.getpid()))
    p['preparation_failure']=put('proof.json',proof)
    # macOS has no /proc; provide the exact process observation for this one case.
    if kind=='live-process':
        from unittest.mock import patch
        original=Path.exists
        with patch.object(Path,'exists',lambda q:q==Path('/proc',str(M.os.getpid()))or original(q)):
            with pytest.raises(ValueError):M.stopped_renewal_preparation(p)
    else:
        with pytest.raises(ValueError):M.stopped_renewal_preparation(p)

@pytest.mark.parametrize('change',[None,'job','outcome','checkpoint','status','workbook','config','published'])
def test_completed_normalization_ready_preserves_exact_final_boundary(normalized_context,change):
    p,put,calls=normalized_context;job=M.read(p['original_job']);n=M.read(p['normalization_context']['plan'])
    result=put('authoring/dispatcher-v2/normalization/025/result.json',dict(status='PUBLICATION_READY',publication_performed=False))
    out=put('authoring/dispatcher-v2/normalization/025/outcome.json',dict(status='COMPLETED_OPERATION',result=result))
    put('authoring/dispatcher-v2/normalization/hold.json',dict(outcome=out))
    ready=dict(status='PUBLICATION_READY',plan=p['normalization_context']['plan'],workbook=p['workbook'],job=p['original_job'],config=job['config'],outcome=out,checkpoint=result,publication_performed=False,automatic_retry=False)
    if change=='published':ready['publication_performed']=True
    elif change:ready[change]='foreign'
    put('normalization/result.json',ready)
    if change:
        with pytest.raises((ValueError,KeyError)):M.authoring_context(p)
    else:
        assert M.authoring_context(p)[1]==job

@pytest.mark.parametrize('isolated',[False,True])
def test_retained_original_renewal_command_accepts_legacy_and_isolated_shape(tmp_path,isolated):
    root=tmp_path/'code';root.mkdir();dispatcher=root/'dispatcher.py';dispatcher.write_text('# original')
    instruction=root/'instruction.py';instruction.write_text('# original instruction')
    output=tmp_path/'semantic';run=tmp_path/'authoring'
    directory=run/'dispatcher-v2'/output.name/'003';directory.mkdir(parents=True)
    intent=M.keep(directory/'intent.json',dict(job={'retained':True},checkpoint={'original':True},seconds=900,driver=M.binding(dispatcher)))
    C=NS();I=NS(__file__=str(instruction));D=NS(__file__=str(dispatcher))
    env=(C,I,None,None,None,dict(run_root=str(run)),root,None)
    command=['python',*(['-I']if isolated else []),'-B',str(dispatcher),'renew-child',intent['path'],intent['sha256']]
    assert M.retained_command(command,dict(output=str(output)),D,env)==('renewal',intent)
    with pytest.raises(ValueError,match='outside the declared'):
        M.retained_command(command,dict(output=str(tmp_path/'other')),D,env)


def test_retained_guard_installs_exact_cold_child_sidecar_and_preserves_limits(tmp_path):
    code=tmp_path/'code';code.mkdir()
    paths=[]
    for name in ('base.py','instruction.py','dispatcher.py'):
        p=code/name;p.write_text('# original');paths.append(p)
    base,instruction,dispatcher=paths;output=tmp_path/'semantic';directory=output/'first-step';directory.mkdir(parents=True)
    controls={'original':'controls'};calls=[]
    C=NS(__file__=str(base),SECONDS=3300,encode=lambda x:x,_run_in_process_group=lambda *a,**k:calls.append((a,k)))
    I=NS(__file__=str(instruction),SCHEMA='original-i');D=NS(__file__=str(dispatcher))
    env=(C,I,None,None,None,dict(run_root=str(tmp_path/'authoring')),code,controls)
    jobpath=tmp_path/'job.json';job=M.keep(jobpath,dict(driver=M.binding(dispatcher),outcome={'prior':True},operations='original'))
    p=dict(output=str(output),original_job=job,helper={'path':'/selected/shared.py','sha256':'a'*64})
    row={'path':'/selected/plan.json','sha256':'b'*64}
    intent=M.keep(directory/'intent.json',dict(schema_version=I.SCHEMA,action='step',arguments={},timeout_seconds=3300,models=True,code=M.binding(instruction),base_code=M.binding(base),controls=controls))
    command=['python','-B',str(instruction),'_child',intent['path'],intent['sha256']]
    M.install_retained_commands(row,p,D,env)
    C._run_in_process_group(command,timeout_seconds=3300,memory_limit_bytes=8589934592,grace_seconds=3)
    side=M.read(M.binding(directory/'semantic-bootstrap.json'))
    assert side['original_command']==command and side['intent']==intent and side['plan']==row
    assert calls[0][0][0][:4]==['python','-I','-B','/selected/shared.py']
    assert calls[0][1]==dict(timeout_seconds=3300,memory_limit_bytes=8589934592,grace_seconds=3)
    with pytest.raises(ValueError,match='resource contract'):
        C._run_in_process_group(command,timeout_seconds=3301,memory_limit_bytes=8589934592)
    assert len(calls)==1


def test_retained_permit_failure_restores_dependency_manager(monkeypatch,tmp_path):
    exits=[];manager=NS(__exit__=lambda *args:exits.append(args))
    def failed(*a,**k):raise LookupError('original authorizer failed')
    p=dict(output=str(tmp_path),retained_context={},completed_takeover={},blocked={'retained':True})
    env=(None,None,None,None,None,None,tmp_path,None)
    loaded=(p,{},None,None,env,None,None,None,None,NS(authorize=failed),NS(manager=manager,takeover={'actual':True}))
    monkeypatch.setattr(M,'context',lambda *a,**k:loaded);monkeypatch.setattr(M,'custody',lambda *a:None)
    with pytest.raises(LookupError,match='original authorizer'):
        M.permit_child({'selected':True})
    assert len(exits)==1 and exits[0][0]is LookupError


def test_original_fixed_guard_predating_guard_seconds_is_retained(failure):
    p,I,controls,_=failure
    del I.guard_seconds;I.base.SECONDS=3300
    assert M.completed_failure(p,I,controls)[0]['status']=='DIALOGUE_REPAIR_REQUIRED'
    I.base.SECONDS=3301
    with pytest.raises(ValueError,match='resource contract'):
        M.completed_failure(p,I,controls)


def test_ordinary_fresh_completed_lease_context_is_exact_original_setup(tmp_path,monkeypatch):
    home=tmp_path/'fresh';(home/'authoring').mkdir(parents=True)
    config=M.keep(home/'authoring/config.json',dict(workbook='0415'))
    driver=M.keep(tmp_path/'driver.json',{'original':'dispatcher'})
    fresh=M.keep(tmp_path/'fresh.json',dict(schema_version='harbor-fleet-fresh-plan/v1',workbook='0415',home=str(home),dispatcher=driver))
    job=M.keep(home/'authoring-job.json',dict(driver=driver,config=config))
    p=dict(helper=M.binding(M.__file__),workbook='0415',fresh_plan=fresh,original_job=job)
    entry,call=M.lease_context(p)
    assert entry==p['helper']and call==dict(function='load',args=[{k:p[k]for k in ('workbook','fresh_plan','original_job')}],kwargs={})
    calls=[];monkeypatch.setattr(M,'validated_authoring',lambda value:calls.append(value)or('fresh','job','originalD','originalEnv'))
    assert M.load(call['args'][0])==('fresh','job','originalD','originalEnv')and calls==call['args']
    for changed in ({**p,'helper':driver},{**p,'normalization_context':{}},{**p,'workbook':'foreign'}):
        with pytest.raises(ValueError):M.lease_context(changed)
    with pytest.raises(ValueError):M.load({**call['args'][0],'owner':'unselected'})


def test_original_takeover_selection_codec_preserves_default_omission():
    from scripts.batch import harbor_lease_takeover as T
    s=T.Selection(campaign_root=Path('/original'),workbook='0415',tracker={},worker_kind='instruction',guard={},checkpoint={},owner='selected',config={})
    supplied=dataclasses.asdict(s);supplied['campaign_root']=str(s.campaign_root)
    assert supplied['controls_migration']is None
    sealed=T._selection_value(T._selection(supplied))
    assert 'controls_migration'not in sealed and T._selection(sealed)==s
    changed={**supplied,'controls_migration':{'actual':'migration'}}
    assert T._selection_value(T._selection(changed))!=sealed


def test_retained_instruction_uses_original_step_specific_guard(tmp_path):
    intent=M.keep(tmp_path/'intent.json',dict(action='step',arguments={'descriptor':'original','checkpoint':'review2'},timeout_seconds=1350))
    calls=[]
    I=NS(guard_seconds=lambda action,args:calls.append((action,args)) or 1350)
    C=NS(SECONDS=3300)
    assert M.retained_seconds('instruction',intent,(C,I))==1350
    assert calls==[('step',dict(descriptor='original',checkpoint='review2'))]
    I.guard_seconds=lambda *args:3300
    with pytest.raises(ValueError,match='duration differs'):M.retained_seconds('instruction',intent,(C,I))
    original=M.keep(tmp_path/'legacy.json',dict(action='step',arguments={},timeout_seconds=3300))
    assert M.retained_seconds('instruction',original,(C,NS()))==3300
    renewal=M.keep(tmp_path/'renewal.json',dict(seconds=900))
    assert M.retained_seconds('renewal',renewal,(C,I))==900


@pytest.fixture
def semantic_tail(tmp_path,monkeypatch):
    from pathlib import Path
    root=tmp_path/'code';root.mkdir();run=tmp_path/'authoring';output=tmp_path/'tail';oldout=tmp_path/'semantic'
    base=root/'base.py';base.write_text('# original base');instruction=root/'instruction.py';instruction.write_text('# original I')
    tracker=M.keep(tmp_path/'0415.json',dict(current=True));source=M.keep(tmp_path/'source.json',dict(original=True))
    auth=M.keep(tmp_path/'auth.json',dict(explicit=True));monkeypatch.setattr(M,'AUTHORIZATION_SHA',auth['sha256'])
    reader=M.keep(tmp_path/'old-reader.json',dict(original=True));fresh=M.keep(tmp_path/'fresh.json',{})
    job=M.keep(tmp_path/'job.json',dict(config={'original':True}));completed=M.keep(tmp_path/'completed.json',{})
    descriptor=M.keep(tmp_path/'descriptor.json',{})
    C=NS(__file__=str(base),MEMORY=8589934592,SECONDS=3300,encode=lambda x:x,decode=lambda x:x)
    selection=NS(content=NS(workbook='0415',bindings={'original_raw_source':source}))
    worker=NS(root=root,run=run/'instruction',context=NS(tracker_root=tmp_path))
    I=NS(__file__=str(instruction),SCHEMA='I',selected=lambda row:(worker,{'controls':True},None),_prior=lambda *a:None,
         codec=NS(decode=lambda x:selection),guard_seconds=lambda *a:1350)
    events=[];W=NS(step_directory=lambda *a:run/'instruction/steps/final-runtime',active=lambda *a:events.append('active'))
    result=dict(status='STEP_COMPLETE',next_step='final-runtime',publication_performed=False,descriptor=descriptor,selection={},lease={})
    D=NS(successful=lambda *a:(completed,result),operation_namespace=lambda *a:events.append('namespace'))
    env=(C,I,None,W,None,dict(run_root=str(run)),root,{'controls':True})
    old=dict(schema_version=M.SCHEMA,helper=reader,completed_takeover={},fresh_plan=fresh,original_job=job,source=source,workbook='0415',authorization=auth,output=str(oldout),fence=3)
    prior=M.keep(tmp_path/'old-plan.json',old)
    manager=NS(__exit__=lambda *a:events.append('restore'))
    parent=NS(context=lambda *a,**kw:(old,{},M.read(job),D,env,None,None,None,None,None,NS(manager=manager)))
    monkeypatch.setattr(M,'module',lambda *a:parent)
    directory=run/'dispatcher-v2'/oldout.name/'002';directory.mkdir(parents=True)
    intent=M.keep(directory/'intent.json',dict(action='step',schema_version='I',code=M.binding(instruction),base_code=M.binding(base),
        arguments=dict(descriptor=descriptor,checkpoint=completed),controls={'controls':True},memory_limit_bytes=8589934592,timeout_seconds=1350))
    (directory/'step.log').write_bytes(b'')
    resource=M.keep(directory/'resource.json',dict(returncode=None,metrics={},failure=dict(type='ValueError',message_sha256=__import__('hashlib').sha256(b'original retained child resource contract changed').hexdigest()),intent=intent,log=M.binding(directory/'step.log')))
    failed=M.keep(directory/'outcome.json',dict(status='RECONCILIATION_REQUIRED',automatic_retry=False,resource=resource))
    p=dict(schema_version=M.TAIL_SCHEMA,workbook='0415',helper=M.binding(M.__file__),fresh_plan=fresh,original_job=job,outcome=completed,
        tracker=tracker,source=source,owner='new-owner',fence=4,output=str(output),operations=str(run/'dispatcher-v2'/output.name),next_step='final-runtime',
        authorization=auth,capacity_policy={},semantic_context=dict(helper=reader,plan=prior),failed_guard=failed)
    return p,tmp_path,events,directory,worker,D


def test_semantic_tail_retains_completed_review_and_exact_unspawned_guard(semantic_tail):
    p,home,events,directory,worker,D=semantic_tail
    row=M.keep(home/'tail-plan.json',p)
    result=M.tail_probe(row)
    assert result['status']=='PASS'and result['result']['next_step']=='final-runtime'
    assert events==['active','namespace','restore']
    assert not (home/'tail').exists()


@pytest.mark.parametrize('defect',['extra-child-evidence','runtime-started','wrong-owner-fence','new-source'])
def test_semantic_tail_rejects_uncertain_or_detached_prior(semantic_tail,defect):
    p,home,events,directory,worker,D=semantic_tail
    if defect=='extra-child-evidence':(directory/'semantic-bootstrap.json').write_text('{}')
    elif defect=='runtime-started':(worker.run/'steps/final-runtime').mkdir(parents=True)
    elif defect=='wrong-owner-fence':p['fence']=3
    else:p['source']={}
    row=M.keep(home/'bad-plan.json',p)
    with pytest.raises(ValueError):M.tail_probe(row)
    if defect in ('extra-child-evidence','runtime-started'):assert events[-1]=='restore'


def test_completed_disclosure_live_permit_uses_original_renewal_and_authorizer(monkeypatch,tmp_path):
    calls=[];exits=[]
    manager=NS(__exit__=lambda *args:exits.append(args))
    p=dict(output=str(tmp_path),retained_context=dict(kind='completed_disclosure'),blocked={'original':'review'})
    D=NS(_retained_disclosure_manager=manager)
    env=(None,None,None,None,None,None,tmp_path,None)
    def authorize(worker,selection,lease,**kwargs):
        calls.append(('authorize',worker,selection,lease,kwargs))
        return worker,selection,lease,'permit'
    loaded=(p,{},None,D,env,'worker','selection','old-lease',None,NS(authorize=authorize),NS())
    monkeypatch.setattr(M,'context',lambda *a,**k:loaded)
    monkeypatch.setattr(M,'custody',lambda *a:None)
    def renew(*args):
        calls.append(('renew',args[5],args[6]));return 'renewed-lease','renewal-receipt'
    monkeypatch.setattr(M,'renew_semantic',renew)
    monkeypatch.setattr(M,'semantic_handoff',lambda *args:args)
    result=M.permit_child({'selected':'plan'})
    assert calls==[('renew','selection','old-lease'),('authorize','worker','selection','renewed-lease',{'blocked':p['blocked']})]
    assert result[7:9]==('renewal-receipt','permit')
    assert len(exits)==1 and exits[0]==(None,None,None)


def test_live_retained_manager_rejects_untyped_history():
    manager=object();D=NS(_retained_disclosure_manager=manager)
    assert M.retained_manager(dict(retained_context=dict(kind='completed_disclosure')),D,None)is manager
    for p in ({},{'retained_context':{}},{'retained_context':{'kind':'foreign'}}):
        with pytest.raises(ValueError,match='retained context manager'):
            M.retained_manager(p,D,None)

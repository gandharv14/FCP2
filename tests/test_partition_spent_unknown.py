"""New spent-unknown policy; every network response is a synthetic fixture."""
from pathlib import Path
import copy,json,os,fcntl,time
import pytest
from scripts.batch import harbor_partition_spent_unknown as S
from scripts.batch import harbor_normalization_partitioned as P,harbor_model_executor as E
from scripts.batch.harbor_admission import read,record
from test_normalization_partitioned import prepared,respond
from test_harbor_normalization_producer import setup

@pytest.fixture
def unknown(prepared,monkeypatch,tmp_path,request):
    selection,campaign,binding,old,old_bytes=prepared
    index=getattr(request,'param',0);monkeypatch.setattr(S,'INDEX',index)
    if index:
        respond(monkeypatch,selection)
        for i in range(index):P.dispatch(binding,i,1,credential='fixture')
    pc=P._prepared_call(binding,index,1)
    def stopped(name):
        if name=='after_dispatch_started':os._exit(71)
    import multiprocessing
    child=multiprocessing.get_context('fork').Process(target=lambda:E.execute(pc,campaign,credential='fixture',_failpoint=stopped))
    child.start();child.join(10);assert child.exitcode==71
    journal=E.location(pc,campaign);assert not (journal/'outcome.json').exists()
    original=E.all_files(journal)
    lock=tmp_path/'book.lock';fd=os.open(lock,os.O_CREAT|os.O_RDWR,0o600);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    config=tmp_path/'config.json';config.write_text(json.dumps(dict(run_root=str(tmp_path),controls=dict(value=dict(slot_lock=dict(value=str(lock)))))))
    plan=tmp_path/'guard-plan.json';plan.write_text(json.dumps(dict(workbooks={'0274':dict(config=record(config))})))
    guard=tmp_path/'guard.json';guard.write_text('{"fixture":"ended local guard"}')
    def checked_guard(p,g):
        assert p==binding and g==record(guard);return {},dict(plan=record(plan))
    monkeypatch.setattr(S,'_guard',checked_guard)
    root=Path(S.__file__).resolve().parents[2]
    monkeypatch.setattr(S,'code_projection',lambda m:(root,root,{}, {n:record(root/n)['sha256'] for n in S.ALTERED|{S.SELF}}))
    tracker=tmp_path/'trackers/workbooks/0274.json';tracker.parent.mkdir(parents=True);tracker.write_text(json.dumps(dict(revision=2,leases=dict(task_agent=dict(owner='fixture',token='fixture',expires_at=time.time_ns()+3600*10**9)))))
    lease=dict(owner='fixture',token='fixture',revision=2,sha256=record(tracker)['sha256'])
    monkeypatch.setattr(S,'authority_evidence',lambda authority,dependency,p,**kw: {'lease':lease} if authority==dependency==record(guard) and p==binding else (_ for _ in ()).throw(ValueError('foreign authority')))
    authorization=S.authorize(binding,record(guard),tmp_path/'abandoned',book_lock_fd=fd,lease_authority=record(guard),dependency=record(guard))
    yield selection,campaign,binding,authorization,journal,original
    os.close(fd)


def test_unknown_consumes_existing_ordinal_without_call_then_full_coverage(unknown,monkeypatch):
    s,campaign,binding,authorization,journal,original=unknown
    first=P._review(binding,0,1,campaign,P._current(binding)[4])
    assert first['kind']=='spent_unknown' and first['valid'] is None and first['call'] is None and first['result'] is None
    with pytest.raises(ValueError,match='cannot be replayed'):P.dispatch(binding,0,1,credential='fixture')
    with pytest.raises(ValueError):P.dispatch(binding,0,2,credential='fixture') # later first round missing
    respond(monkeypatch,s)
    P.dispatch(binding,1,1,credential='fixture')
    with pytest.raises(ValueError,match='valid partition'):P.dispatch(binding,1,2,credential='fixture')
    second=P.dispatch(binding,0,2,credential='fixture');assert second.status=='completed'
    from scripts.batch.harbor_model_review_provenance import validate_call
    checked=validate_call(second.call,read(P._prepared_call(binding,0,2).request),policy=s.policy)
    assert checked['review']['schema_version']=='normalization-review/v1'
    second_journal=E.location(P._prepared_call(binding,0,2),campaign)
    prior=read(record(second_journal/'prior-attempts.json'))
    assert prior==[dict(kind='spent_unknown',authorization=authorization,consumed_ordinal=1,provider_outcome='unknown',call=None)]
    assert read(record(second_journal/'submitted.json'))['spent_unknown_predecessor']==prior[0]
    proof=P.aggregate(binding)
    assert proof['semantic_attempt']==2 and proof['logical_limit']==2 and len(proof['review']['cells'])==4
    assert proof['partitions'][0]['kind']=='spent_unknown'
    assert E.all_files(journal)==original and not (journal/'outcome.json').exists()
    assert len(proof['dependency_reconciliation']['candidate_ids'])==4


@pytest.mark.parametrize('damage',['late_response','changed_request','reclassify','ordinal','budget','journal_inventory','boundary','missing_review_binding'])
def test_unknown_authorization_tamper_fails_closed(unknown,damage,tmp_path):
    s,campaign,binding,authorization,journal,original=unknown
    value=copy.deepcopy(read(authorization))
    if damage=='late_response':(journal/'response.raw.json').write_text('{}')
    if damage=='changed_request':(journal/'request.json').write_text('{}')
    if damage=='reclassify':value['provider_outcome']='failed'
    if damage=='ordinal':value['next_ordinal']=1
    if damage=='budget':value['logical_limit']=3
    if damage=='journal_inventory':value['journal_files']={}
    if damage=='boundary':value['abandoned_at_ns']=0
    if damage=='missing_review_binding':value['retained_reviews']=[dict(index=5,journal='foreign',files={})]
    path=tmp_path/'forged.json';path.write_text(json.dumps(value))
    with pytest.raises((ValueError,AssertionError)):S.validate(record(path),binding)


def test_optional_executor_permission_does_not_relax_generic_unknown_retry(unknown):
    from dataclasses import replace
    s,campaign,binding,authorization,journal,original=unknown
    second=replace(P._prepared_call(binding,0,2),spent_unknown=None)
    with pytest.raises(ValueError,match='missing or incomplete'):E.execute(second,campaign,credential='fixture')
    assert not E.location(second,campaign).exists()


@pytest.mark.parametrize('unknown',[1],indirect=True)
def test_completed_review_is_preserved_and_cannot_be_removed(unknown,monkeypatch,tmp_path):
    s,campaign,binding,authorization,journal,original=unknown
    value=read(authorization);assert len(value['retained_reviews'])==1
    prior=value['retained_reviews'][0];before=copy.deepcopy(prior['files'])
    respond(monkeypatch,s);P.dispatch(binding,1,2,credential='fixture')
    assert len(P.aggregate(binding)['review']['cells'])==4
    assert E.all_files(Path(prior['journal']))==before
    value['retained_reviews']=[];p=tmp_path/'missing-prior.json';p.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='coverage'):S.validate(record(p),binding)


@pytest.mark.parametrize('tamper',[None,'original','extra','symlink','unrelated','unreviewed_delta','manifest_code'])
def test_successor_inventory_and_same_byte_projection_are_exact(tmp_path,monkeypatch,tamper):
    old=tmp_path/'old';new=tmp_path/'new';names=S.ALTERED|{'gen_normalizer.py'}
    for root in [old,new]:
        for name in names:
            p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(root.name if name in S.ALTERED else 'same generator')
    p=new/S.SELF;p.write_text('current helper')
    snapshot=tmp_path/'normalization-snapshot.json';snapshot.write_text(json.dumps(dict(snapshot=dict(root=str(old),files=[dict(relative=n,sha256=record(old/n)['sha256']) for n in names]))))
    monkeypatch.setattr(S,'ORIGINAL_SNAPSHOT_SHA256',record(snapshot)['sha256'])
    monkeypatch.setattr(S,'SUCCESSOR_HASHES',{n:record(new/n)['sha256'] for n in S.ALTERED})
    monkeypatch.setattr(S,'__file__',str(new/S.SELF))
    m=dict(code={str(old/n):record(old/n)['sha256'] for n in names})
    if tamper=='original':(old/'gen_normalizer.py').write_text('changed')
    if tamper=='extra':(new/'sitecustomize.py').write_text('injected')
    if tamper=='symlink':(new/'injected').symlink_to(old/'gen_normalizer.py')
    if tamper=='unrelated':(new/'gen_normalizer.py').write_text('changed generator')
    if tamper=='unreviewed_delta':(new/'scripts/batch/harbor_model_executor.py').write_text('different executor')
    if tamper=='manifest_code':m['code'][str(old/'gen_normalizer.py')]='0'*64
    if tamper is not None:
        with pytest.raises(ValueError):S.code_projection(m)
    else:
        assert S.project_same_bytes(dict(code={str(new/'gen_normalizer.py'):'same'}),m)==dict(code={str(old/'gen_normalizer.py'):'same'})
        with pytest.raises(ValueError,match='silently rebased'):S.project_same_bytes(str(new/'scripts/batch/harbor_model_executor.py'),m)


@pytest.mark.parametrize('tamper',[None,'resource','intent','authority_code','snapshot_extra','lease','dependency','missing_roles'])
def test_separate_owning_authority_requires_exact_guard_and_code(tmp_path,monkeypatch,tamper):
    def keep(path,value):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return record(path)
    root=tmp_path/'lease-code';root.mkdir();code=root/'authority.py';code.write_text('reviewed isolated authority')
    monkeypatch.setattr(S,'AUTHORITY_SHA256',record(code)['sha256'])
    roles=['scripts/harbor_campaign.py','scripts/batch/harbor_environment_worker.py','scripts/batch/harbor_tracker_lifecycle.py','scripts/batch/harbor_lease_takeover.py','scripts/batch/harbor_lane_notes.py','scripts/batch/harbor_lane_lifecycle.py']
    for name in roles:
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('reviewed '+name)
    role_records=[record(root/name) for name in roles] if tamper!='missing_roles' else []
    snapshot=keep(tmp_path/'snapshot.json',dict(snapshot=dict(root=str(root),files=[dict(relative=str(p.relative_to(root)),sha256=record(p)['sha256']) for p in root.rglob('*') if p.is_file()])))
    monkeypatch.setattr(S,'LEASE_SNAPSHOT_SHA256',snapshot['sha256'])
    monkeypatch.setattr(S,'dependency_semantics',lambda *args,**kw:None)
    prepared=keep(tmp_path/'prepared.json',dict(original='same'))
    plan=keep(tmp_path/'plan.json',dict(lease_authority=record(code),lease_snapshot=snapshot,workbooks={'0274':dict(prepared=prepared,lease_authority_root=str(root),lease_authority_code=role_records)}))
    directory=tmp_path/'guard';lease=dict(owner='harbor-orchestrator-0274',token='fixture',revision=2,sha256='0'*64)
    intent=keep(directory/'intent.json',dict(plan=plan,workbook='0274',lease=lease,renew=False))
    log=directory/'step.log';log.write_bytes(b'')
    resource=keep(directory/'resource.json',dict(returncode=0,intent=intent,metrics={},log=record(log)))
    value=dict(intent=intent,authority=record(code),code=role_records,lease=lease,live_lease_verified=True)
    result=keep(directory/'result.json',value)
    dependency=keep(tmp_path/'dependency.json',dict(schema_version='harbor-dependency-observation/v2',checked_at_ns=1,observation=dict(requirements=[])))
    if tamper=='resource':keep(Path(resource['path']),dict(returncode=1,intent=intent))
    if tamper=='intent':value['intent']=prepared;result=keep(directory/'result.json',value)
    if tamper=='authority_code':code.write_text('unreviewed')
    if tamper=='snapshot_extra':(root/'extra.py').write_text('injection')
    if tamper=='lease':value['lease']={**lease,'revision':3};result=keep(directory/'result.json',value)
    if tamper=='dependency':dependency=keep(Path(dependency['path']),dict(schema_version='fake'))
    if tamper is None:assert S.authority_evidence(result,dependency,prepared)==value
    else:
        with pytest.raises((ValueError,KeyError)):S.authority_evidence(result,dependency,prepared)


@pytest.mark.parametrize('change',['expired','hash','revision','owner','token'])
def test_historical_authority_cannot_authorize_current_mutation(tmp_path,change):
    now=time.time_ns();row=dict(owner='owner',token='token',expires_at=now+3600*10**9)
    frame=dict(revision=4,leases=dict(task_agent=row));p=tmp_path/'tracker.json';p.write_text(json.dumps(frame))
    binding=record(p);lease=dict(owner='owner',token='token',revision=4,sha256=binding['sha256'])
    if change=='expired':now+=3600*10**9
    elif change=='hash':lease['sha256']='0'*64
    elif change=='revision':lease['revision']=3
    else:lease[change]='foreign'
    with pytest.raises(ValueError):S.live_frame(binding,lease,now)


@pytest.mark.parametrize('damage',[None,'requirements','observation','expired','foreign_worker'])
def test_dependency_proof_uses_original_recipe_and_fresh_semantics(tmp_path,monkeypatch,damage):
    from types import SimpleNamespace
    from scripts import harbor_campaign as C
    from scripts.batch import harbor_admission as A
    requirement=tmp_path/'requirements.txt';requirement.write_text('fixture==1\n')
    recipe=A.DependencyRecipe((record(requirement),))
    worker=SimpleNamespace(bootstrap=SimpleNamespace(workbook='0274',policy=SimpleNamespace(dependencies=recipe)))
    monkeypatch.setattr(C,'decode',lambda value:worker)
    descriptor=tmp_path/'descriptor.json';descriptor.write_text('{"worker":"fixture"}')
    plan=dict(workbooks={'0274':dict(descriptor=record(descriptor))})
    observation=dict(requirements=list(recipe.requirements),installed_manifest={'fixture':'1'})
    def observe(actual,workspace):assert actual is recipe;return copy.deepcopy(observation)
    monkeypatch.setattr(A,'observe_dependencies',observe)
    report=dict(schema_version='harbor-dependency-observation/v2',checked_at_ns=time.time_ns(),observation=copy.deepcopy(observation))
    if damage=='requirements':report['observation']['requirements']=[]
    if damage=='observation':report['observation']['installed_manifest']['fixture']='bad'
    if damage=='expired':report['checked_at_ns']=0
    if damage=='foreign_worker':worker.bootstrap.workbook='foreign'
    p=tmp_path/'dependency.json';p.write_text(json.dumps(report))
    if damage is None:S.dependency_semantics(plan,record(p),fresh=True)
    else:
        with pytest.raises(ValueError):S.dependency_semantics(plan,record(p),fresh=True)

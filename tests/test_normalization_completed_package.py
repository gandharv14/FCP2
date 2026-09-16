from pathlib import Path
from types import SimpleNamespace
import json
import pytest
from scripts.fleet import normalization_completed_package as P,normalization_imported_continue as N
from scripts.fleet.migration import retain


def fixture(tmp_path):
    def put(name,value):
        path=tmp_path/name;path.parent.mkdir(parents=True,exist_ok=True);return retain(path,value)
    source=put('source',{'source':True});tracker=put('tracker-before',dict(workbook_id='1234',revision=15,bindings=dict(original_raw_source=source),leases=dict(task_agent=dict(owner='old',token='old-token'))));current=SimpleNamespace(owner='new',token='new-token',revision=16,sha256='after')
    done=put('completed',dict(result=put('result',dict(status='STEP_COMPLETE',step='environment',descriptor=put('descriptor',{}),selection={'complete':'source'},lease={'selected':True})),resource=put('resource',{'original_guard':True})))
    oldtp=put('old-tp',dict(schema_version='original',allowance={'old':'allowance'},selection=dict(owner='old',tracker=tracker,checkpoint={'old':'checkpoint'},guard={'old':'guard'},prior_completion={'T':2},allowance={'old':'allowance'},config={'same':'config'})))
    old=put('old',dict(takeover_profile=oldtp,takeover_completion=put('T3',{'done':True})))
    proof=put('proof',dict(previous=old,completed=done,tracker=tracker));q=dict(completed_package=proof,owner='new',source=source,workbook='1234')
    t=N.read(oldtp);d=N.read(done);t.update(allowance={'new':'allowance'});t['selection'].update(allowance=t['allowance'],owner='new',tracker=tracker,checkpoint=d['result'],guard=d['resource'],prior_completion=N.read(old)['takeover_completion']);q['takeover_profile']=put('new-tp',t)
    before=N.read(tracker);after=dict(before,revision=16);stop=put('stop',dict(before=tracker));q['takeover_completion']=put('new-t',dict(after={'sha256':'after'},stopped_owner_proof=stop))
    T=SimpleNamespace(validate_completion=lambda _: (before,after,current))
    return q,T,current,put


def test_exact_completed_package_takeover_preserves_source_guard_and_checkpoint(tmp_path):
    q,T,lease,_=fixture(tmp_path)
    assert P.takeover(N,q,T,lease)==N.read(N.read(q['completed_package'])['completed'])['result']

@pytest.mark.parametrize('field,value',[('checkpoint',{'foreign':1}),('guard',{'foreign':1}),('config',{'foreign':1}),('prior_completion',{'foreign':1}),('owner','foreign')])
def test_takeover_rejects_changed_original_custody(tmp_path,field,value):
    q,T,lease,put=fixture(tmp_path);p=N.read(q['takeover_profile']);p['selection'][field]=value;q['takeover_profile']=put('changed',p)
    with pytest.raises(ValueError,match='detached'):P.takeover(N,q,T,lease)


def test_prior_scope_only_rebinds_exact_completed_package_and_restores(tmp_path):
    q,T,current,_=fixture(tmp_path);proof=N.read(q['completed_package']);done=N.read(proof['completed']);value=N.read(done['result']);old=SimpleNamespace(owner='old',token='old-token',revision=15,sha256=proof['tracker']['sha256']);calls=[]
    def original(checkpoint,descriptor):calls.append(checkpoint);return value,old
    C=SimpleNamespace(_prior=original,worker=SimpleNamespace(_active=lambda w,l: calls.append(('active',l))))
    with pytest.raises(RuntimeError):
        with P.prior_scope(N,q,C,'worker',T):
            assert C._prior(done['result'],value['descriptor'])==(value,current)
            assert C._prior({'unrelated':True},value['descriptor'])==(value,old)
            raise RuntimeError('test caller')
    assert C._prior is original and ('active',current)in calls


def test_package_inventory_rejects_added_file(tmp_path):
    root=tmp_path/'environment/packaging';root.mkdir(parents=True);receipt=retain(root/'complete.json',{'complete':True});prepared=retain(tmp_path/'prepared.json',{});packet=retain(tmp_path/'continuation/environment.json',{}) if (tmp_path/'continuation').exists() else None
    tree=retain(tmp_path/'tree.json',dict(root=str(root),files=[receipt]));proof=retain(tmp_path/'proof.json',dict(package_tree=tree));q=dict(completed_package=proof);(root/'extra').write_text('new')
    with pytest.raises(ValueError,match='files changed'):P.packaging(N,q,SimpleNamespace(run=root.parent),None)


def test_original_environment_guards_have_identical_context_without_base_code():
    original=dict(schema_version='harbor-guard-intent/v1',action='step',arguments={'checkpoint':'phase2'},code={'sha256':'original'},controls={'same':True},dispatch=True,executable='/python',memory_limit_bytes=123,timeout_seconds=3300)
    failed=dict(original,arguments={'checkpoint':'completed-environment'})
    assert P.same_guard_context(original,failed)
    for key in original.keys()-{'arguments'}:
        assert not P.same_guard_context(original,dict(failed,**{key:'changed'})),key
    assert not P.same_guard_context(original,dict(failed,base_code={'foreign':True}))


def test_prior_tracker_witness_is_exact_original_takeover_archive(tmp_path):
    q,T,current,put=fixture(tmp_path);proof=N.read(q['completed_package']);old=N.read(proof['previous']);prior=N.read(old['takeover_profile']);before=N.read(prior['selection']['tracker']);archive=put('T3-before',before);stop=put('T3-stop',dict(selection=prior['selection'],before=archive));old['takeover_completion']=put('T3-complete',dict(stopped_owner_proof=stop,after={'sha256':'old-after'}));proof['previous']=put('old-authentic',old);q['completed_package']=put('proof-authentic',proof);p=N.read(q['takeover_profile']);p['selection']['prior_completion']=old['takeover_completion'];q['takeover_profile']=put('T4-profile-authentic',p)
    T=SimpleNamespace(validate_completion=lambda r:(before,dict(before),SimpleNamespace(sha256='old-after')),_selection=lambda x:x)
    assert P.prior_tracker_witness(N,q,T)==((prior['selection']['tracker']['path'],archive['sha256']),archive)
    p['selection']['prior_completion']={'foreign':True};q['takeover_profile']=put('foreign-prior',p)
    with pytest.raises(ValueError,match='preceding takeover'):P.prior_tracker_witness(N,q,T)

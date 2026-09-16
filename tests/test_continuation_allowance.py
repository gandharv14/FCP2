import copy
import json
from pathlib import Path
import pytest
from scripts.batch import harbor_continuation_allowance as a
from scripts.batch import harbor_tracker_lifecycle as life
from test_harbor_tracker_lifecycle import created, acquire, kw, stopped_proof, validator


def policy(tmp_path, *, source='a'*64, workbook='0245', takeovers=5, reviews=4, directory=None):
    d=directory or tmp_path/'policy';d.mkdir(exist_ok=True)
    auth=tmp_path/'user-authorization.txt'
    if not auth.exists():auth.write_text('Explicit fixture authorization for bounded additional work; preserve history.')
    limits=dict(takeovers=takeovers,lease_duration_ns=10000,stage_seconds=9000,memory_bytes=17179869184,review_attempts=reviews,transport_retries=3)
    row=a.issue(d,expected_previous=None,selected_scope=a.scope(workbook,source),limits=limits,
                authorization=a.record(auth),reason='User-authorized continuation fixture',expires_at_ns=100000,
                approve=lambda declaration:declaration['limits']==limits,clock=lambda:1)
    return d,row,limits


def test_append_policy_cas_retains_original_and_rejects_regression(tmp_path):
    d,row,limits=policy(tmp_path);old=Path(row['path']).read_bytes()
    options=dict(expected_previous=row,selected_scope=a.scope('0245','a'*64),limits={**limits,'takeovers':6},
                 authorization=a.record(tmp_path/'user-authorization.txt'),reason='Additional bounded work',expires_at_ns=200000,
                 approve=lambda _:True,clock=lambda:2)
    second=a.issue(d,**options);assert a.validate(second)['previous']==row
    assert Path(row['path']).read_bytes()==old
    with pytest.raises(life.Conflict,match='CAS'):a.issue(d,**options)
    options.update(expected_previous=second,limits=limits)
    with pytest.raises(life.Conflict,match='regression'):a.issue(d,**options)
    assert len(list(d.glob('authorization-*.json')))==2


@pytest.mark.parametrize('change',['wrong_source','expired','future','nonfinite','bool','tamper','denied'])
def test_policy_fail_closed(tmp_path,change):
    d,row,limits=policy(tmp_path)
    if change=='wrong_source':
        with pytest.raises(life.Conflict):a.validate(row,expected_scope=a.scope('0245','b'*64))
    elif change in ('expired','future'):
        with pytest.raises(life.Conflict):a.validate(row,at=100000 if change=='expired' else 0)
    elif change=='tamper':
        Path(row['path']).write_text('{}')
        with pytest.raises(life.Conflict):a.validate(row)
    else:
        limit={**limits,'review_attempts':float('inf') if change=='nonfinite' else True} if change!='denied' else limits
        with pytest.raises(life.Conflict):
            a.issue(d,expected_previous=row,selected_scope=a.scope('0245','a'*64),limits=limit,
                    authorization=a.record(tmp_path/'user-authorization.txt'),reason='x',expires_at_ns=100000,
                    approve=lambda _:change!='denied',clock=lambda:2)
        assert not (d/'authorization-000002.json').exists()


def test_real_fourth_takeover_needs_explicit_policy_and_keeps_full_history(tmp_path):
    path,start=created(tmp_path);state=acquire(path,start)
    d,allowance,_=policy(tmp_path,source=start['tracker']['raw_source']['sha256'])
    for number in range(1,5):
        old=state['tracker']['leases']['task_agent'];proof=stopped_proof(tmp_path,old)
        # Preserve every proof at a distinct path: history checks every old copy.
        saved=tmp_path/('stopped-%d.json'%number);saved.write_bytes(Path(proof['path']).read_bytes());proof=a.record(saved)
        before=copy.deepcopy(state['tracker']);clock=lambda:old['expires_at']+1
        authority=life.RootAuthority('harbor-orchestrator',10000,lambda *x:True,max_replacements=5,allowance=allowance)
        options=kw(state,authority=authority,owner='agent%d'%number,clock=clock)
        if number==4:
            raw=path.read_bytes();plain=life.RootAuthority('harbor-orchestrator',10000,lambda *x:True,max_replacements=5)
            with pytest.raises(life.Conflict,match='budget'):
                life.lease_operation(path,operation='replace_expired',duration_ns=100,prior_lease=old,
                  stopped_owner_proof=proof,prior_owner_stopped_validator=validator,**{**options,'authority':plain})
            assert path.read_bytes()==raw
        state=life.lease_operation(path,operation='replace_expired',duration_ns=100,prior_lease=old,
              stopped_owner_proof=proof,prior_owner_stopped_validator=validator,**options)
        assert state['tracker']['handoffs'][:-1]==before['handoffs']
        assert state['tracker']['lane_state']==before['lane_state']
        assert life.validate_lifecycle(state['tracker'])==number
    # Historical expiry does not erase a valid authorization at event time.
    assert a.validate(allowance)['expires_at_ns']==100000
    altered=copy.deepcopy(state['tracker']);altered['handoffs'][-1].pop('allowance')
    # Remove every explicit authorization: original cap is still enforced.
    for event in altered['handoffs']:event.pop('allowance',None)
    with pytest.raises(life.Conflict,match='allowance|budget'):life.validate_lifecycle(altered)
    with pytest.raises(life.Conflict,match='steal active'):
        life.lease_operation(path,operation='replace_expired',duration_ns=100,
          prior_lease=state['tracker']['leases']['task_agent'],stopped_owner_proof=proof,
          prior_owner_stopped_validator=validator,**kw(state,authority=authority,clock=lambda:state['tracker']['updated_at']))


def test_long_lease_selected_scope_duration_and_live_window_checked(tmp_path):
    path,start=created(tmp_path)
    d,row,_=policy(tmp_path,source=start['tracker']['raw_source']['sha256'])
    auth=life.RootAuthority('harbor-orchestrator',10000,lambda *args:True,allowance=row)
    state=acquire(path,start,authority=auth)
    old=state['tracker']['leases']['task_agent']
    extended=life.lease_operation(path,operation='renew',duration_ns=9000,token=old['token'],
                                  **kw(state,authority=auth,clock=lambda:30))
    assert extended['tracker']['leases']['task_agent']['expires_at']==9030
    assert extended['tracker']['handoffs'][-1]['allowance']==row
    assert life.validate_lifecycle(extended['tracker'])==0
    raw=path.read_bytes()
    with pytest.raises(life.Conflict):
        life.lease_operation(path,operation='renew',duration_ns=10001,token=old['token'],
            **kw(extended,authority=auth,clock=lambda:40))
    assert path.read_bytes()==raw


def test_review_reservation_preserves_prior_limits_and_cannot_namespace_replay(tmp_path):
    d,row,_=policy(tmp_path);ledger=d/'review-reservations';ledger.mkdir()
    scope=dict(workbook_id='0245',original_sha256='a'*64,lane='dialogue',budget_id='review')
    prior=[]
    for ordinal in (1,2):
        path=tmp_path/('review%d.json'%ordinal);path.write_text(json.dumps(dict(scope=scope,ordinal=ordinal,limit=2,status='completed')));prior.append(a.record(path))
    original=[Path(p['path']).read_bytes() for p in prior]
    def prove(binding,scope,ordinal):
        value=a.read(binding);return value==dict(scope=scope,ordinal=ordinal,limit=2,status='completed')
    options=dict(allowance=row,semantic_scope=scope,prior_records=prior,original_limit=2,expected_ordinal=3,prove_closed=prove,clock=lambda:2)
    reserved=a.reserve_review(ledger,**options)
    assert a.read(reserved)['ordinal']==3 and a.read(reserved)['original_limit']==2
    assert [Path(p['path']).read_bytes() for p in prior]==original
    with pytest.raises(life.Conflict,match='already reserved'):a.reserve_review(ledger,**options)
    other=tmp_path/'new-namespace';other.mkdir()
    with pytest.raises(life.Conflict,match='namespace'):a.reserve_review(other,**options)


@pytest.mark.parametrize('kind',['unknown','duplicate','skipped','wrong_scope'])
def test_review_cannot_bypass_uncertainty_or_consumption(tmp_path,kind):
    d,row,_=policy(tmp_path);ledger=d/'review-reservations';ledger.mkdir()
    scope=dict(workbook_id='0245',original_sha256='a'*64,lane='dialogue',budget_id='review')
    prior=[]
    for i in range(2):
        p=tmp_path/('p%d.json'%i);p.write_text('{}');prior.append(a.record(p))
    if kind=='duplicate':prior[1]=prior[0]
    if kind=='skipped':prior.pop()
    if kind=='wrong_scope':scope['original_sha256']='b'*64
    with pytest.raises(life.Conflict):a.reserve_review(ledger,allowance=row,semantic_scope=scope,prior_records=prior,original_limit=2,expected_ordinal=3,prove_closed=lambda *x:kind!='unknown',clock=lambda:2)
    assert not list(ledger.glob('*.json'))

from test_root_lease_takeover import setup, driver_setup
from scripts.batch import harbor_lease_takeover as T
from dataclasses import replace


def test_actual_shared_takeover_four_and_seal_after_uncertain_commit(driver_setup,tmp_path):
    selected,directory,before,original=driver_setup
    _,allowance,_=policy(tmp_path,source=before['bindings']['original_raw_source']['sha256'])
    complete=None
    for number in range(1,5):
        current=a.read(a.record(Path(selected.tracker['path'])))
        if complete:
            selected=replace(selected,tracker=a.record(Path(selected.tracker['path'])),checkpoint=complete,prior_completion=complete)
        if number==4:selected=replace(selected,allowance=allowance,duration_ns=9000)
        target=directory.parent/('takeover-%d'%number)
        now=current['leases']['task_agent']['expires_at']
        if number==4:
            def interrupted(stage):
                if stage=='committed':raise KeyboardInterrupt('response lost after actual CAS')
            with pytest.raises(KeyboardInterrupt):T.replace_expired(selected,target,clock=lambda:now,_fault=interrupted)
            actual=a.read(a.record(Path(selected.tracker['path'])))
            assert life.validate_lifecycle(actual)==4
            # Recovery only seals the committed event; it cannot create T5.
            lease,complete=T.recover(a.record(target/'stopped-owner.json'))
            assert a.read(a.record(Path(selected.tracker['path'])))==actual
            with pytest.raises(ValueError):T.replace_expired(selected,target,clock=lambda:now)
        else:lease,complete=T.replace_expired(selected,target,clock=lambda:now)
        old,new,proved=T.validate_completion(complete)
        assert proved==lease and life.validate_lifecycle(new)==number
        assert new['handoffs'][:-1]==current['handoffs']
        assert new['lane_state']==before['lane_state']
    assert new['leases']['task_agent']['expires_at']-now==9000
    assert a.read(a.read(complete)['stopped_owner_proof'])['selection']['allowance']==allowance
    assert len(T.completion_records(complete))>=12

@pytest.mark.parametrize('limit',[3,4,2,5,True])
def test_review_can_select_narrower_cumulative_limit_without_reset(tmp_path,limit):
    d,row,_=policy(tmp_path);ledger=d/'review-reservations';ledger.mkdir()
    scope=dict(workbook_id='0245',original_sha256='a'*64,lane='dialogue',budget_id='review')
    prior=[]
    for ordinal in (1,2):
        path=tmp_path/('prior%d.json'%ordinal);path.write_text(str(ordinal));prior.append(a.record(path))
    options=dict(allowance=row,semantic_scope=scope,prior_records=prior,original_limit=2,expected_ordinal=3,prove_closed=lambda b,s,n:b==prior[n-1],clock=lambda:2,cumulative_limit=limit)
    if type(limit)is not int or limit not in(3,4):
        with pytest.raises(life.Conflict):a.reserve_review(ledger,**options)
        assert not list(ledger.glob('*.json'))
    else:
        r=a.reserve_review(ledger,**options)
        value,_=a.validate_review_reservation(r,scope,3,limit)
        assert value['cumulative_limit']==limit and value['prior_records']==prior
        with pytest.raises(life.Conflict):a.reserve_review(ledger,**options)
        with pytest.raises(life.Conflict):a.validate_review_reservation(r,scope,3,5)

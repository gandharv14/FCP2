import copy
import json
import multiprocessing as mp
import os
from pathlib import Path
import pytest
from scripts.batch import harbor_tracker as s
from scripts.batch import harbor_tracker_lifecycle as l

def record(path):return {'path':str(path.resolve()),'sha256':s.sha(path.read_bytes())}
def authority(accepted=True):return l.RootAuthority('harbor-orchestrator',10000,lambda *args:accepted)
def fixtures(tmp_path):
    source=tmp_path/'source.xlsx';source.write_bytes(b'synthetic workbook fixture')
    dep=tmp_path/'preflight.json';dep.write_text('{"configured_runtime":true}')
    intake=tmp_path/'intake.json';intake.write_text('{"inventory_bound":true}')
    return dict(authority=authority(),workbook_id='0245',batch='synthetic',requested_ids=['0245','0334'],original_source=record(source),dependency_preflight=record(dep),intake_evidence=[record(intake)],intake_validator=lambda req:json.loads(Path(req['intake_evidence'][0]['path']).read_text())['inventory_bound'] is True,clock=lambda:10)
def created(tmp_path):
    p=tmp_path/'0245.json';result=l.create_tracker(p,**fixtures(tmp_path));return p,result
def kw(result,**extra):
    value=dict(authority=authority(),lease_kind='task_agent',owner='agent1',scope='workbook:0245',expected_revision=result['revision'],expected_sha256=result['sha256'],clock=lambda:20)
    value.update(extra);return value
def acquire(p,result,**extra):return l.lease_operation(p,operation='acquire',duration_ns=100,**kw(result,**extra))

def test_create_is_closed_queued_and_does_not_approve_gates(tmp_path):
    p,result=created(tmp_path);d=result['tracker'];s.validate_tracker(d)
    assert d['revision']==0 and d['execution_state']=='queued' and d['promotion_failure'] is None
    assert all(x['state']=='pending' and x['current_confidence'] is None for x in d['lane_state'].values())
    assert d['requested_ids']==['0245','0334'] and all(x is None for x in d['leases'].values())
    assert d['bindings']['original_raw_source']==d['raw_source']
    assert l.validate_lifecycle(d)==0

@pytest.mark.parametrize('alteration',['root_denied','false_intake','missing_intake','wrong_id','duplicate_cohort','not_in_cohort','changed_source','candidate_without_lineage'])
def test_create_rejects_unproven_or_invalid_input(tmp_path,alteration):
    options=fixtures(tmp_path);p=tmp_path/'0245.json'
    if alteration=='root_denied':options['authority']=authority(False)
    elif alteration=='false_intake':options['intake_validator']=lambda _:False
    elif alteration=='missing_intake':options['intake_evidence']=[]
    elif alteration=='wrong_id':options['workbook_id']='0334'
    elif alteration=='duplicate_cohort':options['requested_ids']=['0245','0245']
    elif alteration=='not_in_cohort':options['requested_ids']=['0334']
    elif alteration=='changed_source':Path(options['original_source']['path']).write_bytes(b'changed')
    else:
        candidate=tmp_path/'candidate.xlsx';candidate.write_bytes(b'candidate');options['raw_source']=record(candidate)
    with pytest.raises((s.Conflict,s.InvalidTracker)):l.create_tracker(p,**options)
    assert not p.exists()

def test_proved_candidate_keeps_original_identity(tmp_path):
    options=fixtures(tmp_path);candidate=tmp_path/'candidate.xlsx';candidate.write_bytes(b'candidate');options['raw_source']=record(candidate);seen=[]
    options['lineage_validator']=lambda original,raw,evidence:(seen.append((original,raw)) or True)
    d=l.create_tracker(tmp_path/'0245.json',**options)['tracker'];assert seen==[(options['original_source'],options['raw_source'])]
    assert d['bindings']['original_raw_source']!=d['raw_source'] and d['requested_ids']==options['requested_ids']

def test_create_never_overwrites_existing_tracker(tmp_path):
    p,result=created(tmp_path);raw=p.read_bytes()
    with pytest.raises(s.Conflict,match='already exists'):l.create_tracker(p,**fixtures(tmp_path))
    assert p.read_bytes()==raw

def test_creation_rejects_symlink_ancestry(tmp_path):
    options=fixtures(tmp_path);alias=tmp_path/'alias';alias.symlink_to(tmp_path,target_is_directory=True)
    with pytest.raises(s.Conflict,match='symlink ancestry'):l.create_tracker(alias/'0245.json',**options)
    assert not (tmp_path/'0245.json').exists()

def test_acquire_renew_release_exact_owner_and_preserve_scope(tmp_path):
    p,start=created(tmp_path);a=acquire(p,start);old=a['tracker']['leases']['task_agent'];assert old['acquisition_revision']==1
    b=l.lease_operation(p,operation='renew',token=old['token'],duration_ns=200,**kw(a,clock=lambda:30));new=b['tracker']['leases']['task_agent']
    assert new['expires_at']==230 and all(new[k]==old[k] for k in ('owner','token','scope','acquired_at','acquisition_revision'))
    c=l.lease_operation(p,operation='release',token=old['token'],**kw(b,clock=lambda:40));assert c['revision']==3 and c['tracker']['leases']['task_agent'] is None
    assert c['tracker']['raw_source']==start['tracker']['raw_source'] and c['tracker']['requested_ids']==start['tracker']['requested_ids']
    assert [e['operation'] for e in c['tracker']['handoffs'][1:]]==['acquire','renew','release']

@pytest.mark.parametrize('change',[{'owner':'other'},{'token':'wrong'},{'scope':'workbook:0334'},{'expected_revision':0},{'expected_sha256':'0'*64},{'clock':lambda:120},{'clock':lambda:19}])
def test_renew_rejects_stale_wrong_or_expired_lease(tmp_path,change):
    p,start=created(tmp_path);a=acquire(p,start);raw=p.read_bytes();options=kw(a,token=a['tracker']['leases']['task_agent']['token'],duration_ns=200);options.update(change)
    with pytest.raises(s.Conflict):l.lease_operation(p,operation='renew',**options)
    assert p.read_bytes()==raw

def test_active_lease_cannot_be_stolen_even_with_proof_callback(tmp_path):
    p,start=created(tmp_path);a=acquire(p,start);raw=p.read_bytes()
    with pytest.raises(s.Conflict,match='lease occupied'):acquire(p,a,owner='other')
    proof=tmp_path/'stopped.json';proof.write_text('{}')
    with pytest.raises(s.Conflict,match='cannot steal active'):
        l.lease_operation(p,operation='replace_expired',duration_ns=100,prior_lease=a['tracker']['leases']['task_agent'],stopped_owner_proof=record(proof),prior_owner_stopped_validator=lambda *a:True,**kw(a,owner='other'))
    assert p.read_bytes()==raw

def stopped_proof(tmp_path,old):
    proof=tmp_path/'stopped.json';proof.write_text(json.dumps({'lease':old,'prior_owner_stopped':True}));return record(proof)
def validator(before,old,proof):
    data=json.loads(Path(proof['path']).read_text());return data['lease']==old and data['prior_owner_stopped'] is True

def test_one_expired_replacement_requires_exact_prior_and_independent_proof(tmp_path):
    p,start=created(tmp_path);a=acquire(p,start);old=a['tracker']['leases']['task_agent'];proof=stopped_proof(tmp_path,old);raw=p.read_bytes()
    options=kw(a,owner='replacement',clock=lambda:120);options.update(operation='replace_expired',duration_ns=100,prior_lease=old,stopped_owner_proof=proof)
    for callback in (None,lambda *a:False):
        with pytest.raises(s.Conflict,match='independent validator'):l.lease_operation(p,prior_owner_stopped_validator=callback,**options)
    assert p.read_bytes()==raw
    b=l.lease_operation(p,prior_owner_stopped_validator=validator,**options);new=b['tracker']['leases']['task_agent'];assert new['token']!=old['token'] and new['owner']=='replacement';assert l.validate_lifecycle(b['tracker'])==1
    with pytest.raises(s.Conflict,match='budget exhausted'):
        l.lease_operation(p,operation='replace_expired',duration_ns=100,prior_lease=new,stopped_owner_proof=proof,prior_owner_stopped_validator=lambda *a:True,**kw(b,owner='third',clock=lambda:220))

def test_expired_release_cannot_hide_replacement(tmp_path):
    p,start=created(tmp_path);a=acquire(p,start)
    with pytest.raises(s.Conflict,match='release cannot hide replacement'):
        l.lease_operation(p,operation='release',token=a['tracker']['leases']['task_agent']['token'],**kw(a,clock=lambda:120))

def test_lane_heartbeat_then_root_renewal_is_supported(tmp_path):
    p,start=created(tmp_path);a=acquire(p,start);token=a['tracker']['leases']['task_agent']['token']
    b=s.cas_mutate(p,expected_revision=a['revision'],expected_sha256=a['sha256'],owner='agent1',token=token,scope='workbook:0245',mutate=lambda x:x.update(current_gate='source'),allowed_fields={'current_gate'},now_ns=25)
    c=l.lease_operation(p,operation='renew',duration_ns=200,token=token,**kw(b,clock=lambda:30));assert c['revision']==3

def test_immutable_source_and_cohort_rechecked(tmp_path):
    p,start=created(tmp_path);changed=copy.deepcopy(start['tracker']);changed['requested_ids'].append('0999');p.write_text(json.dumps(changed));now={'revision':0,'sha256':s.sha(p.read_bytes())}
    with pytest.raises(s.Conflict,match='immutable original/cohort'):acquire(p,now)

def test_live_schema_helper_not_implicit_legacy_adoption(tmp_path):
    p,start=created(tmp_path);legacy=start['tracker'];legacy['handoffs']=[];p.write_text(json.dumps(legacy));s.validate_tracker(legacy)
    with pytest.raises(s.Conflict,match='no implicit legacy adoption'):acquire(p,{'revision':0,'sha256':s.sha(p.read_bytes())})

def stop_tracker(tmp_path,p,a):
    diagnostic=tmp_path/'diagnostic.json';diagnostic.write_text('{"reason":"provider failure"}');evidence=[record(diagnostic)];signature='b'*64;eh=s.sha(l.canonical(evidence));notes=tmp_path/'notes.md';notes.write_text(signature+'\n'+eh)
    d=copy.deepcopy(a['tracker']);d.update(execution_state='STOPPED',terminal_verdict='STOPPED',final_status='STOPPED');d['promotion_failure']={'schema_version':1,'class':'infrastructure','subclass':'provider_failed','stage':'normalization','lane':'harbor-normalization-engineer','gate':'review','fixability':'terminal','summary':'Provider unavailable','why_not_promoted':'Required review missing','first_seen':1,'last_seen':2,'occurrence_count':1,'diagnostic_signature':signature,'evidence_paths':evidence,'repair_attempts':[],'next_action':'Restore access'};d['agent_notes'].update(path=str(notes),sha256=s.sha(notes.read_bytes()),diagnostic_signature=signature,evidence_set_sha256=eh,updated_at=2);s.validate_tracker(d);p.write_text(json.dumps(d));return {'revision':d['revision'],'sha256':s.sha(p.read_bytes()),'tracker':d}
def failure_notes_validator(before,after,events):
    f=before['promotion_failure'];n=before['agent_notes'];computed=s.sha(l.canonical(f['evidence_paths']));notes=Path(n['path']).read_text()
    return events=={'stop','lease_release'} and n['diagnostic_signature']==f['diagnostic_signature'] and n['evidence_set_sha256']==computed and computed in notes and f['diagnostic_signature'] in notes

@pytest.mark.parametrize('fault',['missing_callback','wrong_signature','wrong_evidence_hash','changed_notes','callback_false','valid'])
def test_stopped_release_requires_exact_independent_notes_validation(tmp_path,fault):
    p,start=created(tmp_path);a=acquire(p,start);stopped=stop_tracker(tmp_path,p,a);d=stopped['tracker'];token=d['leases']['task_agent']['token']
    if fault=='wrong_signature':d['agent_notes']['diagnostic_signature']='c'*64
    if fault=='wrong_evidence_hash':d['agent_notes']['evidence_set_sha256']='c'*64
    if fault=='changed_notes':Path(d['agent_notes']['path']).write_text('changed')
    p.write_text(json.dumps(d));stopped.update(sha256=s.sha(p.read_bytes()));raw=p.read_bytes();callback=None if fault=='missing_callback' else ((lambda *a:False) if fault=='callback_false' else failure_notes_validator)
    if fault=='valid':assert l.lease_operation(p,operation='release',token=token,failure_validator=callback,**kw(stopped,clock=lambda:30))['tracker']['leases']['task_agent'] is None
    else:
        with pytest.raises(s.Conflict):l.lease_operation(p,operation='release',token=token,failure_validator=callback,**kw(stopped,clock=lambda:30))
        assert p.read_bytes()==raw

@pytest.mark.parametrize('has_owner_proof',[False,True])
def test_expired_terminal_release_also_proves_prior_owner_stopped(tmp_path,has_owner_proof):
    p,start=created(tmp_path);a=acquire(p,start);stopped=stop_tracker(tmp_path,p,a);old=stopped['tracker']['leases']['task_agent'];proof=stopped_proof(tmp_path,old)
    options=kw(stopped,clock=lambda:120,token=old['token']);options.update(operation='release',failure_validator=failure_notes_validator)
    if not has_owner_proof:
        with pytest.raises(s.Conflict):l.lease_operation(p,**options)
    else:
        result=l.lease_operation(p,**options,stopped_owner_proof=proof,prior_owner_stopped_validator=validator)
        assert result['tracker']['leases']['task_agent'] is None
        with pytest.raises(s.Conflict,match='terminal tracker'):acquire(p,result,clock=lambda:130)

def test_terminal_cleanup_preserves_identity_without_requiring_source_availability(tmp_path):
    p,start=created(tmp_path);a=acquire(p,start);stopped=stop_tracker(tmp_path,p,a);d=stopped['tracker'];original=copy.deepcopy(d['bindings']['original_raw_source']);Path(original['path']).unlink()
    result=l.lease_operation(p,operation='release',token=d['leases']['task_agent']['token'],failure_validator=failure_notes_validator,**kw(stopped,clock=lambda:30))
    assert result['tracker']['bindings']['original_raw_source']==original
    assert result['tracker']['execution_state']=='STOPPED' and result['tracker']['leases']['task_agent'] is None

def test_proof_change_during_validation_aborts_original_lease(tmp_path):
    p,start=created(tmp_path);a=acquire(p,start);old=a['tracker']['leases']['task_agent'];proof=stopped_proof(tmp_path,old);raw=p.read_bytes()
    def callback(*args):Path(proof['path']).write_text('changed');return True
    with pytest.raises(s.Conflict,match='changed evidence'):
        l.lease_operation(p,operation='replace_expired',duration_ns=100,prior_lease=old,stopped_owner_proof=proof,prior_owner_stopped_validator=callback,**kw(a,owner='new',clock=lambda:120))
    assert p.read_bytes()==raw

def test_replacement_limit_survives_release_and_reacquisition(tmp_path):
    p,start=created(tmp_path);a=acquire(p,start);old=a['tracker']['leases']['task_agent'];proof=stopped_proof(tmp_path,old)
    b=l.lease_operation(p,operation='replace_expired',duration_ns=100,prior_lease=old,stopped_owner_proof=proof,prior_owner_stopped_validator=validator,**kw(a,owner='replacement',clock=lambda:120));lease=b['tracker']['leases']['task_agent']
    c=l.lease_operation(p,operation='release',token=lease['token'],**kw(b,owner='replacement',clock=lambda:130))
    e=acquire(p,c,owner='continued',clock=lambda:140);continued=e['tracker']['leases']['task_agent']
    assert l.validate_lifecycle(e['tracker'])==1
    with pytest.raises(s.Conflict,match='budget exhausted'):
        l.lease_operation(p,operation='replace_expired',duration_ns=100,prior_lease=continued,stopped_owner_proof=proof,prior_owner_stopped_validator=lambda *a:True,**kw(e,owner='second-replacement',clock=lambda:240))

def test_exact_prior_lease_required_even_when_callback_passes(tmp_path):
    p,start=created(tmp_path);a=acquire(p,start);old=a['tracker']['leases']['task_agent'];proof=stopped_proof(tmp_path,old);wrong={**old,'owner':'wrong'}
    with pytest.raises(s.Conflict,match='exact prior owner/token/scope'):
        l.lease_operation(p,operation='replace_expired',duration_ns=100,prior_lease=wrong,stopped_owner_proof=proof,prior_owner_stopped_validator=lambda *a:True,**kw(a,owner='new',clock=lambda:120))

def test_root_tokens_are_fresh_not_caller_supplied(tmp_path):
    p,start=created(tmp_path)
    with pytest.raises(s.Conflict,match='generated by root'):acquire(p,start,token='reused-token')

def test_intake_source_race_never_creates_tracker(tmp_path):
    options=fixtures(tmp_path);path=tmp_path/'0245.json'
    def validator(request):Path(request['original_source']['path']).write_bytes(b'changed after validation');return True
    options['intake_validator']=validator
    with pytest.raises(s.Conflict,match='changed evidence'):l.create_tracker(path,**options)
    assert not path.exists()

def test_renewal_cannot_commit_after_old_expiry(tmp_path):
    p,start=created(tmp_path);a=acquire(p,start);raw=p.read_bytes();clock=iter([30,120])
    with pytest.raises(s.Conflict,match='expired during validation'):
        l.lease_operation(p,operation='renew',token=a['tracker']['leases']['task_agent']['token'],duration_ns=200,**kw(a,clock=lambda:next(clock)))
    assert p.read_bytes()==raw

def test_authorization_denial_does_not_change_existing_lease(tmp_path):
    p,start=created(tmp_path);a=acquire(p,start);raw=p.read_bytes()
    with pytest.raises(s.Conflict,match='authorization rejected'):
        l.lease_operation(p,operation='release',token=a['tracker']['leases']['task_agent']['token'],**kw(a,authority=authority(False),clock=lambda:30))
    assert p.read_bytes()==raw

def test_real_two_expired_replacers_have_one_winner(tmp_path):
    p,start=created(tmp_path);a=acquire(p,start);old=a['tracker']['leases']['task_agent'];proof=stopped_proof(tmp_path,old);queue=mp.get_context('fork').Queue()
    def child(owner):
        try:
            l.lease_operation(p,operation='replace_expired',duration_ns=100,prior_lease=old,stopped_owner_proof=proof,prior_owner_stopped_validator=validator,**kw(a,owner=owner,clock=lambda:120));queue.put('ok')
        except s.Conflict:queue.put('conflict')
    jobs=[mp.get_context('fork').Process(target=child,args=(str(i),)) for i in range(2)]
    for j in jobs:j.start()
    for j in jobs:j.join(5);assert j.exitcode==0
    assert sorted(queue.get(timeout=1) for _ in jobs)==['conflict','ok']
    assert l.validate_lifecycle(s.strict_json(p.read_bytes()))==1

@pytest.mark.parametrize('kind',['create','acquire'])
def test_two_real_root_callers_have_one_winner(tmp_path,kind):
    options=fixtures(tmp_path);p=tmp_path/'0245.json'
    start=l.create_tracker(p,**options) if kind=='acquire' else None
    queue=mp.get_context('fork').Queue()
    def child():
        try:
            if kind=='create':l.create_tracker(p,**options)
            else:acquire(p,start)
            queue.put('ok')
        except s.Conflict:queue.put('conflict')
    jobs=[mp.get_context('fork').Process(target=child) for _ in range(2)]
    for j in jobs:j.start()
    for j in jobs:j.join(5);assert j.exitcode==0
    assert sorted(queue.get(timeout=1) for _ in jobs)==['conflict','ok']

@pytest.mark.parametrize('operation',['create','acquire','renew','release','replace_expired'])
@pytest.mark.parametrize('phase',['after_temp_fsync','after_publish'])
def test_actual_process_death_exposes_no_partial_tracker(tmp_path,operation,phase):
    options=fixtures(tmp_path);p=tmp_path/'0245.json';before=None
    if operation!='create':before=l.create_tracker(p,**options)
    if operation in {'renew','release','replace_expired'}:before=acquire(p,before)
    if operation=='replace_expired':proof=stopped_proof(tmp_path,before['tracker']['leases']['task_agent'])
    def child():
        hook=lambda point:os._exit(77) if point==phase else None
        if operation=='create':l.create_tracker(p,**options,fault_hook=hook)
        elif operation=='acquire':l.lease_operation(p,operation='acquire',duration_ns=100,**kw(before),fault_hook=hook)
        elif operation=='replace_expired':l.lease_operation(p,operation='replace_expired',duration_ns=100,prior_lease=before['tracker']['leases']['task_agent'],stopped_owner_proof=proof,prior_owner_stopped_validator=validator,**kw(before,owner='replacement',clock=lambda:120),fault_hook=hook)
        else:
            args=kw(before,clock=lambda:30,token=before['tracker']['leases']['task_agent']['token'])
            l.lease_operation(p,operation=operation,**args,**({'duration_ns':200} if operation=='renew' else {}),fault_hook=hook)
    proc=mp.get_context('fork').Process(target=child);proc.start();proc.join(5);assert proc.exitcode==77
    if operation=='create' and phase=='after_temp_fsync':assert not p.exists();l.create_tracker(p,**options)
    else:
        loaded=s.validate_tracker(s.strict_json(p.read_bytes()));l.validate_lifecycle(loaded)
        expected=0 if operation=='create' else before['revision']+(phase=='after_publish')
        assert loaded['revision']==expected

def test_real_prior_owner_exit_required_by_replacement_validator(tmp_path):
    p,start=created(tmp_path);a=acquire(p,start);old=a['tracker']['leases']['task_agent'];proof=stopped_proof(tmp_path,old)
    gate=mp.get_context('fork').Event();proc=mp.get_context('fork').Process(target=lambda:gate.wait(10));proc.start()
    options=kw(a,clock=lambda:120,owner='new');options.update(operation='replace_expired',duration_ns=100,prior_lease=old,stopped_owner_proof=proof)
    callback=lambda before,lease,e:validator(before,lease,e) and not proc.is_alive() and proc.exitcode==0
    with pytest.raises(s.Conflict):l.lease_operation(p,prior_owner_stopped_validator=callback,**options)
    gate.set();proc.join(5);assert proc.exitcode==0
    assert l.lease_operation(p,prior_owner_stopped_validator=callback,**options)['tracker']['leases']['task_agent']['owner']=='new'

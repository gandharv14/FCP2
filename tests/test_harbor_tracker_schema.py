import copy
import json
import multiprocessing as mp
import os
from pathlib import Path
import uuid
import pytest
from scripts.batch import harbor_tracker as s

def fresh():
    d={k:None for k in s.TOP}
    d.update(schema_version=2,revision=1,batch='test',requested_ids=['0245'],workbook_id='0245',raw_source={'path':'/private/source.xlsx','sha256':'a'*64},execution_state='running',bindings={},generation_ids=dict.fromkeys(('source','segmentation','task','release')),artifact_hashes={},current_confidence={'level':None,'reason_codes':[],'contributors':[],'computed_at_revision':1},leases=dict.fromkeys(s.LEASE_KINDS),repairs={'count':0,'by_signature':{},'history':[]},scheduler={'attempts':0,'resource_exhausted_retries':0,'resource_exhausted_first_at':None},agent_notes=dict.fromkeys(('path','sha256','diagnostic_signature','evidence_set_sha256','updated_at')),promotion_ids=dict.fromkeys(('task_generation_id','release_id','previous_release_id')))
    d['lane_state']={k:{'state':'pending','current_confidence':None,'confidence_history':[],'attempt':0} for k in s.LANES}
    for k in s.HISTORY:d[k]=[]
    d['leases']['task_agent']={'owner':'agent','token':str(uuid.uuid4()),'scope':'workbook:0245','acquired_at':1,'heartbeat_at':2,'expires_at':1000,'acquisition_revision':1}
    return d

def write(tmp_path,d=None):
    d=d or fresh();p=tmp_path/'0245.json';p.write_text(json.dumps(d));return p,d

def args(p,d,**kw):
    a=dict(expected_revision=d['revision'],expected_sha256=s.sha(p.read_bytes()),owner='agent',token=d['leases']['task_agent']['token'],scope='workbook:0245',allowed_fields={'current_gate'},now_ns=10)
    a.update(kw);return a

@pytest.mark.parametrize('mutation',[
    lambda d:d.update(execution_state='stopped'),
    lambda d:d.update(unregistered='value'),
    lambda d:d.update(revision=True),
    lambda d:d.update(current_confidence='high'),
    lambda d:d['lane_state'][s.LANES[0]].update(extra='value'),
    lambda d:d['bindings'].update(instruction={'path':'/instruction.md','sha256':'a'*64}),
    lambda d:d['bindings'].update(source_file={'path':'relative.xlsx','sha256':'a'*64}),
    lambda d:d['bindings'].update(source_file={'path':'/source.xlsx','sha256':'oops'}),
    lambda d:d['bindings'].update(source_file={'path':'/source.xlsx','aliases':[]}),
    lambda d:d['lane_state'].pop(s.LANES[0]),
    lambda d:d['lane_state'][s.LANES[0]].update(state='STOPPED'),
    lambda d:d['lane_state'][s.LANES[0]].update(current_confidence={}),
    lambda d:d.update(requested_ids=['0245','0245']),
    lambda d:d['leases']['task_agent'].update(scope='workbook:0334'),
    lambda d:d['leases']['task_agent'].update(token='predictable'),
    lambda d:d['leases']['task_agent'].update(heartbeat_at=1000),
    lambda d:d['leases']['task_agent'].update(acquisition_revision=2),
    lambda d:d.update(execution_state='publication_queued'),
    lambda d:d.update(execution_state='STOPPED',terminal_verdict='STOPPED',final_status='STOPPED'),
])
def test_schema_rejects(mutation):
    d=fresh();mutation(d)
    with pytest.raises(s.InvalidTracker):s.validate_tracker(d)

def failure():
    return dict(schema_version=1,**{'class':'infrastructure'},subclass='provider_auth_failed',stage='normalization',lane='harbor-normalization-engineer',gate='semantic_review',fixability='terminal',summary='Provider failed',why_not_promoted='Required review missing',first_seen=1,last_seen=2,occurrence_count=1,diagnostic_signature='b'*64,evidence_paths=[{'path':'/evidence.json','sha256':'c'*64}],repair_attempts=[],next_action='Restore provider')

def test_terminal_failure_and_bad_class():
    d=fresh();d.update(execution_state='STOPPED',terminal_verdict='STOPPED',final_status='STOPPED',promotion_failure=failure());s.validate_tracker(d)
    d['promotion_failure']['class']='quality_gate'
    with pytest.raises(s.InvalidTracker,match='unknown failure class'):s.validate_tracker(d)

def test_artifact_evidence_accepts_labeled_binding_and_path_maps():
    d=fresh();d['artifact_hashes']={'publication_journal':{'path':'/runs/publication.json','sha256':'a'*64},'/runs/proof.json':'b'*64,'relative/proof.json':'c'*64}
    s.validate_tracker(d)

@pytest.mark.parametrize('entry',[{'path':'/evidence.json'},{'path':None,'sha256':'a'*64},{'path':'/evidence.json','sha256':'bad'},{'path':'/evidence.json','sha256':'a'*64,'passed':True},42])
def test_artifact_evidence_rejects_malformed_labeled_binding(entry):
    d=fresh();d['artifact_hashes']['publication_journal']=entry
    with pytest.raises(s.InvalidTracker):s.validate_tracker(d)

def test_valid_mutation_and_heartbeat(tmp_path):
    p,d=write(tmp_path);result=s.cas_mutate(p,mutate=lambda x:x.update(current_gate='source'),**args(p,d))
    assert result['revision']==2 and result['tracker']['leases']['task_agent']['heartbeat_at']==10
    assert result['sha256']==s.sha(p.read_bytes())

def test_lease_expiring_during_validation_cannot_commit(tmp_path,monkeypatch):
    p,d=write(tmp_path);raw=p.read_bytes();clock=iter([10,1000]);monkeypatch.setattr(s.time,'time_ns',lambda:next(clock))
    with pytest.raises(s.Conflict,match='expired during validation'):
        s.cas_mutate(p,mutate=lambda x:x.update(current_gate='source'),**args(p,d,now_ns=None))
    assert p.read_bytes()==raw

@pytest.mark.parametrize('override',[{'expected_revision':0},{'expected_sha256':'0'*64},{'owner':'other'},{'token':str(uuid.uuid4())},{'scope':'workbook:0334'},{'now_ns':1000},{'now_ns':1}])
def test_cas_rejects_stale_and_wrong_lease(tmp_path,override):
    p,d=write(tmp_path);raw=p.read_bytes()
    with pytest.raises(s.Conflict):s.cas_mutate(p,mutate=lambda x:x.update(current_gate='source'),**args(p,d,**override))
    assert p.read_bytes()==raw

def test_unowned_lane_and_append_only_history(tmp_path):
    p,d=write(tmp_path);raw=p.read_bytes()
    with pytest.raises(s.Conflict,match='unowned lane'):
        s.cas_mutate(p,mutate=lambda x:x['lane_state'][s.LANES[1]].update(state='running'),**args(p,d,allowed_fields={'lane_state'},allowed_lanes=[s.LANES[0]]))
    d['handoffs']=[{'evidence':'retained'}];p.write_text(json.dumps(d));raw=p.read_bytes()
    with pytest.raises(s.Conflict,match='history rewrite'):
        s.cas_mutate(p,mutate=lambda x:x.update(handoffs=[]),**args(p,d,allowed_fields={'handoffs'}))
    assert p.read_bytes()==raw

def test_attempt_budget_cannot_reset(tmp_path):
    d=fresh();d['lane_state'][s.LANES[0]]['attempt']=2;p,d=write(tmp_path,d)
    with pytest.raises(s.Conflict,match='budget decreased'):
        s.cas_mutate(p,mutate=lambda x:x['lane_state'][s.LANES[0]].update(attempt=0),**args(p,d,allowed_fields={'lane_state'},allowed_lanes=[s.LANES[0]]))

@pytest.mark.parametrize('replacement',[{}, {'original':0}, {'other':2}, {'original':True}])
def test_per_signature_consumed_repairs_cannot_reset(tmp_path,replacement):
    d=fresh();d['repairs'].update(count=2,by_signature={'original':2});p,d=write(tmp_path,d);raw=p.read_bytes()
    with pytest.raises(s.Conflict,match='per-signature repair budget decreased'):
        s.cas_mutate(p,mutate=lambda x:x['repairs'].update(by_signature=replacement),**args(p,d,allowed_fields={'repairs'}))
    assert p.read_bytes()==raw

def test_per_signature_increment_and_new_signature_accepted(tmp_path):
    d=fresh();d['repairs'].update(count=2,by_signature={'original':2});p,d=write(tmp_path,d)
    result=s.cas_mutate(p,mutate=lambda x:x['repairs'].update(count=4,by_signature={'original':3,'new':1}),**args(p,d,allowed_fields={'repairs'}))
    assert result['tracker']['repairs']['by_signature']=={'original':3,'new':1}

@pytest.mark.parametrize('consumed',[-1,False,'1',None])
def test_signature_counts_have_integer_schema(consumed):
    d=fresh();d['repairs']['by_signature']={'signature':consumed}
    with pytest.raises(s.InvalidTracker):s.validate_tracker(d)

@pytest.mark.parametrize('operation',['clear','replace','stop'])
def test_failure_transitions_require_independent_validator(tmp_path,operation):
    d=fresh();d['promotion_failure']=failure();p,d=write(tmp_path,d);raw=p.read_bytes()
    def mutate(x):
        if operation=='clear':x['promotion_failure']=None
        elif operation=='replace':x['promotion_failure']['summary']='Changed failure'
        else:x.update(execution_state='STOPPED',terminal_verdict='STOPPED',final_status='STOPPED')
    kw=args(p,d,allowed_fields={'promotion_failure','execution_state','terminal_verdict','final_status'})
    with pytest.raises(s.Conflict,match='failure/evidence/notes validator required'):s.cas_mutate(p,mutate=mutate,**kw)
    for result in (False,None,{},1):
        with pytest.raises(s.Conflict,match='failure/evidence/notes validator did not pass'):s.cas_mutate(p,mutate=mutate,failure_validator=lambda *a:result,**kw)
    assert p.read_bytes()==raw
    seen=[]
    s.cas_mutate(p,mutate=mutate,failure_validator=lambda a,b,events:(seen.append(events) or True),**kw)
    assert seen==[frozenset({operation})]

def test_clear_depends_on_independently_revalidated_gate(tmp_path):
    d=fresh();d['promotion_failure']=failure();p,d=write(tmp_path,d);proof=tmp_path/'owning-gate.json';proof.write_text('{"valid":true}');expected=s.sha(proof.read_bytes());raw=p.read_bytes()
    def validator(before,after,events):
        assert events=={'clear'} and before['promotion_failure']['diagnostic_signature']=='b'*64
        return s.sha(proof.read_bytes())==expected and json.loads(proof.read_text())['valid'] is True
    kw=args(p,d,allowed_fields={'promotion_failure'})
    proof.write_text('{"valid":false}')
    with pytest.raises(s.Conflict,match='validator did not pass'):s.cas_mutate(p,mutate=lambda x:x.update(promotion_failure=None),failure_validator=validator,**kw)
    assert p.read_bytes()==raw
    proof.write_text('{"valid":true}');s.cas_mutate(p,mutate=lambda x:x.update(promotion_failure=None),failure_validator=validator,**kw)

@pytest.mark.parametrize('corrupt',['signature','evidence_set','notes_bytes','evidence_bytes','valid'])
def test_stopping_requires_exact_evidence_and_bound_notes(tmp_path,corrupt):
    d=fresh();d['promotion_failure']=failure();evidence=tmp_path/'diagnostic.json';evidence.write_text('{"reason":"provider unavailable"}')
    record={'path':str(evidence),'sha256':s.sha(evidence.read_bytes())};d['promotion_failure']['evidence_paths']=[record]
    evidence_set=s.sha(json.dumps([record],sort_keys=True,separators=(',',':')).encode());signature=d['promotion_failure']['diagnostic_signature']
    notes=tmp_path/'notes.md';notes.write_text(signature+'\n'+evidence_set+'\n')
    d['agent_notes'].update(path=str(notes),sha256=s.sha(notes.read_bytes()),diagnostic_signature=signature,evidence_set_sha256=evidence_set,updated_at=2)
    p,d=write(tmp_path,d);raw=p.read_bytes()
    def stop(x):
        x.update(execution_state='STOPPED',terminal_verdict='STOPPED',final_status='STOPPED')
        if corrupt=='signature':x['agent_notes']['diagnostic_signature']='c'*64
        if corrupt=='evidence_set':x['agent_notes']['evidence_set_sha256']='c'*64
    if corrupt=='notes_bytes':notes.write_text('altered')
    if corrupt=='evidence_bytes':evidence.write_text('altered')
    def validator(before,after,events):
        f=after['promotion_failure'];n=after['agent_notes'];records=f['evidence_paths'];computed=s.sha(json.dumps(records,sort_keys=True,separators=(',',':')).encode())
        return events=={'stop'} and all(s.sha(Path(e['path']).read_bytes())==e['sha256'] for e in records) and n['diagnostic_signature']==f['diagnostic_signature'] and n['evidence_set_sha256']==computed and s.sha(Path(n['path']).read_bytes())==n['sha256'] and f['diagnostic_signature'] in Path(n['path']).read_text() and computed in Path(n['path']).read_text()
    kw=args(p,d,allowed_fields={'execution_state','terminal_verdict','final_status','agent_notes'})
    if corrupt=='valid':assert s.cas_mutate(p,mutate=stop,failure_validator=validator,**kw)['tracker']['execution_state']=='STOPPED'
    else:
        with pytest.raises(s.Conflict,match='validator did not pass'):s.cas_mutate(p,mutate=stop,failure_validator=validator,**kw)
        assert p.read_bytes()==raw

def test_validator_cannot_mutate_approved_payload(tmp_path):
    p,d=write(tmp_path)
    def validator(after):after['requested_ids']=['0334'];return True
    result=s.cas_mutate(p,mutate=qualify,**args(p,d,allowed_fields={'lane_state','current_confidence','execution_state','terminal_verdict'},allowed_lanes=s.LANES,quality_validator=validator))
    assert result['tracker']['requested_ids']==['0245']

def test_binding_replacement_requires_prior_and_independent_validator(tmp_path):
    p,d=write(tmp_path);mut=lambda x:x['bindings'].update(source_file={'path':'/source.xlsx','sha256':'c'*64})
    for opts in ({},{'binding_replacements':{'source_file':None}},{'binding_replacements':{'source_file':{'sha256':'d'*64}},'evidence_validator':lambda *a:None}):
        with pytest.raises(s.Conflict):s.cas_mutate(p,mutate=mut,**args(p,d,allowed_fields={'bindings'},**opts))
    calls=[]
    result=s.cas_mutate(p,mutate=mut,**args(p,d,allowed_fields={'bindings'},binding_replacements={'source_file':None},evidence_validator=lambda a,b,c:(calls.append(c) or True)))
    assert calls==[{'source_file'}] and result['revision']==2

def test_curation_immutable_even_with_replacement_permission(tmp_path):
    d=fresh();d['bindings']['curation']={'path':'/curation.toml','sha256':'a'*64};p,d=write(tmp_path,d)
    with pytest.raises(s.Conflict,match='immutable source/curation'):
        s.cas_mutate(p,mutate=lambda x:x['bindings']['curation'].update(sha256='b'*64),**args(p,d,allowed_fields={'bindings'},binding_replacements={'curation':d['bindings']['curation']},evidence_validator=lambda *a:None))

def qualify(d):
    for lane in d['lane_state'].values():lane.update(state='passed',current_confidence='high')
    d['current_confidence'].update(level='high',contributors=list(s.LANES));d.update(execution_state='publication_queued',terminal_verdict='PUBLICATION_READY')

def test_schema_is_not_publication_quality(tmp_path):
    p,d=write(tmp_path);fields={'lane_state','current_confidence','execution_state','terminal_verdict'}
    with pytest.raises(s.Conflict,match='qualification validator'):
        s.cas_mutate(p,mutate=qualify,**args(p,d,allowed_fields=fields,allowed_lanes=s.LANES))
    with pytest.raises(ValueError,match='proof stale'):
        s.cas_mutate(p,mutate=qualify,**args(p,d,allowed_fields=fields,allowed_lanes=s.LANES,quality_validator=lambda _:(_ for _ in ()).throw(ValueError('proof stale'))))
    s.cas_mutate(p,mutate=qualify,**args(p,d,allowed_fields=fields,allowed_lanes=s.LANES,quality_validator=lambda _:True))

@pytest.mark.parametrize('result',[False,None,{},1])
def test_validator_result_must_be_explicit_pass(tmp_path,result):
    p,d=write(tmp_path);raw=p.read_bytes()
    with pytest.raises(s.Conflict,match='did not pass'):
        s.cas_mutate(p,mutate=qualify,**args(p,d,allowed_fields={'lane_state','current_confidence','execution_state','terminal_verdict'},allowed_lanes=s.LANES,quality_validator=lambda _:result))
    assert p.read_bytes()==raw

def test_symlink_ancestry_and_duplicate_json(tmp_path):
    p,d=write(tmp_path);link=tmp_path/'alias';link.symlink_to(tmp_path,target_is_directory=True)
    with pytest.raises(s.Conflict):s.cas_mutate(link/'0245.json',mutate=lambda x:None,**args(p,d))
    with pytest.raises(ValueError,match='duplicate'):s.strict_json('{"revision":1,"revision":2}')

def test_race_before_commit_preserves_external_change(tmp_path):
    p,d=write(tmp_path)
    def hook(phase):
        if phase=='after_temp_fsync':p.write_text('external mutation')
    with pytest.raises(s.Conflict,match='changed before replacement'):
        s.cas_mutate(p,mutate=lambda x:x.update(current_gate='source'),fault_hook=hook,**args(p,d))
    assert p.read_text()=='external mutation' and not list(tmp_path.glob('.0245.json.*'))

@pytest.mark.parametrize('phase,revision',[('after_temp_fsync',1),('after_replace',2)])
def test_real_process_interruption_is_old_or_complete_new(tmp_path,phase,revision):
    p,d=write(tmp_path);kw=args(p,d)
    def child():s.cas_mutate(p,mutate=lambda x:x.update(current_gate='source'),fault_hook=lambda point:os._exit(77) if point==phase else None,**kw)
    proc=mp.get_context('fork').Process(target=child);proc.start();proc.join(5);assert proc.exitcode==77
    loaded=s.validate_tracker(s.strict_json(p.read_bytes()));assert loaded['revision']==revision
    # Kernel releases the flock after death; no lock stealing is needed.
    s.cas_mutate(p,mutate=lambda x:None,**args(p,loaded,now_ns=11))

def test_two_writers_one_cas_winner(tmp_path):
    p,d=write(tmp_path);kw=args(p,d);q=mp.get_context('fork').Queue()
    def child():
        try:s.cas_mutate(p,mutate=lambda x:x.update(current_gate='source'),**kw);q.put('ok')
        except s.Conflict:q.put('conflict')
    jobs=[mp.get_context('fork').Process(target=child) for _ in range(2)]
    for j in jobs:j.start()
    for j in jobs:j.join(5);assert j.exitcode==0
    assert sorted([q.get(timeout=1),q.get(timeout=1)])==['conflict','ok']

def test_lossless_migration_never_relabels_or_writes_original(tmp_path):
    d=fresh();d.update(execution_state='stopped',unknown={'sensitive_history':[1,2,3]},promotion_failure=failure());d['promotion_failure']['class']='quality_gate';p,d=write(tmp_path,d);raw=p.read_bytes()
    report=s.migration_proposal(p,tmp_path/'proposal');assert p.read_bytes()==raw
    assert Path(report['historical_snapshot']['path']).read_bytes()==raw
    assert report['historical_snapshot']['sha256']==s.sha(raw) and report['candidate'] is None
    issues={x['pointer'] for x in report['issues']};assert {'/unknown','/execution_state','/promotion_failure/class'}<=issues

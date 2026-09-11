import urllib.error
from dataclasses import asdict,replace
from pathlib import Path
from types import SimpleNamespace
import io
import json
import time
import uuid
import pytest
from scripts.batch import harbor_normalization_producer as N
from scripts.batch import harbor_normalization_compact as K
from scripts.batch import harbor_normalization_partitioned as P
from scripts.batch import harbor_model_executor as E
from scripts.batch.harbor_admission import read,record
from test_harbor_normalization_producer import setup


def respond(monkeypatch,selection,*,invalid=None,calls=None):
    def opened(request,timeout):
        payload=json.loads(request.data);user=json.loads(payload['messages'][1]['content'])
        body=K.expand(user['evidence']);refs=user['partition_references']
        if calls is not None:calls.append(refs)
        review={k:body[k] for k in ['draft_sha256','source_sha256','baseline_sha256']}
        review.update(schema_version='normalization-review/v1',reviewer='Synthetic independent review',cells={})
        for row in body['candidates']:
            review['cells'][row['reference']]=dict(eligible=True,reason='Historical primitive fixture.',
                evidence_refs=[row['reference'],row['row']['draft_id']],entity='Fixture',period='Past',scenario='Base',basis='Native source units')
        if invalid and invalid(refs):review['schema']=review.pop('schema_version')
        response=dict(id=str(uuid.uuid4()),object='chat.completion',model=selection.policy.model,created=time.time_ns()//10**9,
            choices=[dict(finish_reason='stop',message=dict(role='assistant',content=json.dumps(review)))])
        return io.BytesIO(json.dumps(response).encode())
    monkeypatch.setattr(E,'_open',opened)


@pytest.fixture
def prepared(setup,monkeypatch,tmp_path):
    s,campaign,_,_=setup();monkeypatch.setattr(K,'MAX_REFERENCES',2)
    old=N.prepare(s);old_bytes={p:p.read_bytes() for p in s.run.iterdir() if p.is_file()}
    current=P.prepare(s,campaign,tmp_path/'partitioned')
    assert len(read(current)['partitions'])==2
    return s,campaign,current,old,old_bytes


def test_exact_partition_calls_current_emission_and_projection(prepared,monkeypatch):
    from scripts.batch.harbor_completed_lanes import normalization_projection
    s,campaign,current,old,old_bytes=prepared;calls=[];respond(monkeypatch,s,calls=calls)
    for i in range(2):assert P.dispatch(current,i,1,credential='synthetic').status=='completed'
    result=read(P.emit_phase1(current));assert result['status']=='phase1_evidence_ready'
    assert len(calls)==2 and len(set(r for refs in calls for r in refs))==4
    b={**s.bindings,'normalization_partitioned_review':result['normalization_partitioned_review'],
        'normalized_phase1':result['phase1'],'normalization_draft':record(s.run/'draft.json'),
        'normalized_final':record(s.run/'normalized.json'),'normalization_report':record(s.run/'normalization_report.json')}
    selected=SimpleNamespace(workbook=s.workbook,bindings=b,normalization_run=s.run,normalization_call=None,
        normalization_prepared=current,model_policy=s.policy)
    assert normalization_projection(selected,phase1=True)['semantic_attempt']==1
    assert P.emit_phase1(current)==record(s.run/'phase1-result.json')
    assert all(path.read_bytes()==raw for path,raw in old_bytes.items())


def test_only_invalid_partition_gets_second_round_and_budget_is_complete(prepared,monkeypatch):
    s,campaign,current,old,_=prepared;first_refs=read(current)['partitions'][0]['references']
    respond(monkeypatch,s,invalid=lambda refs:refs==first_refs)
    for i in range(2):P.dispatch(current,i,1,credential='synthetic')
    with pytest.raises(ValueError,match='has not been submitted'):P.aggregate(current)
    respond(monkeypatch,s)
    with pytest.raises(ValueError,match='valid partition'):P.dispatch(current,1,2,credential='synthetic')
    P.dispatch(current,0,2,credential='synthetic');proof=P.aggregate(current)
    assert proof['semantic_attempt']==2 and len(proof['partitions'])==3
    assert [x['ordinal'] for x in proof['partitions']]==[1,2,1]
    with pytest.raises(ValueError):P.dispatch(current,0,3,credential='synthetic')


def test_uncertain_first_call_cannot_start_second_round_or_new_plan(prepared,monkeypatch,tmp_path):
    s,campaign,current,old,_=prepared
    monkeypatch.setattr(E,'_open',lambda *a,**k:(_ for _ in ()).throw(urllib.error.URLError('opaque synthetic delivery state')))
    assert P.dispatch(current,0,1,credential='synthetic').status=='incomplete'
    with pytest.raises(ValueError,match='incomplete'):P.dispatch(current,0,2,credential='synthetic')
    with pytest.raises(ValueError):P.prepare(replace(s,budget=replace(s.budget,attempt_id=str(uuid.uuid4()))),campaign,tmp_path/'replacement')


def test_submitted_old_protocol_blocks_transition(setup,monkeypatch,tmp_path):
    from test_harbor_normalization_producer import mock_review
    s,campaign,_,_=setup();old=N.prepare(s);mock_review(monkeypatch,s);N.dispatch(s,old,campaign,credential='synthetic')
    with pytest.raises(ValueError,match='already submitted'):P.prepare(s,campaign,tmp_path/'partitioned')


def test_oversize_old_context_retains_failure_evidence_then_partitions(setup,monkeypatch,tmp_path):
    s,campaign,_,_=setup();monkeypatch.setattr(N,'MAX_CONTEXT_BYTES',1)
    with pytest.raises(ValueError,match='exceeds bounded'):N.prepare(s)
    assert not (s.run/'prepared.json').exists()
    evidence=record(s.run/'review-evidence.json')
    current=P.prepare(s,campaign,tmp_path/'partitioned')
    assert read(current)['legacy']['review-evidence.json']==evidence
    assert read(current)['prior_attempt_submitted'] is False


@pytest.mark.parametrize('target',['request','evidence','source','budget','owner'])
def test_tampered_current_or_inherited_evidence_rejects_before_dispatch(prepared,monkeypatch,target):
    s,campaign,current,old,_=prepared;m=read(current)
    binding={'request':m['partitions'][0]['request'],'evidence':m['legacy']['review-evidence.json'],
        'source':s.bindings['source_file'],'budget':s.budget.evidence[0],
        'owner':record(P.owner(s,campaign)/'owner.json')}[target]
    p=Path(binding['path']);p.write_bytes(p.read_bytes()+b' ')
    monkeypatch.setattr(E,'_open',lambda *a,**k:pytest.fail('must reject before model call'))
    with pytest.raises(ValueError):P.dispatch(current,0,1,credential='synthetic')


def test_root_cannot_repartition_same_scope_at_new_path(prepared,tmp_path):
    s,campaign,current,old,_=prepared
    with pytest.raises(ValueError,match='delegated to another'):
        P.prepare(s,campaign,tmp_path/'different-partition-plan')


def test_obsolete_single_call_is_explicitly_reserved_after_transition(prepared,monkeypatch):
    s,campaign,current,old,_=prepared
    monkeypatch.setattr(E,'_open',lambda *a,**k:pytest.fail('obsolete protocol cannot call model'))
    with pytest.raises(ValueError,match='semantic scope is delegated'):
        N.dispatch(s,old,campaign,credential='synthetic')


def test_broken_delegation_symlink_cannot_reenable_obsolete_call(prepared,monkeypatch,tmp_path):
    s,campaign,current,old,_=prepared;marker=P.old_location(s,campaign)/'scope-delegation.json'
    marker.unlink();marker.symlink_to(tmp_path/'missing-marker')
    monkeypatch.setattr(E,'_open',lambda *a,**k:pytest.fail('obsolete scope must fail closed'))
    with pytest.raises(ValueError,match='scope is delegated'):N.dispatch(s,old,campaign,credential='synthetic')


def test_incomplete_and_duplicated_aggregate_decisions_fail(prepared,monkeypatch):
    s,campaign,current,_,_=prepared;respond(monkeypatch,s)
    P.dispatch(current,0,1,credential='synthetic')
    with pytest.raises(ValueError):P.emit_phase1(current)
    assert not (s.run/'normalization-review.json').exists()
    P.dispatch(current,1,1,credential='synthetic');value=P.aggregate(current)
    value['review']['cells'].pop(next(iter(value['review']['cells'])))
    forged=N.keep(Path(read(current)['root'])/'forged-aggregate.json',value)
    _,rows=N.analyse(s)
    with pytest.raises(ValueError,match='altered or stale'):
        P.validate(forged,prepared=current,source=s.bindings['source_file'],baseline=s.bindings['baseline_inputs'],
            draft=record(s.run/'draft.json'),rows=rows,policy=s.policy,bindings=s.bindings)


def test_transient_transport_reuses_same_exact_partition(prepared,monkeypatch):
    import urllib.error
    s,campaign,current,_,_=prepared;respond(monkeypatch,s);opened=E._open;seen=[];sleeps=[]
    def retry(request,timeout):
        seen.append(request.data)
        if len(seen)<=3:raise urllib.error.HTTPError(request.full_url,429,'synthetic',{'Retry-After':'0'},io.BytesIO(b'{}'))
        return opened(request,timeout)
    monkeypatch.setattr(E,'_open',retry);monkeypatch.setattr(E.time,'sleep',sleeps.append)
    assert P.dispatch(current,0,1,credential='synthetic').status=='completed'
    assert len(seen)==4 and len(set(seen))==1 and sleeps==[0,0,0]


def test_same_owner_concurrent_dispatch_fails_before_second_transport(prepared,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    s,campaign,current,_,_=prepared;respond(monkeypatch,s);opened=E._open;entered=Event();release=Event()
    def blocked(request,timeout):
        entered.set();assert release.wait(5);return opened(request,timeout)
    monkeypatch.setattr(E,'_open',blocked)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(P.dispatch,current,0,1,credential='synthetic')
        try:
            assert entered.wait(5)
            with pytest.raises(ValueError,match='already executing'):P.dispatch(current,1,1,credential='synthetic')
        finally:release.set()
        assert future.result(timeout=5).status=='completed'


def test_explicit_unsent_sibling_transition_preserves_old_request_and_budget(setup,tmp_path,monkeypatch):
    s,campaign,_,_=setup();old=N.prepare(s)
    snapshots={p:p.read_bytes() for p in s.run.iterdir() if p.is_file()}
    new=replace(s,run=tmp_path/'fresh'/s.run.name)
    prepared=P.prepare(new,campaign,tmp_path/'parts',prior_plan=record(s.run/'production-selection.json'))
    m=read(prepared)
    assert m['previous']['original_budget']==m['original_budget']
    assert m['previous']['prior_response_reused'] is False
    assert m['previous']['legacy']['prepared.json']==old
    respond(monkeypatch,new)
    P.dispatch(prepared,0,1,credential='synthetic')
    assert all(p.read_bytes()==raw for p,raw in snapshots.items())
    assert P.aggregate(prepared)['semantic_attempt']==1


@pytest.mark.parametrize('change',['budget','draft','candidate','prepared','source_binding'])
def test_sibling_transition_cannot_change_original_context_or_attempt(setup,tmp_path,change):
    s,campaign,_,_=setup();N.prepare(s);new=replace(s,run=tmp_path/'fresh'/s.run.name)
    if change=='budget':new=replace(new,budget=replace(new.budget,attempt_id=str(uuid.uuid4())))
    elif change=='source_binding':
        plan=read(record(s.run/'production-selection.json'));plan['bindings']['source_file']['sha256']='0'*64
        (s.run/'production-selection.json').write_bytes(E.canonical(plan))
    else:
        name={'draft':'draft.json','candidate':'analysis.json','prepared':'prepared.json'}[change]
        value=read(record(s.run/name))
        if change=='draft':value['rows'][0]['variable_name']='Altered'
        elif change=='candidate':value[0]['reference']='Other!A1'
        else:value['candidate_count']=1
        (s.run/name).write_bytes(E.canonical(value))
    with pytest.raises(ValueError):P.prepare(new,campaign,tmp_path/'parts',prior_plan=record(s.run/'production-selection.json'))


def test_aggregate_independently_checks_logical_round_chronology(prepared,monkeypatch):
    s,campaign,current,_,_=prepared;respond(monkeypatch,s,invalid=lambda refs:True)
    P.dispatch(current,0,1,credential='synthetic');respond(monkeypatch,s)
    # Simulate an incorrectly routed root caller bypassing the owning dispatcher.
    E.execute(P._prepared_call(current,0,2),campaign,credential='synthetic')
    P.dispatch(current,1,1,credential='synthetic')
    with pytest.raises(ValueError,match='before the complete first round'):P.aggregate(current)


@pytest.mark.parametrize('mode',['invalid_json','incomplete','wrong_model'])
def test_only_attributable_complete_bad_json_can_use_bounded_second_round(prepared,monkeypatch,mode):
    s,campaign,current,_,_=prepared;respond(monkeypatch,s);opened=E._open
    def bad(request,timeout):
        response=json.loads(opened(request,timeout).read())
        response['choices'][0]['message']['content']='{'
        if mode=='incomplete':response['choices'][0]['finish_reason']='length'
        if mode=='wrong_model':response['model']='unexpected-model'
        return io.BytesIO(json.dumps(response).encode())
    monkeypatch.setattr(E,'_open',bad);assert P.dispatch(current,0,1,credential='synthetic').status=='response_rejected'
    respond(monkeypatch,s);P.dispatch(current,1,1,credential='synthetic')
    if mode!='invalid_json':
        with pytest.raises(ValueError):P.dispatch(current,0,2,credential='synthetic')
    else:
        assert P.dispatch(current,0,2,credential='synthetic').status=='completed'
        proof=P.aggregate(current);assert proof['semantic_attempt']==2
        assert proof['partitions'][0]['call'] is None and proof['partitions'][0]['rejected_response']


def test_interrupted_delegation_blocks_old_protocol_and_resumes_same_plan(setup,monkeypatch,tmp_path):
    s,campaign,_,_=setup();old=N.prepare(s);keep=N.keep
    def interruption(path,value):
        if path.name=='owner.json':raise SystemExit('synthetic interruption after original-scope reservation')
        return keep(path,value)
    with monkeypatch.context() as m:
        m.setattr(N,'keep',interruption)
        with pytest.raises(SystemExit):P.prepare(s,campaign,tmp_path/'parts')
    monkeypatch.setattr(E,'_open',lambda *a,**k:pytest.fail('old call must remain blocked'))
    with pytest.raises(ValueError,match='scope is delegated'):N.dispatch(s,old,campaign,credential='synthetic')
    prepared=P.prepare(s,campaign,tmp_path/'parts')
    assert read(record(P.owner(s,campaign)/'owner.json'))['prepared']==prepared


def test_offline_cli_uses_exact_selected_records(setup,tmp_path,capsys):
    s,campaign,_,_=setup();root_selection=N.keep(tmp_path/'selected.json',N.json_value(asdict(s)))
    selected_campaign=N.keep(tmp_path/'campaign.json',N.json_value(asdict(campaign)))
    capsys.readouterr()
    assert P.main(['prepare','--selection',root_selection['path'],'--selection-sha256',root_selection['sha256'],
        '--campaign',selected_campaign['path'],'--campaign-sha256',selected_campaign['sha256'],'--root',str(tmp_path/'parts')])==0
    result=json.loads(capsys.readouterr().out)
    assert read(result)['schema_version']==P.SCHEMA
    with pytest.raises(ValueError):P.main(['aggregate','--prepared',result['path'],'--prepared-sha256','0'*64])


def test_final_static_consumers_validate_many_calls_and_export_every_raw_journal(prepared,monkeypatch,tmp_path):
    from scripts.batch import harbor_readiness as S,harbor_tracker as T
    from scripts.batch.harbor_complete_readiness import evidence_records
    s,campaign,current,_,_=prepared;respond(monkeypatch,s)
    calls=[P.dispatch(current,i,1,credential='synthetic').call for i in range(2)]
    result=read(P.emit_phase1(current))
    b={**s.bindings,'normalization_partitioned_review':result['normalization_partitioned_review'],
        'normalized_phase1':result['phase1'],'normalization_draft':record(s.run/'draft.json'),
        'normalized_final':record(s.run/'normalized.json'),'normalization_report':record(s.run/'normalization_report.json')}
    assert 'normalization_partitioned_review' in T.BINDINGS
    stage=tmp_path/'stage';(stage/'tests').mkdir(parents=True)
    placeholder=N.keep(stage/'tests/pipeline_bindings.json',read(record(s.run/'production-selection.json'))['pipeline_bindings'])
    nat=tmp_path/'nat';nat.mkdir();N.keep(nat/'placeholder.json',{})
    selected=SimpleNamespace(workbook=s.workbook,stage=stage,bindings=b,normalization_run=s.run,
        normalization_call=None,normalization_prepared=current,model_policy=s.policy,naturalization_root=nat,
        pre_dialogue_witness=None,mcp_build_a=None,mcp_build_b=None,normalization_records=(),
        dialogue_claims=placeholder,dialogue_review=placeholder,dialogue_noop=None,dialogue_reviewed_draft=None,
        scope_evidence=None,dialogue_scope_receipt=None,allowlist=None,naturalization_request=calls[0].request,naturalization_writer_request=calls[1].request,
        naturalization_call=calls[0],naturalization_writer_call=calls[1],
        context_trees={str(p):S.publication._tree_hash(p)[0] for p in (s.run,nat)})
    # Other lane Calls above are only existing file witnesses for the context
    # subgate. No NAT/disclosure/dialogue/runtime qualification is asserted here.
    assert S.normalized_content(selected)['variables']==4
    context=S.context_bindings(selected);exported={x['path']:x['sha256'] for x in evidence_records(context)}
    for call in calls:
        for binding in (call.request,call.submitted,call.response,call.completed,call.parsed):assert exported[binding['path']]==binding['sha256']
    selected.normalization_call=calls[0]
    with pytest.raises(ValueError,match='one Call'):S.normalized_content(selected)

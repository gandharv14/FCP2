import urllib.error
"""Tiny real-generation composition tests: actual notes/CAS and owning gates."""
import dataclasses
import io
import json
import time
import uuid
from pathlib import Path
import pytest
from test_harbor_admission import setup
from test_harbor_workbook_bootstrap import selected as bootstrap_selected,baseline
from scripts.batch import harbor_environment_worker as W
from scripts.batch import harbor_model_executor as E
from scripts.batch.harbor_admission import record,read

@pytest.fixture
def selected(setup,monkeypatch):
    import xl_segment
    original=xl_segment.segment
    def named(workbook,args):
        path=Path(args.out)/workbook/'curation.toml'
        path.write_text(path.read_text()+'name = "Synthetic doubled input"\nsheet = "Sheet"\n')
        return original(workbook,args)
    monkeypatch.setattr(xl_segment,'segment',named)
    return bootstrap_selected.__wrapped__(setup,monkeypatch)

@pytest.fixture
def worker(selected,monkeypatch):
    import xl_variable_source_audit as audit
    selected=(dataclasses.replace(selected[0],model_policy=W.bootstrap.Policy(audit.DEFAULT_PROJECT_ID)),*selected[1:])
    b,prepared,lease,bindings,receipt=baseline(selected,monkeypatch)
    home=b.run.parent;taxonomy=home/'taxonomy.json';taxonomy.write_text('{}')
    campaign=E.Campaign(home/'calls',home/'slots')
    monkeypatch.setenv('HARBOR_MODEL_SLOTS','2');monkeypatch.setenv('HARBOR_MODEL_SLOT_ROOT',str(campaign.slot_root))
    budget=W.normalization.Budget(1,2,str(uuid.uuid4()),(prepared,))
    selection=W.Selection(b,prepared,home/'worker',campaign,record(taxonomy),budget)
    return selection,lease


def responses(monkeypatch):
    seen=[]
    def opened(request,timeout):
        seen.append(json.loads(request.data));messages=seen[-1]['messages']
        user=messages[1]['content'];lines=user.split('(NDJSON):\n')[1].splitlines();rows=[json.loads(line) for line in lines]
        content='| Variable | Inventory row IDs | Plausible external source(s) |\n|---|---|---|\n'+'\n'.join('| Supplied assumption | '+r['row_id']+' | Supplied workbook |' for r in rows)
        class Response(io.BytesIO):
            def __enter__(self):return self
            def __exit__(self,*a):self.close()
        value={'id':str(uuid.uuid4()),'object':'chat.completion','model':'openai/gpt-5.6-sol','created':time.time_ns()//10**9,'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':content}}]}
        return Response(json.dumps(value).encode())
    monkeypatch.setattr(E,'_open',opened);return seen


def test_real_source_baseline_audit_lane_commits_only_after_owning_validation(worker,monkeypatch):
    s,lease=worker;seen=responses(monkeypatch)
    pending=W.advance(s,lease);assert pending['status']=='PENDING_MODEL' and not seen
    result=W.advance(s,lease,dispatch=True,credential='synthetic-token')
    assert result['status']=='STEP_COMPLETE',result
    assert result['step']=='research' and result['lease'].revision==lease.revision+1
    tracker=read(record(s.bootstrap.policy.tracker_root/'0001.json'))
    assert tracker['lane_state']['harbor-research-auditor']['state']=='passed'
    assert len(seen)==1 and tracker['promotion_ids']['task_generation_id'] is None
    assert result['selection'].content.bindings['curation']==read(s.prepared)['validated']['bindings']['curation']
    assert W.encode(result['selection'])['type']=='ReadinessSelection'


def test_unknown_transport_is_unresolved_without_tracker_failure_or_retry(worker,monkeypatch):
    s,lease=worker;count=[]
    def timeout(*a,**k):count.append(1);raise urllib.error.URLError('opaque synthetic delivery state')
    monkeypatch.setattr(E,'_open',timeout);before=(s.bootstrap.policy.tracker_root/'0001.json').read_bytes()
    first=W.advance(s,lease,dispatch=True,credential='synthetic-token')
    second=W.advance(s,lease,dispatch=True,credential='synthetic-token')
    assert first['status']==second['status']=='UNRESOLVED' and len(count)==1
    assert before==(s.bootstrap.policy.tracker_root/'0001.json').read_bytes()
    assert first['evidence'] and all(read(e).get('source_invalidity_claimed') is False for e in first['evidence'])


def test_typed_transport_exhaustion_stays_one_semantic_call_and_unresolved(worker,monkeypatch):
    s,lease=worker;requests=[];delays=[]
    def timeout(request,*args,**kwargs):
        requests.append(request.data)
        raise TimeoutError('synthetic transient open timeout')
    monkeypatch.setattr(E,'_open',timeout);monkeypatch.setattr(E.time,'sleep',delays.append)
    tracker=s.bootstrap.policy.tracker_root/'0001.json';before=tracker.read_bytes()
    first=W.advance(s,lease,dispatch=True,credential='synthetic-token')
    assert first['status']=='UNRESOLVED' and len(requests)==4 and delays==[2,4,8]
    assert all(request==requests[0] for request in requests)
    plans=list(s.campaign.root.rglob('plan.json'));assert len(plans)==1
    plan=json.loads(plans[0].read_bytes())
    assert plan['ordinal']==1 and plan['semantic_budget_reset'] is False
    starts=[json.loads(path.read_bytes()) for path in plans[0].parent.glob('transport-*.started.json')]
    assert len(starts)==4 and {value['attempt_id'] for value in starts}=={plan['attempt_id']}
    journal={str(path.relative_to(s.campaign.root)):path.read_bytes()
             for path in s.campaign.root.rglob('*') if path.is_file()}
    second=W.advance(s,lease,dispatch=True,credential='synthetic-token')
    assert second['status']=='UNRESOLVED' and len(requests)==4
    assert tracker.read_bytes()==before
    assert journal=={str(path.relative_to(s.campaign.root)):path.read_bytes()
                     for path in s.campaign.root.rglob('*') if path.is_file()}
    assert first['evidence'] and all(read(e).get('source_invalidity_claimed') is False for e in first['evidence'])


@pytest.mark.parametrize('changed',['lease','budget','original','run_overlap'])
def test_invalid_context_rejects_before_effects(worker,monkeypatch,changed):
    s,lease=worker;seen=responses(monkeypatch);before=(s.bootstrap.policy.tracker_root/'0001.json').read_bytes()
    if changed=='lease':lease=dataclasses.replace(lease,sha256='0'*64)
    elif changed=='budget':s=dataclasses.replace(s,normalization_budget=dataclasses.replace(s.normalization_budget,ordinal=2))
    elif changed=='original':Path(read(s.prepared)['validated']['original']['path']).write_bytes(b'changed')
    else:s=dataclasses.replace(s,run=s.bootstrap.run/'nested')
    with pytest.raises((ValueError,KeyError)):W.advance(s,lease,dispatch=True,credential='synthetic-token')
    assert not seen and before==(s.bootstrap.policy.tracker_root/'0001.json').read_bytes()


def test_interrupted_packaging_is_not_replayed(worker,monkeypatch):
    s,lease=worker
    # The interruption policy is independent of content qualification: a saved
    # incomplete command may never be dispatched again under the same identity.
    work=s.run;work.mkdir();(work/'packaging').mkdir()
    (work/'packaging/package.intent.json').write_text('{}')
    assert (work/'packaging/package.intent.json').is_file()
    # Exercise the fixed packaging producer's own fresh-work guard directly.
    content=W._content(s,read(s.prepared)['validated']['bindings'],())
    with pytest.raises(ValueError,match='fresh packaging'):W.environment.package(content,None,work/'packaging',s.taxonomy)


def test_real_worker_through_phase2_uses_independent_review_and_generic_profile(worker,monkeypatch):
    from test_harbor_normalization_producer import mock_review
    s,lease=worker;responses(monkeypatch)
    result=W.advance(s,lease,dispatch=True,credential='synthetic-token');assert result['status']=='STEP_COMPLETE',result
    lease=result['lease'];content=result['selection'].content
    ns=W.normalization.Selection(content.workbook,content.bindings,content.normalization_run,content.model_policy,s.normalization_budget)
    mock_review(monkeypatch,ns)
    for step in ('normalization_phase1','source_profile','normalization_phase2'):
        result=W.advance(s,lease,dispatch=True,credential='synthetic-token')
        assert result['status']=='STEP_COMPLETE' and result['step']==step,result
        lease=result['lease']
    tracker=read(record(s.bootstrap.policy.tracker_root/'0001.json'))
    assert tracker['lane_state']['harbor-normalization-engineer']['phase']=='phase2_complete'
    assert tracker['lane_state']['harbor-normalization-engineer']['attempt']==1
    assert result['selection'].public_profiles.calls==()
    assert read(result['selection'].content.bindings['normalized_final'])['variables']==read(result['selection'].content.bindings['normalized_phase1'])['variables']
    # Execute only these tiny fixture CLI entrypoints in-process. All owning
    # builders/validators still run; no Docker, external service or corpus job.
    prior=W.environment.subprocess.run
    commands=[]
    def local(argv,**kwargs):
        if len(argv)>1 and Path(argv[1]).name in {'xl_variable_mcp.py','xl_input_mask.py','xl_output_task.py'}:
            import importlib
            from types import SimpleNamespace
            module=importlib.import_module(Path(argv[1]).stem);commands.append(Path(argv[1]).name)
            result=module.main(argv[2:]);return SimpleNamespace(returncode=result or 0)
        return prior(argv,**kwargs)
    monkeypatch.setattr(W.environment.subprocess,'run',local)
    result=W.advance(s,lease,dispatch=False)
    assert result['status']=='STEP_COMPLETE' and result['step']=='environment',result
    ready=W.advance(s,result['lease'],dispatch=False)
    assert ready['status']=='ENVIRONMENT_READY' and ready['publication_ready'] is False
    assert commands==['xl_variable_mcp.py','xl_variable_mcp.py','xl_input_mask.py','xl_output_task.py']


def test_real_owning_rejection_records_failure_notes_without_fabricated_reason(worker,monkeypatch):
    s,lease=worker;responses(monkeypatch);checkpoint=W._checkpoint
    def corrupt(selection,kind,ready,evidence):
        binding=checkpoint(selection,kind,ready,evidence)
        if kind=='research':Path(ready.content.bindings['variable_source_audit']['path']).write_text('Changed after captured audit evidence')
        return binding
    monkeypatch.setattr(W,'_checkpoint',corrupt)
    result=W.advance(s,lease,dispatch=True,credential='synthetic-token')
    assert result['status']=='OWNING_GATE_FAILED' and result['lease'].revision==lease.revision+1
    tracker=read(record(s.bootstrap.policy.tracker_root/'0001.json'))
    assert tracker['promotion_failure']['class']=='research_audit'
    assert tracker['lane_state']['harbor-research-auditor']['state']!='passed'
    facade=W.HarborLaneLifecycle(W._context(s));assert facade.require_synchronized('0001')
    assert result['evidence'] and tracker['terminal_verdict'] is None


def test_strict_transport_tamper_is_revalidated_before_later_stages(worker,monkeypatch):
    s,lease=worker;responses(monkeypatch)
    result=W.advance(s,lease,dispatch=True,credential='synthetic-token');assert result['status']=='STEP_COMPLETE'
    response=next(s.campaign.root.rglob('response.raw.json'));response.write_bytes(b'changed')
    next_result=W.advance(s,result['lease'])
    assert next_result['status']=='UNRESOLVED' and next_result['step']=='research'
    assert not (s.run/'normalization').exists()


def test_partial_packaging_directory_stops_reentry_after_valid_phase2(worker,monkeypatch):
    from test_harbor_normalization_producer import mock_review
    s,lease=worker;responses(monkeypatch);result=W.advance(s,lease,dispatch=True,credential='synthetic-token');lease=result['lease'];content=result['selection'].content
    ns=W.normalization.Selection(content.workbook,content.bindings,content.normalization_run,content.model_policy,s.normalization_budget);mock_review(monkeypatch,ns)
    for _ in range(3):
        result=W.advance(s,lease,dispatch=True,credential='synthetic-token');assert result['status']=='STEP_COMPLETE';lease=result['lease']
    work=s.run/'packaging';work.mkdir();(work/'package.intent.json').write_text('{"retained":"interrupted"}')
    before=(s.bootstrap.policy.tracker_root/'0001.json').read_bytes()
    monkeypatch.setattr(W.environment,'package',lambda *a,**k:pytest.fail('must not replay interrupted package'))
    for _ in range(2):assert W.advance(s,lease)['status']=='UNRESOLVED'
    assert before==(s.bootstrap.policy.tracker_root/'0001.json').read_bytes()
    assert (work/'package.intent.json').read_text()=='{"retained":"interrupted"}'


def test_public_worklist_waits_for_exact_root_captures_without_claiming_failure(worker,monkeypatch):
    from test_harbor_normalization_producer import mock_review
    s,lease=worker;seen=responses(monkeypatch);opened=E._open
    def public(request,timeout):
        response=opened(request,timeout);raw=json.loads(response.read());response.close()
        raw['choices'][0]['message']['content']=raw['choices'][0]['message']['content'].replace('Supplied workbook |','Public source (https://example.gov/rates) |')
        return io.BytesIO(json.dumps(raw).encode())
    monkeypatch.setattr(E,'_open',public)
    result=W.advance(s,lease,dispatch=True,credential='synthetic-token');assert result['status']=='STEP_COMPLETE'
    lease=result['lease'];content=result['selection'].content
    ns=W.normalization.Selection(content.workbook,content.bindings,content.normalization_run,content.model_policy,s.normalization_budget);mock_review(monkeypatch,ns)
    result=W.advance(s,lease,dispatch=True,credential='synthetic-token');assert result['status']=='STEP_COMPLETE';lease=result['lease']
    monkeypatch.setattr(E,'_open',lambda *a,**k:pytest.fail('public capture must precede model dispatch'))
    result=W.advance(s,lease,dispatch=True,credential='synthetic-token')
    assert result['status']=='PENDING_CAPTURE' and read(result['evidence'][0])
    assert not (s.run/'profile-input.json').exists() and not (s.run/'profiles').exists()
    tracker=read(record(s.bootstrap.policy.tracker_root/'0001.json'))
    assert tracker['promotion_failure'] is None and tracker['lane_state']['harbor-source-profiler']['state']=='pending'

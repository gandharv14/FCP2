import dataclasses,io,json,time
from pathlib import Path
import pytest
from scripts.batch import harbor_instruction_producer as p
from scripts.batch import harbor_model_executor as executor
from scripts.batch.harbor_model_review_provenance import Policy
from scripts.batch.harbor_admission import record
from test_harbor_tracker_lifecycle import created
from test_instruction_naturalization import SOURCE

ROOT=Path(__file__).resolve().parents[1]
# Installed tests use repository root; isolated overlay explicitly supplies it.
import xl_harbor_prep
ROOT=Path(xl_harbor_prep.__file__).parent

@pytest.fixture
def setup(tmp_path,monkeypatch):
    tracker,_=created(tmp_path);value=json.loads(tracker.read_text())
    for lane in ('harbor-source-warden','harbor-segmentation-analyst','harbor-research-auditor','harbor-normalization-engineer','harbor-source-profiler','harbor-environment-packager','harbor-disclosure-writer'):value['lane_state'][lane]['state']='passed'
    value['lane_state']['harbor-normalization-engineer']['phase']='phase2_complete';tracker.write_text(json.dumps(value))
    stage=tmp_path/'0245-outputs';(stage/'tests').mkdir(parents=True);(stage/'environment').mkdir()
    (stage/'instruction.md').write_text(SOURCE);(stage/'task.toml').write_text('[metadata]\nname="fixture"\n[metadata.naturalizer]\nenabled=false\n')
    (stage/'tests/answer_key.json').write_text(json.dumps({'targets':{'Valuation!C10':.123,'Valuation!C20':456.7}}))
    task=p.Task(record(tracker),stage,p.tree(stage),tmp_path/'producer',Policy('fixture-project'),ROOT)
    campaign=executor.Campaign(tmp_path/'journal',tmp_path/'slots')
    monkeypatch.setenv('HARBOR_MODEL_SLOTS','2');monkeypatch.setenv('HARBOR_MODEL_SLOT_ROOT',str(campaign.slot_root))
    def forbidden(*a,**k):raise AssertionError('No real network permitted')
    monkeypatch.setattr(executor,'_open',forbidden)
    calls=[]
    def respond(request,value):
        class Response(io.BytesIO):
            def __enter__(self):return self
            def __exit__(self,*a):self.close()
        raw={'model':task.policy.model,'id':'response-'+str(len(calls)),'object':'chat.completion','created':time.time_ns()//10**9,'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':value if isinstance(value,str) else json.dumps(value)}}]}
        def opened(req,timeout):calls.append(req);return Response(json.dumps(raw).encode())
        monkeypatch.setattr(executor,'_open',opened)
        return p.dispatch(task,request,campaign,credential='synthetic-token')
    return task,campaign,respond,calls


def nat_writer(setup):
    task,campaign,respond,_=setup;p.initialize_naturalization(task,root=ROOT);request=p.prepare_naturalization_writer(task,root=ROOT)
    source=p.text(record(task.run/'naturalization/writer_source.md'));spans=p.skill_module(ROOT,'naturalize-finance-task-instruction','instruction_spans');a,b=spans.extract_editable_bodies(source.encode(),spans.scan_instruction(source.encode()))
    respond(request,{'preamble_body':a.decode(),'input_body':b.decode()})
    result=p.consume_naturalization_writer(task,request,campaign,root=ROOT);assert result['validation']['valid']
    return request


def test_frozen_naturalization_complete_independent_source_clause_review(setup):
    task,campaign,respond,calls=setup;writer=nat_writer(setup);review=p.prepare_naturalization_review(task,root=ROOT)
    body=json.loads(p.read(review.prepared.request)['messages'][1]['content']);claims=p.editable_claims(ROOT,body['source'])
    respond(review,{'valid':True,'blocking_findings':[],'clause_checks':[{'source_claim':line,'candidate_claim':line,'preserved':True} for line in claims]})
    result=p.consume_naturalization_review(task,review,writer,campaign,apply=True,root=ROOT)
    assert result['applied'] is True and result['attempts_used']==1 and len(calls)==2
    assert (task.stage/'instruction.md').read_bytes()==SOURCE.encode()
    assert not list(task.stage.rglob('*.raw.json'))


@pytest.mark.parametrize('change',['omitted_source','invented_candidate','duplicate_source'])
def test_nonempty_but_incomplete_clause_review_rejected(setup,change):
    task,campaign,respond,_=setup;writer=nat_writer(setup);review=p.prepare_naturalization_review(task,root=ROOT)
    body=json.loads(p.read(review.prepared.request)['messages'][1]['content']);lines=p.editable_claims(ROOT,body['source']);checks=[{'source_claim':line,'candidate_claim':line,'preserved':True} for line in lines]
    if change=='omitted_source':checks.pop()
    elif change=='invented_candidate':checks[0]['candidate_claim']='Invented content absent from candidate'
    else:checks.append(checks[0])
    respond(review,{'valid':True,'blocking_findings':[],'clause_checks':checks})
    assert p.consume_naturalization_review(task,review,writer,campaign,root=ROOT)['passed'] is False
    state=p.read(record(task.run/'naturalization/state.json'));assert not state.get('accepted_semantic_review')


@pytest.mark.parametrize('change',['wrong_id','stage_drift','terminal','budget'])
def test_foreign_changed_terminal_and_exhausted_task_reject(setup,change):
    task,campaign,respond,calls=setup
    if change=='wrong_id':task=dataclasses.replace(task,stage=task.stage.with_name('9999-outputs'))
    elif change=='stage_drift':(task.stage/'instruction.md').write_text('changed')
    else:
        tracker=Path(task.tracker['path']);value=json.loads(tracker.read_text())
        if change=='terminal':value['execution_state']='PROMOTED'
        else:value['lane_state'][p.NAT]['attempt']=2
        tracker.write_text(json.dumps(value));task=dataclasses.replace(task,tracker=record(tracker))
    with pytest.raises((ValueError,FileNotFoundError)):p.initialize_naturalization(task,root=ROOT)
    assert calls==[]


def test_writer_has_only_frozen_instruction_context_and_no_line_prefixes(setup):
    task,*_=setup;p.initialize_naturalization(task,root=ROOT);r=p.prepare_naturalization_writer(task,root=ROOT);request=p.read(r.prepared.request);body=json.loads(request['messages'][1]['content'])
    assert set(body)=={'writer_source'} and '[[F' in body['writer_source']
    assert 'Line prefixes such as `10|` are tool metadata and must never be copied into either output.' in request['messages'][0]['content']
    assert r.prepared.limit==2 and r.prepared.ordinal==1


def test_request_kind_and_original_identity_cannot_be_substituted(setup):
    task,*_=setup;p.initialize_naturalization(task,root=ROOT);r=p.prepare_naturalization_writer(task,root=ROOT)
    with pytest.raises(ValueError):p.validate_request(task,r,'dialogue-writer')
    other=dataclasses.replace(r,prepared=dataclasses.replace(r.prepared,scope=dataclasses.replace(r.prepared.scope,original_sha256='b'*64)))
    with pytest.raises(ValueError):p.validate_request(task,other)


def dialogue_fixture(setup, *, private_evidence=None):
    task,campaign,respond,calls=setup
    entry={'entry':'source_selection','value':'source','cells':['S!C3'],'fields':{'label':'"Revenue"','ingredient':'cell S!$B$2 on the row labelled "Opening balance"'}}
    if private_evidence is not None:entry['evidence']=private_evidence
    from openpyxl import Workbook
    source=task.run.parent/'dialogue-source.xlsx';book=Workbook();book.active.title='S';book.active['B2']=10;book.save(source)
    (task.stage/'tests/disclosure.json').write_text(json.dumps({'agent_records':[entry],'records':[entry],'golden':str(source),'golden_sha256':record(source)['sha256']}))
    tracker=Path(task.tracker['path']);value=json.loads(tracker.read_text())
    value['bindings']['source_file']=record(source)
    for lane in (p.NAT,'harbor-independent-verifier'):value['lane_state'][lane]['state']='passed'
    tracker.write_text(json.dumps(value));task=dataclasses.replace(task,tracker=record(tracker),stage_tree=p.tree(task.stage));setup=(task,campaign,respond,calls)
    # The fixture's response dispatcher deliberately closes over the original
    # stage snapshot; use current selection for this independent invocation.
    def send(request,value):
        class Response(io.BytesIO):
            def __enter__(self):return self
            def __exit__(self,*a):self.close()
        raw={'model':task.policy.model,'id':'dialogue-response-'+str(len(calls)),'object':'chat.completion','created':time.time_ns()//10**9,'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':value if isinstance(value,str) else json.dumps(value)}}]}
        def opened(req,timeout):calls.append(req);return Response(json.dumps(raw).encode())
        executor._open=opened
        return p.dispatch(task,request,campaign,credential='synthetic-token')
    p.initialize_dialogue(task,root=ROOT)
    return task,campaign,send,calls


def test_dialogue_writer_context_exposes_frozen_junior_scope_only(setup):
    private_evidence='Reviewer-only evidence sentinel: S!$B$2'
    task,campaign,respond,calls=dialogue_fixture(setup,private_evidence=private_evidence)
    r=p.prepare_dialogue_writer(task,root=ROOT);body=json.loads(p.read(r.prepared.request)['messages'][1]['content'])
    assert set(body)=={'writer_pack.json','draft_template.md','slots.json'}
    serialized=json.dumps(body)
    assert 'reviewer_only' not in serialized and private_evidence not in serialized and 'S!$B$2' not in serialized
    claims=p.read(record(task.run/'dialogue/claims.json'))['claims']
    assert claims[0]['reviewer_only']['cells']==['S!C3']
    assert claims[0]['reviewer_only']['evidence']==private_evidence
    # Disclosed task geometry is intentionally frozen in the junior scaffold;
    # it neither exposes private source references nor licenses senior refs.
    assert body['writer_pack.json']['claims'][0]['junior_scope']==['S!C3']
    assert '**Analyst:** S: For `S!C3`:' in body['draft_template.md']
    slots=body['slots.json']['slots']
    assert all('S!C3' not in json.dumps(slot) for slot in slots.values())
    assert calls==[]
    assert r.prepared.response_mode=='json_object'


@pytest.mark.parametrize('change',[False,True])
def test_senior_paraphrase_keeps_exact_junior_slot_bytes(setup,change):
    task,campaign,respond,calls=dialogue_fixture(setup);r=p.prepare_dialogue_writer(task,root=ROOT);d=task.run/'dialogue';template=p.text(record(d/'draft_template.md'));slots=p.read(record(d/'slots.json'));composer=p.skill_module(ROOT,'additional-assumptions-dialogue','compose_draft')
    values={k:('Which source should I use?' if v['kind']=='junior' else 'Take Revenue from the Opening balance row, using the S tab.') for k,v in slots['slots'].items()}
    filled=composer.assemble_slots(template,slots,{'schema_version':'dialogue-slot-prose/v1','slots':values});respond(r,{'schema_version':'dialogue-slot-prose/v1','slots':values});p.consume_dialogue_writer(task,r,campaign,root=ROOT)
    para=p.prepare_dialogue_paraphraser(task,root=ROOT)
    if change:
        for k,v in slots['slots'].items():
            if v['kind']=='junior':values[k]='A changed junior question.'
    rewritten=composer.assemble_slots(template,slots,{'schema_version':'dialogue-slot-prose/v1','slots':values});respond(para,{'schema_version':'dialogue-slot-prose/v1','slots':values})
    if change:
        with pytest.raises(ValueError,match='junior'):p.consume_dialogue_paraphraser(task,para,r,campaign,root=ROOT)
    else:
        result=p.consume_dialogue_paraphraser(task,para,r,campaign,root=ROOT);assert result['draft']['sha256']==record(d/'round-01/draft.md')['sha256']


def test_dialogue_empty_is_no_model_no_scaffold_and_no_third_round(setup):
    task,campaign,respond,calls=dialogue_fixture(setup);(task.stage/'tests/disclosure.json').write_text('{"agent_records":[],"records":[]}');task=dataclasses.replace(task,stage_tree=p.tree(task.stage),run=task.run.parent/'empty-producer')
    assert p.initialize_dialogue(task,root=ROOT)['empty'] is True
    assert not (task.run/'dialogue/draft_template.md').exists()
    for n in (1,3):
        with pytest.raises(ValueError):p.prepare_dialogue_writer(task,n,root=ROOT)
    assert calls==[]


@pytest.mark.parametrize('kind',['array','data_table','ordinary'])
def test_typed_formula_review_evidence_is_complete_and_repeatable(tmp_path,kind):
    from openpyxl import Workbook
    from openpyxl.worksheet.formula import ArrayFormula,DataTableFormula
    from scripts.batch.harbor_semantic_readiness import workbook_evidence
    path=tmp_path/'formula.xlsx';book=Workbook()
    if kind=='array':book.active['A1']=ArrayFormula(ref='A1:A2',text='=ROW(A1:A2)')
    elif kind=='data_table':book.active['A1']=DataTableFormula(ref='A1:B2',r1='C1',dt2D=True,r2='D1')
    else:book.active['A1']='=2+3'
    book.save(path);book.close();first=workbook_evidence(record(path));second=workbook_evidence(record(path))
    assert first==second
    value=first['sheets'][0]['cells'][0]['value'];assert 'object at 0x' not in json.dumps(first)
    if kind=='array':assert value=={'formula_type':'array','text':'=ROW(A1:A2)','attributes':{'t':'array','ref':'A1:A2'}}
    elif kind=='data_table':assert value['formula_type']=='data_table' and value['attributes']['r1']=='C1' and value['attributes']['r2']=='D1' and value['attributes']['dt2D']=='1'
    else:assert value=='=2+3'


def test_naturalization_writer_and_rejection_checkpoints_resume_without_extra_calls(setup):
    task,campaign,respond,calls=setup;writer=nat_writer(setup)
    assert p.consume_naturalization_writer(task,writer,campaign,root=ROOT)['validation']['valid']
    assert len(calls)==1
    review=p.prepare_naturalization_review(task,root=ROOT)
    respond(review,{'valid':False,'blocking_findings':['Synthetic semantic rejection'],'clause_checks':[]})
    for _ in range(2):assert p.consume_naturalization_review(task,review,writer,campaign,root=ROOT)['passed'] is False
    assert len(calls)==2 and p.prepare_naturalization_writer(task,root=ROOT).prepared.ordinal==2


def test_fresh_naturalization_has_no_third_writer_after_two_real_rejections(setup):
    task,campaign,respond,calls=setup;p.initialize_naturalization(task,root=ROOT)
    source=p.text(record(task.run/'naturalization/writer_source.md'));spans=p.skill_module(ROOT,'naturalize-finance-task-instruction','instruction_spans');a,b=spans.extract_editable_bodies(source.encode(),spans.scan_instruction(source.encode()))
    for n in (1,2):
        writer=p.prepare_naturalization_writer(task,root=ROOT);assert writer.prepared.ordinal==n
        respond(writer,{'preamble_body':a.decode(),'input_body':b.decode()});assert p.consume_naturalization_writer(task,writer,campaign,root=ROOT)['validation']['valid']
        review=p.prepare_naturalization_review(task,root=ROOT);respond(review,{'valid':False,'blocking_findings':['Synthetic semantic rejection'],'clause_checks':[]})
        assert p.consume_naturalization_review(task,review,writer,campaign,root=ROOT)['passed'] is False
    with pytest.raises(ValueError):p.prepare_naturalization_writer(task,root=ROOT)
    assert len(calls)==4


def dialogue_round(task,campaign,respond,round,*,passing):
    directory=task.run/'dialogue';template=p.text(record(directory/'draft_template.md'));slots=p.read(record(directory/'slots.json'));claims=p.read(record(directory/'claims.json'));composer=p.skill_module(ROOT,'additional-assumptions-dialogue','compose_draft')
    values={k:('Which source should I use?' if v['kind']=='junior' else 'Take Revenue from the Opening balance row, using the S tab.') for k,v in slots['slots'].items()}
    filled=composer.assemble_slots(template,slots,{'schema_version':'dialogue-slot-prose/v1','slots':values})
    writer=p.prepare_dialogue_writer(task,round,root=ROOT);respond(writer,{'schema_version':'dialogue-slot-prose/v1','slots':values});p.consume_dialogue_writer(task,writer,campaign,root=ROOT)
    paraphraser=p.prepare_dialogue_paraphraser(task,round,root=ROOT);respond(paraphraser,{'schema_version':'dialogue-slot-prose/v1','slots':values});result=p.consume_dialogue_paraphraser(task,paraphraser,writer,campaign,root=ROOT)
    assert result['draft_report']['passed'],result['draft_report']
    review=p.prepare_dialogue_review(task,round,root=ROOT)
    response={'agent_model':'gpt-5.6-sol-high','round':round,'accuracy':{'verdict':'pass','claims':[{'record_id':c['record_id'],'must_say':'entailed','findings':[]} for c in claims['claims']],'extras':[],'cell_refs_in_senior_turns':[]},'naturalness':{'verdict':'pass' if passing else 'fail','findings':[] if passing else ['Synthetic naturalness failure']},'passed':passing}
    respond(review,response)
    return p.consume_dialogue_review(task,review,writer,paraphraser,campaign,root=ROOT)


def test_dialogue_two_rounds_preserve_independence_coverage_and_budget(setup):
    task,campaign,respond,calls=dialogue_fixture(setup)
    assert dialogue_round(task,campaign,respond,1,passing=False)['passed'] is False
    assert dialogue_round(task,campaign,respond,2,passing=True)['passed'] is True
    with pytest.raises(ValueError):p.prepare_dialogue_writer(task,3,root=ROOT)
    assert len(calls)==6


def test_passed_dialogue_round_cannot_be_rewritten(setup):
    task,campaign,respond,calls=dialogue_fixture(setup)
    assert dialogue_round(task,campaign,respond,1,passing=True)['passed'] is True
    with pytest.raises(ValueError,match='successful'):p.prepare_dialogue_writer(task,2,root=ROOT)


def test_canonical_predecessor_failure_blocks_generation_before_model(setup):
    task,campaign,respond,calls=setup;tracker=Path(task.tracker['path']);value=json.loads(tracker.read_text());value['lane_state']['harbor-disclosure-writer']['state']='pending';tracker.write_text(json.dumps(value));task=dataclasses.replace(task,tracker=record(tracker))
    with pytest.raises(ValueError,match='prior canonical'):p.initialize_naturalization(task,root=ROOT)
    assert calls==[]


def test_offline_cli_prepares_exact_frozen_writer_without_network(setup,tmp_path):
    task,campaign,respond,calls=setup;value=dataclasses.asdict(task)
    for k in ('stage','run','root'):value[k]=str(value[k])
    selected=tmp_path/'task.json';selected.write_text(json.dumps(value))
    p.main(['nat-init','--task',str(selected),'--out',str(tmp_path/'init.json')])
    p.main(['nat-writer','--task',str(selected),'--out',str(tmp_path/'writer.json')])
    request=json.loads((tmp_path/'writer.json').read_text());assert request['prepared']['ordinal']==1 and request['prepared']['response_mode']=='json_object'
    assert calls==[]


def test_dialogue_producer_kind_and_round_substitution_reject_before_consumption(setup):
    task,campaign,respond,calls=dialogue_fixture(setup)
    request=p.prepare_dialogue_writer(task,root=ROOT)
    with pytest.raises(ValueError,match='kind'):
        p.consume_dialogue_paraphraser(task,request,request,campaign,root=ROOT)
    assert calls==[]


def test_changed_request_context_blocks_dispatch(setup):
    task,campaign,respond,calls=setup;p.initialize_naturalization(task,root=ROOT);request=p.prepare_naturalization_writer(task,root=ROOT)
    Path(request.context['path']).write_text('{}')
    with pytest.raises(ValueError):respond(request,{'preamble_body':'','input_body':''})
    assert calls==[]


def test_disclosure_review_cannot_use_foreign_golden_before_model(setup,tmp_path):
    task,campaign,respond,calls=setup
    (task.stage/'tests/disclosure.json').write_text('{}');(task.stage/'environment/0245-inputs.xlsx').write_bytes(b'fixture only')
    task=dataclasses.replace(task,stage_tree=p.tree(task.stage))
    foreign=tmp_path/'foreign.xlsx';foreign.write_bytes(b'different source')
    with pytest.raises(ValueError,match='admitted source'):
        p.prepare_disclosure_review(task,record(foreign),root=ROOT)
    assert calls==[] and not (task.run/'requests').exists()

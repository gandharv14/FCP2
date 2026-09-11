import copy,json
from pathlib import Path
import pytest
from scripts.batch import harbor_semantic_readiness as r
from scripts.batch.harbor_admission import record
from test_harbor_model_review_provenance import fixture as journal
from test_disclosure_copied_scope import fixture as workbook_fixture,record as disclosure_record,SPAN,ROOT


def bind_json(path,value):path.write_text(json.dumps(value));return record(path)


def set_call(call,request,review,*,text=False):
    object.__setattr__(call,'request',bind_json(Path(call.request['path']),request))
    submitted=json.loads(Path(call.submitted['path']).read_text());submitted['request']=call.request
    object.__setattr__(call,'submitted',bind_json(Path(call.submitted['path']),submitted))
    response=json.loads(Path(call.response['path']).read_text());response['choices'][0]['message']['content']=review if text else json.dumps(review)
    object.__setattr__(call,'response',bind_json(Path(call.response['path']),response))
    completed=json.loads(Path(call.completed['path']).read_text());completed['response']=call.response
    object.__setattr__(call,'completed',bind_json(Path(call.completed['path']),completed))
    if text:Path(call.parsed['path']).write_text(review);object.__setattr__(call,'parsed',record(Path(call.parsed['path'])))
    else:object.__setattr__(call,'parsed',bind_json(Path(call.parsed['path']),review))
    completed=json.loads(Path(call.completed['path']).read_text())
    if 'review_sha256' in completed:
        completed['review_sha256']=call.parsed['sha256'];object.__setattr__(call,'completed',bind_json(Path(call.completed['path']),completed))


def disclosure_fixture(tmp_path):
    gold,path,task=workbook_fixture(tmp_path);renderer=r.skill_module(ROOT,'task-disclosure','disclose')
    records=[disclosure_record(gold,[k]) for k in SPAN]
    disclosure=bind_json(task/'tests/disclosure.json',{'records':records,'agent_records':renderer.agent_records(records)})
    instruction=task/'instruction.md';instruction.write_text('Build this model.\n\n'+renderer.render_section(records));instruction=record(instruction)
    args=dict(instruction=instruction,disclosure=disclosure,golden=record(path),delivered=record(task/'environment/delivered.xlsx'))
    manifest=r.disclosure_manifest(**args,root=ROOT);manifest_binding=bind_json(tmp_path/'manifest.json',manifest)
    call,policy,request=journal(tmp_path,'naturalization')
    body={'manifest':manifest,'manifest_sha256':manifest_binding['sha256'],'instruction':Path(instruction['path']).read_text(),'disclosure':r.read(disclosure),'workbooks':{k:r.workbook_evidence(args[k]) for k in ('golden','delivered')}}
    request['messages'][1]['content']=json.dumps(body)
    review={'schema_version':'harbor-disclosure-faithfulness/v1','model':'gpt-5.6-sol-high','input_hashes':manifest['input_hashes'],'manifest_sha256':manifest_binding['sha256'],'sentences':[{k:v for k,v in row.items() if k!='order'}|{'golden_evidence':['S!K153 independently checked'],'delivered_evidence':['S!K153 blank independently checked'],'verdict':'pass','findings':[]} for row in manifest['sentences']],'blocking_findings':[],'non_blocking_flags':[],'passed':True}
    set_call(call,request,review)
    return call,manifest_binding,args,policy,request,review


@pytest.mark.parametrize('change',[None,'missing_sentence','duplicate_record','missing_evidence','blocked','wrong_sentence','wrong_hash','request_instruction','request_disclosure','workbook_cell','workbook_omission','cached_formula','copied_bounds','incomplete_transport'])
def test_disclosure_full_current_snapshot_and_exact_required_review_coverage(tmp_path,change):
    call,manifest,args,policy,request,review=disclosure_fixture(tmp_path)
    if change=='missing_sentence':review['sentences'].pop()
    elif change=='duplicate_record':review['sentences'][1]['record_ids']=review['sentences'][0]['record_ids']
    elif change=='missing_evidence':review['sentences'][0]['golden_evidence']=[]
    elif change=='blocked':review['blocking_findings']=['unresolved financial claim']
    elif change=='wrong_sentence':review['sentences'][0]['text']='Different scope.'
    elif change=='wrong_hash':review['manifest_sha256']='f'*64
    elif change in ('request_instruction','request_disclosure','workbook_cell','workbook_omission','cached_formula','copied_bounds'):
        body=json.loads(request['messages'][1]['content'])
        if change=='request_instruction':body['instruction']='wrong instruction'
        elif change=='request_disclosure':body['disclosure']['records']=[]
        elif change=='workbook_cell':body['workbooks']['golden']['sheets'][0]['cells'][0]['value']='false label'
        elif change=='workbook_omission':body['workbooks']['golden']['sheets'][0]['cells'].pop()
        elif change=='cached_formula':next(x for x in body['workbooks']['golden']['sheets'][0]['cells'] if x['data_type']=='f')['cached_value']=999
        else:body['workbooks']['golden']['sheets'][0]['dimensions']='A1:XFD1048576'
        request['messages'][1]['content']=json.dumps(body)
    set_call(call,request,review)
    if change=='incomplete_transport':
        value=json.loads(Path(call.response['path']).read_text());value['choices'][0]['finish_reason']='length'
        object.__setattr__(call,'response',bind_json(Path(call.response['path']),value))
        value=json.loads(Path(call.completed['path']).read_text());value['response']=call.response;object.__setattr__(call,'completed',bind_json(Path(call.completed['path']),value))
    if change is None:assert r.disclosure_review(call,manifest,**args,policy=policy,root=ROOT)['records']==3
    else:
        with pytest.raises(ValueError):r.disclosure_review(call,manifest,**args,policy=policy,root=ROOT)


def dialogue_fixture(tmp_path):
    claims={'claims':[{'record_id':'claim-1'}],'writer_pack':{'disclosure_body':'Use the retained source.'},'shared_source_links':None}
    prior=copy.deepcopy(claims);prior.pop('shared_source_links')
    args={'claims':bind_json(tmp_path/'claims.json',claims),'reviewed_claims':bind_json(tmp_path/'old-claims.json',prior)}
    notes=tmp_path/'notes.md';notes.write_text('Junior: Which source?\nSenior: Use the retained source.\n');args['notes']=record(notes)
    call,policy,request=journal(tmp_path,'naturalization')
    request['messages'][1]['content']=json.dumps({'claims.json':prior,'draft.md':notes.read_text(),'round':2})
    review={'agent_model':'gpt-5.6-sol-high','round':2,'accuracy':{'verdict':'pass','claims':[{'record_id':'claim-1','must_say':'entailed','findings':[]}],'extras':[],'cell_refs_in_senior_turns':[]},'naturalness':{'verdict':'pass','findings':[]},'passed':True}
    set_call(call,request,review)
    writer_dir=tmp_path/'writer';writer_dir.mkdir();writer,_,wrequest=journal(writer_dir,'naturalization')
    set_call(writer,wrequest,notes.read_text(),text=True)
    response=json.loads(Path(writer.response['path']).read_text());response['id']='writer-response';object.__setattr__(writer,'response',bind_json(Path(writer.response['path']),response))
    completed=json.loads(Path(writer.completed['path']).read_text());completed['response']=writer.response;object.__setattr__(writer,'completed',bind_json(Path(writer.completed['path']),completed))
    args.update(policy=policy,writer_calls=[writer],workbook='0001',current_source_sha256='a'*64)
    return call,args,request,review


def fresh_dialogue_fixture(tmp_path,monkeypatch):
    # Complete synthetic executor calls, exact current author protocols and
    # independent reviewer context; no single-call generic author surrogate.
    from test_harbor_instruction_producer import setup,dialogue_fixture as prepare
    from scripts.batch import harbor_instruction_producer as p
    task,campaign,respond,_=prepare(setup.__wrapped__(tmp_path,monkeypatch))
    slots=p.read(record(task.run/'dialogue/slots.json'))
    value={'schema_version':'dialogue-slot-prose/v1','slots':{k:('Which source should I use?' if v['kind']=='junior' else 'Take Revenue from the Opening balance row, using the S tab.') for k,v in slots['slots'].items()}}
    writer=p.prepare_dialogue_writer(task);respond(writer,value);p.consume_dialogue_writer(task,writer,campaign)
    para=p.prepare_dialogue_paraphraser(task);respond(para,value);result=p.consume_dialogue_paraphraser(task,para,writer,campaign)
    request=p.prepare_dialogue_review(task);claims=record(task.run/'dialogue/claims.json')
    review={'agent_model':'gpt-5.6-sol-high','round':1,'accuracy':{'verdict':'pass','claims':[{'record_id':c['record_id'],'must_say':'entailed','findings':[]} for c in p.read(claims)['claims']],'extras':[],'cell_refs_in_senior_turns':[]},'naturalness':{'verdict':'pass','findings':[]},'passed':True}
    respond(request,review);call=p.observed(request,campaign);identity=p.task_context(task)[1]
    notes=tmp_path/'shipped-notes.md';notes.write_text(r.skill_module(ROOT,'additional-assumptions-dialogue','validate_dialogue').strip_claim_comments(p.text(result['draft'])))
    args={'claims':claims,'reviewed_claims':claims,'notes':record(notes),'policy':task.policy,'writer_calls':[p.observed(writer,campaign),p.observed(para,campaign)],'workbook':identity['workbook'],'original_sha256':identity['original']['sha256'],'current_source_sha256':identity['original']['sha256']}
    return call,args,p.read(call.request),review


@pytest.mark.parametrize('change',[None,'missing_claim','unknown_claim','contradiction','extras','round','naturalness','model','changed_current_claim','changed_draft','non_null_grouping','writer_missing','writer_reused'])
def test_dialogue_context_and_owning_semantic_validator(tmp_path,monkeypatch,change):
    call,args,request,review=fresh_dialogue_fixture(tmp_path,monkeypatch)
    if change=='missing_claim':review['accuracy']['claims']=[]
    elif change=='unknown_claim':review['accuracy']['claims'][0]['record_id']='unknown'
    elif change=='contradiction':review['accuracy']['claims'][0]['must_say']='contradicted'
    elif change=='extras':review['accuracy']['extras']=['unapproved new assumption']
    elif change=='round':review['round']=2
    elif change=='naturalness':review['naturalness']['verdict']='fail'
    elif change=='model':review['agent_model']='other'
    elif change in ('changed_current_claim','non_null_grouping'):
        value=r.read(args['claims'])
        if change=='changed_current_claim':value['claims'][0]['record_id']='other'
        else:value['shared_source_links']={'groups':[['claim-1']]}
        args['claims']=bind_json(Path(args['claims']['path']),value)
    elif change=='changed_draft':Path(args['notes']['path']).write_text('Different dialogue.');args['notes']=record(Path(args['notes']['path']))
    elif change=='writer_missing':args['writer_calls']=[]
    elif change=='writer_reused':
        writer=args['writer_calls'][0];value=r.read(writer.response);value['id']=r.read(call.response)['id'];object.__setattr__(writer,'response',bind_json(Path(writer.response['path']),value));value=r.read(writer.completed);value['response']=writer.response;object.__setattr__(writer,'completed',bind_json(Path(writer.completed['path']),value))
    set_call(call,request,review)
    if change is None:assert r.dialogue_review(call,**args,root=ROOT)['round']==1
    else:
        with pytest.raises(ValueError):r.dialogue_review(call,**args,root=ROOT)


def historical_fixture(tmp_path,monkeypatch):
    from test_release_publication import _real_release_fixture
    import xl_release_publication as pub
    call,args,request,review=dialogue_fixture(tmp_path)
    original_publish=pub.publish_task_generation
    def publish_with_notes(stage,*a,**kw):
        (stage/'tests').mkdir(exist_ok=True)
        (stage/'environment/additional-assumptions.md').write_text(Path(args['notes']['path']).read_text())
        (stage/'environment/Dockerfile').write_text('FROM scratch\nCOPY additional-assumptions.md /app/additional-assumptions.md\n')
        (stage/'tests/dialogue-applied.json').write_text(json.dumps({'applied':True,'review_passed':True,'draft_passed':True}))
        return original_publish(stage,*a,**kw)
    # This only augments synthetic stage construction; all actual publication,
    # component hashing, source/segmentation and resolver checks remain active.
    with monkeypatch.context() as m:
        m.setattr(pub,'publish_task_generation',publish_with_notes)
        case=_real_release_fixture(tmp_path)
    task=case['release_root']/'task-generations'/case['task_id'];tm=json.loads((task/'generation-manifest.json').read_text());treehash=pub.canonical_hash(tm['artifacts'])
    oldwriter=args['writer_calls'][0]
    reviewer=r.HistoricalCall(call.request,call.response,call.parsed);writer=r.HistoricalCall(oldwriter.request,oldwriter.response)
    rounds={n:b for n,b in [('review-request.r2.json',call.request),('review-response.r2.json',call.response),('review.r2.json',call.parsed),('draft.md',args['notes']),('paraphrase-request.r2.json',oldwriter.request),('paraphrase-response.r2.json',oldwriter.response)]}
    reuse=bind_json(tmp_path/'reuse.json',{'budget_reset':False,'new_writer_rounds':0,'new_semantic_rounds':0,'retained_writer_rounds_used':2,'original_round2_evidence':rounds,'original_writer_evidence':{},'comparisons':{'claims.json':{'old':args['reviewed_claims']}}})
    models={b['path']:b['sha256'] for b in (call.request,call.response,oldwriter.request,oldwriter.response)}
    handoff=bind_json(tmp_path/'handoff.json',{'workbook_id':case['workbook'],'staged_tree_sha256':treehash,'model_request_response_hashes':models})
    parent=bind_json(tmp_path/'parent.json',{'handoff_sha256':handoff['sha256'],'stage_sha256':treehash,'current_source_generation':case['source_id'],'current_segmentation_generation':case['segmentation_id']})
    evidence={b['path']:b['sha256'] for b in [handoff,reuse,args['reviewed_claims'],*rounds.values()]}
    evidence=bind_json(tmp_path/'published-evidence.json',evidence)
    source_sha=case['release']['identity']['bindings']['source_sha256']
    args.update(writer_calls=[writer],workbook=case['workbook'],current_source_sha256=source_sha,historical=r.HistoricalApplicability(case['workbook'],case['release_root'],case['source_root'],case['segmentation_root'],case['release_root'],case['release']['release_id'] if 'release_id' in case['release'] else case['release_dir'].name,parent,handoff,evidence,reuse,source_sha))
    return reviewer,args,case


@pytest.mark.parametrize('change',[None,'unrelated_workbook','changed_source','wrong_release','published_task_tampered','missing_publication_call','parent_handoff','changed_claims','budget_reset'])
def test_historical_dialogue_is_tied_to_real_published_tuple_and_exact_root_evidence(tmp_path,monkeypatch,change):
    import dataclasses
    call,args,case=historical_fixture(tmp_path,monkeypatch);h=args['historical']
    if change=='unrelated_workbook':args['workbook']='new-50-task'
    elif change=='changed_source':args['current_source_sha256']='f'*64
    elif change=='wrong_release':args['historical']=dataclasses.replace(h,release_id='f'*64)
    elif change=='published_task_tampered':(case['release_root']/'task-generations'/case['task_id']/'instruction.md').write_text('altered published task')
    elif change=='missing_publication_call':
        e=r.read(h.publication_evidence);e.pop(call.response['path']);args['historical']=dataclasses.replace(h,publication_evidence=bind_json(Path(h.publication_evidence['path']),e))
    elif change=='parent_handoff':
        p=r.read(h.parent_review);p['handoff_sha256']='f'*64;args['historical']=dataclasses.replace(h,parent_review=bind_json(Path(h.parent_review['path']),p))
    elif change=='changed_claims':
        value=r.read(args['reviewed_claims']);value['writer_pack']['disclosure_body']='different mechanics';args['reviewed_claims']=bind_json(Path(args['reviewed_claims']['path']),value)
    elif change=='budget_reset':
        reuse=r.read(h.reuse);reuse['budget_reset']=True;updated=bind_json(Path(h.reuse['path']),reuse);e=r.read(h.publication_evidence);e[updated['path']]=updated['sha256'];args['historical']=dataclasses.replace(h,reuse=updated,publication_evidence=bind_json(Path(h.publication_evidence['path']),e))
    if change is None:
        result=r.dialogue_review(call,**args,root=ROOT)
        assert result['round']==2 and result['provenance']['transport_journals_available'] is False
        assert 'attempt_id' not in result['provenance']
    else:
        with pytest.raises(ValueError):r.dialogue_review(call,**args,root=ROOT)


@pytest.mark.parametrize('change',[None,'changed_prose','unsupported_comment'])
def test_dialogue_reviewed_claim_comments_are_removed_only_by_current_apply_semantics(tmp_path,monkeypatch,change):
    call,args,request,review=fresh_dialogue_fixture(tmp_path,monkeypatch);body=json.loads(request['messages'][1]['content'])
    raw=body['draft.md'];assert '<!-- claim:' in raw
    if change=='changed_prose':body['draft.md']=raw.replace('Opening balance','new assumption')
    elif change=='unsupported_comment':body['draft.md']='<!-- unrelated extra prose -->\n'+raw
    request['messages'][1]['content']=json.dumps(body);set_call(call,request,review)
    if change is None:assert r.dialogue_review(call,**args,root=ROOT)['claims']==1
    else:
        with pytest.raises(ValueError):r.dialogue_review(call,**args,root=ROOT)


@pytest.mark.parametrize('copies',[2,3])
def test_dialogue_duplicate_or_excess_writer_evidence_rejected(tmp_path,copies):
    call,args,_,_=dialogue_fixture(tmp_path);args['writer_calls']*=copies
    with pytest.raises(ValueError):r.dialogue_review(call,**args,root=ROOT)

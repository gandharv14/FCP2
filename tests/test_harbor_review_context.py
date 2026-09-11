import copy,json
from pathlib import Path
import pytest
from openpyxl import Workbook
from scripts.batch.harbor_admission import record
from scripts.batch import harbor_review_context as c
from test_harbor_model_review_provenance import fixture as journal_fixture
import xl_output_task
ROOT=Path(xl_output_task.__file__).parent


def body_fixture(tmp_path):
    def write(name,value):
        p=tmp_path/name;p.write_text(json.dumps(value));return record(p)
    source=tmp_path/'source.xlsx';baseline=tmp_path/'baseline.xlsx'
    wb=Workbook();wb.active['A1']='Rate';wb.active['B1']=0.1;wb.save(source);wb.save(baseline);wb.close()
    source,baseline=record(source),record(baseline);draft=write('draft.json',{'rows':[]});audit=write('audit.json',{});equiv=write('equivalence.json',{})
    bindings={'source_generation':{'id':'a'*64},'segmentation_generation':{'id':'b'*64},'variable_source_audit':audit,'audit_equivalence':equiv}
    rows=[{'clean':True,'reference':'Sheet!B1','cells':[('Sheet','B1',0.1)],'duplicate_refs':[]}]
    body={'source':source,'baseline':baseline,'draft':draft,'source_sha256':source['sha256'],'baseline_sha256':baseline['sha256'],'draft_sha256':draft['sha256'],'audit':audit,'audit_equivalence':equiv,'current_source_generation':bindings['source_generation'],'current_segmentation_generation':bindings['segmentation_generation'],'candidates':c.json_value(rows),'context':{'Sheet':[{'reference':'Sheet!A1','value':'Rate','data_type':'s','number_format':'General'}]},'candidate_primitive_checks':[{'reference':'Sheet!B1','source_data_type':'n','baseline_data_type':'n','source_equals_baseline':True,'number_format':'General','duplicate_refs':[]}]}
    return body,dict(source=source,baseline=baseline,draft=draft,rows=rows,bindings=bindings)


@pytest.mark.parametrize('change',[None,'context','candidate_omission','candidate_value','generation','primitive','duplicate_ref','source_formula'])
def test_normalization_review_body_requires_complete_current_workbook_facts(tmp_path,change):
    body,args=body_fixture(tmp_path)
    if change=='context':body['context']['Sheet'][0]['value']='Wrong label'
    elif change=='candidate_omission':body['candidates']=[]
    elif change=='candidate_value':body['candidates'][0]['cells'][0][2]=0.2
    elif change=='generation':body['current_source_generation']={'id':'f'*64}
    elif change=='primitive':body['candidate_primitive_checks'][0]['baseline_data_type']='f'
    elif change=='duplicate_ref':body['context']['Sheet']*=2
    elif change=='source_formula':
        wb=Workbook();wb.active['A1']='Rate';wb.active['B1']='=0.1';wb.save(args['source']['path']);wb.close()
        args['source']=record(Path(args['source']['path']));body['source']=args['source'];body['source_sha256']=args['source']['sha256']
    if change is None:assert c.normalization_body(body,**args)
    else:
        with pytest.raises(ValueError):c.normalization_body(body,**args)


def nat_call(tmp_path):
    call,policy,request=journal_fixture(tmp_path,'naturalization')
    request['messages'][0]['content']='You are a fresh independent semantic reviewer. Review clause by clause.'
    request['messages'][1]['content']=json.dumps({'source':'Source claim.','candidate':'Same claim.'})
    Path(call.request['path']).write_text(json.dumps(request));object.__setattr__(call,'request',record(Path(call.request['path'])))
    submitted=json.loads(Path(call.submitted['path']).read_text());submitted['request']=call.request;Path(call.submitted['path']).write_text(json.dumps(submitted));object.__setattr__(call,'submitted',record(Path(call.submitted['path'])))
    parsed={'valid':True,'blocking_findings':[],'clause_checks':[{'source_claim':'Source claim.','candidate_claim':'Same claim.','preserved':True}]}
    response=json.loads(Path(call.response['path']).read_text());response['choices'][0]['message']['content']=json.dumps(parsed);Path(call.response['path']).write_text(json.dumps(response));object.__setattr__(call,'response',record(Path(call.response['path'])))
    completed=json.loads(Path(call.completed['path']).read_text());completed['response']=call.response;Path(call.completed['path']).write_text(json.dumps(completed));object.__setattr__(call,'completed',record(Path(call.completed['path'])))
    Path(call.parsed['path']).write_text(json.dumps(parsed));object.__setattr__(call,'parsed',record(Path(call.parsed['path'])))
    return call,policy


@pytest.mark.parametrize('change',[None,'source','candidate','writer_missing','same_response','no_clauses','blocked_clause'])
def test_naturalization_context_and_supported_semantic_gate_are_bound(tmp_path,change):
    call,policy=nat_call(tmp_path);source='Source claim.';candidate='Same claim.'
    writerdir=tmp_path/'writer';writerdir.mkdir();writer,_,_=journal_fixture(writerdir,'naturalization')
    bodies={'preamble_body':'Writer preamble.','input_body':'Writer input.'}
    response=json.loads(Path(writer.response['path']).read_text());response['id']='writer-response';response['choices'][0]['message']['content']=json.dumps(bodies);Path(writer.response['path']).write_text(json.dumps(response));object.__setattr__(writer,'response',record(Path(writer.response['path'])))
    completed=json.loads(Path(writer.completed['path']).read_text());completed['response']=writer.response;Path(writer.completed['path']).write_text(json.dumps(completed));object.__setattr__(writer,'completed',record(Path(writer.completed['path'])))
    Path(writer.parsed['path']).write_text(json.dumps(bodies));object.__setattr__(writer,'parsed',record(Path(writer.parsed['path'])))
    bodyrefs={}
    for key,value in bodies.items():
        path=writerdir/(key+'.md');path.write_text(value);bodyrefs[key]=record(path)
    writerrequest=writer.request
    if change=='source':source='Changed source.'
    elif change=='candidate':candidate='Changed candidate.'
    elif change=='writer_missing':writer=None
    elif change=='same_response':
        response=json.loads(Path(writer.response['path']).read_text());response['id']='response-fixture';Path(writer.response['path']).write_text(json.dumps(response));object.__setattr__(writer,'response',record(Path(writer.response['path'])))
        completed=json.loads(Path(writer.completed['path']).read_text());completed['response']=writer.response;Path(writer.completed['path']).write_text(json.dumps(completed));object.__setattr__(writer,'completed',record(Path(writer.completed['path'])))
    elif change in ('no_clauses','blocked_clause'):
        parsed=json.loads(Path(call.parsed['path']).read_text())
        if change=='no_clauses':parsed['clause_checks']=[]
        else:parsed['clause_checks'][0]['preserved']=False
        response=json.loads(Path(call.response['path']).read_text());response['choices'][0]['message']['content']=json.dumps(parsed);Path(call.response['path']).write_text(json.dumps(response));object.__setattr__(call,'response',record(Path(call.response['path'])))
        completed=json.loads(Path(call.completed['path']).read_text());completed['response']=call.response;Path(call.completed['path']).write_text(json.dumps(completed));object.__setattr__(call,'completed',record(Path(call.completed['path'])))
        Path(call.parsed['path']).write_text(json.dumps(parsed));object.__setattr__(call,'parsed',record(Path(call.parsed['path'])))
    args=dict(source=source,candidate=candidate,policy=policy,writer_call=writer,writer_request=writerrequest,writer_bodies=bodyrefs,root=ROOT)
    if change is None:assert c.naturalization_review(call,call.request,**args)['review']['valid']
    else:
        with pytest.raises(ValueError):c.naturalization_review(call,call.request,**args)


def normalization_call(tmp_path, *, fresh_audit=False):
    body,args=body_fixture(tmp_path)
    if fresh_audit:
        body.pop('audit_equivalence')
        args['bindings'].pop('audit_equivalence')
    call,policy,request=journal_fixture(tmp_path,'normalization')
    evidence=tmp_path/'evidence.json';evidence.write_text(json.dumps(body))
    analysis=tmp_path/'analysis.json';analysis.write_text(json.dumps(c.json_value(args['rows'])))
    request['messages'][1]['content']=evidence.read_text()
    Path(call.request['path']).write_text(json.dumps(request));object.__setattr__(call,'request',record(Path(call.request['path'])))
    submitted=json.loads(Path(call.submitted['path']).read_text());submitted['request_sha256']=call.request['sha256'];Path(call.submitted['path']).write_text(json.dumps(submitted));object.__setattr__(call,'submitted',record(Path(call.submitted['path'])))
    review={'schema_version':'normalization-review/v1','reviewer':'independent fixture','cells':{'Sheet!B1':{'eligible':True,'reason':'explicit supplied assumption','evidence_refs':['Sheet!B1'],'entity':'Fixture','period':'2025','scenario':'Base','basis':'nominal'}}}
    set_normalization_response(call,review)
    manifest=tmp_path/'prepared.json';manifest.write_text(json.dumps({'request':call.request,'evidence':record(evidence),'analysis':record(analysis)}))
    return call,record(manifest),args,policy


def set_normalization_response(call,review):
    response=json.loads(Path(call.response['path']).read_text());response['choices'][0]['message']['content']=json.dumps(review)
    Path(call.response['path']).write_text(json.dumps(response));object.__setattr__(call,'response',record(Path(call.response['path'])))
    Path(call.parsed['path']).write_text(json.dumps(review));object.__setattr__(call,'parsed',record(Path(call.parsed['path'])))
    completed=json.loads(Path(call.completed['path']).read_text());completed.update(response_sha256=call.response['sha256'],review_sha256=call.parsed['sha256'])
    Path(call.completed['path']).write_text(json.dumps(completed));object.__setattr__(call,'completed',record(Path(call.completed['path'])))


def test_fresh_audit_normalization_does_not_require_a_historical_reuse_receipt(tmp_path):
    call,prepared,args,policy=normalization_call(tmp_path,fresh_audit=True)
    assert c.normalization_review(call,prepared,policy=policy,**args)['review']['cells']['Sheet!B1']['eligible']


@pytest.mark.parametrize('change',['omitted_required','unexpected_receipt','changed_receipt'])
def test_normalization_cannot_detach_or_invent_audit_reuse(tmp_path,change):
    body,args=body_fixture(tmp_path)
    if change=='omitted_required':body.pop('audit_equivalence')
    elif change=='unexpected_receipt':args['bindings'].pop('audit_equivalence')
    else:Path(body['audit_equivalence']['path']).write_text('changed')
    with pytest.raises(ValueError):c.normalization_body(body,**args)


@pytest.mark.parametrize('change',[None,'current_generation','current_value','analysis','request','evidence','missing_review_cell','extra_review_cell','same_writer_invocation'])
def test_normalization_call_binds_current_context_preparation_and_raw_review(tmp_path,change):
    call,prepared,args,policy=normalization_call(tmp_path);other=[]
    if change=='current_generation':args['bindings']['source_generation']={'id':'c'*64}
    elif change=='current_value':args['rows'][0]['cells']=[('Sheet','B1',0.2)]
    elif change in ('analysis','request','evidence'):
        manifest=json.loads(Path(prepared['path']).read_text());binding=manifest[change];path=Path(binding['path']);value=json.loads(path.read_text())
        if change=='analysis':value=[]
        elif change=='request':value['messages'][1]['content']='different evidence'
        else:value['context']['Sheet'][0]['value']='different label'
        path.write_text(json.dumps(value));manifest[change]=record(path)
        Path(prepared['path']).write_text(json.dumps(manifest));prepared=record(Path(prepared['path']))
    elif change in ('missing_review_cell','extra_review_cell'):
        review=json.loads(Path(call.parsed['path']).read_text())
        if change=='missing_review_cell':review['cells']={}
        else:review['cells']['Sheet!C1']=review['cells']['Sheet!B1']
        set_normalization_response(call,review)
    elif change=='same_writer_invocation':other=[c.validate_call(call,c.read(call.request),policy=policy)]
    if change is None:assert c.normalization_review(call,prepared,policy=policy,**args)['review']['cells']['Sheet!B1']['eligible']
    else:
        with pytest.raises(ValueError):c.normalization_review(call,prepared,policy=policy,other_calls=other,**args)

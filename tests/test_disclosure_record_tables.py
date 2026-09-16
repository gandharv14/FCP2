import copy,json
from pathlib import Path
import pytest
from scripts.batch import harbor_disclosure_evidence as E,harbor_disclosure_payload_repair as R,harbor_semantic_readiness as S
from test_harbor_semantic_readiness import disclosure_fixture,bind_json,set_call,ROOT


def test_lossless_records_preserve_order_absence_null_and_nested_types():
    records=[{'label':'Debt','value':None,'formula':'=IF(A1>0,A1,0)','evidence':{'cells':['S!A1']}},
             {'label':'Debt','value':0,'flag':False},{'label':'Debt','value':0.0,'flag':True},
             {'label':'Debt','value':'0'},{}]
    before=copy.deepcopy(records);encoded=E.encode_records(records)
    assert E.packed(E.decode_records(encoded))==E.packed(records) and records==before
    assert encoded['rows'][-1]==[-1]*len(encoded['columns'])


@pytest.mark.parametrize('kind',['boolean_index','negative_index','out_of_range','missing_column','duplicate_column','duplicate_value','unknown_note','unknown_encoding'])
def test_invalid_record_dictionary_is_rejected(kind):
    table=E.encode_records([{'value':None,'formula':'=A1'},{'value':False}])
    if kind=='boolean_index':table['rows'][0][0]=True
    elif kind=='negative_index':table['rows'][0][0]=-2
    elif kind=='out_of_range':table['rows'][0][0]=len(table['values'][0])
    elif kind=='missing_column':table['rows'][0].pop()
    elif kind=='duplicate_column':table['columns'][1]=table['columns'][0]
    elif kind=='duplicate_value':table['values'][0].append(None)
    elif kind=='unknown_note':table['row_semantics']='Trust upstream pass'
    else:table['encoding']='unknown'
    with pytest.raises(ValueError):E.decode_records(table)


def test_actual_0079_rejected_request_retains_every_original_byte_value():
    path=ROOT/'diagnostics/bulk-production/0079-http-rejection-001/request.json'
    original_bytes=path.read_bytes();request=json.loads(original_bytes);body=json.loads(request['messages'][1]['content'])
    compact=R.project(request,projection=R.RECORD_PROJECTION);actual=json.loads(compact['messages'][1]['content'])
    assert len(body['disclosure']['records'])==3420
    assert E.packed(E.decode_body(actual))==E.packed(E.decode_body(body))
    assert len(E.packed(compact).encode())<.6*len(original_bytes)
    assert path.read_bytes()==original_bytes and compact['messages'][0]==request['messages'][0]
    decoded=E.decode_body(body);automatic=E.project_body(decoded)
    assert automatic['evidence_projection']==E.RECORD_VERSION
    assert E.packed(E.decode_body(automatic))==E.packed(decoded)


@pytest.mark.parametrize('change',[None,'record_value','missing_record','missing_sentence','blocking'])
def test_complete_reviewer_validation_accepts_encoding_but_rejects_evidence_loss(tmp_path,change):
    call,manifest,args,policy,request,review=disclosure_fixture(tmp_path)
    disclosure=S.read(args['disclosure']);disclosure['method_assessments']=[]
    args['disclosure']=bind_json(Path(args['disclosure']['path']),disclosure)
    current=S.disclosure_manifest(**args,root=ROOT);manifest=bind_json(Path(manifest['path']),current)
    original=json.loads(request['messages'][1]['content']);original.update(disclosure=disclosure,manifest=current,manifest_sha256=manifest['sha256'])
    encoded=E.compact_records(E.project_body(original))
    records=encoded['disclosure']['records']
    if change=='record_value':records['values'][0][0]='changed source identity'
    elif change=='missing_record':records['rows'].pop()
    elif change=='missing_sentence':review['sentences'].pop()
    elif change=='blocking':review['sentences'][0]['verdict']='blocking'
    review.update(input_hashes=current['input_hashes'],manifest_sha256=manifest['sha256'])
    request['messages'][1]['content']=json.dumps(encoded);set_call(call,request,review)
    if change is None:assert S.disclosure_review(call,manifest,**args,policy=policy,root=ROOT)['records']==3
    else:
        with pytest.raises(ValueError):S.disclosure_review(call,manifest,**args,policy=policy,root=ROOT)

"""Complete workbook evidence survives the optional lossless physical projection."""
import copy,json
from pathlib import Path
from dataclasses import replace
import pytest
from scripts.batch import harbor_disclosure_payload_repair as R,harbor_model_executor as E
from scripts.batch import harbor_disclosure_review_resume as D
from test_harbor_model_executor import setup,Response,response_bytes,TOKEN
from test_disclosure_payload_repair import original,reject


def workbooks():
    cells=[{'address':'A1','value':1,'number_format':'0.00','data_type':'n'},
           {'address':'A2','value':1.0,'number_format':'0.00','data_type':'n','cached_value':None},
           {'address':'A3','value':'=A1+A2','number_format':'0.00','data_type':'f','cached_value':2.0},
           {'address':'A4','value':False,'number_format':'General','data_type':'b'},
           {'address':'A5','value':'','number_format':'General','data_type':'s'},
           {'address':'A6','value':None,'number_format':'General','data_type':'n'}]
    wb={'path':'complete.xlsx','sha256':'f'*64,'blank_cell_semantics':'omitted cells are blank',
        'sheets':[{'sheet':'A "quoted" sheet','state':'hidden','dimensions':'A1:A6','merged_ranges':['B1:C1'],'cells':cells},
                  {'sheet':'Empty','state':'visible','dimensions':'A1','merged_ranges':[],'cells':[]}]}
    return {'golden':wb,'delivered':copy.deepcopy(wb)}


def test_roundtrip_preserves_types_missing_null_formulas_and_metadata():
    original=workbooks();encoded=R.encode_workbooks(original);decoded=R.decode_workbooks(encoded)
    assert E.canonical(decoded)==E.canonical(original)
    rows=encoded['workbooks']['golden']['sheets'][0]['cells']['rows']
    assert len(rows[0])==4 and len(rows[1])==5 and rows[1][-1] is None
    assert type(rows[0][1]) is int and type(rows[1][1]) is float
    assert decoded['golden']['sheets'][0]['cells'][2]['value']=='=A1+A2'


@pytest.mark.parametrize('kind',['column','index','boolean_index','row_length','schema','format_duplicate','unknown_cell_key'])
def test_uncertain_or_malformed_table_rejected(kind):
    original=workbooks()
    if kind=='unknown_cell_key':
        original['golden']['sheets'][0]['cells'][0]['extra']='must not disappear'
        with pytest.raises(ValueError):R.encode_workbooks(original)
        return
    value=R.encode_workbooks(original);table=value['workbooks']['golden']['sheets'][0]['cells']
    if kind=='column':table['columns']=list(reversed(table['columns']))
    elif kind=='index':table['rows'][0][2]=99
    elif kind=='boolean_index':table['rows'][0][2]=True
    elif kind=='row_length':table['rows'][0].append(None);table['rows'][0].append(None)
    elif kind=='schema':value['encoding']='unknown'
    else:value['number_formats'].append(value['number_formats'][0])
    with pytest.raises(ValueError):R.decode_workbooks(value)


def prepared_fixture(setup,monkeypatch):
    c,p=original(setup);request=R.read(p.request);body=json.loads(request['messages'][1]['content'])
    body['workbooks']=workbooks();request['messages'][1]['content']=json.dumps(body)
    Path(p.request['path']).write_bytes(E.canonical(request));p=replace(p,request=E.record(p.request['path']))
    reject(p,c,monkeypatch)
    return c,p


def test_versioned_physical_call_keeps_uuid_budget_and_full_original_body(setup,monkeypatch,tmp_path):
    c,p=prepared_fixture(setup,monkeypatch)
    r=R.prepare(p,c,tmp_path/'physical',projection=R.TABLE_VERSION)
    assert R.read(r.physical_repair)['projection']==R.TABLE_VERSION
    assert (r.scope,r.attempt_id,r.ordinal,r.limit,r.budget_evidence)==(p.scope,p.attempt_id,1,1,p.budget_evidence)
    body=json.loads(R.read(r.request)['messages'][1]['content'])
    assert E.canonical(R.decode_workbooks(body['workbooks']))==E.canonical(workbooks())
    monkeypatch.setattr(E,'_open',lambda *a:Response(response_bytes(p.policy)))
    call=E.execute(r,c,credential=TOKEN).call
    assert D.original_review_request(call)==R.read(p.request)
    with pytest.raises(ValueError):R.prepare(p,c,tmp_path/'second',projection=R.TABLE_VERSION)


def test_legacy_projection_default_remains_byte_identical(setup):
    _,p=original(setup);request=R.read(p.request)
    expected=copy.deepcopy(request);body=json.loads(expected['messages'][1]['content'])
    body['disclosure']['method_assessments']=[body['disclosure']['method_assessments'][0],body['disclosure']['method_assessments'][2]]
    expected['messages'][1]['content']=json.dumps(body,ensure_ascii=False,allow_nan=False)
    assert E.canonical(R.project(request))==E.canonical(expected)
    with pytest.raises(ValueError):R.project(request,projection='unknown')


@pytest.mark.parametrize('kind',['drop_cell','cached_presence','numeric_type','formula'])
def test_forged_lossless_projection_cannot_change_any_cell(setup,monkeypatch,tmp_path,kind):
    c,p=prepared_fixture(setup,monkeypatch);r=R.prepare(p,c,tmp_path/'physical',projection=R.TABLE_VERSION)
    request=R.read(r.request);body=json.loads(request['messages'][1]['content'])
    rows=body['workbooks']['workbooks']['golden']['sheets'][0]['cells']['rows']
    if kind=='drop_cell':rows.pop()
    elif kind=='cached_presence':rows[0].append(None)
    elif kind=='numeric_type':rows[0][1]=1.0
    else:rows[2][1]='=A1-A2'
    request['messages'][1]['content']=json.dumps(body,separators=(',',':'),ensure_ascii=False,allow_nan=False)
    request_path=tmp_path/'forged-request.json';request_path.write_bytes(E.canonical(request))
    permit=R.read(r.physical_repair);permit['request']=E.record(request_path)
    permit_path=tmp_path/'forged-permit.json';permit_path.write_bytes(E.canonical(permit))
    forged=replace(r,request=E.record(request_path),physical_repair=E.record(permit_path))
    with pytest.raises(ValueError,match='complete projection'):
        R.validate_prepared(forged,c)

import copy,json
from pathlib import Path
import pytest
from scripts.batch import harbor_disclosure_evidence as E,harbor_semantic_readiness as S
from test_disclosure_lossless_tables import workbooks
from test_harbor_semantic_readiness import disclosure_fixture,bind_json,set_call,ROOT


def test_conservative_scope_never_drops_records_or_uncertain_assessments():
    value={'records':[{'cell_keys':['S!A1']}],'agent_records':[{'cell_keys':['S!A1']}],
           'method_assessments':[{'cell_keys':['S!A1']},{'cell_keys':['S!B2']},{'cell_keys':[]},{'cell_keys':['S!1:1']}],
           'other_metadata':{'must':'survive'}}
    projected=E.scoped_disclosure(value)
    assert projected=={**value,'method_assessments':[value['method_assessments'][i] for i in (0,2,3)]}
    value['records'][0]['cell_keys']=['S!1:1']
    assert E.scoped_disclosure(value)==value


def test_empty_or_already_scoped_assessments_still_encode_complete_workbooks():
    body={'manifest':{},'manifest_sha256':'a'*64,'instruction':'Complete instruction',
          'disclosure':{'records':[],'agent_records':[],'method_assessments':[]},'workbooks':workbooks()}
    encoded=E.project_body(body)
    assert S.json.dumps(E.decode_body(encoded),sort_keys=True)==S.json.dumps(body,sort_keys=True)


@pytest.mark.parametrize('change',[None,'drop_cell','numeric_type','cached','formula','metadata','record','assessment','version','missing_sentence','blocking'])
def test_versioned_initial_review_preserves_all_evidence_and_review_coverage(tmp_path,change):
    call,manifest,args,policy,request,review=disclosure_fixture(tmp_path)
    disclosure=S.read(args['disclosure'])
    disclosure['method_assessments']=[{'cell_keys':[],'note':'Uncertain scope retained'},
                                    {'cell_keys':['Unrelated!Z999'],'note':'unrelated'}]
    args['disclosure']=bind_json(Path(args['disclosure']['path']),disclosure)
    current=S.disclosure_manifest(**args,root=ROOT)
    manifest=bind_json(Path(manifest['path']),current)
    body=json.loads(request['messages'][1]['content'])
    body.update(disclosure=disclosure,manifest=current,manifest_sha256=manifest['sha256'])
    projected=E.project_body(body)
    table=projected['workbooks']['workbooks']['golden']['sheets'][0]['cells']['rows']
    if change=='drop_cell':table.pop()
    elif change=='numeric_type':
        row=next(r for r in table if type(r[1]) is int);row[1]=float(row[1])
    elif change=='cached':next(r for r in table if r[3]=='f')[4]=999
    elif change=='formula':next(r for r in table if r[3]=='f')[1]='=0'
    elif change=='metadata':projected['workbooks']['workbooks']['golden']['sheets'][0]['state']='veryHidden'
    elif change=='record':projected['disclosure']['records'].pop()
    elif change=='assessment':projected['disclosure']['method_assessments']=[]
    elif change=='version':projected['evidence_projection']='unknown'
    elif change=='missing_sentence':review['sentences'].pop()
    elif change=='blocking':review['sentences'][0]['verdict']='blocking'
    request['messages'][1]['content']=json.dumps(projected)
    review.update(input_hashes=current['input_hashes'],manifest_sha256=manifest['sha256'])
    set_call(call,request,review)
    if change is None:assert S.disclosure_review(call,manifest,**args,policy=policy,root=ROOT)['records']==3
    else:
        with pytest.raises(ValueError):S.disclosure_review(call,manifest,**args,policy=policy,root=ROOT)

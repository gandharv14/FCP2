import copy,json
from pathlib import Path
import pytest
from scripts.batch import harbor_review_context as C,harbor_normalization_compact as K
from scripts.batch.harbor_admission import record
from test_harbor_review_context import normalization_call


def prepared_shared(tmp_path):
    call,prepared,args,policy=normalization_call(tmp_path)
    duplicate_refs=['Sheet!D'+str(i)for i in range(2000)]
    for i in range(120):
        args['rows'].append(dict(clean=False,reference='Sheet!C'+str(i),cells=[('Sheet','C'+str(i),None)],
            duplicate_refs=duplicate_refs,extra={'nested':('preserved',None,False,1.5)}))
    artifact=dict(schema_version='normalization-indexed-artifact/v1',kind='analysis',evidence=K.compact(dict(candidates=K.json_value(args['rows']),candidate_primitive_checks=[])))
    manifest=C.read(prepared);p=Path(manifest['analysis']['path']);p.write_text(json.dumps(artifact));manifest['analysis']=record(p)
    Path(prepared['path']).write_text(json.dumps(manifest));prepared=record(Path(prepared['path']))
    return call,prepared,args,policy


def test_full_original_review_avoids_expanding_shared_analysis_to_json(tmp_path,monkeypatch):
    call,prepared,args,policy=prepared_shared(tmp_path)
    def forbidden(_):raise AssertionError('whole evidence JSON roundtrip would duplicate shared containers')
    monkeypatch.setattr(C,'json_value',forbidden)
    result=C.normalization_review(call,prepared,policy=policy,**args)
    assert result['review']['cells']['Sheet!B1']['eligible']is True


@pytest.mark.parametrize('change',['leaf','key','multiplicity','order','tuple-value'])
def test_shared_analysis_still_rejects_every_semantic_difference(tmp_path,change):
    call,prepared,args,policy=prepared_shared(tmp_path)
    if change=='leaf':args['rows'][1]['duplicate_refs'][0]='Sheet!Different'
    elif change=='key':del args['rows'][1]['extra']
    elif change=='multiplicity':args['rows'].pop()
    elif change=='order':args['rows'][1:3]=reversed(args['rows'][1:3])
    else:args['rows'][1]['extra']['nested']=('changed',None,False,1.5)
    with pytest.raises(ValueError,match='prepared analysis differs'):C.normalization_review(call,prepared,policy=policy,**args)

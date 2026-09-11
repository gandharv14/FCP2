import json
from dataclasses import replace
from pathlib import Path
from test_harbor_model_executor import setup,Response,response_bytes
from test_disclosure_payload_repair import original,reject
from test_harbor_semantic_readiness import disclosure_fixture,bind_json
from scripts.batch import harbor_semantic_readiness as S,harbor_disclosure_payload_repair as P


def test_physical_completion_still_requires_exact_full_disclosure_semantics(setup,monkeypatch,tmp_path):
    home=tmp_path/'owning';home.mkdir()
    _,manifest,args,_,request,review=disclosure_fixture(home)
    disclosure=S.read(args['disclosure'])
    disclosure['method_assessments']=[{'cell_keys':[],'proof':'unknown must be retained'},
                                    {'cell_keys':['Unrelated!Z999'],'proof':'unrelated assessment'}]
    args['disclosure']=bind_json(Path(args['disclosure']['path']),disclosure)
    current=S.disclosure_manifest(**args)
    manifest=bind_json(Path(manifest['path']),current)
    body=json.loads(request['messages'][1]['content'])
    body.update(disclosure=disclosure,manifest=current,manifest_sha256=manifest['sha256'])
    request['messages'][1]['content']=json.dumps(body)
    c,p=original(setup)
    Path(p.request['path']).write_bytes(P.E.canonical(request));p=replace(p,request=P.E.record(p.request['path']))
    reject(p,c,monkeypatch);repaired=P.prepare(p,c,tmp_path/'physical')
    review.update(input_hashes=current['input_hashes'],manifest_sha256=manifest['sha256'])
    monkeypatch.setattr(P.E,'_open',lambda *a:Response(response_bytes(p.policy,review)))
    result=P.E.execute(repaired,c,credential='synthetic-token')
    assert result.status=='completed'
    assert S.disclosure_review(result.call,manifest,**args,policy=p.policy)['records']==3
    Path(args['instruction']['path']).write_text('Changed financial instruction')
    import pytest
    with pytest.raises(ValueError):S.disclosure_review(result.call,manifest,**args,policy=p.policy)

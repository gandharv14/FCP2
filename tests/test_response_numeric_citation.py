import pytest
from scripts.fleet.response_numeric_citation import citation_matches

@pytest.mark.parametrize('value,expected',[(20,'20'),(20.0,'20'),(.000976809811511885,'0.000976809811511885'),(298.310673313075,'298.310673313075'),(1.4142135623731,'1.4142135623731'),(1e3,'1000'),(-0.,'0')])
def test_exact_numeric_representation(value,expected):assert citation_matches(value,{'type':'n','value':expected})

@pytest.mark.parametrize('value,cell',[(True,{'type':'n','value':'1'}),(False,{'type':'n','value':'0'}),(20,{'type':'s','value':'20'}),(20,{'type':'inlineStr','value':'20'}),('20',{'type':'n','value':20}),(.333333,{'type':'n','value':'0.333333333333333'}),(float('nan'),{'type':'n','value':'NaN'}),(float('inf'),{'type':'n','value':'Infinity'}),('=SUM(A1:A2)',{'type':'n','value':'3','formula':'SUM(A1:A2)'})])
def test_no_coercion_of_text_bool_nonfinite_rounding_or_formula(value,cell):assert not citation_matches(value,cell)

def test_exact_existing_text():assert citation_matches('20',{'type':'s','value':'20'})


def test_owning_checks_remain_and_original_function_restored(tmp_path):
    import importlib.util
    from pathlib import Path
    from scripts.fleet import response_review as R,response_numeric_citation as N
    from scripts.batch import harbor_response_guidance as G
    spec=importlib.util.spec_from_file_location('numeric_provenance_fixtures',Path(__file__).with_name('test_response_guidance.py'))
    F=importlib.util.module_from_spec(spec);spec.loader.exec_module(F)
    value=F.fixture.__wrapped__(tmp_path);_,_,ctx,instruction,key,task=value
    result=F.review()
    for row in result['checks'].values():row['source_refs']=[dict(sheet='DCF',cell='B1',value=10)]
    args=F.qualify_args(value,result=result);original=G.qualify
    with pytest.raises(ValueError,match='citation differs'):G.qualify(ctx,instruction,key,**args)
    with N.installed(R,Path(G.__file__).resolve().parents[2]):
        receipt=G.qualify(ctx,instruction,key,**args)
        bound=F.save(tmp_path/'qualification.json',receipt)
        assert G.revalidate(bound,instruction=instruction,answer_key=key,task_root=task)['passed']
        instruction.write_text('The answer is 20')
        with pytest.raises(ValueError,match='differs from reviewed'):G.revalidate(bound,instruction=instruction,answer_key=key,task_root=task)
    assert G.qualify is original


@pytest.mark.parametrize('fault',['hold','wrong_cell','wrong_value','same_author','no_refs','fake_claim'])
def test_correction_still_rejects_every_semantic_or_provenance_fault(tmp_path,fault):
    import importlib.util
    from pathlib import Path
    from scripts.fleet import response_review as R,response_numeric_citation as N
    from scripts.batch import harbor_response_guidance as G
    spec=importlib.util.spec_from_file_location('numeric_negative_fixtures',Path(__file__).with_name('test_response_guidance.py'));F=importlib.util.module_from_spec(spec);spec.loader.exec_module(F)
    value=F.fixture.__wrapped__(tmp_path);_,_,ctx,instruction,key,_=value;result=F.review()
    for row in result['checks'].values():row['source_refs']=[dict(sheet='DCF',cell='B1',value=10)]
    if fault=='hold':result['verdict']='HOLD'
    elif fault=='wrong_cell':result['checks']['source_match']['source_refs'][0]['cell']='B99'
    elif fault=='wrong_value':result['checks']['source_match']['source_refs'][0]['value']=11
    elif fault=='no_refs':result['checks']['source_match']['source_refs']=[]
    elif fault=='fake_claim':result['response_assessment'][0]['claim']='Unsupported unrelated instruction claim'
    args=F.qualify_args(value,result=result,reuse=fault=='same_author')
    with N.installed(R,Path(G.__file__).resolve().parents[2]):
        with pytest.raises(ValueError):G.qualify(ctx,instruction,key,**args)


def test_actual_4290_canary_scope_rejects_other_workbook_or_source():
    from types import SimpleNamespace
    from scripts.fleet import response_numeric_citation_4290 as N,response_review as R
    values={'plan':{},'qualification':{'workbook_id':'4301','context':'context'},'context':{'source':{'sha256':N.CANARY_SOURCE_SHA256}}}
    fake=SimpleNamespace(checked=lambda k:values[k],require=R.require)
    result={'citation_correction':{'plan':'plan'},'qualification':'qualification','workbook':'4301'}
    with pytest.raises(ValueError,match='only for exact4290'):N.check_correction(fake,result)
    result['workbook']='4290';values['qualification']['workbook_id']='4290';values['context']['source']['sha256']='0'*64
    with pytest.raises(ValueError,match='only for exact4290'):N.check_correction(fake,result)


def test_canary_uses_identical_tested_exact_comparator_and_context_patch():
    import inspect
    from scripts.fleet import response_numeric_citation as N,response_numeric_citation_4290 as C
    assert inspect.getsource(N.citation_matches)==inspect.getsource(C.citation_matches)
    assert inspect.getsource(N.installed)==inspect.getsource(C.installed)

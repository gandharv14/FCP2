"""Real executor journals and current normalization, synthetic historical identities."""
import copy
import dataclasses
from pathlib import Path
import uuid
import pytest
from scripts.batch import harbor_normalization_producer as N
from scripts.batch import harbor_normalization_recovery as R
from scripts.batch import harbor_model_executor as E
from scripts.batch.harbor_admission import record
from test_harbor_normalization_producer import setup,mock_review


@pytest.fixture
def prior(setup,monkeypatch,tmp_path,request):
    s,campaign,*_=setup();prompt=N.PROMPT;codes=N.code_bindings;current=codes();witnesses={};upgrades={}
    for name in R.UPGRADES:
        p=tmp_path/name;p.write_text('synthetic historical implementation witness '+name)
        witnesses[name]=record(p);after=next(v for k,v in current.items() if Path(k).name==name)
        upgrades[name]=[witnesses[name]['sha256'],after]
    monkeypatch.setattr(R,'UPGRADES',upgrades)
    old={k:upgrades[Path(k).name][0] if Path(k).name in upgrades else v for k,v in current.items()}
    monkeypatch.setattr(N,'PROMPT',R.old_prompt());monkeypatch.setattr(N,'code_bindings',lambda:old)
    prepared=N.prepare(s)
    def response_change(review):
        mode=getattr(request,'param','alias')
        if mode=='valid':return
        review['schema']=review.pop('schema_version')
        if mode=='unknown_schema':review['schema']='other'
        if mode=='missing_dimension':next(iter(review['cells'].values())).pop('entity')
    mock_review(monkeypatch,s,change=response_change)
    execution=N.dispatch(s,prepared,campaign,credential='fixture-unused')
    # Original pipeline could write this before owning schema validation failed.
    N.keep(s.run/'normalization-review.json',Path(execution.call.parsed['path']).read_bytes())
    monkeypatch.setattr(N,'PROMPT',prompt);monkeypatch.setattr(N,'code_bindings',codes)
    N.keep(s.run.parent/'owning-failure.json',dict(automatic_retry=False,error_sha256='abf6742f443f82708972fdcd79d40a24e52b83ef3310fe734e7757c04038597b',error_type='SystemExit',source_invalidity_claimed=False,status='UNRESOLVED',step='normalization_phase1'))
    return s,prepared,execution,campaign,witnesses


def recover(prior,tmp_path,**kw):
    s,p,x,c,w=prior
    return R.prepare_recovery(s,p,x.call,c,prior_worker_failure=record(s.run.parent/'owning-failure.json'),code_witnesses=w,root=tmp_path/'recovery',
        new_run=tmp_path/'fresh'/s.run.name,attempt_id=kw.pop('attempt_id',str(uuid.uuid4())),**kw)


def test_exact_second_attempt_preserves_first_and_financial_inputs(prior,tmp_path,monkeypatch):
    s,p,x,c,w=prior;old={str(f):f.read_bytes() for f in s.run.iterdir() if f.is_file()}
    monkeypatch.setattr(E,'_open',lambda *a,**k:pytest.fail('prepare cannot call model'))
    fresh,prepared,pc,receipt=recover(prior,tmp_path)
    assert pc.ordinal==pc.limit==2 and pc.scope==E.SemanticScope(s.workbook,s.bindings['original_raw_source']['sha256'],N.LANE,'normalization-review')
    assert fresh.budget.evidence==s.budget.evidence+(receipt,)
    assert R.validate_receipt(receipt,selection=fresh)['failure_class']=='invalid_model_content'
    assert all(Path(k).read_bytes()==v for k,v in old.items())
    assert N.read(s.run and record(s.run/'normalization-review.json'))['schema']=='normalization-review/v1'
    mock_review(monkeypatch,fresh);second=N.dispatch(fresh,prepared,c,credential='fixture-unused')
    result=N.read(N.emit_phase1(fresh,prepared,second.call));assert result['status']=='phase1_evidence_ready'
    assert N.read(second.call.submitted)['budget_evidence'][-1]==receipt
    assert N.prepared_call(fresh,prepared)==pc
    assert N.dispatch(fresh,prepared,c,credential='fixture-unused').reused
    with pytest.raises(ValueError):R.prepare_recovery(fresh,prepared,second.call,c,prior_worker_failure=record(s.run.parent/'owning-failure.json'),code_witnesses=w,root=tmp_path/'third',new_run=tmp_path/'third-run'/s.run.name,attempt_id=str(uuid.uuid4()))


@pytest.mark.parametrize('field',['ordinal','limit','id','source','run','bindings','budget_evidence'])
def test_next_selection_drift_rejected(prior,tmp_path,field):
    fresh,p,pc,receipt=recover(prior,tmp_path)
    if field in ('ordinal','limit','id'):
        key={'id':'attempt_id'}.get(field,field);value=str(uuid.uuid4()) if field=='id' else 1
        altered=dataclasses.replace(fresh,budget=dataclasses.replace(fresh.budget,**{key:value}))
    elif field=='budget_evidence':altered=dataclasses.replace(fresh,budget=dataclasses.replace(fresh.budget,evidence=(receipt,)))
    elif field=='run':altered=dataclasses.replace(fresh,run=tmp_path/'other'/fresh.run.name)
    else:
        b=copy.deepcopy(fresh.bindings);b['source_file']['sha256']='f'*64
        altered=dataclasses.replace(fresh,bindings=b)
    with pytest.raises(ValueError):R.validate_receipt(receipt,selection=altered)


@pytest.mark.parametrize('field',['response','parsed','prepared','analysis','evidence','request','old_code','receipt'])
def test_prior_tamper_blocks_every_current_prepare(prior,tmp_path,field):
    fresh,p,pc,receipt=recover(prior,tmp_path);s,old,x,c,w=prior
    paths={'response':x.call.response,'parsed':x.call.parsed,'prepared':old,'analysis':N.read(old)['analysis'],
        'evidence':N.read(old)['evidence'],'request':N.read(old)['request'],'old_code':next(iter(w.values())),'receipt':receipt}
    path=Path(paths[field]['path']);path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(ValueError):N.prepare(fresh)


@pytest.mark.parametrize('field',['valid','unknown_schema','missing_dimension','uncertain','other_dependency','same_run','same_id'])
def test_only_demonstrated_completed_first_format_error_can_recover(prior,tmp_path,monkeypatch,field):
    s,p,x,c,w=prior
    if field in ('valid','unknown_schema','missing_dimension'):
        # Use a fresh actual synthetic journal to keep response provenance valid.
        outcome_root=Path(x.call.request['path']).parent
        review=N.read(x.call.parsed)
        if field=='valid':review['schema_version']=review.pop('schema')
        elif field=='unknown_schema':review['schema']='other'
        else:next(iter(review['cells'].values())).pop('entity')
        # Tampering cannot be hidden by passing a newly nominated Call hash.
        Path(x.call.parsed['path']).write_bytes(E.canonical(review))
    elif field=='uncertain':Path(x.outcome['path']).unlink()
    elif field=='other_dependency':
        codes=N.code_bindings();codes[next(k for k in codes if Path(k).name=='gen_normalizer.py')]='f'*64
        monkeypatch.setattr(N,'code_bindings',lambda:codes)
    elif field=='same_run':
        with pytest.raises(ValueError):R.prepare_recovery(s,p,x.call,c,prior_worker_failure=record(s.run.parent/'owning-failure.json'),code_witnesses=w,root=tmp_path/'recovery',new_run=s.run,attempt_id=str(uuid.uuid4()))
        return
    elif field=='same_id':
        with pytest.raises(ValueError):recover(prior,tmp_path,attempt_id=s.budget.attempt_id)
        return
    with pytest.raises(ValueError):recover(prior,tmp_path)


def test_interrupted_preparation_resumes_identical_identity(prior,tmp_path,monkeypatch):
    s,p,x,c,w=prior;attempt=str(uuid.uuid4());actual=N.prepare
    monkeypatch.setattr(N,'prepare',lambda *a:(_ for _ in ()).throw(InterruptedError('fixture')))
    with pytest.raises(InterruptedError):recover(prior,tmp_path,attempt_id=attempt)
    retained=(tmp_path/'recovery/recovery.json').read_bytes()
    monkeypatch.setattr(N,'prepare',actual)
    fresh,p,pc,r=recover(prior,tmp_path,attempt_id=attempt)
    assert (tmp_path/'recovery/recovery.json').read_bytes()==retained
    with pytest.raises(ValueError):recover(prior,tmp_path)


@pytest.mark.parametrize('prior',['valid','unknown_schema','missing_dimension'],indirect=True)
def test_actual_completed_nonmatching_content_not_format_recovery(prior,tmp_path):
    with pytest.raises(ValueError):recover(prior,tmp_path)


def test_final_owning_review_rechecks_spent_attempt(prior,tmp_path,monkeypatch):
    from scripts.batch import harbor_review_context as C
    fresh,p,pc,receipt=recover(prior,tmp_path);old,oldp,x,c,w=prior
    mock_review(monkeypatch,fresh);second=N.dispatch(fresh,p,c,credential='fixture-unused')
    draft,rows=N.analyse(fresh);b=fresh.bindings
    kwargs=dict(source=b['source_file'],baseline=b['baseline_inputs'],draft=record(fresh.run/'draft.json'),rows=rows,bindings=b,policy=fresh.policy)
    assert C.normalization_review(second.call,p,**kwargs)['review']['schema_version']=='normalization-review/v1'
    Path(x.call.response['path']).write_bytes(Path(x.call.response['path']).read_bytes()+b' ')
    with pytest.raises(ValueError):C.normalization_review(second.call,p,**kwargs)

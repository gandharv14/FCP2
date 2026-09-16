"""Actual capture/assembler/reader flow with synthetic HTTP; no networking."""
from dataclasses import asdict,replace
import copy,json
from pathlib import Path
import pytest
from test_public_capture_projection import ctx,fake,P,write
from scripts.batch import harbor_profile_skips as S,harbor_profile_producer as producer,harbor_profile_readiness as readiness


def selection(ctx,monkeypatch):
    monkeypatch.setattr(S,'capture',P)
    seen=fake(monkeypatch,{'https://www.example.com/':dict(status=403,body=b'<main>Never profile this value 999 or follow <a href="/next">Next</a></main>')})
    inventory,outcomes=P.collect(ctx);sources,forbidden=P.context(ctx)
    return inventory,outcomes,sources,forbidden,seen


@pytest.mark.parametrize('source_name',['Example Statistics','Original source description. '*12])
def test_owning_skip_assembly_medium_generic_no_models(ctx,monkeypatch,source_name):
    phase=producer.read(ctx.phase1);phase['variables'][0]['sources'][0]['name']=source_name
    ctx=replace(ctx,phase1=write(Path(ctx.phase1['path']),phase))
    inventory,outcomes,sources,forbidden,seen=selection(ctx,monkeypatch)
    caps=write(ctx.campaign.parent/'captures.json',inventory)
    policy=producer.Policy('fixture-project')
    inp=write(ctx.campaign.parent/'producer-input.json',dict(schema_version=producer.INPUT_SCHEMA,workbook_id=ctx.workbook_id,original_source=ctx.original_source,phase1=ctx.phase1,inventory=ctx.inventory,captures=caps,policy=asdict(policy),campaign=dict(root=str(ctx.campaign.parent/'model-journal'),slot_root=str(ctx.campaign.parent/'slots'),model_slots=2)))
    monkeypatch.setenv('HARBOR_MODEL_SLOTS','2');monkeypatch.setenv('HARBOR_MODEL_SLOT_ROOT',str(ctx.campaign.parent/'slots'))
    monkeypatch.setattr(producer.executor,'_open',lambda *a,**k:pytest.fail('skip must never dispatch model'))
    plan=producer.prepare(inp,ctx.campaign.parent/'prepared');p=producer.read(plan)
    assert p['blocked']==[] and p['jobs']==[] and len(p['skips'])==1
    result=producer.run(plan,dispatch=True,credential='synthetic-unused')
    assert result['status']=='COMPLETE' and result['model_calls']==0
    proof=readiness.validate_profiles(result['selection']);assert proof['confidence']=='medium' and proof['accepted_profiles']==0 and proof['skipped_profiles']==1
    from scripts.batch import harbor_tracker as tracker
    sel=result['selection'];observed=dict(workbook_id=ctx.workbook_id,raw_source=ctx.original_source,bindings={key:getattr(sel,attr)for attr,key in [('phase1','normalized_phase1'),('inventory','variable_source_inventory'),('profiles','source_profiles'),('mapping','source_profile_mapping')]},lane_state={lane:dict(state='passed',current_confidence='medium'if lane=='harbor-source-profiler'else'high')for lane in tracker.LANES})
    receipt=S.marker(observed,sel);assert S.validate_marker(observed,receipt)is True
    for kind in ('source','policy','selection','lane','binding'):
        foreign=copy.deepcopy(observed);changed=copy.deepcopy(receipt)
        if kind=='source':changed['source']['sha256']='f'*64
        elif kind=='policy':changed['policy'][0]['sha256']='f'*64
        elif kind=='selection':changed['selection']['value']['skips']={}
        elif kind=='lane':foreign['lane_state']['harbor-dialogue-author']['current_confidence']='medium'
        else:foreign['bindings']['source_profiles']['sha256']='f'*64
        with pytest.raises((ValueError,TypeError,KeyError)):S.validate_marker(foreign,changed)
    document=producer.read(result['selection'].profiles);profile=document['profiles'][0]
    assert profile['source_name']==('Example Statistics' if len(source_name)<=160 else 'Skipped public source: www.example.com')
    assert producer.read(ctx.phase1)['variables'][0]['sources'][0]['name']==source_name
    assert profile['status']=='skipped' and profile['review']['status']=='pending' and profile['terminology']==[] and profile['evidence']==dict(attributions=[],excerpts=[])
    assert producer.read(result['selection'].mapping)['mappings']==[] and len(seen)==1
    from scripts.batch import harbor_transition_codec as codec
    encoded=codec.encode(result['selection']);assert 'skips'in encoded['value'] and codec.decode(encoded)==result['selection']
    legacy=replace(result['selection'],skips=());old=codec.encode(legacy);assert 'skips'not in old['value'] and codec.decode(old)==legacy
    assert not (ctx.campaign.parent/'model-journal').exists()
    assert producer.run(plan)['receipt']==result['receipt']
    assert P.collect(ctx)==(inventory,outcomes) and len(seen)==1
    with pytest.raises(ValueError):readiness.validate_profiles(replace(result['selection'],skips=()))
    with pytest.raises(ValueError):readiness.validate_profiles(replace(result['selection'],skips=result['selection'].skips*2))


@pytest.mark.parametrize('change',['reviewed_content','response_status','extra_read','unknown_completion','different_source','changed_projection','different_phase'])
def test_skip_requires_actual_complete_bound_barrier(ctx,monkeypatch,change):
    inv,outcomes,sources,forbidden,seen=selection(ctx,monkeypatch);bindings=inv['sources'][0]['captures'];row=bindings[0];cap=P.read(row);root=Path(row['path']).parent
    phase=ctx.phase1
    if change=='different_source':sources[0]=dict(sources[0],canonical_url='https://www.other.org/')
    elif change=='different_phase':phase=write(ctx.campaign.parent/'foreign.json',dict(environment_id='fcp-0087',variables=[]))
    elif change=='unknown_completion':(root/'outcome.json').unlink()
    elif change=='extra_read':write(root/'http-02.intent.json',dict(extra=True))
    elif change=='changed_projection':write(Path(cap['projection']['path']),dict(text='Invented metadata'))
    else:
        cap['status']=200 if change=='response_status' else 403
        if change=='reviewed_content':cap['authentication']='cookies'
        write(Path(row['path']),cap);bindings=[P.record(Path(row['path']))]
        outcome=P.read(P.record(root/'outcome.json'));outcome['captures']=bindings;outcome['artifacts']={p.name:P.record(p)['sha256']for p in root.iterdir()if p.is_file()and p.name not in{'scope.lock','outcome.json'}};write(root/'outcome.json',outcome)
    with pytest.raises((ValueError,OSError)):S.validate(sources[0],bindings,forbidden,phase1=phase,inventory=ctx.inventory)
    assert len(seen)==1

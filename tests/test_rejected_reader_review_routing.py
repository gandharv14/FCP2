from types import SimpleNamespace
from contextlib import ExitStack
import pytest
from test_harbor_model_executor import setup
from scripts.fleet import dialogue_revision_review as review
from scripts.fleet import retained_rejected_review_context as context


def test_new_schema_routes_only_to_bound_adapter(monkeypatch):
    p={'schema_version':context.SCHEMA,'context_helper':{'path':'/bound','sha256':'a'}}
    seen=[]
    monkeypatch.setattr(review,'checked',lambda _:p)
    def select(name,row):
        assert row==p['context_helper']
        return SimpleNamespace(load=lambda selected,caller:seen.append((selected,caller))or 'ok')
    monkeypatch.setattr(review,'module',select)
    with ExitStack() as stack:assert review._load({},stack)=='ok'
    assert seen==[(p,review)]


def test_unknown_schema_cannot_enter_retained_adapter(monkeypatch):
    monkeypatch.setattr(review,'checked',lambda _:{'schema_version':'unknown','context_helper':{}})
    monkeypatch.setattr(review,'module',lambda *_:pytest.fail('unselected adapter called'))
    with ExitStack() as stack,pytest.raises(ValueError,match='closed source-review'):
        review._load({},stack)


def test_current_reservation_api_check_has_no_effects(monkeypatch):
    from scripts.batch import harbor_continuation_allowance as allowance
    monkeypatch.setattr(allowance,'validate',lambda *_a,**_k:pytest.fail('must not execute reservation'))
    context.require_review_reservation_api(allowance)


def test_legacy_reservation_api_fails_before_cold_reader(monkeypatch):
    from scripts.fleet import migration
    def old_reserve(directory, *, allowance, semantic_scope, prior_records,
                    original_limit, expected_ordinal, prove_closed):
        pytest.fail('must not execute reservation')
    p={k:None for k in context.FIELDS}
    p.update(schema_version=context.SCHEMA,helper={'path':'review'},
             context_helper={'path':'context'},workbook='0061',source='source',
             proof='proof',candidate='candidate',reader_selection='selection')
    selected=dict(workbook='0061',source='source',revision='proof')
    monkeypatch.setattr(migration,'binding',lambda name:{'path':'review' if name==review.__file__ else 'context'})
    monkeypatch.setattr(migration,'checked',lambda row:selected if row=='selection' else dict(corrected_draft='candidate'))
    monkeypatch.setattr(context,'module',lambda *_:SimpleNamespace(reserve_review=old_reserve))
    monkeypatch.setattr(context.subprocess,'run',lambda *_a,**_k:pytest.fail('cold reader must not start'))
    with pytest.raises(ValueError,match='cumulative review reservation API'):
        context.load(p,review)


@pytest.mark.parametrize('mutation',['extra','wrong_helper','wrong_context'])
def test_context_rejects_unsealed_identity_before_cold_reader(monkeypatch,mutation):
    from scripts.fleet import migration
    p={k:None for k in context.FIELDS};p.update(schema_version=context.SCHEMA,helper={'path':'review'},context_helper={'path':'context'})
    monkeypatch.setattr(migration,'binding',lambda name:{'path':'review'if name==review.__file__ else 'context'})
    monkeypatch.setattr(context.subprocess,'run',lambda *_a,**_k:pytest.fail('cold reader must not start'))
    if mutation=='extra':p['skip_checks']=True
    elif mutation=='wrong_helper':p['helper']={}
    else:p['context_helper']={}
    with pytest.raises(ValueError,match='closed rejected-reader'):context.load(p,review)


@pytest.mark.parametrize('mutation',['workbook','source','proof','candidate'])
def test_context_rejects_foreign_source_or_derivative_before_reader(monkeypatch,mutation):
    from scripts.fleet import migration
    p={k:None for k in context.FIELDS};p.update(schema_version=context.SCHEMA,helper={'path':'review'},context_helper={'path':'context'},workbook='0061',source='source',proof='proof',candidate='candidate',reader_selection='selection')
    selected=dict(workbook='0061',source='source',revision='proof')
    monkeypatch.setattr(migration,'binding',lambda name:{'path':'review'if name==review.__file__ else 'context'})
    monkeypatch.setattr(migration,'checked',lambda row:selected if row=='selection'else dict(corrected_draft='candidate'))
    monkeypatch.setattr(context.subprocess,'run',lambda *_a,**_k:pytest.fail('cold reader must not start'))
    p[mutation]='foreign'
    with pytest.raises(ValueError,match='foreign retained source'):context.load(p,review)


def test_original_nonfleet_call_remains_readable_under_new_fleet_identity(setup,tmp_path,monkeypatch):
    from dataclasses import asdict,replace
    from scripts.batch import harbor_model_executor as E
    from test_harbor_model_executor import Response,response_bytes,TOKEN
    campaign,prepare=setup;campaign=replace(campaign,model_slots=1);monkeypatch.setenv("HARBOR_MODEL_SLOTS","1");old=prepare()
    monkeypatch.setattr(E,'_open',lambda *_:Response(response_bytes(old.policy)))
    result=E.execute(old,campaign,credential=TOKEN)
    monkeypatch.setenv('HARBOR_FLEET_URL','http://example.invalid')
    adapter=context.historical_executor(E)
    actual=review.historical_call(adapter,result.call,campaign,asdict(old.scope),1)
    assert actual[1].call==result.call
    assert adapter.execute is E.execute
    with pytest.raises(ValueError,match='only original completed'):
        adapter.inspect(replace(old,ordinal=3,limit=3),campaign)


def test_local_history_projection_rejects_changed_original_policy(setup,monkeypatch):
    from dataclasses import replace
    from scripts.batch import harbor_model_executor as E
    from test_harbor_model_executor import Response,response_bytes,TOKEN
    campaign,prepare=setup;campaign=replace(campaign,model_slots=1);monkeypatch.setenv("HARBOR_MODEL_SLOTS","1");old=prepare()
    monkeypatch.setattr(E,'_open',lambda *_:Response(response_bytes(old.policy)))
    E.execute(old,campaign,credential=TOKEN)
    monkeypatch.setenv('HARBOR_FLEET_URL','http://example.invalid')
    with pytest.raises(ValueError):context.historical_executor(E).inspect(replace(old,budget_evidence=old.budget_evidence+old.budget_evidence),campaign)

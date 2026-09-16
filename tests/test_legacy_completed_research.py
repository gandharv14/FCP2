from pathlib import Path
import copy,json
import pytest
from scripts.batch import harbor_legacy_research_history as L
ROOT=Path(__file__).resolve().parents[1]
H=ROOT/'diagnostics/vm-fleet-20260912/control/source-pool-legacy-research-001'

def save(path,value):path.write_text(json.dumps(value,sort_keys=True));return L.binding(path)

@pytest.fixture
def actual(tmp_path):
    original=L.binding(H/'0565-history.json');value=L.read(original)
    # Tests read immutable historical source/evidence, mutate only a temporary selection.
    return value,tmp_path

def test_real_legacy_namespace_and_opaque_count_are_preserved(actual):
    v,t=actual;p=save(t/'selected.json',v);before=copy.deepcopy(v)
    assert L.validate(p)['retained_opaque_research_calls']==1
    assert L.read(p)==before and not any('request' in x for x in v['audit_files'])

@pytest.mark.parametrize('fault',['wrong_authorization','wrong_source','counts_reset','new_unbound_history','omitted_history','expired','expanded_budget','foreign_namespace','changed_metadata','changed_generation'])
def test_legacy_authority_refuses_unaccounted_or_changed_evidence(actual,fault):
    v,t=actual
    if fault=='wrong_authorization':v['authorization']['sha256']='0'*64
    elif fault=='wrong_source':v['original']['sha256']='0'*64
    elif fault=='counts_reset':v['retained_opaque_research_calls']=0
    elif fault=='new_unbound_history':v['histories']['0565'].append(save(t/'unknown-model.json',{'status':'uncertain'}))
    elif fault=='omitted_history':v['histories']['0565']=[]
    elif fault=='expired':v['expires_at_ns']=v['created_at_ns']
    elif fault=='expanded_budget':v['new_research_max_calls']+=1
    elif fault=='foreign_namespace':v['home']='/tmp/another-workbook'
    elif fault=='changed_metadata':v['audit_files']['0565-inputs-variable-sources.metadata.json']['sha256']='0'*64
    elif fault=='changed_generation':v['source_manifest']['sha256']='0'*64
    with pytest.raises(ValueError):L.validate(save(t/'selected.json',v))


def test_new_research_budget_keeps_opaque_consumption_and_actual_chunks(actual):
    v,t=actual;proof=save(t/'selected.json',v)
    inventory=L.read(v['audit_files']['0565-inputs-variable-sources.inventory.json'])
    result=L.budget(proof,v['histories']['0565'],inventory,workbook='0565',inherited_budget=None)
    assert result['legacy_calls_opaque']==1 and result['legacy_calls_replayed']is False
    assert result['new_batches']==1 and result['new_maximum_calls']==2
    assert result['cumulative_maximum_calls']==3
    assert 'previous'not in result and 'typed_calls'not in result
    with pytest.raises(ValueError,match='cannot be combined'):
        L.budget(proof,v['histories']['0565'],inventory,workbook='0565',inherited_budget={'opaque':1})
    with pytest.raises(ValueError,match='omitted'):
        L.budget(proof,[],inventory,workbook='0565',inherited_budget=None)


def test_legacy_budget_rejects_new_unbounded_batches(actual,monkeypatch):
    v,t=actual;proof=save(t/'selected.json',v)
    monkeypatch.setattr('xl_variable_source_audit._split_chunks',lambda _:list(range(9)))
    with pytest.raises(ValueError,match='batch count'):
        L.budget(proof,v['histories']['0565'],{},workbook='0565',inherited_budget=None)


def test_selected_legacy_policy_requires_unique_exact_source_authority(actual):
    from types import SimpleNamespace
    v,t=actual;proof=save(t/'selected.json',v)
    assert L.selected(SimpleNamespace(campaign_evidence=[proof]),'0565')==proof
    assert L.selected(SimpleNamespace(campaign_evidence=[proof]),'0570')is None
    with pytest.raises(ValueError,match='ambiguous'):
        L.selected(SimpleNamespace(campaign_evidence=[proof,proof]),'0565')


def test_fresh_legacy_selection_requires_full_old_history_and_existing_source(actual):
    from scripts.fleet import fresh as F
    v,t=actual;v['home']=str(t/'0565');proof=save(t/'selected.json',v)
    selected=json.loads((H/'selection.json').read_bytes());inventory=L.read(selected['original_inventory'])
    source=next(x for x in inventory['sources']if x['workbook_id']=='0565')
    item=dict(workbook_id='0565',original_source=source,history_class='legacy_model_artifacts',preserved_history=v['histories']['0565'],preserved_curations=v['curations'])
    helper=t/'helper.py';helper.write_text('# no owning code\n');hb=L.binding(helper)
    plan=dict(schema_version=F.SCHEMA,workbook='0565',legacy_research_history=proof,queue=save(t/'queue.json',dict(items=[item])),inventory=selected['original_inventory'],history_roots=v['history_roots'],code_root=str(ROOT),home=v['home']+'/authoring',preparer=hb,dispatcher=hb,taxonomy=hb,env_file='/unused',batch='test',owner='unclaimed',publication_root='/unused',output_root='/unused',reconstruction=dict(mode='existing',generated_curation=None,handoff={}))
    survey=lambda roots,ids:(v['excluded'],v['histories'])
    assert F._validate(plan,survey)==source
    for key,value in [('home',v['home']+'/another'),('reconstruction',dict(mode='fresh',generated_curation=None))]:
        with pytest.raises(ValueError):F._validate(dict(plan,**{key:value}),survey)
    changed=copy.deepcopy(item);changed['preserved_history']=[]
    with pytest.raises(ValueError,match='history or curation'):
        F._validate(dict(plan,queue=save(t/'wrongqueue.json',dict(items=[changed]))),survey)
    with pytest.raises(ValueError,match='prior task'):
        F._validate(plan,lambda roots,ids:(v['excluded'],{}))


def test_completed_native_source_handoff_checks_actual_retained_curation(actual,monkeypatch):
    v,t=actual
    rows=json.loads((H/'selected-002/source-custody/mapping.json').read_bytes())
    by={x['original']['path']:x['local']['path']for x in rows}
    real_binding,real_read,real_inventory=L.binding,L.read,L.inventory
    def bound(path):
        key=str(path)
        if key not in by:return real_binding(path)
        return dict(path=key,sha256=real_binding(by[key])['sha256'])
    def read(row):
        if row['path']not in by:return real_read(row)
        assert bound(row['path'])==row
        return json.loads(Path(by[row['path']]).read_bytes())
    def inventory(path):
        path=Path(path)
        selected={str(Path(k).relative_to(path)):bound(k)for k in by if Path(k).is_relative_to(path)}
        return selected if selected else real_inventory(path)
    monkeypatch.setattr(L,'binding',bound);monkeypatch.setattr(L,'read',read);monkeypatch.setattr(L,'inventory',inventory)
    original_stat=Path.stat
    monkeypatch.setattr(Path,'stat',lambda path,**kw:original_stat(Path(by.get(str(path),str(path))),**kw))
    proof=real_binding(H/'0565-history.json')
    handoff=real_read(real_binding(H/'0565-authoring-001/handoff.json'))
    result=L.source_handoff(proof,handoff)
    assert result['curation']['sha256']==v['curations'][0]['sha256']
    assert result['reconstruction_replayed']is False
    wrong=copy.deepcopy(handoff);wrong['ledger']['sha256']='0'*64
    with pytest.raises((AssertionError,ValueError)):L.source_handoff(proof,wrong)
    # A changed generated curation fails even though the original remains intact.
    target=result['curation']['path'];changed=t/'changed.toml';changed.write_text('changed target scope')
    by[target]=str(changed)
    with pytest.raises((AssertionError,ValueError)):L.source_handoff(proof,handoff)

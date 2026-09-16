"""Changed source/code/reservation cannot reuse a costly verified projection."""
import copy,json
from pathlib import Path
from dataclasses import dataclass,replace
import pytest
from scripts.batch import harbor_disclosure_partition_cache as C
from scripts.batch.harbor_admission import record

@dataclass(frozen=True)
class Policy:
    model:str='fixture-model'

def put(path,value):
    path.write_text(json.dumps(value));return record(path)

@pytest.fixture
def cached(tmp_path,monkeypatch):
    root=tmp_path/'code';root.mkdir();source=root/'selected.py';source.write_text('selected implementation\n')
    snapshot=put(tmp_path/'snapshot.json',dict(root=str(root),files=[dict(relative=source.name,sha256=record(source)['sha256'])]))
    evidence=[put(tmp_path/(name+'.json'),dict(name=name))for name in ('plan','workbook','instruction','reservation','projection')]
    runs=[]
    def validate(binding,*,policy,root,current_inputs):
        assert current_inputs is True and binding==evidence[0]
        for r in evidence:assert record(Path(r['path']))==r
        runs.append(True)
        return dict(binding=binding),dict(batches=[dict(source='complete independent source projection')]),copy.deepcopy(evidence)
    monkeypatch.setattr(C.review,'validate_plan',validate)
    cache=C.ValidationCache(evidence[0],policy=Policy(),root=root,code_snapshots=[snapshot])
    return cache,root,snapshot,evidence,runs

def test_full_validation_once_and_exact_unchanged_evidence_each_use(cached):
    cache,root,_,_,runs=cached
    for _ in range(8):assert cache.get(policy=Policy(),root=root)[1]['batches']
    assert runs==[True]

@pytest.mark.parametrize('index',range(5))
def test_changed_bound_input_never_revalidates_or_retries(cached,index):
    cache,root,_,evidence,runs=cached;Path(evidence[index]['path']).write_text('{}')
    with pytest.raises(ValueError):cache.get(policy=Policy(),root=root)
    assert runs==[True]

@pytest.mark.parametrize('fault',['changed','added','removed','symlink','snapshot'])
def test_code_inventory_changes_invalidate(cached,fault):
    cache,root,snapshot,_,runs=cached
    if fault=='changed':(root/'selected.py').write_text('different')
    elif fault=='added':(root/'unexpected.py').write_text('different')
    elif fault=='removed':(root/'selected.py').unlink()
    elif fault=='symlink':(root/'linked').symlink_to(root/'selected.py')
    else:Path(snapshot['path']).write_text('{}')
    with pytest.raises(ValueError):cache.get(policy=Policy(),root=root)
    assert runs==[True]

def test_cache_is_not_mutable_authority(cached):
    cache,root,_,_,_=cached;cache.get(policy=Policy(),root=root)[1]['batches'].clear()
    with pytest.raises(ValueError,match='projection mutated'):cache.get(policy=Policy(),root=root)

def test_policy_or_root_cannot_change(cached):
    cache,root,_,_,_=cached
    with pytest.raises(ValueError):cache.get(policy=replace(Policy(),model='different'),root=root)
    with pytest.raises(ValueError):cache.get(policy=Policy(),root=root.parent)

def test_input_changed_during_initial_validation_is_rejected(tmp_path,monkeypatch):
    root=tmp_path/'code';root.mkdir();(root/'a.py').write_text('source')
    snapshot=put(tmp_path/'snapshot.json',dict(root=str(root),files=[dict(relative='a.py',sha256=record(root/'a.py')['sha256'])]))
    binding=put(tmp_path/'plan.json',{'initial':True})
    def invalidating(*args,**kwargs):
        Path(binding['path']).write_text('{}');return {},{},[binding]
    monkeypatch.setattr(C.review,'validate_plan',invalidating)
    with pytest.raises(ValueError):C.ValidationCache(binding,policy=Policy(),root=root,code_snapshots=[snapshot])

def test_new_uncertain_primary_operation_invalidates_even_with_unchanged_bytes(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from scripts.batch import harbor_disclosure_review_round as rounds
    root=tmp_path/'code';root.mkdir();(root/'a.py').write_text('source')
    snapshot=put(tmp_path/'snapshot.json',dict(root=str(root),files=[dict(relative='a.py',sha256=record(root/'a.py')['sha256'])]))
    campaign=SimpleNamespace(root=tmp_path/'campaign');campaign.root.mkdir()
    authority=put(tmp_path/'authority.json',dict(build={}))
    plan=put(tmp_path/'plan.json',dict(review_round=authority,workbook='fixture',original_source={'sha256':'a'*64}))
    p=json.loads(Path(plan['path']).read_text());runs=[]
    monkeypatch.setattr(C.review,'validate_plan',lambda *a,**k:(p,{},[plan,authority]))
    monkeypatch.setattr(rounds,'completed_build',lambda *a,**k:(SimpleNamespace(campaign=campaign),None,None))
    def validate(*a,**k):runs.append(k);return dict(round=1,prior_rejections=[])
    monkeypatch.setattr(rounds,'validate',validate)
    cache=C.ValidationCache(plan,policy=Policy(),root=root,code_snapshots=[snapshot])
    cache.get(policy=Policy(),root=root)
    primary=campaign.root/rounds.E.sha(rounds.E.canonical(rounds.semantic_scope(p)));primary.mkdir()
    (primary/'unknown-started.json').write_text('{}')
    with pytest.raises(ValueError,match='executor history'):cache.get(policy=Policy(),root=root)
    assert len(runs)==3 and all(r=={'check_build':True}for r in runs)

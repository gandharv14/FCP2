import ast,json
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from scripts.fleet import criterion_pre_review_bootstrap as B,criterion_pre_review_reader as R


def put(path,value):
    path.write_text(json.dumps(value));return B.binding(path)


def test_original_inventory_rejects_extra_and_changed_files(tmp_path):
    root=tmp_path/'code';root.mkdir();source=root/'one.py';source.write_text('pass\n')
    snap=put(tmp_path/'snapshot.json',dict(root=str(root),files=[dict(relative='one.py',sha256=B.binding(source)['sha256'])]))
    assert B.inventory(snap)==root
    source.write_text('raise RuntimeError\n')
    with pytest.raises(ValueError):B.inventory(snap)
    source.write_text('pass\n');(root/'extra.py').write_text('pass')
    with pytest.raises(ValueError):B.inventory(snap)


def test_later_renewal_requires_original_validator_and_exact_q_link(tmp_path):
    class Reader(R.Reader):
        def owned(self):return {}
        def old_worker(self):return NS(lease_renewal={'old':True})
    r=object.__new__(Reader);r.authority={'path':'/selected','sha256':'a'}
    permit=put(tmp_path/'permit.json',dict(prior_continuations={'dialogue_pre_review_repair':r.authority}))
    complete=put(tmp_path/'complete.json',dict(permit=permit));worker=NS(lease_renewal=complete,dialogue_pre_review_repair=r.authority)
    calls=[];original={'checkpoint':'old completed'}
    r.R=NS(validate=lambda w,b:(calls.append((w,b)),original)[1])
    assert r.historical_lease(worker,{'tracker':'current'})is original and len(calls)==1
    def reject(*args):raise ValueError('original CAS rejected')
    r.R.validate=reject
    with pytest.raises(ValueError,match='CAS rejected'):r.historical_lease(worker,{})
    permit=put(tmp_path/'foreign.json',dict(prior_continuations={'dialogue_pre_review_repair':{'foreign':True}}))
    worker.lease_renewal=put(tmp_path/'foreign-complete.json',dict(permit=permit))
    with pytest.raises(ValueError,match='omitted'):r.historical_lease(worker,{})


def test_declared_dispatcher_and_child_guards_keep_reviewed_original_functions():
    original=ast.parse(Path('scripts/fleet/source_card_execution.py').read_text())
    current=ast.parse(Path('scripts/fleet/criterion_pre_review_execution.py').read_text())
    for name in ('dispatcher','authorization_intent'):
        old=next(n for n in original.body if isinstance(n,ast.FunctionDef)and n.name==name)
        new=next(n for n in current.body if isinstance(n,ast.FunctionDef)and n.name==name)
        if name=='authorization_intent':
            old=ast.parse(ast.unparse(old).replace('fleet-0200-source-card-authorization-guard/v1','fleet-criterion-pre-review-authorization-guard/v1')).body[0]
        assert ast.dump(old,include_attributes=False)==ast.dump(new,include_attributes=False)

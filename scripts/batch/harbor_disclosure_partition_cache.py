"""Process-local reuse of a fully verified, unchanged review projection.

This never grants a lease, budget, dispatch permission, or semantic approval.
The owning controller must enforce those at every call. A changed byte, code
inventory, or in-memory projection invalidates the cache; it never refreshes
automatically. New processes must perform the full source rederivation again.
"""
from pathlib import Path
from dataclasses import asdict
import copy,hashlib,json
from scripts.batch.harbor_admission import read,record,require
from scripts.batch import harbor_disclosure_partition_review as review

def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def inventory(snapshot):
    value=read(snapshot);require(set(value)=={'root','files'},'closed complete code snapshot required')
    root=Path(value['root']);require(root.is_absolute() and root.is_dir(),'absolute selected code root required')
    expected={r['relative']:r['sha256']for r in value['files']}
    require(len(expected)==len(value['files']) and expected,'nonempty unique code inventory required')
    actual={}
    for p in root.rglob('*'):
        if '__pycache__'in p.parts:continue
        require(not p.is_symlink(),'symlink in selected code inventory')
        if p.is_file():actual[str(p.relative_to(root))]=record(p)['sha256']
    require(actual==expected,'selected complete code inventory changed')
    return root

class ValidationCache:
    def __init__(self,binding,*,policy,root,code_snapshots):
        require(type(code_snapshots)in(list,tuple) and code_snapshots,'complete selected code inventories required')
        self._snapshots=copy.deepcopy(code_snapshots)
        roots={inventory(s)for s in self._snapshots}
        require(Path(root)in roots,'review root absent from selected complete inventories')
        self._binding=copy.deepcopy(binding);self._policy=asdict(policy);self._root=Path(root)
        # Modules selected outside the historical root remain separately pinned.
        from scripts.batch import harbor_disclosure_partition_evidence as E,harbor_disclosure_partition_wire as W,harbor_semantic_readiness as S,harbor_disclosure_payload_repair as T,harbor_method_assessments as M
        self._modules=[record(Path(m.__file__))for m in (review,E,W,S,T,M)]+[record(Path(__file__))]
        self._value=review.validate_plan(binding,policy=policy,root=root,current_inputs=True)
        self._round_campaign=None
        if 'review_round'in self._value[0]:
            from scripts.batch import harbor_disclosure_review_round as rounds
            self._modules.append(record(Path(rounds.__file__)))
            authority=read(self._value[0]['review_round'])
            worker,_,_=rounds.completed_build(authority['build'])
            self._round_campaign=worker.campaign
        self._digest=digest(self._value)
        self._records=copy.deepcopy(self._value[2])
        require(self._binding in self._records,'validated evidence omits owning plan')
        self.get(policy=policy,root=root)

    def get(self,*,policy,root):
        require(asdict(policy)==self._policy and Path(root)==self._root,'cached review policy or root changed')
        for snapshot in self._snapshots:inventory(snapshot)
        for binding in (*self._modules,*self._records):
            require(record(Path(binding['path']))==binding,'cached review evidence changed')
        require(digest(self._value)==self._digest,'cached review projection mutated')
        if self._round_campaign is not None:
            from scripts.batch import harbor_disclosure_review_round as rounds
            inputs={k:v for k,v in self._value[0].items()if k!='review_round'}
            authority=rounds.validate(self._value[0]['review_round'],inputs,check_build=True)
            # Directory membership and full terminal HTTP journals can change
            # without changing the previously sealed outcome's bytes.
            rounds._primary_history(self._round_campaign,rounds.semantic_scope(inputs),authority['round'],authority['prior_rejections'])
        return self._value

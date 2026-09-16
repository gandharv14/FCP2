import copy
from pathlib import Path
import pytest
from scripts.batch import harbor_environment_refresh as F

def witness(tmp_path,name,raw=b'retained',key='normalization_report',original='/managed/report.json'):
 p=tmp_path/name;p.write_bytes(raw);record=F.record(p)
 return dict(key=key,original=dict(path=original,sha256=record['sha256']),copy=record)

def test_checked_accepts_two_independently_verified_copies_and_preserves_manifests(tmp_path):
 a,b=witness(tmp_path,'prior'),witness(tmp_path,'current');before=copy.deepcopy([a,b])
 F.checked(a['original'],[a,b]);assert F.merge_witnesses([a,b])==(a,);assert [a,b]==before
 assert all(Path(w['copy']['path']).exists() for w in [a,b])

@pytest.mark.parametrize('reverse',[False,True])
def test_corrupt_duplicate_never_hidden_by_history_collapse(tmp_path,reverse):
 a,b=witness(tmp_path,'prior'),witness(tmp_path,'current');Path(b['copy']['path']).write_bytes(b'changed')
 rows=[b,a] if reverse else [a,b]
 with pytest.raises(ValueError):F.merge_witnesses(rows)
 with pytest.raises(ValueError):F.checked(a['original'],rows)

def test_both_versions_at_one_mutable_path_remain_available(tmp_path):
 a=witness(tmp_path,'phase1',b'one','normalized_final');b=witness(tmp_path,'phase2',b'two','normalized_final')
 assert F.merge_witnesses([a,b])==(a,b)
 F.checked(a['original'],[a,b]);F.checked(b['original'],[a,b])

@pytest.mark.parametrize('change',['role','digest','schema'])
def test_duplicate_with_inconsistent_role_or_proof_rejected(tmp_path,change):
 a,b=witness(tmp_path,'a'),witness(tmp_path,'b')
 if change=='role':b['key']='normalization_exclusions'
 if change=='digest':b['copy']['sha256']='f'*64
 if change=='schema':b['unbound']='extra'
 with pytest.raises(ValueError):F.merge_witnesses([a,b])

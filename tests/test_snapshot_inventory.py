import copy
from pathlib import Path
import pytest
from scripts.fleet.snapshot_inventory import equivalent,ordered_reader

def rows():return dict(root='/immutable/code',files=[dict(relative='z.py',sha256='a'*64),dict(relative='.pipeline-code.json',sha256='b'*64)])
def test_original_sorted_reader_accepts_only_lossless_row_permutation():
 selected=rows();actual={**selected,'files':list(reversed(selected['files']))}
 assert actual!=selected and equivalent(actual,selected)
 wrapped=ordered_reader(lambda root:actual,selected)
 assert wrapped(Path(selected['root'])) is selected
 assert wrapped(Path('/foreign')) is actual
@pytest.mark.parametrize('kind',['duplicate','missing','extra','changed','root','traversal','absolute','alias','digest','field'])
def test_real_inventory_changes_not_hidden_by_order_reader(kind):
 selected=rows();actual=copy.deepcopy(selected)
 if kind=='duplicate':actual['files'].append(actual['files'][0])
 elif kind=='missing':actual['files'].pop()
 elif kind=='extra':actual['files'].append(dict(relative='new.py',sha256='c'*64))
 elif kind=='changed':actual['files'][0]['sha256']='f'*64
 elif kind=='root':actual['root']='/foreign'
 elif kind in ('traversal','absolute','alias'):actual['files'][0]['relative']={'traversal':'../x','absolute':'/x','alias':'a/./b'}[kind]
 elif kind=='digest':actual['files'][0]['sha256']='bad'
 else:actual['files'][0]['ignore']=True
 try:assert not equivalent(actual,selected)
 except ValueError:pass

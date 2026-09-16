import json
from pathlib import Path
import pytest
from scripts.batch.harbor_dialogue_first_round_warning import inventory,validate_code_manifest

@pytest.mark.parametrize('case',['valid','stale','missing','extra','untracked','symlink'])
def test_successor_manifest_is_truthful_and_old_code_is_untouched(tmp_path,case):
    old=tmp_path/'old';old.mkdir();(old/'worker.py').write_text('old code');oldbytes=(old/'worker.py').read_bytes()
    new=tmp_path/'new';new.mkdir();(new/'worker.py').write_text('new reviewed code');rows=inventory(new)
    if case=='stale':rows['worker.py']='0'*64
    elif case=='missing':rows={}
    elif case=='extra':rows['absent.py']='0'*64
    elif case=='untracked':(new/'unexpected.py').write_text('untracked')
    elif case=='symlink':(new/'linked.py').symlink_to(old/'worker.py')
    (new/'.pipeline-code.json').write_text(json.dumps(rows))
    if case=='valid':validate_code_manifest(new)
    else:
        with pytest.raises(ValueError):validate_code_manifest(new)
    assert (old/'worker.py').read_bytes()==oldbytes

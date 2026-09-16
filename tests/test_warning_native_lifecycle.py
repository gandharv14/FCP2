from pathlib import Path
from types import SimpleNamespace
import json,sys
import pytest
from scripts.fleet import retained_warning_continue as M


def save(path,value):path.write_text(json.dumps(value));return M.binding(path)

@pytest.mark.parametrize('fault',[None,'source','owner','selection','validation'])
def test_scoped_actual_completion_and_restore(tmp_path,monkeypatch,fault):
    import scripts.batch as package
    old={name:(sys.modules.get('scripts.batch.'+name),getattr(package,name,None))for name in('harbor_tracker_lifecycle','harbor_continuation_allowance','harbor_lease_takeover')}
    bindings={k:dict(path='/bound/'+k,sha256=k)for k in('lifecycle','allowance','takeover','stdlib')}
    snap=dict(path='/bound/snapshot',sha256='snapshot');allow=dict(path='/bound/allowance',sha256='allowance');selection=dict(workbook='0064',owner='original-native-owner',allowance=allow)
    stop=save(tmp_path/'stop.json',dict(selection=dict(selection)));complete=save(tmp_path/'complete.json',dict(stopped_owner_proof=stop))
    profile=save(tmp_path/'profile.json',dict(schema_version='harbor-allowance-takeover-direct/v1',original_snapshot=snap,shared=bindings,selection=selection,allowance=allow))
    p=dict(native_lifecycle=dict(profile=profile,completion=complete),live_renewal=dict(completed_semantic={}),old_snapshot=snap,workbook='0064',source_sha256='original-source')
    before=dict(workbook_id='0064',bindings=dict(original_raw_source=dict(sha256='original-source')));after=json.loads(json.dumps(before));lease=SimpleNamespace(owner=selection['owner']);calls=[]
    if fault=='source':after['bindings']['original_raw_source']['sha256']='foreign'
    if fault=='owner':lease.owner='foreign'
    if fault=='selection':p['native_lifecycle']['profile']=save(tmp_path/'changed-profile.json',dict(M.read(profile),selection=dict(selection,owner='other')))
    def validate(bound):
        assert bound==complete;calls.append(bound)
        if fault=='validation':raise RuntimeError('original T rejected')
        return before,after,lease
    T=SimpleNamespace(validate_completion=validate,_selection=lambda x:x)
    def load(name,bound):
        key=next(k for k,v in bindings.items()if v==bound);got=T if key=='takeover'else SimpleNamespace(selected=key);sys.modules[name]=got;return got
    monkeypatch.setattr(M,'module',load)
    if fault:
        with pytest.raises((ValueError,RuntimeError)):
            with M.native_lifecycle_scope(p):pass
    else:
        with pytest.raises(RuntimeError,match='after owning scope'):
            with M.native_lifecycle_scope(p)as result:
                assert result==(after,lease)and package.harbor_lease_takeover is T
                raise RuntimeError('after owning scope')
    assert calls==[complete]
    for name,(module,attribute)in old.items():
        assert sys.modules.get('scripts.batch.'+name)is module
        assert getattr(package,name,None)is attribute

def test_ordinary_context_cannot_select_semantic_lifecycle(tmp_path):
    with pytest.raises(ValueError,match='completed semantic'):
        with M.native_lifecycle_scope(dict(native_lifecycle=dict(profile={},completion={}),live_renewal={})):pass

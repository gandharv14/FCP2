from dataclasses import dataclass,replace
from pathlib import Path
from types import SimpleNamespace
import json,sys
import pytest
from scripts.fleet import retained_takeover_history as H

@dataclass
class Worker:
    run:Path
    root:Path
    lease_renewal:dict
    warning_requalification:object=None

def put(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return H.binding(path)

@pytest.fixture
def selected(tmp_path):
    root=tmp_path/'old';batch=root/'scripts/batch';batch.mkdir(parents=True)
    original="def _validate(worker,before,seen,*,historical_code_inventory=None):\n    _renewal_chain(permit['tracker'],permit['lease'],after,Lease(**complete['lease']))\n    return (before, historical_code_inventory)\n"
    rp=batch/'harbor_instruction_lease_renewal.py';rp.write_text(original)
    tp=batch/'harbor_lease_takeover.py';tp.write_text("MARKER='original'\n")
    (batch/'harbor_tracker_lifecycle.py').write_text('MARKER=True\n')
    snap=put(tmp_path/'snapshot.json',dict(root=str(root),files=[dict(relative=str(p.relative_to(root)),sha256=H.binding(p)['sha256'])for p in root.rglob('*')if p.is_file()]))
    ap=tmp_path/'adapter.py';ap.write_text('ORIGINAL_R='+repr(H.binding(rp))+"\ndef checked(row):return None\ndef _takeover_chain(worker,permit,complete,after):\n    from scripts.batch import harbor_lease_takeover as T\n    assert T.MARKER=='original'\n    if after.get('raise'):raise RuntimeError('real chain failure')\n"+original.replace('def _validate','def _continued_validate').replace("_renewal_chain(permit['tracker'],permit['lease'],after,Lease(**complete['lease']))",'_takeover_chain(worker,permit,complete,after)'))
    authority=put(tmp_path/'authority.json',dict(reader=H.binding(ap)));takeover=put(tmp_path/'takeover.json',{});permit=put(tmp_path/'permit.json',dict(takeover_read_authority=authority));complete=put(tmp_path/'complete.json',dict(permit=permit,expired_takeover=takeover))
    profile=put(tmp_path/'profile.json',dict(adapter=H.binding(ap),continued_checkpoint=complete,takeover=takeover,authority=authority,takeover_implementation=H.binding(tp)))
    row=put(tmp_path/'selection.json',dict(schema_version='fleet-retained-takeover-history/v1',helper=H.binding(H.__file__),adapter=H.binding(ap),profile=profile,original_renewal=H.binding(rp),original_snapshot=snap))
    R=H.module('history_fixture',H.binding(rp));R.permit={};R.complete={};R.after={};R._historical_inventory=lambda *a:{'actual':'inventory'}
    W=SimpleNamespace();worker=Worker(tmp_path/'run',tmp_path/'new',complete)
    return row,R,W,worker,snap,ap

def test_original_chain_scope_and_exception_restore(selected):
    row,R,W,w,snap,_=selected;original=R._validate
    from scripts.batch import harbor_lease_takeover as T
    with H.installed(row,R,W,w,snap):
        old=replace(w,root=Path(H.read(snap)['root']))
        assert R._validate(old,{'actual':True},set(),historical_code_inventory={'actual':'inventory'})==({'actual':True},{'actual':'inventory'})
        assert sys.modules['scripts.batch.harbor_lease_takeover'] is T
        R.after['raise']=True
        with pytest.raises(RuntimeError,match='real chain'):R._validate(old,{},set(),historical_code_inventory={'actual':'inventory'})
        assert sys.modules['scripts.batch.harbor_lease_takeover'] is T
    assert R._validate is original

@pytest.mark.parametrize('fault',['current_root','other_run','warning','inventory'])
def test_rejects_foreign_or_current_worker(selected,fault):
    row,R,W,w,snap,_=selected
    with H.installed(row,R,W,w,snap):
        old=replace(w,root=Path(H.read(snap)['root']));inventory={'actual':'inventory'}
        if fault=='current_root':old=w
        if fault=='other_run':old=replace(old,run=old.run/'foreign')
        if fault=='warning':old=replace(old,warning_requalification={})
        if fault=='inventory':inventory={}
        with pytest.raises(ValueError):R._validate(old,{},set(),historical_code_inventory=inventory)

def test_changed_selected_bytes_and_snapshot_rejected(selected):
    row,R,W,w,snap,ap=selected;ap.write_text(ap.read_text()+'\n# changed')
    with pytest.raises(ValueError):
        with H.installed(row,R,W,w,snap):pass

def test_unrelated_validator_change_rejected(selected,tmp_path):
    _,R,_,_,_,ap=selected;path=tmp_path/'foreign.py';path.write_text(ap.read_text().replace('return (before, historical_code_inventory)','return True'))
    with pytest.raises(ValueError,match='unrelated'):H.exact_validator(R.__file__,path)

def test_exact_current_warning_delegates_original_before_historical_projection(selected):
    row,R,W,w,snap,_=selected
    current=replace(w,warning_requalification={'actual':'permit'})
    historical=replace(w,root=Path(H.read(snap)['root']))
    calls=[]
    def original(selected,before,seen,*,historical_code_inventory=None):
        calls.append((selected,before,seen,historical_code_inventory))
        if before.get('reject'):raise RuntimeError('original warning rejected')
        return R._validate(historical,before,seen,historical_code_inventory={'actual':'inventory'})
    R._validate=original
    with H.installed(row,R,W,current,snap):
        assert R._validate(current,{'actual':True},set())==({'actual':True},{'actual':'inventory'})
        assert calls==[(current,{'actual':True},set(),None)]
        with pytest.raises(RuntimeError,match='original warning rejected'):
            R._validate(current,{'reject':True},set())
        with pytest.raises(ValueError):R._validate(replace(current,run=current.run/'foreign'),{},set())
        with pytest.raises(ValueError):R._validate(current,{},set(),historical_code_inventory={'actual':'inventory'})
    assert R._validate is original

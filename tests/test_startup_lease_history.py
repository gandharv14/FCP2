from dataclasses import replace
from pathlib import Path
import copy,json
import pytest
from scripts.fleet import package_recovery_continue as M
from scripts import harbor_campaign as C
from scripts.batch import harbor_tracker_lifecycle as life
from test_dialogue_semantic_retry import failed
from test_harbor_instruction_producer import setup


def case(failed,tmp_path):
    worker,selection,old,_,_,_,_,calls=failed
    path=worker.context.tracker_root/(selection.content.workbook+'.json')
    before=tmp_path/'before.json';before.write_bytes(path.read_bytes());before_row=M.binding(before)
    def put(path,value):path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(life.canonical(value));return M.binding(path)
    result=life.lease_operation(path,authority=life.RootAuthority('harbor-orchestrator',C.DURATION,lambda *a:True),operation='renew',lease_kind='task_agent',owner=old.owner,scope='workbook:'+selection.content.workbook,token=old.token,duration_ns=C.DURATION,expected_revision=old.revision,expected_sha256=old.sha256)
    new=replace(old,revision=result['revision'],sha256=result['sha256']);home=tmp_path/'continuation'
    completion=put(tmp_path/'takeover.json',dict(after=before_row));profile=put(tmp_path/'profile.json',dict(selection={'tracker':dict(path=str(path),sha256=old.sha256)}))
    receipt=put(home/'instruction-start/lease-renewal.json',dict(previous=C.encode(old),lease=C.encode(new),tracker=M.binding(path)))
    p=dict(home=str(home),takeover_completion=completion,takeover_profile=profile,source=selection.content.bindings['original_raw_source'])
    return p,old,new,path,receipt,calls


def test_actual_original_lifecycle_renewal_survives_later_history(failed,tmp_path):
    p,old,new,path,receipt,calls=case(failed,tmp_path)
    assert M.startup_lease_history(p,C,C.encode(old),C.encode(new))==receipt
    life.lease_operation(path,authority=life.RootAuthority('harbor-orchestrator',C.DURATION,lambda *a:True),operation='renew',lease_kind='task_agent',owner=new.owner,scope='workbook:'+M.read(M.binding(path))['workbook_id'],token=new.token,duration_ns=C.DURATION,expected_revision=new.revision,expected_sha256=new.sha256)
    assert M.startup_lease_history(p,C,C.encode(old),C.encode(new))==receipt
    assert len(calls)==3

@pytest.mark.parametrize('fault',['source','owner','hash','event','receipt','expired'])
def test_foreign_or_fabricated_renewal_rejected(failed,tmp_path,fault):
    p,old,new,path,receipt,_=case(failed,tmp_path)
    if fault=='source':p['source']={'foreign':True}
    if fault=='owner':new=replace(new,owner='foreign')
    if fault=='hash':new=replace(new,sha256='0'*64)
    if fault in ('event','expired'):
        value=M.read(M.binding(path));event=value['handoffs'][-1]
        if fault=='event':event['operation']='replace_expired'
        else:event['at']=event['before']['expires_at']
        path.write_bytes(life.canonical(value))
    if fault=='receipt':Path(receipt['path']).write_bytes(b'{}')
    with pytest.raises((ValueError,KeyError)):M.startup_lease_history(p,C,C.encode(old),C.encode(new))

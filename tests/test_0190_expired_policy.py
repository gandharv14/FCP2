from pathlib import Path
from dataclasses import dataclass,asdict
from types import SimpleNamespace
import importlib.util
import pytest
from scripts.fleet.coordinator import Coordinator
from scripts.fleet.capacity_policy import AUTHORIZATION_SHA256,CHANGE_ID

ROOT=Path(__file__).parents[1]
P=ROOT/'diagnostics/vm-fleet-20260912/control/0190-expired-semantic-002/continue.py'
spec=importlib.util.spec_from_file_location('expired0190policy',P)
M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)


def test_two_other_live_models_allowed_but_uncertainty_and_target_still_block(tmp_path):
    c=Coordinator(tmp_path/'ledger.sqlite')
    c.set_model_limit(None,dict(path='/retained/authorization.json',sha256=AUTHORIZATION_SHA256),CHANGE_ID,0)
    auth=[]
    for wb in ('0064','0303'):
        c.enroll(wb,'a'*64,'source');c.reconcile(wb,'stopped')
        v=c.claim(wb,'vm:'+wb,wb,'b'*64);a={k:v[k] for k in ('workbook','owner','token','fence')}
        c.begin('model',wb,request_sha256='c'*64,**a);auth.append(a)
    assert len(M.operation_gate(c.status(),'0190'))==2
    with pytest.raises(ValueError):M.operation_gate(c.status(),'0064')
    c.end('model','0064',evidence='unknown',outcome='uncertain',**auth[0])
    with pytest.raises(ValueError):M.operation_gate(c.status(),'0190')


@dataclass(frozen=True)
class Lease:
    owner:str='exact-owner'


def test_original_replace_tuple_unpacked_before_seal(tmp_path,monkeypatch):
    p=dict(output=str(tmp_path/'home'),takeover_directory=str(tmp_path/'takeover'),alias_witness={})
    selected=SimpleNamespace(tracker={});lease=Lease();receipt={'path':'/completion','sha256':'a'*64}
    calls=[]
    def replace(*args):
        assert (tmp_path/'home/intent.json').is_file();calls.append(1);return lease,receipt
    T=SimpleNamespace(replace_expired=replace)
    monkeypatch.setattr(M,'inspect',lambda row:(p,{},T,selected,{}));monkeypatch.delenv('HARBOR_FLEET_CONTINUATION_READ_ONLY',raising=False)
    real_read=M.read;monkeypatch.setattr(M,'read',lambda r:{'lease':asdict(lease)} if r==receipt else real_read(r))
    monkeypatch.setattr(M,'seal',lambda plan,p,T,s,intent,complete:complete)
    assert M.replace_once({'fixture':True})==receipt and calls==[1]
    with pytest.raises(FileExistsError):M.replace_once({'fixture':True})
    assert calls==[1]


def test_recover_unpacks_original_tuple_without_replacement(tmp_path,monkeypatch):
    lease=Lease();receipt={'path':'/completion','sha256':'a'*64};plan={'fixture':True}
    p=dict(output=str(tmp_path),takeover_directory=str(tmp_path/'takeover'))
    T=SimpleNamespace(recover=lambda r:(lease,receipt),replace_expired=lambda *a:pytest.fail('must not replace'))
    monkeypatch.setattr(M,'load',lambda row:(p,{},T,None));monkeypatch.setattr(M,'stopped',lambda p:None);monkeypatch.setattr(M,'central',lambda p:None)
    monkeypatch.setattr(M,'record',lambda p:{'path':str(p),'sha256':'b'*64})
    monkeypatch.setattr(M,'read',lambda r:{'lease':asdict(lease)} if r==receipt else {'plan':plan})
    monkeypatch.setattr(M,'seal',lambda plan,p,T,s,intent,complete:complete)
    assert M.recover(plan)==receipt

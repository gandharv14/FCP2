from types import SimpleNamespace
from pathlib import Path
import json
import pytest
from scripts.batch import harbor_dialogue_semantic_retry as S,harbor_disclosure_command_repair as K,harbor_instruction_repair as I


def fixture(tmp_path,kind):
    def put(name,value):
        p=tmp_path/name;p.write_text(json.dumps(value));return S.record(p)
    source=put('source',dict(original='preserved'))
    profile=put('profile',dict(source=source))
    receipt=put('permit',dict(schema_version=kind,profile=profile,initial=source,original=source,tracker=source,
        selection=dict(stage=dict(path=str(tmp_path/'historical-directory'),sha256='0'*64))))
    worker=SimpleNamespace(**{k:None for k in ('dialogue_pre_review_repair','lease_renewal','retry','dialogue_retry','boundary_recovery','disclosure_revision','disclosure_repair')},code_repair=receipt)
    return worker,receipt,source,profile


def test_disclosure_suffix_uses_owning_validator_and_retains_source(tmp_path,monkeypatch):
    w,receipt,source,profile=fixture(tmp_path,K.SCHEMA);seen=[];before={'workbook':'0187'}
    monkeypatch.setattr(K,'validate',lambda worker,tracker:seen.append((worker,tracker))or S.read(receipt))
    monkeypatch.setattr(I,'validate_historical_integrity',lambda *a:pytest.fail('wrong repair type'))
    records=S._history(w,before)
    assert seen==[(w,before)]
    assert all(r in records for r in (receipt,profile,source))


def test_failed_original_disclosure_validator_cannot_be_waived(tmp_path,monkeypatch):
    w,*_=fixture(tmp_path,K.SCHEMA)
    def rejected(*a):raise ValueError('retained source/code changed')
    monkeypatch.setattr(K,'validate',rejected)
    with pytest.raises(ValueError,match='source/code'):S._history(w,{})


def test_other_repair_keeps_original_historical_validator(tmp_path,monkeypatch):
    w,receipt,source,profile=fixture(tmp_path,I.SCHEMA);seen=[]
    monkeypatch.setattr(K,'validate',lambda *a:pytest.fail('foreign discriminator'))
    monkeypatch.setattr(I,'validate_historical_integrity',lambda worker,before:seen.append(worker)or({},[source]))
    assert source in S._history(w,{}) and seen==[w]


def test_worker_historical_dispatch_retains_disclosure_type(tmp_path,monkeypatch):
    w,receipt,source,profile=fixture(tmp_path,K.SCHEMA);before={'revision':42};seen=[]
    monkeypatch.setattr(K,'validate',lambda worker,tracker:seen.append((worker,tracker))or S.read(receipt))
    permit,records=I.validate_historical_integrity(w,before)
    assert seen==[(w,before)] and permit==S.read(receipt)
    assert all(r in records for r in (receipt,source,profile))
    assert not any(r['path'].endswith('historical-directory')for r in records)


def test_worker_historical_dispatch_preserves_original_rejection(tmp_path,monkeypatch):
    w,*_=fixture(tmp_path,K.SCHEMA)
    def rejected(*a):raise ValueError('retained snapshot changed')
    monkeypatch.setattr(K,'validate',rejected)
    with pytest.raises(ValueError,match='snapshot changed'):I.validate_historical_integrity(w,{})

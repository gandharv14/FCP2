from pathlib import Path
from types import SimpleNamespace
import json
import pytest
from scripts.batch import harbor_disclosure_round_adoption as A
from scripts.batch import harbor_disclosure_review_round as Q
from scripts.batch.harbor_admission import read,record


def retained(tmp_path):
    original=tmp_path/'instruction.md';original.write_text('{"text":"original truthful source"}')
    old=record(original);archive=tmp_path/'instruction.snapshot.md';archive.write_bytes(original.read_bytes())
    receipt=dict(historical_records=[dict(original=old,archive=record(archive))],historical_instruction=old)
    return original,archive,old,receipt


def test_original_round_read_is_historical_and_live_custody_stays_current(tmp_path):
    original,archive,old,receipt=retained(tmp_path)
    original.write_text('{"text":"later independently validated NAT"}')
    fresh=record(original);prior=(Q.read,Q.record,Q.life)
    with A.historical_round(receipt):
        assert Q.read(old)=={'text':'original truthful source'}
        assert Q.record(original)==old
        assert Q.life.checked_record(old)==old
        assert record(original)==fresh and read(fresh)=={'text':'later independently validated NAT'}
    assert (Q.read,Q.record,Q.life)==prior
    with pytest.raises(ValueError):Q.read(old)


@pytest.mark.parametrize('fault',['archive','digest','foreign','missing'])
def test_historical_projection_rejects_tampering_or_unlisted_old_paths(tmp_path,fault):
    original,archive,old,receipt=retained(tmp_path)
    original.write_text('{"text":"changed"}')
    if fault=='archive':archive.write_text('{}')
    elif fault=='digest':receipt['historical_records'][0]['archive']['sha256']='a'*64
    elif fault=='missing':archive.unlink()
    with pytest.raises((ValueError,FileNotFoundError)):
        with A.historical_round(receipt):
            if fault=='foreign':Q.read(dict(path=str(tmp_path/'foreign.json'),sha256=old['sha256']))
            else:Q.read(old)


def test_projection_restores_original_reader_on_exception(tmp_path):
    _,_,_,receipt=retained(tmp_path);prior=(Q.read,Q.record,Q.life)
    with pytest.raises(RuntimeError):
        with A.historical_round(receipt):raise RuntimeError('consumer failed')
    assert (Q.read,Q.record,Q.life)==prior

from dataclasses import replace,asdict
from scripts.batch import harbor_instruction_worker as W,harbor_disclosure_review_resume as D
from scripts import harbor_instruction_campaign as I
from test_instruction_worker import case,instruction_setup
from test_harbor_environment_worker import worker,selected,setup


def profile():
    return dict(implementation=record(A.__file__),instruction=record(I.__file__),worker=record(W.__file__),resume=record(D.__file__),functions=dict(operation=A.function_sha(W._operation),disclosure_history=A.function_sha(I._disclosure_history),aggregate=A.function_sha(A.review.validate_aggregate)))


def test_actual_worker_and_retry_codec_consume_one_new_adoption_step(case,monkeypatch):
    w,s,l,*_=case;original=W._operation
    monkeypatch.setattr(W,'_operation',lambda w,s,l,*a,**k:(s,l,None))
    build=W.step(w,s,l);assert build.next_step=='disclosure-review'
    monkeypatch.setattr(W,'_operation',original)
    receipt=dict.fromkeys(A.KEYS,None);receipt.update(schema_version=A.SCHEMA,implementation=record(A.__file__),prior_continuations={},blocked=build.evidence,selection=I.codec.encode(s),lease=asdict(l))
    bound=W.keep(w.run/'adoption/permit.json',receipt);new=replace(w,disclosure_repair=bound);calls=[]
    monkeypatch.setattr(A,'validate',lambda worker,before:receipt)
    def consume(worker,selection,lease,directory):calls.append(directory);return selection,lease,None
    monkeypatch.setattr(A,'operation',consume)
    monkeypatch.setattr(W,'_model',lambda *a,**k:pytest.fail('adoption cannot transport'))
    p=profile()
    with A.installed(p,receipt=bound):
        descriptor=W.keep(w.run/'adoption/descriptor.json',dict(worker=I.worker_encode(new)))
        result=W.keep(w.run/'adoption/result.json',I._result('RETRY_READY',descriptor,s,l,checkpoint=bound,next_step='disclosure-review',evidence=[bound]))
        assert I._prior(result,descriptor)[1]==s
        completed=W.step(new,s,l,checkpoint=bound)
        assert completed.status=='STEP_COMPLETE'and completed.next_step=='disclosure-commit'
        assert calls==[w.run/'adoption/steps/disclosure-review']
        assert W.read(completed.evidence)['intent']==W.record(calls[0]/'intent.json')
        with pytest.raises(ValueError,match='preceding checkpoint'):W.step(new,s,l,checkpoint=bound)
        assert len(calls)==1
    assert W._operation is original


@pytest.mark.parametrize('defect',['worker','operation','instruction','extra'])
def test_undeclared_owning_function_never_installed(defect):
    p=profile();old=W._operation
    if defect=='operation':p['functions']['operation']='a'*64
    elif defect=='extra':p['extra']=True
    else:p[defect]={**p[defect],'sha256':'a'*64}
    with pytest.raises(ValueError):
        with A.installed(p):pytest.fail('unlisted runtime installed')
    assert W._operation is old

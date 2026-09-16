from dataclasses import replace,asdict
from pathlib import Path
import pytest
from scripts import harbor_instruction_campaign as I
from scripts.batch import harbor_disclosure_round_adoption as A,harbor_instruction_worker as W
from scripts.fleet import disclosure_adoption_continue as S
from scripts.batch.harbor_admission import read,record
from test_disclosure_round_adoption import profile
from test_instruction_worker import case,instruction_setup
from test_harbor_environment_worker import worker,selected,setup

@pytest.mark.parametrize('fault',[None,'module','prior','decode','history'])
def test_real_relocated_prior_decodes_sealed_permit_through_declared_history(case,tmp_path,monkeypatch,fault):
    w,s,l,*_=case
    copied=tmp_path/'retained_instruction.py';copied.write_bytes(Path(I.__file__).read_bytes())
    other=S.module('fixture_retained_checkpoint',record(copied));monkeypatch.setattr(I,'_prior',other._prior)
    earlier=W.keep(tmp_path/'old-command-permit.json',dict(schema_version='harbor-disclosure-command-repair/v1'))
    value=dict.fromkeys(A.KEYS,None);value.update(schema_version=A.SCHEMA,implementation=record(A.__file__),prior_continuations=dict(code_repair=earlier),selection=I.codec.encode(s),lease=asdict(l))
    receipt=W.keep(tmp_path/'adoption.json',value);new=replace(w,code_repair=earlier,disclosure_repair=receipt)
    witness=dict(module=record(copied),prior=A.function_sha(other._prior),decode=A.function_sha(other.worker_decode),history=A.function_sha(other._disclosure_history));p=dict(owning_profile=profile(),checkpoint_reader=witness)
    with A.installed(p['owning_profile']):
        descriptor=W.keep(tmp_path/'descriptor.json',dict(worker=I.worker_encode(new)))
        checkpoint=W.keep(tmp_path/'ready.json',I._result('RETRY_READY',descriptor,s,l,checkpoint=receipt,next_step='disclosure-review',evidence=[receipt]))
        with pytest.raises(ValueError,match='detached continuations'):I._prior(checkpoint,descriptor)
    original=other._disclosure_history
    if fault=='module':witness['module']={**witness['module'],'sha256':'f'*64}
    elif fault:witness[fault]='f'*64
    if fault:
        with pytest.raises(ValueError):
            with S.owning_runtime(A,p,receipt=receipt):pytest.fail('foreign reader installed')
    else:
        with S.owning_runtime(A,p,receipt=receipt):
            assert I._prior(checkpoint,descriptor)[1:]==(s,l)
            assert other.worker_decode(read(descriptor)['worker'])==new
    assert other._disclosure_history is original

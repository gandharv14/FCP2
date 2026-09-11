"""A parser repair preserves the completed disclosure transport and namespace."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.batch import harbor_instruction_repair as R
from scripts.batch import harbor_instruction_worker as W
from scripts import harbor_instruction_campaign as C
from test_instruction_worker import case,instruction_setup
from test_dialogue_scope_names import modules


def failure(message):
    return dict(type='ValueError',message_sha256=W.executor.sha(message.encode()),automatic_replay=False)


@pytest.mark.parametrize('ingredient,passed',[("cell 'Inputs'!B2 on the row labelled 'Debt \"A\"'",True),
    ('"Debt" (cell B2 on the \'Inputs\' sheet)',False),('unparsed Debt "A"',False)])
def test_quoted_kind_requires_complete_actual_source_phrase(modules,tmp_path,monkeypatch,ingredient,passed):
    spoken,_=modules
    monkeypatch.setattr(W.producer,'skill_module',lambda *a:spoken)
    W.keep(tmp_path/'tests/disclosure.json',{'agent_records':[{'entry':'source_selection','fields':{'ingredient':ingredient}}]})
    claims={'claims':[{'reviewer_only':{'entry':'source_selection'}}]}
    args=(failure('source selection requires a complete named source reference'),claims,tmp_path,tmp_path)
    if passed:assert R.extraction_kind(*args)=='quoted-source-label'
    else:
        with pytest.raises(ValueError):R.extraction_kind(*args)
    with pytest.raises(ValueError,match='omitted'):
        R.extraction_kind(args[0],{'claims':[]},tmp_path,tmp_path)


def test_row_policy_and_unknown_failure_remain_closed(tmp_path):
    claims={'claims':[{'reviewer_only':{'entry':'row_populated'}}]}
    assert R.extraction_kind(failure('calculation scope requires explicit cell identities'),claims,tmp_path,tmp_path)=='row-policy'
    with pytest.raises(ValueError,match='unsupported'):
        R.extraction_kind(failure('arbitrary parser failure'),claims,tmp_path,tmp_path)


def test_code_repair_codec_retains_disclosure_and_routes_only_failed_step(case,tmp_path):
    worker,*_=case
    disclosure=W.keep(tmp_path/'disclosure/permit.json',{'phase':'disclosure-review'})
    repair=W.keep(tmp_path/'parser/permit.json',{'phase':'dialogue-init','prior_continuations':{'disclosure_repair':disclosure}})
    selected=replace(worker,disclosure_repair=disclosure,code_repair=repair)
    assert C.worker_decode(C.worker_encode(selected))==selected
    assert W.step_directory(selected,'dialogue-init')==tmp_path/'parser/steps/dialogue-init'
    assert W.step_directory(selected,'verification-commit')==tmp_path/'disclosure/steps/verification-commit'
    assert W.step_directory(selected,'dialogue-writer')==tmp_path/'disclosure/steps/dialogue-writer'
    missing=W.keep(tmp_path/'missing.json',{'phase':'dialogue-init'})
    with pytest.raises(ValueError,match='detached'):
        C.worker_encode(replace(selected,code_repair=missing))

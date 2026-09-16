import pytest
from scripts.batch.harbor_admission import DispatcherAdmission,AdmissionError
from scripts.batch import harbor_model_executor as E,harbor_lease_takeover as T
from scripts.fleet import continuation_history as H
from scripts.fleet.migration import retain


def test_continuation_context_cannot_create_fresh_admission(monkeypatch):
    monkeypatch.setenv('HARBOR_FLEET_CONTINUATION_HISTORY_PATH','/bound/continuation.json')
    dispatcher=object.__new__(DispatcherAdmission)
    with pytest.raises(AdmissionError,match='fresh admission'):dispatcher._check('0006')


def test_read_only_context_stops_model_and_takeover_before_any_work(monkeypatch,tmp_path):
    monkeypatch.setenv('HARBOR_FLEET_CONTINUATION_READ_ONLY','1')
    with pytest.raises(ValueError,match='read-only'):E.execute(None,None,credential='not-read')
    with pytest.raises(ValueError,match='read-only'):T.replace_expired(None,tmp_path/'lease-takeovers/one')
    assert not list(tmp_path.iterdir())


def test_history_cannot_be_relabeled_as_fresh_authority(tmp_path):
    b=retain(tmp_path/'forged.json',{'schema_version':H.SCHEMA,'fresh_admission_allowed':True})
    with pytest.raises(ValueError,match='unsupported'):H.validate(b,[],[], '0006')


def test_actual_full_history_cannot_omit_continued_tracker(tmp_path):
    from pathlib import Path
    from scripts.fleet.migration import binding,checked
    p=Path('/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0006/history-001.json')
    if not p.exists():pytest.skip('optional real continued history fixture')
    value=checked(binding(p));H.validate(binding(p),value['roots'],value['known_ids'],'0006')
    value['central_histories'].pop('0006')
    forged=retain(tmp_path/'pruned.json',value)
    with pytest.raises(ValueError,match='omitted'):H.validate(forged,value['roots'],value['known_ids'],'0006')

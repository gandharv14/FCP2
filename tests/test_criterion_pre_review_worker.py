"""Real W checkpoint routing under an explicit authority fixture.

Source/author proof semantics are independently checked by the native reader
probe. These fixtures do not claim a live lease or execute a model transport.
"""
from dataclasses import replace
import importlib,sys
from types import SimpleNamespace as NS
import pytest
from scripts.batch import harbor_dialogue_pre_review_repair as REAL_Q
from scripts.batch import harbor_instruction_worker as W,harbor_transition_codec as codec
from scripts.batch.harbor_admission import read
from scripts.fleet import criterion_pre_review_install as Install
from test_dialogue_pre_review_worker_integration import repaired
from test_instruction_lease_renewal import boundary,case,instruction_setup


def test_real_worker_uses_exact_permit_and_changed_claim_selection_only(repaired,monkeypatch):
    worker,selection,lease,old,permit,blocked,tracker=repaired
    from scripts.batch.harbor_admission import record
    # Make the selected permit carry the explicit corrected-card selection.
    claims=W.keep(worker.run/'corrected-claims.json',{'claims':[]})
    selected=replace(selection,content=replace(selection.content,dialogue_claims=claims))
    value=read(permit);value.update(selection=codec.encode(selected))
    from pathlib import Path
    import json
    Path(permit['path']).write_text(json.dumps(value));bound=record(permit['path'])
    worker=replace(worker,dialogue_pre_review_repair=bound)
    monkeypatch.setitem(sys.modules,Install.NAMES['q'],REAL_Q)
    originals={k:importlib.import_module(n)for k,n in Install.NAMES.items()}
    r=NS(authority=bound,plan={'corrected_claims':claims},dependencies=lambda:[],
         is_card=lambda v:getattr(v,'dialogue_pre_review_repair',None)==bound)
    for name in ('validate','validate_task','context','historical_lease','validate_retained_request',
                 'review_projection','verify_pair','validate_review_call'):
        setattr(r,name,lambda *a,**kw:read(bound))
    saved=Path(blocked['path']).read_bytes()
    with Install.installed(originals,r)as runtime:
        with pytest.raises(ValueError,match='preceding checkpoint'):
            runtime['worker'].step(worker,selected,lease,checkpoint=old)
        with pytest.raises(ValueError,match='previous worker checkpoint'):
            runtime['worker'].step(worker,selection,lease,checkpoint=bound)
        result=runtime['worker'].step(worker,selected,lease,checkpoint=bound)
        step=read(result.evidence);intent=read(step['intent'])
        assert step['step']=='dialogue-review' and intent['previous']==bound
        assert step['selection']==codec.encode(selected)
        assert Path(result.evidence['path']).is_relative_to(Path(bound['path']).parent)
    assert Path(blocked['path']).read_bytes()==saved
    assert not (W.step_directory(worker,'dialogue-paraphraser')/'complete.json').exists()

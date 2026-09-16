from pathlib import Path
import json
import pytest
from test_harbor_completed_failure_adapter import context
from test_harbor_ready_transitions import kw
from scripts.batch import harbor_completed_failure_history as H,harbor_lane_notes as N,harbor_completed_lanes as L,harbor_transition_codec as codec,harbor_tracker_lifecycle as life

@pytest.mark.parametrize('fault',[None,'changed_gate','after','notes','completion','journal'])
def test_actual_original_nat_failure_mutation_comparison(tmp_path,monkeypatch,fault):
    f,path,start,selection,nat=context(tmp_path,monkeypatch)
    instruction=selection.content.stage/'instruction.md';original=instruction.read_bytes();instruction.write_text('invalid changed instruction')
    failed=f.record_failure('0245',L.CompletionEvidence('naturalization',selection),**kw(start));transaction=failed['journal'];tx=Path(transaction['path']).parent;completion=N.record(tx/'complete.json');current=path.read_bytes()
    if fault=='changed_gate':instruction.write_bytes(original)
    if fault in ('after','notes','completion'):
        p=tx/({'after':'after.json','notes':'notes.md','completion':'complete.json'}[fault]);p.write_bytes(b'corrupt')
    if fault=='journal':transaction=dict(path=str(tmp_path/'foreign/transaction.json'),sha256=transaction['sha256'])
    if fault:
        with pytest.raises(Exception):H.validate(notes=N,codec=codec,context=f.context,transaction=transaction,completion=completion)
    else:
        result=H.validate(notes=N,codec=codec,context=f.context,transaction=transaction,completion=completion)
        assert result['after']['sha256']==failed['sha256']and result['result']['passed']is False
        assert result['models_dispatched']==result['task_mutations']==0
    assert path.read_bytes()==current

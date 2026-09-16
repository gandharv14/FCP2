from types import SimpleNamespace
from pathlib import Path
import pytest
from scripts.batch import harbor_retained_takeover_reader as R

class Reader:pass

def test_only_original_final_chain_query_selects_declared_later_takeover(tmp_path,monkeypatch):
    old_row={'path':'original','sha256':'old'};new_row={'path':'selected','sha256':'new'};plan={'path':'plan','sha256':'plan'};seen=[]
    old=SimpleNamespace(validate_completion=lambda row:seen.append(('old_validate',row)),completion_records=lambda row:['old',row])
    new=SimpleNamespace(validate_completion=lambda row:seen.append(('new_validate',row)),completion_records=lambda row:['new',row],continuation_chain=lambda *a,**k:seen.append(('chain',a,k)))
    p=dict(reader_plan=plan,reader_module=R.binding(__file__),prior_completion=old_row,completion=new_row)
    monkeypatch.setattr(R,'validate',lambda row:(p,new,None,{}))
    reader=Reader();reader.plan_binding=plan;reader.takeover=old
    worker=SimpleNamespace(declared_namespace={'_source_cards':reader})
    assert R.install_projection(worker,{'bound':'profile'})is worker
    reader.takeover.validate_completion(old_row)
    reader.takeover.continuation_chain('original-budget','original-lease','current-before','current-lease',takeover=old_row,tracker_path='original-tracker')
    assert seen[-1]==('chain',('original-budget','original-lease','current-before','current-lease'),{'takeover':new_row,'tracker_path':'original-tracker'})
    assert seen[:2]==[('old_validate',old_row),('old_validate',old_row)]
    assert not hasattr(reader.takeover,'replace_expired') and not hasattr(reader.takeover,'recover')
    with pytest.raises(ValueError):reader.takeover.continuation_chain(takeover=new_row)
    with pytest.raises(ValueError):reader.takeover.validate_completion({'path':'foreign'})

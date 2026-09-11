import dataclasses
import json
from pathlib import Path
import pytest
from test_harbor_environment_producer import current_case
from test_harbor_normalization_producer import setup
from scripts.batch import harbor_completed_lanes as L
from scripts.batch import harbor_environment_producer as P
from scripts.batch.harbor_admission import read,record


def test_actual_validated_ordinal_survives_phase2(setup,monkeypatch):
    content,profiles,root=current_case(setup,monkeypatch)
    assert L.normalization_projection(content,phase1=True)['semantic_attempt']==1
    after,_=P.finish_normalization(content,profiles,root/'phase2')
    assert L.normalization_projection(after)['semantic_attempt']==1


@pytest.mark.parametrize('field,value',[('semantic_ordinal',None),('semantic_ordinal',0),('semantic_ordinal',True),('semantic_ordinal',2),('semantic_limit',3),('budget_evidence',[]),('scope',{})])
def test_bound_ordinal_cannot_be_removed_inflated_or_reassigned(setup,monkeypatch,field,value):
    content,_,root=current_case(setup,monkeypatch)
    submitted=read(content.normalization_call.submitted);submitted[field]=value
    path=root/'changed-submitted.json';path.write_text(json.dumps(submitted))
    content=dataclasses.replace(content,normalization_call=dataclasses.replace(content.normalization_call,submitted=record(path)))
    with pytest.raises(ValueError):L.normalization_projection(content,phase1=True)

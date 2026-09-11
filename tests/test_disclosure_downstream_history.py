"""Downstream permits cannot omit or substitute the consumed disclosure call."""
from dataclasses import replace
import pytest
from scripts.batch import harbor_instruction_worker as W
from scripts.batch import harbor_nat_retry as N,harbor_dialogue_semantic_retry as D
from test_instruction_worker import case,instruction_setup


@pytest.mark.parametrize('lane',['nat','dialogue'])
@pytest.mark.parametrize('change',['omit','substitute'])
def test_downstream_remaining_round_requires_exact_disclosure_history(case,tmp_path,lane,change):
    worker,*_=case
    disclosure=W.keep(tmp_path/'disclosure.json',{'physical_count':1})
    other=W.keep(tmp_path/'other.json',{'physical_count':1})
    worker=replace(worker,disclosure_repair=disclosure)
    prior=N._prior_continuations(worker) if lane=='nat' else D._continuations(worker)
    if change=='omit':prior.pop('disclosure_repair')
    else:prior['disclosure_repair']=other
    module=N if lane=='nat' else D
    value={'schema_version':module.SCHEMA,'worker_run':str(worker.run),'code':W.code(worker),
           'prior_continuations':prior,'round':2,'consumed_rounds':1,'semantic_limit':2,
           'structural_retry_authorized':False,'semantic_budget_reset':False}
    permit=W.keep(tmp_path/'permit.json',value)
    complete=W.keep(tmp_path/'complete.json',{'schema_version':module.SCHEMA,'permit':permit})
    if lane=='nat':
        worker=replace(worker,retry=complete)
        with pytest.raises(ValueError,match='detached disclosure'):N.validate(worker,{})
    else:
        worker=replace(worker,dialogue_semantic_retry=complete)
        with pytest.raises(ValueError,match='history detached'):D.validate_completed_integrity(worker,{})

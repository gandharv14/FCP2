import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from recursion_opus_max_scores import summarize


def attempts(scores):
    return [{'attemptNumber':i,'status':'completed','finalScore':score} for i,score in enumerate(scores,1)]


def test_pass_requires_one_full_answer_and_all_three_scored():
    assert summarize({'task':'x'},attempts([0.8,1,0.2]))['passAt3']==1
    assert summarize({'task':'x'},attempts([0.8,0.9999,0.2]))['passAt3']==0
    assert summarize({'task':'x'},attempts([1,None,0]))['passAt3'] is None


def test_infrastructure_failure_is_not_a_zero_score():
    runs=attempts([1,0,0]);runs[2]['status']='failed'
    assert summarize({'task':'x'},runs)['state']=='unscored_error'
    assert summarize({'task':'x'},runs)['passAt3'] is None


def test_duplicate_attempts_are_rejected():
    runs=attempts([1,0,0]);runs[2]['attemptNumber']=2
    with pytest.raises(ValueError):summarize({'task':'x'},runs)

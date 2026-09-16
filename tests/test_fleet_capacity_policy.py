import copy
import pytest
from scripts.fleet.capacity_policy import AUTHORIZATION_SHA256, CHANGE_ID, validate_limits, validate_occupancy


def status():
    return dict(model_limit=None, publication_limit=1, operations=[], model_policy=dict(
        schema_version='fleet-concurrency-policy/v1', revision=1, model_limit=None,
        authorization=dict(path='/retained/authorization.json',sha256=AUTHORIZATION_SHA256),
        change_id=CHANGE_ID, per_workbook_model_limit=1,
        unknown_model_hold_barrier='global', publication_limit=1))


def test_truthful_unbounded_policy_and_historical_status():
    assert validate_limits(status()) is None
    assert validate_limits(dict(model_limit=1, publication_limit=1)) == 1


@pytest.mark.parametrize('field,value', [('revision',True),('revision',2),('model_limit',1),
    ('authorization',dict(path='/auth',sha256='a'*64)),('change_id','different'),
    ('per_workbook_model_limit',2),('unknown_model_hold_barrier','none'),('publication_limit',2)])
def test_policy_rejects_unapproved_or_relaxed_safeguards(field,value):
    s=status();s['model_policy'][field]=value
    with pytest.raises(ValueError):validate_limits(s)


def test_parallel_workbooks_held_operations_and_duplicate_prevention():
    s=status();s['operations']=[dict(kind='model',state='active',workbook=str(i)) for i in range(16)]
    assert len(validate_occupancy(s)[0]) == 16
    s['operations'][0]['state']='hold'
    assert len(validate_occupancy(s)[0]) == 16  # Count the hold; caller enforces reconciliation.
    s['operations'].append(copy.deepcopy(s['operations'][0]))
    with pytest.raises(ValueError):validate_occupancy(s)


def test_publication_remains_one_and_no_missing_policy():
    s=status();s['operations']=[dict(kind='publication',state='active')]*2
    with pytest.raises(ValueError):validate_occupancy(s)
    s=status();del s['model_policy']
    with pytest.raises(ValueError):validate_limits(s)

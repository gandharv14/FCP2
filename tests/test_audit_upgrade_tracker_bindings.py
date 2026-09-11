"""Structural record transport only; no simulated financial approval."""
import copy
import pytest
from scripts.batch import harbor_tracker as tracker
from test_harbor_tracker_lifecycle import created,record

KEYS=('audit_validator_upgrade','audit_upgrade_original_code','normalization_audit_applicability','normalization_original_locator_code')


def test_explicit_upgrade_records_preserve_closed_tracker_shape(tmp_path):
    _,result=created(tmp_path);value=copy.deepcopy(result['tracker']);p=tmp_path/'evidence.json';p.write_text('{}')
    for key in KEYS:value['bindings'][key]=record(p)
    tracker.validate_tracker(value)
    assert value['execution_state']=='queued' and value['terminal_verdict'] is None


@pytest.mark.parametrize('change',['unknown','relative','bad_hash','extra_field'])
def test_new_records_do_not_open_arbitrary_bindings(tmp_path,change):
    _,result=created(tmp_path);value=copy.deepcopy(result['tracker']);p=tmp_path/'evidence.json';p.write_text('{}');bound=record(p)
    key=KEYS[0]
    if change=='unknown':key='audit_approve_anything'
    elif change=='relative':bound['path']='relative.json'
    elif change=='bad_hash':bound['sha256']='not-a-hash'
    else:bound['passed']=True
    value['bindings'][key]=bound
    with pytest.raises(tracker.InvalidTracker):tracker.validate_tracker(value)

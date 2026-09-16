"""A preserved model hold may not authorize replay or obstruct another publication."""
import copy
import time
from types import SimpleNamespace
import pytest
from scripts.fleet import publication_launch as L, publication_finalization_launch as F

@pytest.mark.parametrize('module',[L,F])
@pytest.mark.parametrize('bad',[None,'target','publication','owner','fence','not-held','missing-owner'])
def test_publication_does_not_clear_or_reuse_unrelated_held_model(module,bad):
    p=dict(expected_owner='publisher-before',expected_fence=3,prior_reason='retained',worker_outcome={'path':'/out','sha256':'a'})
    plan=dict(workbook='0303',source_sha256='s',code_root='/code/version')
    import json
    reason='retained' if module is F else json.dumps(p['worker_outcome'],sort_keys=True)
    own=dict(workbook='0303',owner=p['expected_owner'],fence=3,state='hold',source_sha256='s',version='version',reason=reason)
    other=dict(workbook='0413',owner='old-worker',fence=1,state='hold')
    op=dict(workbook='0413',owner='old-worker',fence=1,state='hold',kind='model')
    status=dict(at=time.time(),workbooks=[own,other],operations=[op]);original=copy.deepcopy(status)
    if bad=='target':op['workbook']='0303'
    elif bad=='publication':op['kind']='publication'
    elif bad=='owner':op['owner']='unknown'
    elif bad=='fence':op['fence']=2
    elif bad=='not-held':other['state']='active'
    elif bad=='missing-owner':status['workbooks'].pop()
    cap=SimpleNamespace(validate_occupancy=lambda s:None)
    if bad:
        with pytest.raises(ValueError):module.central(status,p,plan,cap)
    else:
        assert module.central(status,p,plan,cap)==own
        assert status==original

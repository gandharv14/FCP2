import copy
import pytest
from test_inventory_approval import _inventory
from xl_inventory_approval import approval_claims, object_hash
from scripts.fleet.carry_forward_approval import extend


def registry(approvals):
    core={'schema_version':'source-inventory-approval-registry/v1','approvals':approvals}
    return {**core,'registry_sha256':object_hash(core)}


def test_exact_historical_entry_carries_forward_without_changing_prior(tmp_path):
    inventory=_inventory(tmp_path)
    prior=approval_claims(inventory,batch_id='prior');historical=approval_claims(inventory,batch_id='historical')
    current=registry([prior]);old=registry([prior,historical]);core={'schema_version':'source-inventory-approval/v1','approval':historical}
    document={**core,'inventory_approval_sha256':object_hash(core)}
    result,added=extend(current,old,[(inventory,document)])
    assert result==old and added==['historical'] and current==registry([prior])
    assert extend(result,old,[(inventory,document)])==(result,[])
    changed=copy.deepcopy(document);changed['approval']['policy_version']='changed'
    changed['inventory_approval_sha256']=object_hash({k:v for k,v in changed.items() if k!='inventory_approval_sha256'})
    with pytest.raises(ValueError,match='absent from pinned'):
        extend(current,old,[(inventory,changed)])
    wrong=copy.deepcopy(document);wrong['inventory_approval_sha256']='0'*64
    with pytest.raises(ValueError,match='envelope changed'):
        extend(current,old,[(inventory,wrong)])

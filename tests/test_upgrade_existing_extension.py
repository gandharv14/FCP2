import copy
import pytest
from scripts.fleet import coordinator_upgrade as U
from tests.test_physical_capacity_extension import fixture


def test_code_upgrade_preserves_actual_existing_extension(tmp_path):
    c,_,_,args=fixture(tmp_path)
    c.permit_physical_capacity_extension(**args)
    before=c.status()
    assert before['physical_capacity_extensions']
    U.preserved_policy(before, c.status())


@pytest.mark.parametrize('field', ['physical_capacity_extensions','quarantines','quarantine_host_capacity','author_reservations','operations','workbooks','author_capacity_policy'])
def test_code_upgrade_rejects_changed_existing_authority(tmp_path, field):
    c,_,_,args=fixture(tmp_path)
    c.permit_physical_capacity_extension(**args)
    before=c.status();after=copy.deepcopy(before)
    after[field]={'changed':True} if isinstance(before[field],dict) else before[field]+[{'changed':True}]
    with pytest.raises(ValueError,match='retained '+field):U.preserved_policy(before,after)

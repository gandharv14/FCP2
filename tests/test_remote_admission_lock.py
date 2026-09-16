import fcntl,json,os
from pathlib import Path

import pytest

from scripts.fleet import admission_lock as L,launch_job_v3 as J,source_pool_control as S
from scripts.fleet import remote_coordinator_preparation as R
from tests.test_remote_authority_control import selection


def test_completed_fence_reuses_original_mutex_without_recreating_database(tmp_path):
    original=tmp_path/'job-launch.lock'
    first=os.open(original,os.O_RDWR|os.O_CREAT,0o600)
    try:
        fcntl.flock(first,fcntl.LOCK_EX|fcntl.LOCK_NB)
        receipt=selection(tmp_path)
        assert Path(receipt['database']).is_dir()
        assert L.path(receipt)==original
        second=os.open(L.path(receipt),os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):fcntl.flock(second,fcntl.LOCK_EX|fcntl.LOCK_NB)
        finally:os.close(second)
        assert Path(receipt['database']).is_dir()
        assert R.ledger(tmp_path/'fleet.sqlite3.retired-001')['held_operations']
    finally:os.close(first)


@pytest.mark.parametrize('controller,key',[(S,'coordinator'),(J,'coordinator_receipt')])
def test_original_controller_selects_authenticated_migration_lock(tmp_path,controller,key):
    receipt=selection(tmp_path)
    helper=R.binding(Path(L.__file__).resolve())
    plan={key:dict(path=str(tmp_path/'relocated/receipt.json'),sha256='a'*64),
          'admission_lock':helper}
    assert controller.admission_lock_path(plan,receipt)==tmp_path/'job-launch.lock'
    receipt['private_directory']=str(tmp_path/'foreign-token-directory')
    with pytest.raises(ValueError):controller.admission_lock_path(plan,receipt)


@pytest.mark.parametrize('remote',[None,{},dict(schema_version='invented')])
def test_declared_remote_authority_never_falls_back_to_existing_local_database(tmp_path,remote):
    database=tmp_path/'local.sqlite3';database.touch()
    with pytest.raises((ValueError,KeyError)):
        L.path(dict(database=str(database),remote_authority=remote))
    assert database.is_file()and not(tmp_path/'job-launch.lock').exists()


def test_remote_claim_without_completed_fence_cannot_open_mutex(tmp_path):
    receipt=selection(tmp_path)
    complete=Path(receipt['database'])/'complete.json';complete.unlink()
    with pytest.raises((ValueError,FileNotFoundError)):
        L.path(receipt)
    assert Path(receipt['database']).is_dir()and not(tmp_path/'job-launch.lock').exists()


def test_foreign_migration_ledger_rejected_even_with_rebound_receipt(tmp_path):
    receipt=selection(tmp_path);path=Path(receipt['remote_authority']['migration']['path'])
    value=json.loads(path.read_text());value['ledger']['held_operations']=[];path.write_text(json.dumps(value))
    receipt['remote_authority']['migration']=R.binding(path)
    with pytest.raises(ValueError):L.path(receipt)
    assert not(tmp_path/'job-launch.lock').exists()

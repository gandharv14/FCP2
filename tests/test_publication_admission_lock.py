import fcntl,os
from pathlib import Path
import pytest
from scripts.fleet import admission_lock as L,publication_launch as P,source_pool_control as S


def test_publication_and_authoring_contend_despite_different_receipt_locations(tmp_path):
    database=tmp_path/'fleet.sqlite3';database.touch();receipt=dict(database=str(database))
    helper=P.binding(Path(L.__file__).resolve())
    pub=dict(coordinator_receipt=dict(path=str(tmp_path/'nested/pub.json')),admission_lock=helper)
    author=dict(coordinator=dict(path=str(tmp_path/'other/author.json')),admission_lock=helper)
    a=P.admission_lock_path(pub,receipt);b=S.admission_lock_path(author,receipt)
    first=os.open(a,os.O_RDWR|os.O_CREAT,0o600);second=os.open(b,os.O_RDWR)
    try:
        fcntl.flock(first,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):fcntl.flock(second,fcntl.LOCK_EX|fcntl.LOCK_NB)
    finally:os.close(first);os.close(second)


def test_changed_publication_lock_helper_rejected(tmp_path):
    helper=P.binding(Path(L.__file__).resolve());helper['sha256']='0'*64
    with pytest.raises(ValueError,match='selected module changed'):
        P.admission_lock_path(dict(admission_lock=helper),{})


def test_unmodified_historical_publication_profile_keeps_original_mutex(tmp_path):
    assert P.admission_lock_path(dict(coordinator_receipt=dict(path=str(tmp_path/'old/receipt.json'))),{})==tmp_path/'old/job-launch.lock'

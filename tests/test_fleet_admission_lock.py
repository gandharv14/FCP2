from pathlib import Path
import fcntl,os
import pytest
from scripts.fleet import admission_lock as L,source_pool_control as S,launch_job_v3 as J


def test_relocated_receipts_for_same_authority_contend_on_one_actual_mutex(tmp_path):
    database=tmp_path/'authority/fleet.sqlite3';database.parent.mkdir();database.touch()
    receipts=[dict(database=str(database),receipt=str(tmp_path/n/'receipt.json'))for n in ['old','nested/new']]
    assert L.path(receipts[0])==L.path(receipts[1])
    first=os.open(L.path(receipts[0]),os.O_RDWR|os.O_CREAT,0o600);second=os.open(L.path(receipts[1]),os.O_RDWR)
    try:
        fcntl.flock(first,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):fcntl.flock(second,fcntl.LOCK_EX|fcntl.LOCK_NB)
    finally:os.close(first);os.close(second)


@pytest.mark.parametrize('kind',['missing','relative','traversal','symlink','directory'])
def test_unknown_or_aliased_authority_rejected(tmp_path,kind):
    db=tmp_path/'db';db.touch();value=str(db)
    if kind=='missing':value=str(tmp_path/'absent')
    if kind=='relative':value='db'
    if kind=='traversal':value=str(tmp_path/'../db')
    if kind=='symlink':link=tmp_path/'alias';link.symlink_to(db);value=str(link)
    if kind=='directory':value=str(tmp_path)
    with pytest.raises(ValueError):L.path(dict(database=value))


@pytest.mark.parametrize('controller,key',[(S,'coordinator'),(J,'coordinator_receipt')])
def test_both_controller_profiles_select_same_bound_lock(tmp_path,controller,key):
    db=tmp_path/'authority.sqlite3';db.touch();receipt=dict(database=str(db));row=S.binding(Path(L.__file__).resolve())
    p={key:dict(path=str(tmp_path/'nested/receipt.json'),sha256='a'*64), 'admission_lock':row}
    assert controller.admission_lock_path(p,receipt)==L.path(receipt)
    p['admission_lock']=dict(row,sha256='f'*64)
    with pytest.raises(ValueError):controller.admission_lock_path(p,receipt)

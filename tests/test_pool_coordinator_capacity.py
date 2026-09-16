from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.fleet import pool_v5 as P
G=1024**3

def fixture(tmp_path,monkeypatch):
    monkeypatch.setattr(P,'admission_module',lambda row:SimpleNamespace(validate_occupancy=lambda s:None,model_admission_barriers=lambda *a:[]))
    monkeypatch.setattr(P,'native_census',lambda h:{})
    monkeypatch.setattr(P,'resources',lambda p:dict(memory_available_bytes=100*G,memory_total_bytes=124*G,disk_free_bytes=100*G,load_average=[0,0,0],cpus=16))
    jobs=[({'path':str(tmp_path/(w+'.json')),'sha256':'a'*64},dict(workbook=w,source={'sha256':'b'*64},version='v',run=str(tmp_path/w),cwd=str(tmp_path),driver={'path':'/code/source_first.py'},memory_limit_bytes=16*G,minimum_disk_bytes=12*G))for w in ('0289','0292')]
    status=dict(author_capacity_policy={'authors_per_host':2},author_reservations=[dict(host='hhu-server-8',workbook='0028',state='hold')],operations=[],workbooks=[dict(workbook=j['workbook'],state='ready',source_sha256='b'*64,version='v')for _,j in jobs])
    policy=dict(max_total_workers=4,max_source_workers=2,max_author_workers=2,headroom_bytes=4*G)
    return jobs,status,policy

def test_held_reservation_and_pending_source_prevent_second_claim(tmp_path,monkeypatch):
    jobs,status,policy=fixture(tmp_path,monkeypatch)
    first=P.reserve(tmp_path,jobs,status,0,'hhu-server-8',policy,{})
    assert first[1]['workbook']=='0289'
    assert P.reserve(tmp_path,jobs,status,1,'hhu-server-8',policy,{})is None
    assert not(tmp_path/'dispatches/0292.json').exists()
    # Once the first worker returned and the coordinator released that claim,
    # the next source can start; the unrelated uncertain slot stays occupied.
    done=tmp_path/'0289/outcome.json';done.parent.mkdir();done.write_text('{}')
    status['workbooks'][0]['state']='hold'
    assert P.reserve(tmp_path,jobs,status,1,'hhu-server-8',policy,{})[1]['workbook']=='0292'

def test_active_reservation_counted_once_with_native_pending():
    s=dict(author_capacity_policy={'authors_per_host':2},author_reservations=[dict(host='hhu-server-7',workbook='0496',state='active')])
    assert P.coordinator_room(s,'hhu-server-7',{'0496'})
    assert not P.coordinator_room(s,'hhu-server-7',{'0496','0293'})

@pytest.mark.parametrize('fault',['missing','state','limit'])
def test_incomplete_coordinator_capacity_fails_closed(fault):
    s=dict(author_capacity_policy={'authors_per_host':2},author_reservations=[])
    if fault=='missing':del s['author_reservations']
    elif fault=='state':s['author_reservations']=[dict(state='unknown')]
    else:s['author_capacity_policy']['authors_per_host']=True
    with pytest.raises(ValueError):P.coordinator_room(s,'hhu-server-7',set())

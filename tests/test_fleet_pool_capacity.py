from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pytest
from scripts.fleet import pool_v4 as P
G=1024**3
POLICY=dict(max_total_workers=4,max_author_workers=2,max_source_workers=2,headroom_bytes=4*G)
def setup(tmp_path,monkeypatch,phases):
    census={str(i):dict(phase=p,rss_bytes=G)for i,p in enumerate(phases)}
    monkeypatch.setattr(P,'native_census',lambda host:census)
    monkeypatch.setattr(P,'job_phase',lambda job:job.get('phase','author'))
    monkeypatch.setattr(P,'resources',lambda _:dict(memory_available_bytes=100*G,memory_total_bytes=124*G,disk_free_bytes=100*G,load_average=[0,0,0],cpus=16))
    jobs=[(dict(path=str(tmp_path/(wb+'.json')),sha256='a'*64),dict(workbook=wb,source=dict(sha256='b'*64),version='c'*64,run=str(tmp_path/wb),cwd=str(tmp_path),memory_limit_bytes=16*G,minimum_disk_bytes=12*G))for wb in ['1001','1002','1003']]
    status=dict(operations=[],workbooks=[dict(workbook=j['workbook'],state='ready',version=j['version'],source_sha256=j['source']['sha256'])for _,j in jobs])
    return jobs,status

def test_two_sources_allow_two_authors_and_close_concurrent_race(tmp_path,monkeypatch):
    jobs,status=setup(tmp_path,monkeypatch,['source','source'])
    with ThreadPoolExecutor(max_workers=3)as ex:values=list(ex.map(lambda i:P.reserve(tmp_path,jobs,status,i,'hhu-server',POLICY),range(3)))
    assert sum(v is not None for v in values)==2

@pytest.mark.parametrize('phases',[['author','author'],['source','source','author','author']])
def test_author_and_total_cap(tmp_path,monkeypatch,phases):
    jobs,status=setup(tmp_path,monkeypatch,phases)
    assert P.reserve(tmp_path,jobs,status,0,'hhu-server',POLICY) is None

def test_third_source_never_admitted(tmp_path,monkeypatch):
    jobs,status=setup(tmp_path,monkeypatch,['source','source'])
    for _,j in jobs:j['phase']='source'
    assert P.reserve(tmp_path,jobs,status,0,'hhu-server',POLICY)is None

def test_full_source_phase_does_not_starve_ready_author(tmp_path,monkeypatch):
    jobs,status=setup(tmp_path,monkeypatch,['source','source'])
    jobs[0][1]['phase']='source'
    selected=P.reserve(tmp_path,jobs,status,0,'hhu-server',POLICY)
    assert selected[1]['workbook']=='1002'
    assert not (tmp_path/'dispatches/1001.json').exists()

@pytest.mark.parametrize('kind',['rss','available','hold','unknown'])
def test_resource_uncertainty_barriers(tmp_path,monkeypatch,kind):
    jobs,status=setup(tmp_path,monkeypatch,['source'])
    if kind=='rss':monkeypatch.setattr(P,'native_census',lambda h:dict(a=dict(phase='source',rss_bytes=110*G)))
    elif kind=='available':monkeypatch.setattr(P,'resources',lambda _:dict(memory_available_bytes=19*G,memory_total_bytes=124*G,disk_free_bytes=100*G,load_average=[0,0,0],cpus=16))
    elif kind=='hold':status['operations']=[dict(state='hold')]
    else:
        status['workbooks'].append(dict(workbook='0190',state='active',owner='unknown'))
        with pytest.raises(ValueError):P.reserve(tmp_path,jobs,status,0,'hhu-server',POLICY)
        return
    assert P.reserve(tmp_path,jobs,status,0,'hhu-server',POLICY)is None

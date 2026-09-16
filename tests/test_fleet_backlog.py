from pathlib import Path
import json,sys,time
import pytest
from scripts.fleet import backlog as B,pool_v5 as P,worker as W


def save(path,value):return B.persist(path,value,immutable=True)


def setup(tmp_path):
    root=Path(W.__file__).resolve().parents[2];worker=B.binding(Path(W.__file__).resolve())
    auth=B.binding(root/'diagnostics/bulk-production/authorization-20260913.md')
    p=dict(schema_version=B.SCHEMA,host='hhu-server-2',code_root=str(root),worker=worker,
           directory=str(tmp_path/'backlog'),authorization=auth,maximum_jobs=100,
           expires_at=time.time()+3600,helper=B.binding(Path(B.__file__).resolve()))
    row=save(tmp_path/'policy.json',p)
    def job(i):
        source=tmp_path/f'{i}.xlsx';source.write_bytes(b'original')
        value=dict(schema_version=W.SCHEMA,workbook=f'{i:04d}',source=B.binding(source),version='a'*64,
                   driver=worker,inputs=[],command=[sys.executable,'-I','-B',worker['path']],cwd=str(root),
                   run=str(tmp_path/f'worker-{i}'),result=str(tmp_path/f'result-{i}.json'),
                   memory_limit_bytes=8*1024**3,timeout_seconds=7200,minimum_disk_bytes=12*1024**3)
        return save(tmp_path/f'job-{i}.json',value)
    return row,p,job


def test_running_supervisor_refreshes_new_validated_jobs_without_changing_prior(tmp_path):
    row,p,job=setup(tmp_path);first,second=job(1),job(2)
    batch=save(tmp_path/'batch-1.json',[first]);B.append(row,batch,expected=None)
    selected,head=B.load(row)
    cfg={**p,'backlog':row,'jobs':[]};oldbytes=Path(first['path']).read_bytes()
    assert selected==[first]
    B.append(row,save(tmp_path/'batch-2.json',[second]),expected=head)
    loaded,new=P.refresh_jobs(cfg,[],head)
    assert [x for x,_ in loaded]==[first,second]and new['sequence']==2
    assert Path(first['path']).read_bytes()==oldbytes
    assert not Path(B.read(first)['run']).exists() # Replenishment cannot dispatch.


def test_duplicate_or_stale_append_cannot_replay_or_consume_an_entry(tmp_path):
    row,p,job=setup(tmp_path);one=job(1);batch=save(tmp_path/'batch.json',[one])
    B.append(row,batch,expected=None);_,head=B.load(row)
    with pytest.raises(ValueError,match='changed'):B.append(row,batch,expected=None)
    with pytest.raises(ValueError,match='repeats'):B.append(row,batch,expected=head)
    assert len(list((Path(p['directory'])/'entries').iterdir()))==1


def test_uncommitted_entry_requires_reconciliation_without_advancing_head(tmp_path):
    row,p,job=setup(tmp_path);batch=save(tmp_path/'batch.json',[job(1)])
    entry=Path(p['directory'])/'entries/00000001.json';save(entry,dict(uncommitted=True))
    with pytest.raises(ValueError,match='uncommitted'):B.append(row,batch,expected=None)
    assert not(Path(p['directory'])/'head.json').exists()


@pytest.mark.parametrize('kind',['job','chain','rollback','foreign','resource'])
def test_changed_jobs_or_authority_never_become_admissible(tmp_path,kind):
    row,p,job=setup(tmp_path);one,two=job(1),job(2)
    B.append(row,save(tmp_path/'batch-1.json',[one]),expected=None);_,head=B.load(row)
    B.append(row,save(tmp_path/'batch-2.json',[two]),expected=head);_,latest=B.load(row)
    if kind=='job':Path(one['path']).write_text('{}')
    elif kind=='chain':Path(head['entry']['path']).write_text('{}')
    elif kind=='rollback':B.persist(Path(p['directory'])/'head.json',head,immutable=False)
    elif kind in {'foreign','resource'}:
        wrong=B.read(two)
        wrong['workbook']='0003'
        if kind=='foreign':wrong['cwd']=str(tmp_path)
        else:wrong['memory_limit_bytes']=1024
        wrong=save(tmp_path/'wrong.json',wrong);batch=save(tmp_path/'wrong-batch.json',[wrong])
        with pytest.raises(ValueError):B.append(row,batch,expected=latest)
        return
    with pytest.raises(ValueError):P.refresh_jobs({**p,'backlog':row},[],latest)


def test_legacy_pool_without_backlog_keeps_exact_selection():
    rows=[({'path':'/old','sha256':'a'*64},{'workbook':'0001'})]
    assert P.refresh_jobs({},rows)==(rows,None)


def test_durable_cursor_rejects_head_rollback_after_supervisor_restart(tmp_path):
    row,p,job=setup(tmp_path)
    B.append(row,save(tmp_path/'one.json',[job(1)]),expected=None);_,old=B.load(row)
    B.append(row,save(tmp_path/'two.json',[job(2)]),expected=old)
    cfg={**p,'backlog':row};P.refresh_jobs(cfg,[])
    B.persist(Path(p['directory'])/'head.json',old,immutable=False)
    with pytest.raises(ValueError,match='rolled back'):P.refresh_jobs(cfg,[])


@pytest.mark.parametrize('expires',[float('inf'),float('-inf'),float('nan')])
def test_policy_refuses_nonfinite_expiry(tmp_path,expires):
    row,p,_=setup(tmp_path);p['expires_at']=expires
    path=tmp_path/'nonfinite.json';path.write_text(json.dumps(p))
    with pytest.raises(ValueError,match='nonfinite'):B.policy(B.binding(path))

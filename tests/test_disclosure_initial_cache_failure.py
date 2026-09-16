from pathlib import Path
import pytest
from test_disclosure_interrupted_reservation import case,keep
from scripts.fleet import disclosure_reserved_round as S
from scripts.fleet import disclosure_review_continue as C
from scripts.batch.harbor_admission import read,record

@pytest.fixture
def cached(case):
    proof,p,w,l,Q,calls,h,wd,review=case
    v=read(proof);old=read(v['plan']);root=h.parent/'code';root.mkdir();source=root/'original.py';source.write_text('original')
    old['code_snapshots']=[keep(h.parent/'deployment.json',dict(root=str(root),fingerprint='code',bindings={'original.py':record(source)},parent={}))]
    keep(Path(v['plan']['path']),old);v['plan']=record(v['plan']['path'])
    jobpath=Path(read(read(v['worker_outcome'])['intent'])['job']['path']);job=read(record(jobpath));job['command'][-1]=v['plan']['sha256'];jobrow=keep(jobpath,job)
    intentpath=wd/'intent.json';intent=read(record(intentpath));intent['job']=jobrow;intentrow=keep(intentpath,intent)
    (wd/'worker.log').write_text("cache=m['cache'].ValidationCache(plan)\nscripts.batch.harbor_admission.AdmissionError: closed complete code snapshot required\n")
    outer=read(v['worker_outcome']);outer.update(returncode=1,failure_type=None,intent=intentrow,log=record(wd/'worker.log'));v['worker_outcome']=keep(wd/'outcome.json',outer);(wd/'finish-uncertain.json').unlink()
    process=read(v['process']);process['job']=jobrow;process['command'][process['command'].index('--job-sha256')+1]=jobrow['sha256'];v['process']=keep(Path(v['process']['path']),process)
    retained=read(v['retained_lease']);retained['authority']=v['plan'];v['retained_lease']=keep(Path(v['retained_lease']['path']),retained)
    keep(h/'intent.json',dict(plan=v['plan'],automatic_retry=False));v['schema_version']='fleet-disclosure-initial-cache-failure/v1';proof=keep(Path(proof['path']),v)
    p['reserved_round']=proof;p['code_snapshots']=[keep(h.parent/'native.json',dict(root=str(root),files=[dict(relative='original.py',sha256=record(source)['sha256'])]))]
    return proof,p,w,l,Q,calls,h,wd,review,root

def test_sealed_initial_cache_failure_reuses_same_consumed_reservation(cached):
    proof,p,w,l,Q,calls,h,wd,review,root=cached
    before={x:x.read_bytes() for x in h.parent.rglob('*')if x.is_file()}
    assert S.validate(proof,p,w,None,l,Q)==(Path(review['path']),read(review))
    assert calls==['native process absence','original authority','original executor history']
    assert all(x.read_bytes()==value for x,value in before.items())

@pytest.mark.parametrize('fault',['model_effect','uncertain','code_changed','code_added','budget','review'])
def test_cache_failure_does_not_waive_effects_or_authority(cached,fault):
    proof,p,w,l,Q,calls,h,wd,review,root=cached
    if fault=='model_effect':keep(h/'batches/0000/request.json',{})
    if fault=='uncertain':keep(wd/'finish-uncertain.json',{})
    if fault=='code_changed':(root/'original.py').write_text('changed')
    if fault=='code_added':(root/'extra.py').write_text('extra')
    if fault=='budget':p['limit']+=1
    if fault=='review':Q.validate=lambda *a: (_ for _ in ()).throw(ValueError('original rejection'))
    with pytest.raises(ValueError):S.validate(proof,p,w,None,l,Q)

def test_inventory_contract_checked_before_run_writes(cached):
    proof,p,*_=cached
    from scripts.batch import harbor_disclosure_partition_cache as cache
    C.validate_code_snapshots(p,cache)
    old=read(read(proof)['plan']);p['code_snapshots']=old['code_snapshots']
    with pytest.raises(ValueError,match='closed complete'):C.validate_code_snapshots(p,cache)


def test_owning_run_rejects_bad_inventory_before_custody_or_output(cached,monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from scripts.batch import harbor_disclosure_partition_cache as cache
    proof,p,*_=cached;p['code_snapshots']=read(read(proof)['plan'])['code_snapshots']
    def forbidden(*args):pytest.fail('owning custody or write reached with invalid inventory')
    @contextmanager
    def context(row):
        yield p,SimpleNamespace(custody=forbidden),(),None,None,None,None,{'cache':cache}
    monkeypatch.setattr(C,'context',context)
    with pytest.raises(ValueError,match='closed complete'):C.run({})
    assert not Path(p['output']).exists()

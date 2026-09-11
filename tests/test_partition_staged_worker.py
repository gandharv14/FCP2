"""Exercise the actual staged worker with a new partition completion lease."""
from pathlib import Path
from dataclasses import replace,asdict
from types import SimpleNamespace as NS
import importlib.util,sys,json
import pytest
from test_harbor_instruction_producer import setup as instruction_setup
ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture
def routed(instruction_setup,monkeypatch):
    import scripts.batch as package
    import test_instruction_worker as original
    path=ROOT/'diagnostics/requested-fixes-20260910/0184-partition-reviewed-overlay-v2/scripts/batch/harbor_instruction_worker.py'
    spec=importlib.util.spec_from_file_location('scripts.batch.harbor_instruction_worker',path);w=importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules,spec.name,w);monkeypatch.setattr(package,'harbor_instruction_worker',w);spec.loader.exec_module(w)
    monkeypatch.setattr(original,'w',w)
    worker,selected,lease,*_=original.case.__wrapped__(instruction_setup,monkeypatch)
    local=NS(validate=lambda *a:None,directory=lambda *a:None)
    monkeypatch.setitem(sys.modules,'scripts.batch.harbor_instruction_local_command',local)
    monkeypatch.setattr(package,'harbor_instruction_local_command',local,raising=False)
    partition=NS(exists=lambda *a:False)
    monkeypatch.setitem(sys.modules,'scripts.batch.harbor_disclosure_partition_continuation',partition)
    monkeypatch.setattr(package,'harbor_disclosure_partition_continuation',partition,raising=False)
    seen=[]
    def operation(worker,s,l,phase,directory,**options):
        seen.append((phase,l));return s,l,None
    monkeypatch.setattr(w,'_operation',operation)
    first=w.step(worker,selected,lease,phase='disclosure-build')
    renewed=replace(lease,revision=lease.revision+1,sha256='f'*64)
    path=worker.run/'partition-complete.json'
    completion=dict(schema_version='harbor-disclosure-partition-continuation/v1',previous=first.evidence,
        selection=w.codec.encode(selected),lease=asdict(renewed))
    path.write_text(json.dumps(completion));bound=w.record(path)
    partition.exists=lambda *a:True;partition.validate=lambda *a:{'current':'partition'};partition.completion=lambda *a:(bound,completion)
    local.validate=lambda *a:{'retained':'local command'}
    return w,worker,selected,lease,renewed,first,bound,seen


def test_new_completion_replaces_original_build_lease_at_review(routed):
    w,worker,s,old,new,first,bound,seen=routed
    result=w.step(worker,s,new,phase='disclosure-review',checkpoint=bound,models=False)
    assert result.status=='STEP_COMPLETE' and seen==[('disclosure-build',old),('disclosure-review',new)]
    saved=w.read(result.evidence);assert w.read(saved['intent'])['previous']==bound

@pytest.mark.parametrize('fault',['old-checkpoint','old-lease'])
def test_old_build_boundary_cannot_replace_partition_completion(routed,fault):
    w,worker,s,old,new,first,bound,seen=routed
    with pytest.raises(ValueError):w.step(worker,s,old if fault=='old-lease' else new,
        phase='disclosure-review',checkpoint=first.evidence if fault=='old-checkpoint' else bound)
    assert seen==[('disclosure-build',old)]


def test_staged_coordinator_accepts_only_bound_partition_checkpoint(routed,monkeypatch):
    w,worker,s,old,new,first,bound,seen=routed
    import scripts.batch.harbor_disclosure_partition_continuation as P
    path=ROOT/'diagnostics/requested-fixes-20260910/0184-partition-reviewed-overlay-v2/scripts/harbor_instruction_campaign.py'
    spec=importlib.util.spec_from_file_location('staged_partition_coordinator',path);C=importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules,spec.name,C);spec.loader.exec_module(C)
    P.validate=lambda *a:{'selection':C.codec.encode(s)}
    descriptor=C.base.keep(worker.run/'descriptor.json',{'worker':C.worker_encode(worker)})
    result=C.base.keep(worker.run/'ready.json',C._result('RETRY_READY',descriptor,s,new,checkpoint=bound,next_step='disclosure-review',evidence=[bound]))
    _,selection,lease=C._prior(result,descriptor)
    assert selection==s and lease==new
    P.completion=lambda *a:({'path':'/foreign','sha256':'0'*64},{})
    with pytest.raises(ValueError,match='detached'):C._prior(result,descriptor)

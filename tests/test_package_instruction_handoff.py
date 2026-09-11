from pathlib import Path
from dataclasses import replace
import json
import pytest
from scripts import harbor_package_instruction_handoff as H,harbor_package_recovery as R,harbor_parallel_policy as policy
from scripts.batch.harbor_admission import read,record
from test_harbor_package_recovery import scenario,completed,direct,setup,selected,mock_review
from test_harbor_environment_worker import worker as original_worker

@pytest.fixture
def worker(selected,monkeypatch):
    s,lease=original_worker.__wrapped__(selected,monkeypatch);c=s.bootstrap.policy.tracker_root.parent
    campaign=R.E.Campaign(c/'model-journal',c/'model-slots',1)
    monkeypatch.setenv('HARBOR_MODEL_SLOTS','1');monkeypatch.setenv('HARBOR_MODEL_SLOT_ROOT',str(campaign.slot_root))
    return replace(s,campaign=campaign),lease

@pytest.fixture
def packaged(scenario,monkeypatch):
    c=scenario;p=R.prepare(c.descriptor,c.checkpoint,c.outcome,c.lease,c.home/'recovery');result=R.execute(p,dispatch_commands=True)
    assert result['status']=='ENVIRONMENT_READY';return c,p,result


def test_actual_completed_package_can_start_exact_installed_instruction_worker(packaged,monkeypatch):
    c,p,result=packaged;root=c.s.bootstrap.policy.tracker_root.parent
    monkeypatch.setattr(policy,'WORKBOOKS',('0001','0042','0076','0184'))
    for n in ('parallel-policy','parallel-workbook-locks','model-journal','model-slots'):(root/n).mkdir(exist_ok=True)
    (root/'actual-workbook-slot.lock').touch();(root/'model-slots/capacity').write_bytes(b'1\n')
    contract=policy.put(root/'parallel-policy/selected.json',policy.proposal(root));activation=policy.activate(contract);parallel=dict(contract=contract,activation=activation)
    tracker=c.s.bootstrap.policy.tracker_root/'0001.json';before=tracker.read_bytes()
    with (root/'parallel-workbook-locks/0001.lock').open('r+b') as unheld:
        with pytest.raises(ValueError,match='not held'):H.start(p,c.home/'instruction-worker',c.home/'instruction-ready',parallel=parallel,lane_fd=unheld.fileno())
    with policy.lane_lock(contract,activation,'0001') as fd:
        bound=H.start(p,c.home/'instruction-worker',c.home/'instruction-ready',parallel=parallel,lane_fd=fd)
    value=read(bound);assert value['status']=='INSTRUCTION_READY' and value['selection']==result['selection'] and value['lease']==result['lease']
    w,controls,recipe=H.I.selected(value['descriptor']);assert controls.slot_lock==root/'parallel-workbook-locks/0001.lock'
    assert H.I._prior(bound,value['descriptor'])[2]==H.I.base.decode(result['lease'])
    assert tracker.read_bytes()==before and not w.run.exists()


@pytest.mark.parametrize('change',['stage','gate_intent','result','continuation','tracker'])
def test_changed_completed_artifact_or_cas_cannot_become_instruction_handoff(packaged,change):
    c,p,result=packaged;directory=Path(p['path']).parent
    targets={'stage':c.current.content.stage/'instruction.md','gate_intent':directory/'gate-intent.json','result':directory/'result.json','continuation':Path(read(p)['refresh']['path']).parent/'continuation/environment.json','tracker':c.s.bootstrap.policy.tracker_root/'0001.json'}
    path=targets[change]
    if change=='tracker':
        v=json.loads(path.read_text());v['scheduler']['attempts']+=1;path.write_bytes(R.W.T.canonical(v) if hasattr(R.W,'T') else (json.dumps(v,sort_keys=True,indent=2)+'\n').encode())
    else:path.write_bytes(path.read_bytes()+b'changed')
    with pytest.raises((ValueError,KeyError,SystemExit)):H.completed(p)

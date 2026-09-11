"""The metadata write continuation cannot alter the evaluator or qualification."""
import json,shutil
import pytest
from scripts.batch import harbor_grouped_plain_qualification as G
from scripts.batch.harbor_admission import record

@pytest.fixture
def local_profile(tmp_path,monkeypatch):
    prior=tmp_path/'prior';prior.mkdir()
    baseline=tmp_path/'restart';baseline.mkdir()
    entries={G.SELF:'old gate',G.LOCAL_RESTART:'old restart',G.COORDINATOR:'same coordinator',
        G.TAKEOVER:'same lease authority','plain_eligibility.py':'same eligibility',
        'grader/finance_grader/core.py':'same evaluator','scripts/batch/harbor_instruction_worker.py':'old worker',
        '.cursor/skills/task-disclosure/scripts/disclose.py':'old renderer'}
    for name,content in entries.items():
        path=baseline/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content)
    snap=tmp_path/'0184-local-restart-snapshot.json'
    snap.write_text(json.dumps({'snapshot':{'root':str(baseline),'files':[dict(relative=k,sha256=v) for k,v in G._inventory(baseline).items()]}}))
    monkeypatch.setattr(G,'RESTART_SNAPSHOT_SHA256',record(snap)['sha256'])
    current=tmp_path/'current';shutil.copytree(baseline,current)
    overlayroot=tmp_path/'overlay';rows=[]
    for name in (G.LOCAL_COMMAND,G.LOCAL_RESTART,'scripts/batch/harbor_instruction_worker.py','.cursor/skills/task-disclosure/scripts/disclose.py'):
        p=overlayroot/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('reviewed data, deliberately not executable '+name)
        rows.append(dict(relative=name,**record(p)))
        (current/name).parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,current/name)
    (current/G.SELF).write_text('current installed validator')
    overlay=tmp_path/'0184-local-command-reviewed-overlay-v3.json'
    overlay.write_text(json.dumps(dict(root=str(overlayroot),predecessor=record(snap),files=rows)))
    monkeypatch.setattr(G,'LOCAL_COMMAND_OVERLAY_SHA256',record(overlay)['sha256'])
    return prior,baseline,current,snap,overlay,overlayroot


def test_closed_successor_uses_history_as_data(local_profile):
    prior,_,current,*_=local_profile
    G._instruction_code_profile(prior,current,G._inventory(current))

@pytest.mark.parametrize('fault',['evaluator','eligibility','coordinator','takeover','worker','renderer','restart','helper','extra','missing','baseline','overlay','overlay_bytes','snapshot','overlay_scope','predecessor'])
def test_local_successor_rejects_changes(local_profile,monkeypatch,fault):
    prior,baseline,current,snap,overlay,overlayroot=local_profile
    names={'evaluator':'grader/finance_grader/core.py','eligibility':'plain_eligibility.py','coordinator':G.COORDINATOR,
        'takeover':G.TAKEOVER,'worker':'scripts/batch/harbor_instruction_worker.py','renderer':'.cursor/skills/task-disclosure/scripts/disclose.py',
        'restart':G.LOCAL_RESTART,'helper':G.LOCAL_COMMAND}
    if fault in names:(current/names[fault]).write_text('tampered')
    elif fault=='extra':(current/'extra.py').write_text('extra')
    elif fault=='missing':(current/G.TAKEOVER).unlink()
    elif fault=='baseline':(baseline/G.TAKEOVER).write_text('tampered')
    elif fault=='overlay':overlay.write_text('{}')
    elif fault=='overlay_bytes':(overlayroot/G.LOCAL_COMMAND).write_text('tampered')
    elif fault=='snapshot':snap.write_text('{}')
    else:
        value=json.loads(overlay.read_text())
        if fault=='overlay_scope':value['files'].append(value['files'][0])
        else:value['predecessor']['sha256']='0'*64
        overlay.write_text(json.dumps(value));monkeypatch.setattr(G,'LOCAL_COMMAND_OVERLAY_SHA256',record(overlay)['sha256'])
    with pytest.raises(ValueError):G._instruction_code_profile(prior,current,G._inventory(current))

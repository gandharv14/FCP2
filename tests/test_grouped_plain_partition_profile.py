"""A review-only successor cannot alter financial or lifecycle policy."""
import importlib.util,json,shutil,sys
from pathlib import Path
import pytest
from scripts.batch.harbor_admission import record
ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture
def gate(monkeypatch):
    p=ROOT/'diagnostics/requested-fixes-20260910/0184-partition-gate-development-v2/scripts/batch/harbor_grouped_plain_qualification.py'
    spec=importlib.util.spec_from_file_location('partition_gate_test',p);m=importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules,spec.name,m);spec.loader.exec_module(m);return m

@pytest.fixture
def profile(gate,tmp_path,monkeypatch):
    G=gate;prior=tmp_path/'prior';prior.mkdir();baseline=tmp_path/'baseline';baseline.mkdir()
    entries={G.SELF:'old gate',G.LOCAL_RESTART:'same restart',G.COORDINATOR:'old coordinator',G.TAKEOVER:'same lease',
        'plain_eligibility.py':'same eligibility','xl_seg/evaluate.py':'same evaluator',
        'scripts/batch/harbor_instruction_worker.py':'old worker',G.LOCAL_COMMAND:'old local command',
        '.cursor/skills/task-disclosure/scripts/disclose.py':'same renderer','scripts/batch/harbor_complete_readiness.py':'old readiness',
        'scripts/batch/harbor_transition_codec.py':'old codec'}
    for name,text in entries.items():
        p=baseline/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
    snap=tmp_path/'0184-local-command-snapshot-v3.json'
    snap.write_text(json.dumps(dict(snapshot=dict(root=str(baseline),files=[dict(relative=k,sha256=v) for k,v in G._inventory(baseline).items()]))))
    monkeypatch.setattr(G,'PARTITION_BASE_SHA256',record(snap)['sha256'])
    current=tmp_path/'current';shutil.copytree(baseline,current);overlayroot=tmp_path/'overlay';rows=[]
    for name in sorted(G.PARTITION_FILES):
        p=overlayroot/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('reviewed nonexecutable data '+name)
        rows.append(dict(relative=name,**record(p)));(current/name).parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,current/name)
    (current/G.SELF).write_text('installed gate')
    overlay=tmp_path/'0184-partition-reviewed-overlay-v2.json';overlay.write_text(json.dumps(dict(root=str(overlayroot),predecessor=record(snap),files=rows)))
    monkeypatch.setattr(G,'PARTITION_OVERLAY_SHA256',record(overlay)['sha256'])
    return G,prior,baseline,current,snap,overlay,overlayroot


def test_exact_review_successor_accepts_old_code_as_data(profile):
    G,prior,_,current,*_=profile;G._instruction_code_profile(prior,current,G._inventory(current))

@pytest.mark.parametrize('fault',['evaluator','eligibility','takeover','restart','renderer','worker','controller','review','evidence','codec','readiness','command','coordinator','extra','missing','baseline','snapshot','overlay','overlaybytes','duplicates','path','predecessor'])
def test_review_profile_rejects_unreviewed_changes(profile,monkeypatch,fault):
    G,prior,baseline,current,snap,overlay,overlayroot=profile
    names=dict(evaluator='xl_seg/evaluate.py',eligibility='plain_eligibility.py',takeover=G.TAKEOVER,restart=G.LOCAL_RESTART,
        renderer='.cursor/skills/task-disclosure/scripts/disclose.py',worker='scripts/batch/harbor_instruction_worker.py',controller=G.PARTITION,
        review='scripts/batch/harbor_disclosure_partition_review.py',evidence='scripts/batch/harbor_disclosure_partition_evidence.py',
        codec='scripts/batch/harbor_transition_codec.py',readiness='scripts/batch/harbor_complete_readiness.py',command=G.LOCAL_COMMAND,coordinator=G.COORDINATOR)
    if fault in names:(current/names[fault]).write_text('tampered')
    elif fault=='extra':(current/'extra.py').write_text('unreviewed')
    elif fault=='missing':(current/G.TAKEOVER).unlink()
    elif fault=='baseline':(baseline/G.TAKEOVER).write_text('tampered')
    elif fault=='snapshot':snap.write_text('{}')
    elif fault=='overlay':overlay.write_text('{}')
    elif fault=='overlaybytes':(overlayroot/G.PARTITION).write_text('tampered')
    else:
        v=json.loads(overlay.read_text())
        if fault=='duplicates':v['files'].append(v['files'][0])
        elif fault=='path':v['files'][0]['path']='/foreign'
        else:v['predecessor']['sha256']='0'*64
        overlay.write_text(json.dumps(v));monkeypatch.setattr(G,'PARTITION_OVERLAY_SHA256',record(overlay)['sha256'])
    with pytest.raises(ValueError):G._instruction_code_profile(prior,current,G._inventory(current))

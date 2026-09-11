"""Only the reviewed instruction successor can retain grouped qualification."""
import json,hashlib,shutil
from pathlib import Path
import pytest
from scripts.batch import harbor_grouped_plain_qualification as G
from scripts.batch.harbor_admission import record

@pytest.fixture
def profile(tmp_path,monkeypatch):
    prior=tmp_path/'prior';prior.mkdir();baseline=tmp_path/'performance';current=tmp_path/'current'
    entries={G.SELF:'old gate',G.COORDINATOR:G.RESTART_ANCHOR+'\n',G.TAKEOVER:'old takeover',
        'plain_eligibility.py':'unchanged eligibility','grader/finance_grader/core.py':'unchanged evaluator','method.py':'indexed method'}
    for name,text in entries.items():
        p=baseline/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
    files=G._inventory(baseline)
    wrapper=tmp_path/'0184-indexed-methods-snapshot.json';wrapper.write_text(json.dumps({'snapshot':{'root':str(baseline),'files':[{'relative':k,'sha256':v} for k,v in files.items()]}}))
    monkeypatch.setattr(G,'PERFORMANCE_SNAPSHOT_SHA256',record(wrapper)['sha256'])
    shutil.copytree(baseline,current)
    (current/G.SELF).write_text('new installed gate')
    (current/G.COORDINATOR).write_text(entries[G.COORDINATOR].replace(G.RESTART_ANCHOR,G.RESTART_HOOK+G.RESTART_ANCHOR))
    (current/G.LOCAL_RESTART).write_text('reviewed helper bytes, deliberately not executable Python')
    (current/G.TAKEOVER).write_text('reviewed takeover')
    monkeypatch.setattr(G,'LOCAL_RESTART_SHA256',record(current/G.LOCAL_RESTART)['sha256'])
    monkeypatch.setattr(G,'TAKEOVER_SHA256',record(current/G.TAKEOVER)['sha256'])
    return prior,baseline,current,wrapper


def test_exact_profile_reads_old_bytes_without_execution(profile):
    prior,_,current,_=profile
    G._instruction_code_profile(prior,current,G._inventory(current))


@pytest.mark.parametrize('fault',['extra','missing','helper','takeover','hook','eligibility','evaluator','baseline','wrapper'])
def test_changed_profile_is_rejected(profile,fault):
    prior,baseline,current,wrapper=profile
    if fault=='extra':(current/'extra.py').write_text('unexpected')
    elif fault=='missing':(current/'method.py').unlink()
    elif fault=='baseline':(baseline/'method.py').write_text('changed')
    elif fault=='wrapper':wrapper.write_text('{}')
    else:
        target={'helper':G.LOCAL_RESTART,'takeover':G.TAKEOVER,'hook':G.COORDINATOR,
            'eligibility':'plain_eligibility.py','evaluator':'grader/finance_grader/core.py'}[fault]
        (current/target).write_text('tampered')
    with pytest.raises(ValueError):G._instruction_code_profile(prior,current,G._inventory(current))

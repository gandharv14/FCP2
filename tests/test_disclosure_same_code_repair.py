"""The installed physical repair can handle another exact rejected request."""
from pathlib import Path
import pytest
from scripts.batch import harbor_disclosure_review_resume as R
from scripts.batch.harbor_admission import record
from scripts.batch import harbor_instruction_worker as W


@pytest.fixture
def snapshot(tmp_path):
    old=tmp_path/'old';new=tmp_path/'new'
    names=['scripts/batch/harbor_disclosure_payload_repair.py',
           'scripts/batch/harbor_disclosure_review_resume.py','scripts/batch/harbor_instruction_worker.py']
    for root in (old,new):
        for name in names:
            p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('# existing '+name+'\n')
    manifest=W.keep(tmp_path/'snapshot.json',{'root':str(old),'files':[
        {'relative':name,'sha256':record(old/name)['sha256']} for name in names]})
    return old,new,manifest,names


def test_same_code_and_adapter_only_keep_payload_unchanged(snapshot):
    old,new,manifest,names=snapshot
    assert R.changes(manifest,new)==[]
    (new/names[1]).write_text('# owning same-code compatibility\n')
    assert R.changes(manifest,new)==[names[1]]
    assert record(old/names[0])['sha256']==record(new/names[0])['sha256']


@pytest.mark.parametrize('kind',['other_allowed','unknown','historical','removed'])
def test_same_code_does_not_allow_unrelated_rebases_or_history_changes(snapshot,kind):
    old,new,manifest,names=snapshot
    if kind=='other_allowed':(new/names[2]).write_text('# changed worker\n')
    elif kind=='unknown':(new/'unreviewed.py').write_text('# new implementation\n')
    elif kind=='historical':(old/names[0]).write_text('# overwritten old implementation\n')
    else:(new/names[0]).unlink()
    with pytest.raises(ValueError):R.changes(manifest,new)


def test_original_explicit_payload_upgrade_still_works(snapshot):
    old,new,manifest,names=snapshot
    (new/names[0]).write_text('# explicitly reviewed payload upgrade\n')
    (new/names[2]).write_text('# supporting worker upgrade\n')
    assert R.changes(manifest,new)==sorted([names[0],names[2]])


@pytest.mark.parametrize('kind',['exact','lease_duration','memory','other_guard','new_file'])
def test_only_exact_campaign_timeout_extension_is_admissible(snapshot,tmp_path,kind):
    old,new,manifest,names=snapshot
    name='scripts/harbor_campaign.py';raw=b'"""Original owning campaign."""\nSECONDS=900\nDURATION=3600*10**9\nMEMORY=8*1024**3\n'
    (old/name).parent.mkdir(exist_ok=True);(new/name).parent.mkdir(exist_ok=True)
    (old/name).write_bytes(raw)
    value=W.read(manifest)
    if kind!='new_file':value['files'].append({'relative':name,'sha256':record(old/name)['sha256']})
    upgraded=raw.replace(b'SECONDS=900',b'SECONDS=3300')
    if kind=='lease_duration':upgraded=upgraded.replace(b'DURATION=3600',b'DURATION=7200')
    elif kind=='memory':upgraded=upgraded.replace(b'MEMORY=8',b'MEMORY=16')
    elif kind=='other_guard':upgraded=upgraded.replace(b'SECONDS=3300',b'SECONDS=3600')
    (new/name).write_bytes(upgraded)
    manifest=W.keep(tmp_path/'with-campaign.json',value)
    if kind=='exact':assert R.changes(manifest,new)==[name]
    else:
        with pytest.raises(ValueError):R.changes(manifest,new)

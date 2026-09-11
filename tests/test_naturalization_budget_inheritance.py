"""Changing an upstream source cannot reset the two-writer budget."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from test_instruction_naturalization import SOURCE, CANDIDATE, RECOVERY, SPANS

sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def runs(tmp_path):
    def create(name,source=SOURCE):
        root=tmp_path/name;root.mkdir();instruction=root/'instruction.md';instruction.write_text(source);task=root/'task.toml';task.write_text('[metadata]\nname = "sample"\n[metadata.naturalizer]\nenabled = false\n');key=root/'answer_key.json';key.write_text(json.dumps({'targets':{'Valuation!C10':0.123,'Valuation!C20':456.7}}));state=root/'recovery';RECOVERY.init_recovery(instruction,state,instruction_path=instruction,task_toml_path=task,answer_key_path=key);return state
    def submit(root,candidate=CANDIDATE):
        pre,inp=SPANS.extract_editable_bodies(candidate.encode(),SPANS.scan_instruction(candidate.encode()));return RECOVERY.submit_attempt(root,pre,inp)
    first=create('first');assert submit(first)['valid'];return create,submit,first


def inherit(new,old):
    return RECOVERY.inherit_attempt_budget(new,old,previous_state_sha256=sha(old/'state.json'),reason='Reviewed upstream source correction')


def test_new_source_uses_only_attempt_two_and_requires_fresh_review(runs):
    create,submit,old=runs;old_state=(old/'state.json').read_bytes();old_candidate=(old/'attempt-01/candidate.md').read_bytes();new=create('new',SOURCE.replace('A colleague','A client'));state=inherit(new,old)
    assert state['inherited_attempts']==1 and state['next_attempt']==2 and state['attempts']==[]
    assert (old/'state.json').read_bytes()==old_state
    assert (new/'inherited-budget/previous/attempt-01/candidate.md').read_bytes()==old_candidate
    result=submit(new);assert result['valid']and result['attempt']==2
    with pytest.raises(RECOVERY.RecoveryError)as missing:RECOVERY.apply_recovery(new)
    assert missing.value.reason_code=='semantic_review_required'
    RECOVERY.accept_semantic_review(new,message='Independent complete clause review passed',reviewer='fresh reviewer')
    applied=RECOVERY.apply_recovery(new);assert applied['applied']
    metadata=RECOVERY.verify_naturalizer_metadata(new.parent/'instruction.md',new.parent/'task.toml');assert metadata['attempts']==2


def test_chained_inheritance_never_resets_consumed_attempts(runs):
    create,submit,first=runs;second=create('second');inherit(second,first);third=create('third');state=inherit(third,second)
    assert state['inherited_attempts']==1 and state['next_attempt']==2
    assert submit(third)['attempt']==2
    RECOVERY.reject_semantic_review(third,message='Independent review rejected second attempt')
    fourth=create('fourth');state=inherit(fourth,third)
    assert state['inherited_attempts']==2 and state['status']=='exhausted'and state['next_attempt']is None
    with pytest.raises(RECOVERY.RecoveryError)as exhausted:submit(fourth)
    assert exhausted.value.reason_code=='terminal_state'


@pytest.mark.parametrize('artifact',['attempt-01/candidate.md','attempt-01/preamble_body.md','attempt-01/input_body.md','attempt-01/source.snapshot.md','source.snapshot.md'])
def test_changed_prior_evidence_cannot_be_inherited(runs,artifact):
    create,submit,old=runs;new=create('new');digest=sha(old/'state.json');p=old/artifact;p.chmod(0o600);p.write_bytes(p.read_bytes()+b'corruption')
    with pytest.raises(RECOVERY.RecoveryError):RECOVERY.inherit_attempt_budget(new,old,previous_state_sha256=digest,reason='correction')
    assert not json.loads((new/'state.json').read_text()).get('budget_inheritance')


def test_recorded_prior_state_digest_is_required(runs):
    create,submit,old=runs;new=create('new')
    with pytest.raises(RECOVERY.RecoveryError)as mismatch:RECOVERY.inherit_attempt_budget(new,old,previous_state_sha256='0'*64,reason='correction')
    assert mismatch.value.reason_code=='prior_state_changed'


@pytest.mark.parametrize('fault',['archived_candidate','counter_reset','missing_binding','archived_state'])
def test_changed_inherited_evidence_or_counter_blocks_next_submission(runs,fault):
    create,submit,old=runs;new=create('new');inherit(new,old)
    if fault.startswith('archived'):
        p=new/'inherited-budget/previous'/('state.json'if fault=='archived_state'else'attempt-01/candidate.md');p.chmod(0o600);p.write_bytes(p.read_bytes()+b' ')
    else:
        p=new/'state.json';state=json.loads(p.read_text())
        if fault=='counter_reset':state['inherited_attempts']=0
        else:state.pop('budget_inheritance')
        p.write_text(json.dumps(state))
    with pytest.raises(RECOVERY.RecoveryError):submit(new)


def test_inheritance_is_idempotent_but_cannot_replace_a_budget(runs):
    create,submit,old=runs;new=create('new');state=inherit(new,old);assert inherit(new,old)==state
    other=create('other');submit(other)
    with pytest.raises(RECOVERY.RecoveryError)as error:inherit(new,other)
    assert error.value.reason_code=='budget_already_inherited'


def test_inheritance_requires_an_unused_destination_and_consumed_source(runs):
    create,submit,old=runs;new=create('new');submit(new)
    with pytest.raises(RECOVERY.RecoveryError)as error:inherit(new,old)
    assert error.value.reason_code=='budget_target_not_fresh'
    unused=create('unused');fresh=create('fresh')
    with pytest.raises(RECOVERY.RecoveryError)as error:inherit(fresh,unused)
    assert error.value.reason_code=='invalid_budget_inheritance'


def test_inherit_budget_cli_preserves_global_next_attempt(runs):
    create,submit,old=runs;new=create('new');result=subprocess.run([sys.executable,RECOVERY.__file__,'inherit-budget',str(new),'--previous-state-dir',str(old),'--previous-state-sha256',sha(old/'state.json'),'--reason','Corrected source'],text=True,capture_output=True)
    assert result.returncode==0,result.stdout+result.stderr
    assert json.loads(result.stdout)['next_attempt']==2


def test_deleting_current_attempt_record_cannot_reopen_budget(runs):
    create,submit,old=runs;new=create('new');inherit(new,old);submit(new);path=new/'state.json';state=json.loads(path.read_text());state['attempts']=[];state['status']='initialized';state['next_attempt']=2;path.write_text(json.dumps(state))
    with pytest.raises(RECOVERY.RecoveryError)as reset:submit(new)
    assert reset.value.reason_code=='invalid_attempt_budget'


def test_idempotent_submission_rechecks_archived_evidence(runs):
    create,submit,old=runs;new=create('new');inherit(new,old);submit(new);path=new/'inherited-budget/previous/attempt-01/input_body.md';path.chmod(0o600);path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(RECOVERY.RecoveryError)as changed:submit(new)
    assert changed.value.reason_code=='inherited_evidence_changed'


def test_uncommitted_archive_can_resume_without_new_attempt(runs,monkeypatch):
    create,submit,old=runs;new=create('new');original=RECOVERY.atomic_write_json
    def interrupted(path,payload):
        if path==new/'state.json'and payload.get('budget_inheritance'):raise RuntimeError('simulated crash before state publication')
        return original(path,payload)
    with monkeypatch.context()as patch:
        patch.setattr(RECOVERY,'atomic_write_json',interrupted)
        with pytest.raises(RuntimeError,match='simulated crash'):inherit(new,old)
    state=inherit(new,old);assert state['next_attempt']==2 and submit(new)['attempt']==2

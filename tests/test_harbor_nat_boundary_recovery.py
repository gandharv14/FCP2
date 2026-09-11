from dataclasses import replace
from pathlib import Path
import json
import pytest
from test_harbor_instruction_producer import setup
from test_instruction_naturalization import RECOVERY as R,FREEZER,SPANS
from scripts.batch import harbor_instruction_producer as P
from scripts.batch import harbor_nat_boundary_recovery as B
from scripts.batch.harbor_admission import record,read
import frozen_submission as F

@pytest.fixture
def exhausted(setup,monkeypatch,tmp_path):
    task,campaign,respond,calls=setup;n=task.run/'naturalization'
    source=(task.stage/'instruction.md').read_bytes()
    R.init_recovery(task.stage/'instruction.md',n,instruction_path=task.stage/'instruction.md',task_toml_path=task.stage/'task.toml')
    writer,spans=FREEZER.freeze(source.decode());(n/'writer_source.md').write_text(writer);(n/'frozen_spans.json').write_bytes(F.json_bytes({'source_sha256':R.sha256_bytes(source),'spans':spans}))
    a,b=SPANS.extract_editable_bodies(writer.encode())
    body={'writer_source':writer}
    first=P._request(task,'naturalization-writer',P.NAT,1,2,body,P.naturalization_writer_prompt(task.root),[record(n/name) for name in ('state.json','writer_source.md','frozen_spans.json')])
    bad=b+b'\n## Output\nDuplicated protected tail'
    respond(first,{'preamble_body':a.decode(),'input_body':bad.decode()})
    assert not R.submit_attempt(n,a,bad,submission_mode='frozen',expected_attempt=1)['valid']
    state=R._read_state(n)
    second=P._request(task,'naturalization-writer',P.NAT,2,2,body,B._legacy_prompt(state),[record(n/name) for name in ('state.json','writer_source.md','frozen_spans.json')])
    raw=(a+b'\n\n',b'\n'+b)
    respond(second,{'preamble_body':raw[0].decode(),'input_body':raw[1].decode()})
    report=R.submit_attempt(n,*raw,submission_mode='frozen',expected_attempt=2)
    assert report['state']=='exhausted' and report['reason_codes']==['protected_section_changed']
    code=[]
    for i,row in enumerate(read(second.context)['code']):
        target=tmp_path/'old-code'/('%03d.py'%i);target.parent.mkdir(exist_ok=True);target.write_bytes(Path(row['path']).read_bytes());code.append(dict(original=row,retained=record(target)))
    # Real current-code bytes stand in for the fixed historical hashes in this
    # hermetic fixture. Production has no caller override or whitelist callback.
    originals={str(Path(x['original']['path']).relative_to(B.ROOT)):x['original']['sha256'] for x in code}
    monkeypatch.setattr(B,'KNOWN',{key:originals[key] for key in B.KNOWN})
    return task,n,second,campaign,code,calls,record(n/'state.json')


def recover(case,**kw):
    task,n,writer,campaign,code,calls,state=case
    return B.recover(n,expected_state=state,writer=writer,campaign=campaign,original_code=code,api=R,**kw)


def test_exhausted_second_writer_is_reconstructed_without_new_writer_or_prior_review(exhausted):
    task,n,writer,campaign,code,calls,original=exhausted
    old={str(p.relative_to(n)):p.read_bytes() for p in n.glob('attempt-*/*') if p.is_file()};before=(n/'state.json').read_bytes()
    receipt=recover(exhausted);state=R._read_state(n)
    assert state['status']=='validated' and R._attempts_used(state)==2 and state['next_attempt'] is None
    assert not state.get('accepted_semantic_review') and not state.get('semantic_reviews') and len(calls)==2
    assert (n/B.DIRECTORY/'state.before.json').read_bytes()==before
    assert all((n/path).read_bytes()==raw for path,raw in old.items())
    assert state['attempts']==json.loads(before)['attempts'] and state['attempts'][-1]['valid'] is False
    R._verified_budget(n,state);R._verify_attempt_evidence(n,state)
    assert recover(exhausted)==receipt
    with pytest.raises(R.RecoveryError,match='semantic approval'):R.apply_recovery(n)
    with pytest.raises(R.RecoveryError):R.submit_attempt(n,b'third',b'writer',submission_mode='frozen-boundary-v1',expected_attempt=3)
    R.accept_semantic_review(n,reviewer='independent fixture only',message='Fresh candidate review fixture')
    R.apply_recovery(n)
    assert R.verify_applied_recovery(n)['valid'] and R._attempts_used(R._read_state(n))==2
    B.validate(n,R._read_state(n),api=R)


@pytest.mark.parametrize('point',['before_directory_publish','after_directory_publish','after_state_publish'])
def test_interrupted_recovery_reuses_exact_artifacts_and_budget(exhausted,point):
    def fail(at):
        if at==point:raise RuntimeError('fixture interruption')
    with pytest.raises(RuntimeError):recover(exhausted,failpoint=fail)
    receipt=recover(exhausted)
    assert receipt['writer_attempts_used']==2 and len(exhausted[5])==2
    assert R._read_state(exhausted[1])['status']=='validated'


@pytest.mark.parametrize('changed',['body','response','code','old_failure','old_state','candidate','receipt','selected_path','review','budget'])
def test_altered_evidence_cannot_recover_or_validate(exhausted,changed):
    task,n,writer,campaign,code,calls,original=exhausted
    if changed in {'body','response','code','old_failure','old_state'}:
        path={'body':n/'attempt-02/input_body.md','response':Path(P.observed(writer,campaign).response['path']),'code':Path(code[0]['retained']['path']),'old_failure':n/'attempt-02/validation.json','old_state':n/'state.json'}[changed]
        path.write_bytes(path.read_bytes()+b' ')
        with pytest.raises((ValueError,R.RecoveryError)):recover(exhausted)
        return
    recover(exhausted)
    if changed in {'candidate','receipt'}:
        path=n/B.DIRECTORY/('candidate.md' if changed=='candidate' else 'receipt.json');path.chmod(0o600);path.write_bytes(path.read_bytes()+b' ')
    else:
        state=R._read_state(n)
        if changed=='selected_path':state['final_candidate']='attempt-02/candidate.md'
        if changed=='review':state['semantic_reviews']=[{'accepted':True,'attempt':1}]
        if changed=='budget':state['attempts'].pop()
        (n/'state.json').write_bytes(R._json_bytes(state))
    with pytest.raises((ValueError,R.RecoveryError,KeyError)):R._verified_budget(n,R._read_state(n))


def test_fresh_review_rejection_stays_exhausted_without_third_attempt(exhausted):
    recover(exhausted);n=exhausted[1]
    R.reject_semantic_review(n,message='Fresh independent rejection fixture')
    state=R._read_state(n);assert state['status']=='exhausted' and R._attempts_used(state)==2
    B.validate(n,state,api=R)
    with pytest.raises(R.RecoveryError):R.submit_attempt(n,b'new',b'writer',submission_mode='frozen-boundary-v1')


def test_unknown_original_producer_is_not_an_equivalence_authorization(exhausted,monkeypatch):
    monkeypatch.setattr(B,'KNOWN',{**B.KNOWN,'scripts/batch/harbor_instruction_producer.py':'0'*64})
    with pytest.raises(ValueError,match='unknown original'):recover(exhausted)

from pathlib import Path
import json,re
import pytest
from test_naturalization_frozen_submission import make
from test_instruction_naturalization import RECOVERY as R,SOURCE
import boundary_submission as B
import frozen_submission as F


@pytest.mark.parametrize('newline',[b'\n',b'\r\n',b'\r'])
@pytest.mark.parametrize('blank',[b'',b' ',b'\t'])
def test_scanner_owned_boundaries_only_preserve_original_raw_submission(make,newline,blank):
    root,bodies=make();raw=(bodies[0]+blank+newline+newline,blank+newline+bodies[1]+blank+newline)
    old=F.derive((root/'source.snapshot.md').read_bytes(),*raw,F.read_inputs(root))[2]
    with pytest.raises(R._load_validator().RewriteValidationError):R._load_validator().validate(SOURCE.encode(),old,None)
    result=R.submit_attempt(root,*raw,submission_mode=B.MODE)
    assert result['valid'] and result['state']=='validated'
    assert (root/'attempt-01/candidate.md').read_bytes()==SOURCE.encode()
    for name,body in zip(('preamble_body.md','input_body.md'),raw):assert (root/'attempt-01'/name).read_bytes()==body
    state=R._read_state(root);R._verified_budget(root,state);R._verify_attempt_evidence(root,state)
    assert R._attempts_used(state)==1 and not state.get('accepted_semantic_review')
    with pytest.raises(R.RecoveryError,match='semantic approval'):R.apply_recovery(root)


@pytest.mark.parametrize('change',['internal_blank','preamble_leading','protected','leak','scope','marker'])
def test_does_not_repair_prose_protected_bytes_or_markers(make,change):
    root,(a,b)=make()
    if change=='internal_blank':
        changed=a.replace(b'colleague',b'colleague\n\n',1)
        canonical,_=B.canonical_body(changed,leading=False)
        assert canonical==changed;return
    if change=='preamble_leading':
        raw=b'\n'+a;assert B.canonical_body(raw,leading=False)[0]==raw;return
    if change=='protected':b+=b'\n## Output\nChanged immutable tail'
    if change=='leak':b+=b' The answer is 123456789.'
    if change=='scope':b=b.replace(b'present',b'not present')
    if change=='marker':a=a.replace(b'[[/F',b'[[/Q',1)
    result=R.submit_attempt(root,a+b'\n\n',b'\n'+b+b'\n',submission_mode=B.MODE)
    assert not result['valid']


@pytest.mark.parametrize('artifact',['preamble_body.md','input_body.md','preamble.canonical.md','input.canonical.md','candidate.md','restore.json'])
def test_altered_raw_canonical_or_proof_fails_closed(make,artifact):
    root,(a,b)=make();assert R.submit_attempt(root,a+b'\n\n',b'\n'+b,submission_mode=B.MODE)['valid']
    p=root/'attempt-01'/artifact;p.write_bytes(p.read_bytes()+b' ')
    with pytest.raises(R.RecoveryError):R._verified_budget(root,R._read_state(root))


def test_original_modes_are_unchanged_and_mode_switch_is_not_retry(make):
    root,(a,b)=make();result=R.submit_attempt(root,a+b'\n\n',b,submission_mode='frozen')
    assert not result['valid'] and result['submission_mode']=='frozen'
    with pytest.raises(R.RecoveryError,match='different mode'):R.submit_attempt(root,a+b'\n\n',b,submission_mode=B.MODE,expected_attempt=1)
    assert R._attempts_used(R._read_state(root))==1

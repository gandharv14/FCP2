import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from test_instruction_naturalization import SOURCE, CANDIDATE, FREEZER, SPANS, RECOVERY as R
import frozen_submission as F


@pytest.fixture
def make(tmp_path):
    def create(name='run', source=SOURCE.encode()):
        parent=tmp_path/name;parent.mkdir();i=parent/'instruction.md';i.write_bytes(source)
        t=parent/'task.toml';t.write_text('[metadata]\nname="fixture"\n[metadata.naturalizer]\nenabled=false\n')
        root=parent/'nat';R.init_recovery(i,root,instruction_path=i,task_toml_path=t)
        writer, spans=FREEZER.freeze(source.decode())
        (root/'writer_source.md').write_bytes(writer.encode())
        (root/'frozen_spans.json').write_bytes(F.json_bytes({'source_sha256':R.sha256_bytes(source),'spans':spans}))
        return root, SPANS.extract_editable_bodies(writer.encode())
    return create


def submit(root,bodies,**kwargs):
    return R.submit_attempt(root,*bodies,submission_mode='frozen',**kwargs)


@pytest.mark.parametrize('bom',[b'',b'\xef\xbb\xbf'])
@pytest.mark.parametrize('newline',[b'\n',b'\r\n'])
def test_fresh_restoration_keeps_bytes_raw_bodies_and_requires_fresh_review(make,bom,newline):
    source=bom+SOURCE.encode().replace(b'\n',newline)
    root,bodies=make(source=source)
    result=submit(root,bodies)
    assert result['valid'] and result['submission_mode']=='frozen'
    attempt=root/'attempt-01'
    assert (attempt/'candidate.md').read_bytes()==source
    assert (attempt/'preamble_body.md').read_bytes()==bodies[0]
    assert (attempt/'input_body.md').read_bytes()==bodies[1]
    assert b'[[F' in (attempt/'candidate.marked.md').read_bytes()
    proof=json.loads((attempt/'restore.json').read_bytes())
    assert proof['errors']==proof['drift']==[] and proof['restored'] is True
    state=R.recovery_status(root)
    assert len(state['attempts'])==1 and not state.get('accepted_semantic_review')
    with pytest.raises(R.RecoveryError,match='semantic approval'):R.apply_recovery(root)
    R.accept_semantic_review(root,message='Independent fixture review of restored text',reviewer='fresh fixture reviewer')
    R.apply_recovery(root)
    assert R.verify_applied_recovery(root)['valid']
    assert root.parent.joinpath('instruction.md').read_bytes()==source


@pytest.mark.parametrize('fault',['missing','duplicate','unknown','drift','malformed','nested'])
def test_marker_fault_consumes_same_bounded_attempt(make,fault):
    root,(p,i)=make();marker=FREEZER.MARKER_PAIR_RE.search(p.decode()).group().encode()
    wrong={'missing':b'','duplicate':marker*2,'unknown':marker.replace(b'F00',b'F999'),
           'drift':marker.replace(b'[[/',b'x[[/',1),'malformed':marker.replace(b'F00',b'F0'),
           'nested':b'[[F999]]'+marker+b'[[/F999]]'}[fault]
    first=submit(root,(p.replace(marker,wrong,1),i))
    assert not first['valid'] and first['state']=='retry_ready'
    assert first['reason_codes']==['frozen_span_invalid']
    assert len(R.recovery_status(root)['attempts'])==1
    assert (root/'attempt-01/restore.json').exists()
    with pytest.raises(R.RecoveryError):R.accept_semantic_review(root,message='Must not approve')
    second=submit(root,(p.replace(marker,wrong+b'other',1),i))
    assert not second['valid'] and second['state']=='exhausted'
    with pytest.raises(R.RecoveryError):submit(root,(p,i))
    assert len(R.recovery_status(root)['attempts'])==2


@pytest.mark.parametrize('fault',['writer','map','missing_map','invalid_utf8','raw_bom'])
def test_bad_authoring_inputs_preserve_failure_and_budget(make,fault):
    root,(p,i)=make()
    if fault=='writer':(root/'writer_source.md').write_bytes(b'changed')
    elif fault=='map':(root/'frozen_spans.json').write_bytes(b'{}')
    elif fault=='missing_map':(root/'frozen_spans.json').unlink()
    elif fault=='invalid_utf8':p=b'\xff'+p
    else:p=b'\xef\xbb\xbf'+p
    result=submit(root,(p,i));assert not result['valid'] and result['state']=='retry_ready'
    assert R.recovery_status(root)['attempts'][0]['preamble_submission_sha256']==R.sha256_bytes(p)


@pytest.mark.parametrize('point',['after_submission_staged','after_frozen_restoration','after_submission_validation'])
def test_interrupted_submission_resumes_exact_mode_and_inputs(make,point):
    root,bodies=make()
    def fail(name):
        if name==point:raise R.RecoveryInterruption('test_interruption',name)
    with pytest.raises(R.RecoveryInterruption):submit(root,bodies,failpoint=fail)
    before=(root/'state.json').read_bytes()
    with pytest.raises(R.RecoveryError,match='different submission'):R.submit_attempt(root,*bodies)
    assert (root/'state.json').read_bytes()==before
    assert submit(root,bodies)['valid']
    assert submit(root,bodies)['idempotent']
    assert len(R.recovery_status(root)['attempts'])==1
    with pytest.raises(R.RecoveryError,match='different mode'):R.submit_attempt(root,*bodies)


@pytest.mark.parametrize('name',['writer_source.md','frozen_spans.json'])
def test_staged_authoring_change_rejects_without_reset(make,name):
    root,bodies=make()
    def fail(point):raise R.RecoveryInterruption('test_interruption',point)
    with pytest.raises(R.RecoveryInterruption):submit(root,bodies,failpoint=fail)
    (root/name).write_bytes((root/name).read_bytes()+b' ')
    with pytest.raises(R.RecoveryError,match='different submission'):submit(root,bodies)
    assert R._read_state(root)['status']=='staged'


@pytest.mark.parametrize('file',['preamble_body.md','input_body.md','writer_source.md','frozen_spans.json','candidate.marked.md','candidate.restored.md','candidate.md','restore.json','validation.json','source.snapshot.md'])
@pytest.mark.parametrize('consumer',['status','accept','apply','inherit','readiness'])
def test_all_bound_evidence_revalidated(make,file,consumer):
    root,bodies=make();submit(root,bodies)
    if consumer=='apply':R.accept_semantic_review(root,message='Fresh fixture review')
    p=root/'attempt-01'/file;p.chmod(0o600);p.write_bytes(p.read_bytes()+b' ')
    with pytest.raises(Exception):
        if consumer=='status':R.recovery_status(root)
        elif consumer=='accept':R.accept_semantic_review(root,message='No changed evidence')
        elif consumer=='apply':R.apply_recovery(root)
        elif consumer=='readiness':R._verify_attempt_evidence(root,R._read_state(root))
        else:
            new,_=make('new');R.inherit_attempt_budget(new,root,previous_state_sha256=R.sha256_bytes((root/'state.json').read_bytes()),reason='Upstream correction')


@pytest.mark.parametrize('edit',['mode','binding','candidate','candidate_path','budget'])
def test_state_detachment_rejects(make,edit):
    root,bodies=make();submit(root,bodies);state=R._read_state(root)
    if edit=='mode':state['attempts'][0].pop('submission_mode')
    elif edit=='binding':state['attempts'][0].pop('frozen_submission')
    elif edit=='candidate':state['final_instruction_sha256']='0'*64
    elif edit=='candidate_path':state['final_candidate']='attempt-01/candidate.restored.md'
    else:state['attempts']=[]
    R.atomic_write_json(root/'state.json',state)
    with pytest.raises(R.RecoveryError):R.recovery_status(root)


def test_plain_submit_is_still_plain_and_marked_plain_is_not_reinterpreted(make):
    root,_=make();bodies=SPANS.extract_editable_bodies(CANDIDATE.encode())
    assert R.submit_attempt(root,*bodies)['valid']
    assert not (root/'attempt-01/restore.json').exists()
    other,marked=make('marked');result=R.submit_attempt(other,*marked)
    assert not result['valid'] and result['reason_codes']==['frozen_span_marker_residue']
    with pytest.raises(R.RecoveryError,match='different mode'):submit(other,marked)


def test_semantic_rejection_and_inheritance_do_not_reset_budget(make):
    root,bodies=make();submit(root,bodies);R.reject_semantic_review(root,message='Retain temporal clause')
    new,new_bodies=make('new');R.inherit_attempt_budget(new,root,previous_state_sha256=R.sha256_bytes((root/'state.json').read_bytes()),reason='Changed upstream scaffold')
    assert submit(new,new_bodies)['attempt']==2
    R.reject_semantic_review(new,message='Still fails complete semantic review')
    assert R.recovery_status(new)['status']=='exhausted'
    with pytest.raises(R.RecoveryError):submit(new,(new_bodies[0]+b' ',new_bodies[1]))
    assert len(R._read_state(new)['semantic_reviews'])==1
    previous=R._read_state(new/'inherited-budget/previous')
    assert len(previous['semantic_reviews'])==1


def test_full_validator_still_rejects_unprotected_new_fact(make):
    root,(p,i)=make();result=submit(root,(p+b' The result is 999888.',i))
    assert not result['valid'] and result['reason_codes']!=['frozen_span_invalid']
    assert json.loads((root/'attempt-01/restore.json').read_bytes())['restored']
    assert R.recovery_status(root)['attempts'][0]['valid'] is False


def test_cli_freeze_and_submit_preserve_bom_crlf(make):
    source=b'\xef\xbb\xbf'+SOURCE.encode().replace(b'\n',b'\r\n');root,_=make(source=source)
    scripts=Path(R.__file__).parent
    result=subprocess.run([sys.executable,'-B',str(scripts/'freeze_protected_spans.py'),'freeze',str(root/'source.snapshot.md'),'--writer-source',str(root/'writer_source.md'),'--span-map',str(root/'frozen_spans.json')],capture_output=True)
    assert result.returncode==0,result.stderr
    bodies=SPANS.extract_editable_bodies((root/'writer_source.md').read_bytes())
    for name,raw in zip(['preamble_body.md','input_body.md'],bodies):(root/'attempt-01'/name).write_bytes(raw)
    result=subprocess.run([sys.executable,'-B',str(scripts/'naturalize_recovery.py'),'submit-frozen',str(root),'--preamble',str(root/'attempt-01/preamble_body.md'),'--input',str(root/'attempt-01/input_body.md')],capture_output=True)
    assert result.returncode==0,result.stdout+result.stderr
    assert (root/'attempt-01/candidate.md').read_bytes()==source


def test_fresh_prose_is_writer_owned_with_only_wrapper_restoration(make):
    root,(p,i)=make()
    changed=p.replace(b'A colleague has sent you',b'You are taking over',1)
    assert changed != p
    result=submit(root,(changed,i));assert result['valid']
    attempt=root/'attempt-01'
    marked=attempt.joinpath('candidate.marked.md').read_bytes()
    candidate=attempt.joinpath('candidate.md').read_bytes()
    assert b'You are taking over' in candidate
    assert re.sub(rb'\[\[/?F\d+\]\]',b'',marked)==candidate
    assert attempt.joinpath('preamble_body.md').read_bytes()==changed
    R._verify_attempt_evidence(root,R._read_state(root))

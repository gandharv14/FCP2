import copy
import importlib.util
import json
from pathlib import Path
import sys
import types

import pytest

from test_instruction_naturalization import SOURCE, CANDIDATE, FREEZER, SPANS, RECOVERY as R
import span_restoration as sr


def sha(path):return sr.digest(path.read_bytes())


@pytest.fixture
def history(tmp_path,monkeypatch):
    def create(name):
        p=tmp_path/name;p.mkdir();i=p/'instruction.md';i.write_text(SOURCE);t=p/'task.toml';t.write_text('[metadata]\nname="fixture"\n[metadata.naturalizer]\nenabled=false\n');k=p/'answer_key.json';k.write_text(json.dumps({'targets':{'Valuation!C10':.123,'Valuation!C20':456.7}}));n=p/'recovery';R.init_recovery(i,n,instruction_path=i,task_toml_path=t,answer_key_path=k);return n
    def submit(root,text):
        p,i=SPANS.extract_editable_bodies(text.encode(),SPANS.scan_instruction(text.encode()));return R.submit_attempt(root,p,i)
    prior=create('prior');submit(prior,CANDIDATE);root=create('current');R.inherit_attempt_budget(root,prior,previous_state_sha256=sha(prior/'state.json'),reason='Changed packaged context')
    writer,spans=FREEZER.freeze(SOURCE);(root/'writer_source.md').write_text(writer);(root/'frozen_spans.json').write_text(json.dumps({'source_sha256':sr.digest(SOURCE.encode()),'spans':spans}))
    # Execute exactly the previous validator implementation to reproduce a
    # historically accepted marked submission, rather than inventing a report.
    validator=R._load_validator();text=Path(validator.__file__).read_text();start=text.index('    # Frozen spans are writer-only transport syntax.');end=text.index('    try:\n        source_span_map',start);text=text[:start]+text[end:];old=types.ModuleType('old_validator');old.__file__=validator.__file__;exec(compile(text,old.__file__,'exec'),old.__dict__)
    with monkeypatch.context() as m:
        m.setattr(R,'_load_validator',lambda:old);assert submit(root,writer)['valid']
    request={'model':'openai/gpt-5.6-sol','reasoning_effort':'high','messages':[{'role':'system','content':'You are a fresh independent semantic reviewer. Compare clause by clause.'},{'role':'user','content':json.dumps({'source':SOURCE,'candidate':writer})}]}
    review={'valid':True,'blocking_findings':[],'clause_checks':[{'source_claim':SOURCE,'candidate_claim':SOURCE,'preserved':True}]}
    response={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(review)}}]}
    (root/'review-request.json').write_text(json.dumps(request));(root/'review-response.json').write_text(json.dumps(response))
    return root,create


def restore(root,**kw):
    return sr.restore(R,root,expected_state_sha256=kw.pop('expected',sha(root/'state.json')),request_relative='review-request.json',response_relative='review-response.json',**kw)


def test_exact_restoration_preserves_original_attempts_and_requires_explicit_review(history):
    root,_=history;before=R._read_state(root);files={p: p.read_bytes()for p in root.rglob('*')if p.is_file()};receipt=restore(root);after=R._read_state(root)
    assert after['attempts']==before['attempts']and after['inherited_attempts']==1
    assert receipt['writer_attempts_used']==2 and receipt['new_writer_calls']==0
    assert after.get('accepted_semantic_review')is None
    for p,data in files.items():
        if p.name!='state.json':assert p.read_bytes()==data
    assert sr.validate(R,root,after)==receipt
    with pytest.raises(R.RecoveryError,match='semantic approval'):R.apply_recovery(root)
    with pytest.raises(R.RecoveryError,match='explicit prior-review applicability'):
        R.accept_semantic_review(root,message='Not a new model review')
    approve(root)
    assert R.apply_recovery(root)['applied'];assert R.verify_applied_recovery(root)['valid']
    assert R.verify_naturalizer_metadata(root.parent/'instruction.md',root.parent/'task.toml')['attempts']==2


@pytest.mark.parametrize('file',['source.snapshot.md','writer_source.md','frozen_spans.json','attempt-02/preamble_body.md','attempt-02/input_body.md','attempt-02/candidate.md','review-request.json','review-response.json'])
def test_changed_inputs_reject_before_state_mutation(history,file):
    root,_=history;p=root/file;p.chmod(0o600);p.write_bytes(p.read_bytes()+b'changed');before=(root/'state.json').read_bytes()
    with pytest.raises(Exception):restore(root)
    assert (root/'state.json').read_bytes()==before and not (root/sr.DIRECTORY).exists()


@pytest.mark.parametrize('fault',['drift','missing','duplicate','unknown'])
def test_rebound_writer_marker_errors_reject(history,fault):
    root,_=history;state=R._read_state(root);p=root/'attempt-02/preamble_body.md';text=p.read_text();marker=FREEZER.MARKER_PAIR_RE.search(text).group(0)
    replacement={'drift':marker.replace('[[/','altered[[/',1),'missing':'','duplicate':marker+marker,'unknown':marker.replace('F00','F999')}[fault];text=text.replace(marker,replacement,1);p.write_text(text);source=(root/'source.snapshot.md').read_bytes();candidate=R.assemble_instruction(source,R.scan_instruction(source),p.read_bytes(),(root/'attempt-02/input_body.md').read_bytes());(root/'attempt-02/candidate.md').write_bytes(candidate)
    state['attempts'][-1]['preamble_submission_sha256']=sha(p);state['attempts'][-1]['candidate_sha256']=sr.digest(candidate);state['final_instruction_sha256']=sr.digest(candidate);v=root/'attempt-02/validation.json';report=json.loads(v.read_text());report.update(preamble_submission_sha256=sha(p),candidate_sha256=sr.digest(candidate));v.write_text(json.dumps(report));(root/'state.json').write_text(json.dumps(state))
    with pytest.raises(ValueError,match='restore'):restore(root)


@pytest.mark.parametrize('point',['before_directory_publish','after_directory_publish','after_state_publish'])
def test_interruption_has_exact_idempotent_recovery(history,point):
    root,_=history;before=(root/'state.json').read_bytes();expected=sr.digest(before)
    def fail(name):
        if name==point:raise InterruptedError(name)
    with pytest.raises(InterruptedError):restore(root,expected=expected,failpoint=fail)
    if point!='after_state_publish':assert (root/'state.json').read_bytes()==before
    receipt=restore(root,expected=expected);assert receipt['writer_attempts_used']==2
    assert restore(root,expected=expected)==receipt
    assert len(R._read_state(root)['attempts'])==1


def test_state_conflict_and_replayed_different_operation_reject(history):
    root,_=history
    with pytest.raises(ValueError,match='state conflict'):restore(root,expected='0'*64)
    expected=sha(root/'state.json');restore(root,expected=expected)
    with pytest.raises(ValueError,match='replay state mismatch'):restore(root,expected='0'*64)
    with pytest.raises(ValueError,match='replay evidence mismatch'):sr.restore(R,root,expected_state_sha256=expected,request_relative='different.json',response_relative='review-response.json')


@pytest.mark.parametrize('file',['submitted-span-restoration/receipt.json','submitted-span-restoration/state.before.json','submitted-span-restoration/candidate.md','submitted-span-restoration/validation.json','review-response.json','frozen_spans.json','attempt-02/candidate.md'])
@pytest.mark.parametrize('consumer',['accept','apply','inherit'])
def test_changed_restoration_evidence_blocks_consumers(history,file,consumer):
    root,create=history;restore(root);p=root/file;p.chmod(0o600);p.write_bytes(p.read_bytes()+b' ')
    with pytest.raises(Exception):
        if consumer=='accept':R.accept_semantic_review(root,message='Should fail')
        elif consumer=='apply':R.apply_recovery(root)
        else:R.inherit_attempt_budget(create('next'),root,previous_state_sha256=sha(root/'state.json'),reason='Next correction')


def test_restored_history_inherits_exact_exhausted_budget(history):
    root,create=history;restore(root);new=create('next');state=R.inherit_attempt_budget(new,root,previous_state_sha256=sha(root/'state.json'),reason='Another upstream correction')
    assert state['status']=='exhausted'and state['inherited_attempts']==2
    R._verified_budget(new,state)
    with pytest.raises(R.RecoveryError):R.submit_attempt(new,b'',b'')


@pytest.mark.parametrize('key,value',[('attempts',[]),('inherited_attempts',0),('final_candidate','attempt-02/candidate.md'),('span_restoration',None)])
def test_selected_state_cannot_be_detached_or_budget_reset(history,key,value):
    root,_=history;restore(root);state=R._read_state(root);state[key]=value;(root/'state.json').write_text(json.dumps(state))
    with pytest.raises(Exception):R.accept_semantic_review(root,message='Must fail closed')


def review_binding(root):
    state=R._read_state(root);receipt=sr.validate(R,root,state);proof=receipt['proof']
    report={'schema_version':'naturalization-span-review-applicability/v1','reviewer':'independent fixture reviewer','reason':'All changed prose covered by original retained independent review; only exact canonical wrappers removed.','restoration_receipt_sha256':sha(root/sr.DIRECTORY/'receipt.json'),'original_request_sha256':proof['original_evidence'][proof['original_review_request']],'original_response_sha256':proof['original_evidence'][proof['original_review_response']],'source_sha256':proof['source_sha256'],'restored_candidate_sha256':proof['restored_candidate_sha256'],'all_changed_prose_covered':True,'canonical_span_text_only':True,'approved':True}
    p=root/'independent-applicability.json';p.write_text(json.dumps(report));return p


def approve(root):
    p=review_binding(root);return sr.accept_review(R,root,expected_state_sha256=sha(root/'state.json'),applicability_path=p.name,applicability_sha256=sha(p))


@pytest.mark.parametrize('field,value',[('all_changed_prose_covered',False),('approved',False),('canonical_span_text_only',False),('restored_candidate_sha256','0'*64),('original_response_sha256','0'*64),('restoration_receipt_sha256','0'*64),('reviewer','')])
def test_applicability_requires_exact_independent_review(history,field,value):
    root,_=history;restore(root);p=review_binding(root);report=json.loads(p.read_text());report[field]=value;p.write_text(json.dumps(report))
    with pytest.raises(ValueError):sr.accept_review(R,root,expected_state_sha256=sha(root/'state.json'),applicability_path=p.name,applicability_sha256=sha(p))
    assert R._read_state(root).get('accepted_semantic_review')is None


def test_changed_bound_applicability_blocks_apply_and_inheritance(history):
    root,create=history;restore(root);approve(root);p=root/'independent-applicability.json';p.write_bytes(p.read_bytes()+b' ')
    with pytest.raises(ValueError):R.apply_recovery(root)
    with pytest.raises(ValueError):R.inherit_attempt_budget(create('next'),root,previous_state_sha256=sha(root/'state.json'),reason='next')


def test_approved_restoration_preserves_review_kind_through_budget_inheritance(history):
    root,create=history;restore(root);approve(root);new=create('next');state=R.inherit_attempt_budget(new,root,previous_state_sha256=sha(root/'state.json'),reason='next');R._verified_budget(new,state)
    old=R._read_state(new/'inherited-budget/previous');assert old['accepted_semantic_review']['review_kind']=='prior_review_exact_marker_restoration'
    assert state['inherited_attempts']==2


@pytest.mark.parametrize('field,value',[('candidate_sha256','0'*64),('source_sha256','0'*64),('attempt',1),('accepted',False),('review_kind','fresh_model_review')])
def test_changed_approval_identity_blocks_history_inheritance(history,field,value):
    root,create=history;restore(root);approve(root);state=R._read_state(root);state['accepted_semantic_review'][field]=value;(root/'state.json').write_text(json.dumps(state))
    with pytest.raises(ValueError):R.inherit_attempt_budget(create('next'),root,previous_state_sha256=sha(root/'state.json'),reason='next')


def test_removed_semantic_history_blocks_history_inheritance(history):
    root,create=history;restore(root);approve(root);state=R._read_state(root);state['semantic_reviews']=[];(root/'state.json').write_text(json.dumps(state))
    with pytest.raises(ValueError):R.inherit_attempt_budget(create('next'),root,previous_state_sha256=sha(root/'state.json'),reason='next')

"""Rejected old work may gain new explicit allowance; its consumption stays fixed."""
import json
from pathlib import Path
import pytest
from scripts.batch import harbor_legacy_nat_exhausted as N
from scripts.batch import harbor_legacy_research_history as L
from scripts.batch import harbor_legacy_staged_history as S

@pytest.fixture
def case(tmp_path):
    wb='0001';package=tmp_path/(wb+'-outputs');nat=tmp_path/(wb+'-instruction-naturalization');tr=tmp_path/(wb+'.json');package.mkdir();nat.mkdir();files={};pack={}
    def keep(root,name,value):
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(value if isinstance(value,str)else json.dumps(value));return L.binding(p)
    pack['instruction.md']=keep(package,'instruction.md','original instruction');pack['tests/answer_key.json']=keep(package,'tests/answer_key.json',{})
    files['source.snapshot.md']=keep(nat,'source.snapshot.md','original instruction');files['answer_key.snapshot.json']=keep(nat,'answer_key.snapshot.json',{})
    for name in ('frozen_spans.json','spans.json','writer_source.md','recovery.lock'):files[name]=keep(nat,name,'')
    attempts=[]
    for n in (1,2):
        prefix=f'attempt-{n:02d}/'
        for name in ('candidate.md','preamble_body.md','input_body.md'):files[prefix+name]=keep(nat,prefix+name,'rejected '+str(n)+' '+name)
        a=dict(attempt=n,valid=False,reason_codes=['protected_section_changed'],candidate_sha256=files[prefix+'candidate.md']['sha256'],input_submission_sha256=files[prefix+'input_body.md']['sha256'],preamble_submission_sha256=files[prefix+'preamble_body.md']['sha256']);attempts.append(a)
        report={**a,'source_sha256':pack['instruction.md']['sha256']};files[prefix+'validation.json']=keep(nat,prefix+'validation.json',report)
    files['validation.json']=keep(nat,'validation.json',report)
    state=dict(version='naturalize-recovery-v1',status='exhausted',attempt_limit=2,next_attempt=None,final_instruction_sha256=None,instruction_path=str(package/'instruction.md'),source_path=str(package/'instruction.md'),source_sha256=pack['instruction.md']['sha256'],answer_key_sha256=pack['tests/answer_key.json']['sha256'],attempts=attempts)
    files['state.json']=keep(nat,'state.json',state)
    h=dict(dialogue_custody_kind=N.KIND,package=str(package),namespaces={str(package):pack,str(nat):files,str(tr):{},str(tmp_path/'disclosure'/(wb+'-outputs')):{}},naturalization_reservations_upper_bound=2,dialogue_rounds_upper_bound=0,new_authoring_authorized=False)
    p=dict(schema_version=L.MCP_STAGED_SOURCE_SCHEMA,workbook=wb,tracker={'path':str(tr)},staged_history=h)
    old=dict(status='blocked_instruction_naturalization',mode='mcp',promotion_attempted=False,first_blocker={'gate':'instruction_naturalization'},gates={'instruction_naturalization':'blocker'})
    return p,old,pack,files,state,keep,nat


def test_preserves_two_consumed_failed_attempts_without_adopting_quality(case):
    p,old,pack,*_=case
    assert N.history(p,old,pack)==dict(old_nat_attempts_consumed=2,old_dialogue_rounds=0,historical_quality_adopted=False)

@pytest.mark.parametrize('defect',['reset','author','unknown_stage','applied','missing','unknown_attempt','pending','altered_state','source','candidate','partial_report','unknown_failure','partial_source'])
def test_rejects_changed_consumption_pending_effects_or_detached_source(case,defect):
    p,old,pack,files,state,keep,nat=case
    if defect=='reset':p['staged_history']['naturalization_reservations_upper_bound']=0
    elif defect=='author':p['staged_history']['namespaces'][str(nat.parent/'0001-additional-assumptions')]={}
    elif defect=='unknown_stage':old['first_blocker']['gate']='numerical_failure'
    elif defect=='missing':del files['attempt-01/candidate.md']
    elif defect=='unknown_attempt':files['attempt-03/request.json']=keep(nat,'attempt-03/request.json',{})
    elif defect=='source':pack['instruction.md']={**pack['instruction.md'],'sha256':'changed'}
    elif defect=='candidate':files['attempt-01/candidate.md']=keep(nat,'attempt-01/candidate.md','changed')
    elif defect=='partial_report':files['attempt-01/validation.json']=keep(nat,'attempt-01/validation.json',{**state['attempts'][0],'source_sha256':'foreign'})
    else:
        if defect=='applied':state['status']='applied'
        elif defect=='pending':state['next_attempt']=3
        elif defect=='altered_state':state['attempts'][0]['valid']=True
        elif defect=='unknown_failure':state['attempts'][0]['reason_codes']=['incomplete_response']
        elif defect=='partial_source':state['source_sha256']='foreign'
        files['state.json']=keep(nat,'state.json',state)
    with pytest.raises(ValueError):N.history(p,old,pack)

@pytest.mark.parametrize('wb',['0034','0031','0080'])
def test_actual_complete_retained_evidence_does_not_reset_or_publish(wb):
    path=Path(__file__).resolve().parents[1]/'diagnostics/bulk-production/legacy-nat-exhausted-001'/(wb+'-history.json')
    if not path.exists():pytest.skip('optional retained deployment evidence absent')
    p=L.read(L.binding(path));result=S.validate(L.binding(path),at_ns=p['created_at_ns']+1)
    assert result==p and p['staged_history']['naturalization_reservations_upper_bound']==2
    assert p['staged_history']['new_authoring_authorized']is False

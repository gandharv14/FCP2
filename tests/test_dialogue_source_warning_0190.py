"""Closed source/evidence regressions; native owning mechanics are separate."""
from pathlib import Path
import copy
import hashlib
import json
from types import SimpleNamespace
import pytest
from scripts.fleet import dialogue_source_warning_0190 as S
from scripts.batch import harbor_instruction_producer as P

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT/'diagnostics/vm-fleet-20260912/control/0190-warning-continuation-001'


@pytest.fixture
def case(monkeypatch, tmp_path):
    custody = HOME/'evidence-custody.json'
    if not custody.exists(): pytest.skip('retained 0190 evidence unavailable')
    records = json.loads(custody.read_bytes())
    by_name = {Path(x['original']['path']).name: x['copy'] for x in records}
    args = [by_name[x] for x in ('review.json','claims.json','draft.md',
            'draft_template.md','writer.filled.md','paraphraser.filled.md')]
    source = ROOT.parent/'RawWorkbooks3/0190.xlsx'
    args.append(dict(path=str(source),sha256=S.SOURCE))
    actual = P.skill_module(ROOT,'additional-assumptions-dialogue','validate_dialogue')
    # Isolate the source/scaffold classifier in these unit tests. The separate
    # native probe uses the real task and unchanged owning check_draft.
    facade = SimpleNamespace(fill_faults=actual.fill_faults,
        strip_slot_scaffold=actual.strip_slot_scaffold,
        check_draft=lambda *a,**kw: {'passed': True, 'unit_fixture_only': True})
    monkeypatch.setattr(P,'skill_module',lambda *a:facade)
    return args,tmp_path,facade


def run(case):
    args, task, _ = case
    return S.validate(*args,task_dir=task,root=ROOT)


def test_actual_source_and_unchanged_scaffold(case):
    result=run(case)
    assert result['claim_count']==3 and len(result['warnings'])==3
    assert [x['formula']for x in result['source_formulas']]==['=Trades!D9','=Trades!E9','=Loans!E66']
    assert json.loads(Path(case[0][0]['path']).read_bytes())['passed'] is False


@pytest.mark.parametrize('index',range(7))
def test_every_evidence_hash_is_required(case,index):
    case[0][index]=dict(case[0][index],sha256='0'*64)
    with pytest.raises(ValueError,match='evidence'):run(case)


@pytest.mark.parametrize('fault',['missing_claim','contradicted','unknown_finding','extras','leak','wrong_scope','wrong_label'])
def test_no_substantive_finding_or_clause_change(case,monkeypatch,fault):
    args,tmp,_=case
    i=1 if fault in ('missing_claim','wrong_scope','wrong_label')else 0
    value=json.loads(Path(args[i]['path']).read_bytes())
    if fault=='missing_claim':value['claims'].pop()
    elif fault=='wrong_scope':value['claims'][0]['junior_scope']=['Summary!G31']
    elif fault=='wrong_label':value['claims'][0]['must_say'][1]='the row labelled "Net"'
    elif fault=='contradicted':value['accuracy']['claims'][0]['must_say']='contradicted'
    elif fault=='unknown_finding':value['naturalness']['findings'].append('Wrong source.')
    elif fault=='extras':value['accuracy']['extras']=['Unsupported assumption.']
    else:value['accuracy']['cell_refs_in_senior_turns']=['Summary!G30']
    raw=json.dumps(value).encode();p=tmp/'changed.json';p.write_bytes(raw);digest=hashlib.sha256(raw).hexdigest()
    args[i]=dict(path=str(p),sha256=digest)
    # Even a hypothetical re-pinned evidence artifact cannot bypass the
    # independent clause, entailment, finding, and scope checks.
    monkeypatch.setitem(S.HASHES,'claims' if i else 'review',digest)
    with pytest.raises(ValueError):run(case)


def test_failed_owning_mechanics_stays_blocking(case):
    case[2].check_draft=lambda *a,**kw:{'passed':False}
    with pytest.raises(ValueError,match='mechanics'):run(case)


def test_proof_revalidation_rejects_detached_input(case):
    proof=run(case);args=case[0]
    review=json.loads(Path(args[0]['path']).read_bytes());claims=json.loads(Path(args[1]['path']).read_bytes());draft=Path(args[2]['path']).read_text()
    assert S.revalidate(proof,review,claims,draft,root=ROOT)==proof
    review['passed']=True
    with pytest.raises(ValueError,match='inputs'):S.revalidate(proof,review,claims,draft,root=ROOT)

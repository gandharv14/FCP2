"""A grouped audit with one actual review keeps the atomic coverage floor."""
import copy
import pytest
from scripts.batch import harbor_grouped_plain_qualification as G


def case():
    rows=[dict(reference=f'S!{col}9',clean=True,cells=[['S',f'{col}9',value]])
          for col,value in zip('DEF',[13.0,14.0,15.0])]
    manifest=dict(candidate_count=3,candidate_references=sorted(r['reference'] for r in rows))
    review=dict(cells={r['reference']:dict(eligible=False,reason='Provided historical input; external provenance unestablished') for r in rows})
    return rows,manifest,review


def test_single_review_covers_three_atoms_in_one_grouped_audit_row():
    rows,m,r=case()
    assert G.single_coverage(rows,copy.deepcopy(rows),m,r,minimum=3)==dict(
        audit_atoms=3,unique_audited_references=3,reviewed_candidate_references=3,partitions=0)


@pytest.mark.parametrize('change',['value','missing_review','missing_preparation','count','positive','empty_reason','too_few','duplicate_atoms'])
def test_single_review_never_weakens_evidence_or_floor(change):
    rows,m,r=case();fresh=copy.deepcopy(rows)
    if change=='value':rows[0]['cells'][0][2]=99
    if change=='missing_review':r['cells'].pop('S!D9')
    if change=='missing_preparation':m['candidate_references'].pop()
    if change=='count':m['candidate_count']=2
    if change=='positive':r['cells']['S!D9']['eligible']=True
    if change=='empty_reason':r['cells']['S!D9']['reason']=''
    if change=='too_few':rows=rows[:2];fresh=copy.deepcopy(rows);m=dict(candidate_count=2,candidate_references=['S!D9','S!E9']);r['cells'].pop('S!F9')
    if change=='duplicate_atoms':
        rows=[copy.deepcopy(rows[0]) for _ in range(3)];fresh=copy.deepcopy(rows)
        m=dict(candidate_count=3,candidate_references=['S!D9']);r['cells']={k:v for k,v in r['cells'].items() if k=='S!D9'}
    with pytest.raises(ValueError):G.single_coverage(rows,fresh,m,r,minimum=3)


def test_explicit_qualification_is_validated_without_mutating_run(tmp_path,monkeypatch):
    import json
    import plain_eligibility as P
    run=tmp_path/'0564-variable-sources';run.mkdir()
    values={'draft.json':{'rows':[{'variable_text':'grouped actuals'}]},'normalized.json':{'variables':[]},
            'normalization_report.json':{'workbook':'0564','first_atomic_variables':0,
             'dispositions':[{'status':'excluded','reason':'Provided context'}]}}
    for name,value in values.items():(run/name).write_text(json.dumps(value))
    assert P.evaluate(run)['mode']=='fail'
    with pytest.raises(ValueError):P.evaluate(run,qualification={'path':'missing','sha256':'0'*64})
    receipt={'path':'bound receipt','sha256':'f'*64};seen=[]
    def validated(binding,*,run):
        assert binding==receipt;seen.append(run)
        return {'coverage':{'audit_atoms':3,'reviewed_candidate_references':3}}
    monkeypatch.setattr(G,'validate',validated)
    assert P.evaluate(run,qualification=receipt)['mode']=='plain'
    assert seen==[run] and not (run/G.SIDECAR).exists()
    assert P.evaluate(run,qualification=receipt,allow_grouped=False)['mode']=='fail'


@pytest.mark.parametrize('tamper',[None,'floor','numeric_prerequisite'])
def test_scoped_receipt_policy_rejects_unrelated_changes_and_restores_original(tmp_path,monkeypatch,tamper):
    import json
    import plain_eligibility as P
    from pathlib import Path
    from scripts.batch.harbor_admission import record
    current=Path(P.__file__).read_text()
    old=current.replace('def evaluate(run: Path, *, allow_grouped: bool = True, qualification: dict | None = None) -> dict:',
                        'def evaluate(run: Path, *, allow_grouped: bool = True) -> dict:')
    old=old.replace("    if draft_rows < floor and allow_grouped and (qualification is not None or (run / 'grouped-plain-qualification.json').exists()):\n        from scripts.batch.harbor_grouped_plain_qualification import validate, validate_sidecar\n        grouped = validate(qualification,run=run) if qualification is not None else validate_sidecar(run)",
                    "    if draft_rows < floor and allow_grouped and (run / 'grouped-plain-qualification.json').exists():\n        from scripts.batch.harbor_grouped_plain_qualification import validate_sidecar\n        grouped = validate_sidecar(run)")
    predecessor=tmp_path/'old.py';predecessor.write_text(old);selected=tmp_path/'new.py';selected.write_text(current)
    if tamper=='floor':selected.write_text(current.replace('MIN_DRAFT_ROWS = 3','MIN_DRAFT_ROWS = 1'))
    if tamper=='numeric_prerequisite':selected.write_text(current.replace('if forced:', 'if False:'))
    run=tmp_path/'0564-variable-sources';run.mkdir()
    for name,value in {'draft.json':{'rows':[{}]},'normalized.json':{'variables':[]},
        'normalization_report.json':{'workbook':'0564','dispositions':[{'status':'excluded','reason':'provided'}]}}.items():
        (run/name).write_text(json.dumps(value))
    proof={'run':str(run),'coverage':{'audit_atoms':3,'reviewed_candidate_references':3}}
    receipt=tmp_path/'qualification.json';receipt.write_text(json.dumps(proof))
    monkeypatch.setattr(G,'validate',lambda binding,*,run:proof)
    monkeypatch.setattr(P,'__file__',str(predecessor));previous=P.evaluate
    if tamper:
        with pytest.raises(ValueError,match='beyond explicit receipt'):
            with G.qualification_scope(record(receipt),record(selected)):pass
    else:
        with G.qualification_scope(record(receipt),record(selected)):
            assert P.evaluate(run)['mode']=='plain'
            assert P.evaluate(run,allow_grouped=False)['mode']=='fail'
        assert P.evaluate(run)['mode']=='fail'
    assert P.evaluate is previous

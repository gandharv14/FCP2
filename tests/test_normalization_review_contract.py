"""Pure reviewer contract parity and tiny real producer/executor regressions."""
import copy
import json
from pathlib import Path
import pytest
import gen_normalizer as gen
from scripts.batch import harbor_review_context as C
from scripts.batch import harbor_normalization_producer as N
from scripts.batch.harbor_admission import record
from test_harbor_normalization_producer import setup, mock_review


def values():
    evidence={'source_sha256':'a'*64,'draft_sha256':'b'*64,'baseline_sha256':'c'*64}
    rows=[{'reference':'Sheet!A1','clean':True,'evidence':evidence}]
    review=dict(evidence,schema_version='normalization-review/v1',reviewer='Independent reviewer',
        cells={'Sheet!A1':dict(eligible=True,reason='Supplied historical assumption',
            evidence_refs=['Sheet!A1','draft-1'],entity='Company',period='2024',scenario='Observed',basis='Workbook units')})
    return rows,review


@pytest.mark.parametrize('change',['valid','rejected_cell','schema_alias','schema_missing','reviewer','cells',
    'missing_cell','eligible','reason','evidence_refs','blank_evidence','entity','period','scenario','basis',
    'variable_name','source_sha256','draft_sha256','baseline_sha256','decision_type'])
def test_pure_contract_matches_unchanged_generator_accept_reject(tmp_path,change):
    rows,review=values();cell=review['cells']['Sheet!A1']
    if change=='rejected_cell':cell['eligible']=False
    elif change=='schema_alias':review['schema']=review.pop('schema_version')
    elif change=='schema_missing':review.pop('schema_version')
    elif change=='reviewer':review['reviewer']=' '
    elif change=='cells':review['cells']=[]
    elif change=='missing_cell':review['cells']={}
    elif change=='eligible':cell['eligible']=1
    elif change=='reason':cell['reason']=' '
    elif change=='evidence_refs':cell['evidence_refs']=[]
    elif change=='blank_evidence':cell['evidence_refs']=[' ']
    elif change in ('entity','period','scenario','basis'):cell.pop(change)
    elif change=='variable_name':cell['variable_name']=' '
    elif change in ('source_sha256','draft_sha256','baseline_sha256'):review[change]='0'*64
    elif change=='decision_type':review['cells']['Sheet!A1']=[]
    original=copy.deepcopy((rows,review));pure_ok=True
    try:C.validate_normalization_review(review,rows)
    except C.NormalizationReviewRejected:pure_ok=False
    assert (rows,review)==original
    (tmp_path/'normalization-review.json').write_text(json.dumps(review));owning_ok=True
    try:gen.apply_semantic_review(copy.deepcopy(rows),tmp_path)
    except (SystemExit,AttributeError,TypeError):owning_ok=False
    assert pure_ok==owning_ok==(change in ('valid','rejected_cell'))


def test_extra_cells_remain_forbidden_by_current_context_completeness_gate():
    rows,review=values();review['cells']['Sheet!B1']=review['cells']['Sheet!A1']
    with pytest.raises(C.NormalizationReviewRejected,match='exactly complete'):C.validate_normalization_review(review,rows)


def test_schema_alias_completed_response_is_model_failure_not_transport(setup,monkeypatch):
    selection,campaign,*_=setup();prepared=N.prepare(selection)
    mock_review(monkeypatch,selection,change=lambda r:r.update(schema=r.pop('schema_version')))
    executed=N.dispatch(selection,prepared,campaign,credential='fixture-unused')
    assert executed.status=='completed'
    prior={k:Path(getattr(executed.call,k)['path']).read_bytes() for k in ('request','submitted','response','completed','parsed')}
    with pytest.raises(C.NormalizationReviewRejected) as error:N.emit_phase1(selection,prepared,executed.call)
    assert error.value.failure_class=='invalid_model_content' and error.value.reason_code=='invalid_schema'
    assert error.value.semantic_attempt_consumed is True and error.value.source_invalidity_claimed is False
    assert error.value.automatic_retry is False
    assert not (selection.run/'normalized.json').exists()
    assert not (selection.run/'normalization-review.json').exists()
    assert all(Path(getattr(executed.call,k)['path']).read_bytes()==v for k,v in prior.items())
    assert N.read(executed.call.submitted)['semantic_ordinal']==1


def test_prompt_names_exact_required_schema_field():
    assert '"schema_version": "normalization-review/v1" (not "schema")' in N.PROMPT


def test_valid_review_does_not_change_generator_emission(setup,monkeypatch):
    selection,campaign,*_=setup();prepared=N.prepare(selection);mock_review(monkeypatch,selection)
    call=N.dispatch(selection,prepared,campaign,credential='fixture-unused').call
    draft,rows=N.analyse(selection);original=copy.deepcopy(rows)
    C.validate_normalization_review(N.read(call.parsed),rows);assert rows==original
    first=N.emit_phase1(selection,prepared,call)
    before={p.name:p.read_bytes() for p in selection.run.glob('*.json')}
    assert N.emit_phase1(selection,prepared,call)==first
    assert all((selection.run/name).read_bytes()==raw for name,raw in before.items())

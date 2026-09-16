from pathlib import Path
import json
import pytest
from xl_ast_graph import parse_formula
from scripts.fleet import retained_rejected_review as R


def test_subtracted_sum_retains_every_operand():
    assert R.sum_references(parse_formula('=+MAX(F52/F77-F52-SUM(F62,F64:F68),0)')) == ['F62','F64','F65','F66','F67','F68']


@pytest.mark.parametrize('formula',[
 '=MAX(F52+SUM(F62,F64:F68),0)',
 '=MAX(F52-SUM(F62,F64:F68),1)',
 '=MIN(F52-SUM(F62,F64:F68),0)',
 '=MAX(F52-SUM(F62,F62),0)',
 '=MAX(F52-SUM(F62,G64:G68),0)',
 '=MAX(F52-SUM(F62,F64:F168),0)',
 '=MAX(F52-SUM(F62,Other!F64:F68),0)',
 '=MAX(F52-SUM(F62,OFFSET(F64,0,0)),0)',
])
def test_other_source_meanings_never_authorize_correction(formula):
    with pytest.raises(ValueError):R.sum_references(parse_formula(formula))


def test_only_grouping_is_changed_in_generated_sentence():
    old,new=R.sentence_pair(['Beginning','Contribution','Distribution'])
    assert old=='Next, minus the row labelled "Beginning" plus the row labelled "Contribution", and the row labelled "Distribution".'
    assert new=='Next, subtract the combined sum of the row labelled "Beginning" plus the row labelled "Contribution" plus the row labelled "Distribution" from the preceding result.'


@pytest.mark.parametrize('labels',[['A','A','B'],['A','B'],['A','B',''],['A','B','C\nD']])
def test_ambiguous_source_labels_rejected(labels):
    with pytest.raises(ValueError):R.sentence_pair(labels)


def test_changed_and_symlinked_evidence_rejected(tmp_path):
    p=tmp_path/'proof.json';p.write_text('{}');b=R.binding(p);p.write_text('{"passed":true}')
    with pytest.raises(ValueError):R.read(b)
    link=tmp_path/'link.json';link.symlink_to(p)
    with pytest.raises(ValueError):R.binding(link)


def test_success_is_not_a_rejected_predecessor(tmp_path,monkeypatch):
    rejection=tmp_path/'rejection.json';rejection.write_text(json.dumps(dict(schema_version='harbor-completed-review-consumption-proof/v1',reader={},snapshot={},passed=True,round=2,model_calls_executed=0,publication_ready=False)))
    p=tmp_path/'plan.json';p.write_text(json.dumps(dict(schema_version=R.SCHEMA,helper=R.binding(Path(R.__file__).absolute()),reader={},rejection=R.binding(rejection),revision={},workbook='0061',source={},snapshot={})))
    monkeypatch.setattr(R,'module',lambda *_:pytest.fail('must reject before executing a reader'))
    with pytest.raises(ValueError,match='actual completed second-review rejection'):R.inspect(R.binding(p))

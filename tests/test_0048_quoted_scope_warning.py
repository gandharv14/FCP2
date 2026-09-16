"""Current-reader recognition of an exactly quoted, source-scoped junior header."""
import copy, importlib.util, json, sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
PRIVATE=ROOT/'diagnostics/authoring-queue-extension-20260911T185027Z/0048-warning-repair/code/.cursor/skills/additional-assumptions-dialogue/scripts/validate_dialogue.py'
@pytest.fixture
def v(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT/'.cursor/skills/task-disclosure/scripts'))
    monkeypatch.syspath_prepend(str(ROOT/'.cursor/skills/additional-assumptions-dialogue/scripts'))
    spec=importlib.util.spec_from_file_location('quoted_scope_validator',PRIVATE);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
@pytest.fixture
def actual():
    home=ROOT/'diagnostics/authoring-queue-extension-20260911T185027Z/authoring/0048-generated-02/instruction/producer/dialogue'
    return json.loads((home/'claims.json').read_text()),json.loads((home/'round-01/review.json').read_text()),(home/'round-01/draft.md').read_text(),(home/'draft_template.md').read_text()
def test_actual_six_claims_and_unchanged_frozen_header(v,actual):
    claims,review,draft,template=actual;old=copy.deepcopy(review)
    assert len(claims['claims'])==6
    assert all(row['must_say']=='entailed' for row in review['accuracy']['claims'])
    header='**Analyst:** Model: For `Model!E17`:'
    assert header in template and header in draft
    assert v.review_passed(review,claims,draft_text=draft)
    assert len(v.review_warnings(review,claims,draft_text=draft))==1
    assert review==old and review['passed'] is False
@pytest.mark.parametrize('fault',['missing','contradicted','unknown','wrong_actual_quote','wrong_required_quote','wrong_draft_scope','wrong_draft_sheet','extra','leak','omit_claim','duplicate_claim'])
def test_quoted_warning_remains_fail_closed(v,actual,fault):
    claims,review,draft,_=actual;row=review['accuracy']['claims'][0]
    if fault in ('missing','contradicted'):row['must_say']=fault
    elif fault=='unknown':row['findings'].append('The multiplication sign is wrong.')
    elif fault=='wrong_actual_quote':row['findings'][0]=row['findings'][0].replace('Model: For','Other: For')
    elif fault=='wrong_required_quote':row['findings'][0]=row['findings'][0].replace('required "For `Model!E17`:"','required "For `Model!E18`:"')
    elif fault=='wrong_draft_scope':draft=draft.replace('For `Model!E17`:','For `Model!E18`:')
    elif fault=='wrong_draft_sheet':draft=draft.replace('Model: For','Other: For')
    elif fault=='extra':review['accuracy']['extras']=['Missing required subtraction.']
    elif fault=='leak':review['accuracy']['cell_refs_in_senior_turns']=['Model!E17']
    elif fault=='omit_claim':review['accuracy']['claims'].pop()
    else:review['accuracy']['claims'].append(copy.deepcopy(row))
    assert not v.review_passed(review,claims,draft_text=draft)

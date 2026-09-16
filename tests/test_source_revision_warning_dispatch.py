"""Typed warning dispatch must not bypass its source proof or entailment gate."""
import importlib.util
import sys
from pathlib import Path
import pytest
from scripts.batch import harbor_dialogue_source_revision_warnings as revision
from scripts.batch import harbor_dialogue_source_warnings as ordinary


def validator():
    folder = Path(__file__).resolve().parents[1] / '.cursor/skills/additional-assumptions-dialogue/scripts'
    sys.path.insert(0, str(folder))
    try:
        spec = importlib.util.spec_from_file_location('warning_dispatch_validator', folder / 'validate_dialogue.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(folder))


def inputs():
    claims = {'claims': [{'record_id': 'r1'}]}
    review = dict(passed=False, accuracy=dict(verdict='fail',
        claims=[dict(record_id='r1', must_say='entailed', findings=[])],
        extras=[], cell_refs_in_senior_turns=[]), naturalness=dict(verdict='fail', findings=[]))
    return review, claims


@pytest.mark.parametrize('schema,target', [(revision.SCHEMA, revision), ('foreign-schema', ordinary)])
def test_exact_typed_reader_called_and_rejection_propagates(monkeypatch, schema, target):
    v = validator(); review, claims = inputs(); proof = {'schema_version': schema}
    def reject(p, r, c, text, *, root):
        assert p is proof and r is review and c is claims and text == 'unchanged draft'
        assert root == Path(v.__file__).resolve().parents[4]
        raise ValueError('source proof rejected')
    monkeypatch.setattr(target, 'revalidate', reject)
    with pytest.raises(ValueError, match='source proof rejected'):
        v.review_warnings(review, claims, draft_text='unchanged draft', source_warning_proof=proof)


def test_missing_entailment_cannot_use_derivative_warning(monkeypatch):
    v = validator(); review, claims = inputs()
    review['accuracy']['claims'][0]['must_say'] = 'contradicted'
    def forbidden(*args, **kwargs):
        raise AssertionError('substantive failure reached warning reader')
    monkeypatch.setattr(revision, 'revalidate', forbidden)
    assert v.review_warnings(review, claims, draft_text='draft',
        source_warning_proof={'schema_version': revision.SCHEMA}) == []

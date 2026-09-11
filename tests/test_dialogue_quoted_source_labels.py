"""Source identities survive labels containing embedded quotation marks."""
import importlib
from pathlib import Path

import pytest


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] /
        '.cursor/skills/additional-assumptions-dialogue/scripts'))
    return importlib.import_module('spoken_formula'), importlib.import_module('extract_claims')


def source_record():
    return dict(entry='source_selection', value='source',
        cells=["'Summary Annual P&L'!E10"],
        fields=dict(label="'(+) \"Go Get Revenue\"'",
            ingredient="cell '2022 P&L Monthly'!C21 on the row labelled '(+) Unidentified / \"Go Get\" Revenue'"))


def test_embedded_quotes_preserve_the_entire_source_and_target_label(modules):
    spoken, extract = modules
    record = source_record()
    text = spoken.speak_record(record)
    assert "the row labelled '(+) Unidentified / \"Go Get\" Revenue'" in text
    assert "The row labelled '(+) \"Go Get Revenue\"'" in text
    assert 'on the 2022 P&L Monthly tab' in text
    assert 'C21' not in text and 'E10' not in text
    claim = extract.build_claim(record, 1)
    assert "the row labelled '(+) Unidentified / \"Go Get\" Revenue'" in claim['must_say']
    assert "the row labelled '(+) \"Go Get Revenue\"'" in claim['must_say']
    assert 'the row labelled "Go Get"' not in claim['must_say']
    assert 'the 2022 P&L Monthly tab' in claim['must_say']


@pytest.mark.parametrize('labels', [
    ['Revenue'], ['Revenue', 'Cost'], ['Revenue', 'Cost', 'Margin'],
    ['Owner\'s revenue', 'The "Go Get" row'], ['Owner\'s "Go Get" revenue'],
])
def test_row_label_lists_round_trip_without_splitting_embedded_quotes(modules, labels):
    spoken, extract = modules
    phrase = spoken.named_rows(labels)
    assert spoken.all_labels(phrase)==labels
    atoms = extract.must_say_atoms(phrase)
    assert atoms == ['the row labelled '+spoken.quote_label(label) for label in labels]


@pytest.mark.parametrize('ingredient', [
    "cell '2022 P&L Monthly'!C21",  # no named source
    "cell '2022 P&L Monthly'!C21 on the row labelled 'unterminated",
    "cell '2022 P&L Monthly'!C21 on the row labelled 'Revenue' trailing instruction",
])
def test_incomplete_or_extra_source_identity_is_still_rejected(modules, ingredient):
    spoken, _ = modules
    record=source_record();record['fields']['ingredient']=ingredient
    with pytest.raises(ValueError, match='complete named source reference'):
        spoken.speak_record(record)

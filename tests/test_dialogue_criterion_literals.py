"""Formula string arguments cannot become worksheet row identities."""
import importlib
from pathlib import Path

import pytest


@pytest.fixture
def modules(monkeypatch):
    import sys
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] /
        '.cursor/skills/additional-assumptions-dialogue/scripts'))
    for name in ('spoken_formula', 'extract_claims'):
        monkeypatch.delitem(sys.modules, name, raising=False)
    return importlib.import_module('spoken_formula'), importlib.import_module('extract_claims')


@pytest.mark.parametrize('row,label', [
    (120, '(x) Avg. # of Full Time Employee ("FTE") Count Per Portfolio Company'),
    (121, '(x) Uptake % Rate for Next Gen Talent Solutions'),
    (128, '(x) Avg. # of Managers Per Portfolio Company'),
    (129, '(x) Uptake % Rate for Community Engagement Platform'),
])
def test_actual_averageif_argument_keeps_its_literal_type(modules,row,label):
    spoken,extract=modules
    quoted=spoken.quote_label(label)
    steps=(f'use (averageif applied to range \'P&L Projection\'!AI{row}:AT{row} '
           f'on the row labelled {quoted}, "<>0"), or use (0) if that calculation errors')
    formula=f'=IFERROR(AVERAGEIF(AI{row}:AT{row},"<>0"),0)'
    record=dict(entry='method_revenue',value='out_of_catalogue',
        cells=[f"'P&L Projection'!G{row}"],evidence=formula,
        fields=dict(label=quoted,representative=f"'P&L Projection'!G{row}",
            band=f"'P&L Projection'!G{row}",calculation_kind='single-cell calculation',steps=steps))
    phrases=spoken.collect_phrases(steps)
    assert len(phrases)==1 and phrases[0]['labels']==[label]
    card=extract.build_claim(record,1)
    assert 'the row labelled "<>0"' not in card['must_say']
    assert 'the text literal "<>0"' in card['must_say']
    assert 'the row labelled '+quoted in card['must_say']
    assert card['reviewer_only']['evidence']==formula
    assert '"<>0"' in card['spoken']
    assert 'average' in card['must_say'] and 'errors' in card['must_say']


def test_real_same_spelling_row_is_not_dropped(modules):
    spoken,extract=modules
    text='range Sheet1!A1:A4 on the row labelled "<>0", "criterion"'
    assert spoken.collect_phrases(text)[0]['labels']==['<>0']
    assert extract.must_say_atoms('the row labelled "<>0", "criterion"')==['the row labelled "<>0"']


def test_explicit_plural_source_labels_still_preserved(modules):
    spoken,extract=modules
    text='range Sheet1!A1:A4 on the rows labelled "Sales", "Costs", and "Margin"'
    assert spoken.collect_phrases(text)[0]['labels']==['Sales','Costs','Margin']
    assert extract.must_say_atoms(text)==['the row labelled "Sales"','the row labelled "Costs"','the row labelled "Margin"']

from pathlib import Path
import importlib.util
import pytest
P=Path(__file__).resolve().parents[1]/'.cursor/skills/additional-assumptions-dialogue/scripts/aa_lib.py'
s=importlib.util.spec_from_file_location('short_label_coverage',P);M=importlib.util.module_from_spec(s);s.loader.exec_module(M)

@pytest.mark.parametrize('text',["Divide this period's row labelled \"BK\" by the rate.","Take last period's row labelled \"BK\".","Use the row labelled \"BK\"."])
def test_exact_short_source_label_survives_possessive_prose(text):
    assert M.clause_covered('the row labelled "BK"',text)

@pytest.mark.parametrize('text',['Use row labelled "BB".','Use row labelled "BKX".','Use BK4.','Use "BK" as a text literal.','No named row here.'])
def test_foreign_short_label_is_not_coverage(text):
    assert not M.clause_covered('the row labelled "BK"',text)


def test_exact_escaped_row_label_is_preserved():
    assert M.clause_covered('the row labelled "A\\nB"','This period\'s row labelled "A\\nB".')

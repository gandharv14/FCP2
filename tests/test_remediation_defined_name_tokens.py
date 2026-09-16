import pytest
from xl_volatile_formula_remediation import _name_tokens, _defined_name_plan


@pytest.mark.parametrize('formula',[
    'XIRR($I$17:$I$148,$B$17:$B$148)',
    "XNPV($E$14,'CF Schedule'!$K$17:$K$148,'CF Schedule'!$B$17:$B$148)",
    "'Ratio Assumptions'!$C$74*'P&L Monthly Output'!C15",
    "'P''s B sheet'!B$1+$B2+B$3+$B$4",
])
def test_cell_and_quoted_sheet_text_are_not_defined_names(formula):
    assert _name_tokens(formula,{'b','p','c','cf','sheet','ratio'})==set()


@pytest.mark.parametrize('formula,expected',[
    ('B+$B$17',{'b'}),
    ("'P&L Monthly Output'!B+$B$17",{'b'}),
    ('SUM(B,P,Z)',{'b','p','z'}),
    ('SUM(XFE1,XFD1048577,$XFD$1048576)',{'xfe1','xfd1048577'}),
    ('_B17+B17_name+prefix.B17',{'_b17','b17_name','prefix.b17'}),
    ('"B"&"P"&B',{'b'}),
    ('SUM(B:B)',{'b'}),  # Unchanged conservative handling of whole columns.
])
def test_real_or_unresolved_name_dependencies_remain_live(formula,expected):
    assert _name_tokens(formula,expected|{'p','b','z','xfd'})==expected


def xml():
    return b'<workbook><definedNames><definedName name="B">OFFSET(#REF!,0,0)</definedName><definedName name="P">OFFSET(#REF!,0,0)</definedName><definedName name="Alias">B</definedName></definedNames></workbook>'


def test_only_proven_dead_names_can_be_removed():
    dead,unknown=_defined_name_plan(xml(),["'P&L Output'!$B$17"])
    assert {r['name']for r in dead}=={'B','P'} and unknown==[]
    dead,unknown=_defined_name_plan(xml(),["'P&L Output'!Alias+$B$17"])
    assert {r['name']for r in dead}=={'P'}
    assert [r['name']for r in unknown]==['B']

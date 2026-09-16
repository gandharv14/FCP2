from xml.etree import ElementTree as E
import pytest
from xl_volatile_formula_remediation import _defined_name_plan


def book(name='YEAR',value='[3]MAIN!$H$12',local=None,extra=None):
    root=E.Element('workbook');names=E.SubElement(root,'definedNames');attrs={'name':name}
    if local is not None:attrs['localSheetId']=local
    E.SubElement(names,'definedName',attrs).text=value
    if extra:E.SubElement(names,'definedName',{'name':'Other'}).text=extra
    return E.tostring(root)


@pytest.mark.parametrize('formula', ['YEAR(A1)', 'year ( A1 )', 'IF(YEAR(A1)=2026,YEAR(B1),0)'])
def test_builtin_calls_do_not_keep_unrelated_external_name(formula):
    actions, unresolved=_defined_name_plan(book(),[formula])
    assert len(actions)==1 and actions[0]['name']=='YEAR' and not unresolved


@pytest.mark.parametrize('formula', ['YEAR', 'YEAR+YEAR(A1)', 'Sheet!YEAR', "'Sheet'!YEAR(A1)", 'INDIRECT(A1)', 'EVALUATE(A1)'])
def test_explicit_qualified_or_dynamic_name_use_stays_blocking(formula):
    actions, unresolved=_defined_name_plan(book(),[formula])
    if formula in ['INDIRECT(A1)','EVALUATE(A1)']:
        # Add a built-in call: uncertain dynamic resolution disables the exemption.
        actions, unresolved=_defined_name_plan(book(),[formula,'YEAR(A1)'])
    assert not actions and unresolved


def test_local_and_unknown_callable_names_are_not_disambiguated():
    for xml,formula in [(book(local='0'),'YEAR(A1)'),(book(name='CUSTOM'),'CUSTOM(A1)'),(book(value='LAMBDA(x,[3]MAIN!$H$12)'),'YEAR(A1)')]:
        actions, unresolved=_defined_name_plan(xml,[formula])
        assert not actions and unresolved


def test_other_name_reference_keeps_year_even_beside_calls():
    actions, unresolved=_defined_name_plan(book(extra='YEAR'),['YEAR(A1)','Other'])
    assert not actions and unresolved

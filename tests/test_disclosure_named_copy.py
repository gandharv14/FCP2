"""Named operands stay symbolic while A1 copy references move."""
from types import SimpleNamespace
from test_disclosure_copied_scope import disclose as D


def book(*formulas):
    cells=['Debt Schedule!'+c+'20' for c in ('F','G','H')[:len(formulas)]]
    return SimpleNamespace(formula=dict(zip(cells,formulas))),cells


def test_circ_copy_distinguishes_named_operand_from_moving_cells():
    g,c=book('=IF(Circ=1,(($D$14-AVERAGE(F16,F18))*$D$13)/12,0)',
             '=IF(Circ=1,(($D$14-AVERAGE(G16,G18))*$D$13)/12,0)',
             '=IF(Circ=1,(($D$14-AVERAGE(H16,H18))*$D$13)/12,0)')
    text=D.copy_lock_note(g,c)
    assert 'every other cell reference shifts' in text
    assert 'named reference "Circ" retains the same name throughout the band' in text
    assert 'every other reference shifts' not in text
    assert 'fixed value' not in text and 'Circ stays fixed' not in text


def test_last_cell_changed_name_cannot_claim_same_named_operand():
    g,c=book('=Circ+$D$14+F16','=Circ+$D$14+G16','=Other+$D$14+H16')
    assert D._copied_named_operands(g,c)==[]


def test_relative_named_definition_is_not_claimed_absolute():
    g,c=book('=RelativeName+F16','=RelativeName+G16')
    text=D.copy_lock_note(g,c)
    assert 'same name throughout the band' in text
    assert 'same cell' not in text and 'stays fixed' not in text


def test_strings_functions_and_plain_cells_do_not_change_existing_note():
    g,c=book('=IF(F16="Circ",$D$14,0)','=IF(G16="Circ",$D$14,0)')
    assert D.copy_lock_note(g,c)==D._cell_copy_lock_note(g,c)
    assert D._copied_named_operands(g,c)==[]


def test_qualified_name_preserved_but_structured_reference_not_guessed():
    g,c=book("='Debt Schedule'!Circ+$D$14+F16","='Debt Schedule'!Circ+$D$14+G16")
    assert D._copied_named_operands(g,c)==["'Debt Schedule'!Circ"]
    g,c=book('=Table1[Value]+F16','=Table1[Value]+G16')
    assert D._copied_named_operands(g,c)==[]


def test_duplicate_names_are_listed_once_and_numeric_changes_block_claim():
    g,c=book('=Circ+Circ+$D$14+F16','=Circ+Circ+$D$14+G16')
    assert D._copied_named_operands(g,c)==['Circ']
    g.formula[c[1]]='=Circ+Circ+$D$14+G16+1'
    assert D._copied_named_operands(g,c)==[]

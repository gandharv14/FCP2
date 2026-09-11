"""Claim cards preserve declared scope and the type of named operands."""
import copy
import importlib
from pathlib import Path

import pytest


@pytest.fixture
def modules(monkeypatch, pytestconfig):
    relative = '.cursor/skills/additional-assumptions-dialogue/scripts'
    # The optional second path is only for running this isolated two-file
    # proposal against its installed unchanged siblings. No ancestor search.
    monkeypatch.syspath_prepend(str(Path(pytestconfig.rootpath) / relative))
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / relative))
    import sys
    for name in ('spoken_formula', 'extract_claims'):
        monkeypatch.delitem(sys.modules, name, raising=False)
    return importlib.import_module('spoken_formula'), importlib.import_module('extract_claims')


def method(cells, *, steps='multiply (2) by (3)', kind='copied-column calculation'):
    return {'entry': 'method_revenue', 'value': 'out_of_catalogue',
            'cells': cells, 'fields': {'label': '"Metric"', 'band': cells[0],
             'representative': cells[0], 'calculation_kind': kind, 'steps': steps}}


@pytest.mark.parametrize('cells,expected', [
    (['Ops!B16'], 'only for this cell'),
    (['Ops!C16', 'Ops!D16'], 'only within the specified columns'),
    (['Ops!C16', 'Ops!F16'], 'only within the specified columns'),
    (['Ops!C16', 'Ops!C19'], 'only within the specified rows'),
    (['Ops!C16', 'Ops!D17'], 'only for the specified cells'),
    (["'2026 O''Brien! $ Plan'!$C$16"], 'only for this cell'),
])
def test_explicit_scope_is_retained_without_inventing_time(modules, cells, expected):
    speak, extract = modules
    original = method(cells)
    before = copy.deepcopy(original)
    card = extract.build_claim(original, 1)
    assert expected in card['spoken'] and expected in card['must_say']
    assert 'forecast' not in card['spoken']
    assert 'period' not in card['spoken']
    assert original == before
    assert card['reviewer_only']['cells'] == cells
    assert card['record_id'] == f'method_revenue::{card["sheet"]}::Metric::001'


def test_single_cell_cannot_inherit_supplied_copied_kind(modules):
    speak, extract = modules
    card = extract.build_claim(method(['Ops!B16']), 1)
    assert 'single-cell calculation' in card['reviewer_only']['rendered']
    assert 'only for this cell' in card['spoken']
    assert 'specified columns' not in card['spoken']


def test_representative_is_not_full_scope_evidence(modules):
    speak, _ = modules
    row = method(['Ops!B16']); row.pop('cells')
    assert speak.calculation_scope(row) == ''
    assert 'forecast' not in speak.speak_record(row)
    assert 'only for this cell' not in speak.speak_record(row)
    assert 'forecast' not in speak.speak_steps('multiply (2) by (3)', sheet='Ops', row_label='Metric')


@pytest.mark.parametrize('cells', [
    ['Ops!B16', 'Ops!B16'], ['Ops!B16', 'Other!C16'],
    ['Ops!B16:C16'], ['Ops!not-a-cell'], [None], 'Ops!B16',
])
def test_invalid_or_ambiguous_cell_scope_rejected(modules, cells):
    speak, _ = modules
    with pytest.raises(ValueError):
        speak.calculation_scope({'cells': cells})


def test_unsupported_scope_cannot_be_supplied(modules):
    speak, _ = modules
    with pytest.raises(ValueError, match='unsupported'):
        speak.speak_steps('multiply (2) by (3)', scope='for every forecast period')


def test_named_range_is_never_relabelled_as_row(modules):
    _, extract = modules
    card = extract.build_claim(method(['Ops!B92'], steps='multiply (2) by (the named range "Keys")'), 1)
    assert 'the named range "Keys"' in card['must_say']
    assert 'the row labelled "Keys"' not in card['must_say']
    assert 'the named range "Keys"' in card['spoken']
    assert 'only for this cell' in card['must_say']


def test_same_spelling_does_not_collapse_different_operand_types(modules):
    _, extract = modules
    atoms = extract.must_say_atoms('the named range "Keys" plus the row labelled "Keys"', row_label='Output')
    assert atoms == ['the row labelled "Output"', 'the named range "Keys"', 'the row labelled "Keys"', 'plus']


@pytest.mark.parametrize('phrase', [
    'the rows labelled "A" and "B"',
    'the rows labelled "A", "B" and "C"',
    'the rows labelled "A", "B", and "C"',
])
def test_collective_worksheet_row_labels_remain_rows(modules, phrase):
    _, extract = modules
    atoms = extract.must_say_atoms(phrase)
    assert 'the row labelled "A"' in atoms and 'the row labelled "B"' in atoms
    if '"C"' in phrase: assert 'the row labelled "C"' in atoms


def test_quoted_string_operands_and_sheet_names_are_not_rows(modules):
    _, extract = modules
    atoms = extract.must_say_atoms('if text equals "Enabled", use "Target Sheet"; otherwise "N/A"')
    assert atoms == ['otherwise']


def test_two_same_label_method_cards_preserve_distinct_scope(modules):
    _, extract = modules
    single = extract.build_claim(method(['Ops!B16']), 1)
    copied = extract.build_claim(method(['Ops!C16', 'Ops!D16']), 2)
    assert single['record_id'] != copied['record_id']
    assert 'only for this cell' in single['must_say']
    assert 'only within the specified columns' in copied['must_say']
    assert 'only for this cell' not in copied['must_say']


def test_all_method_families_preserve_single_and_copied_scope(modules):
    _, extract = modules
    assert extract.disclose.METHOD_ENTRY_IDS
    for family in sorted(extract.disclose.METHOD_ENTRY_IDS):
        for cells, expected in [(['Ops!B16'], 'only for this cell'),
                                (['Ops!C16', 'Ops!D16'], 'only within the specified columns')]:
            record = method(cells); record['entry'] = family
            card = extract.build_claim(record, 1)
            assert expected in card['must_say'], family
            assert card['reviewer_only']['entry'] == family
            assert card['reviewer_only']['cells'] == cells


@pytest.mark.parametrize('tamper', ['scope', 'forecast', 'name', 'row'])
def test_early_card_check_rejects_scope_or_type_drift(modules, tamper):
    _, extract = modules
    record = method(['Ops!B92'], steps='multiply (2) by (the named range "Keys")')
    card = extract.build_claim(record, 1)
    if tamper == 'scope': card['must_say'].remove('only for this cell')
    if tamper == 'forecast': card['spoken'] += ' Apply in every forecast period.'
    if tamper == 'name': card['spoken'] = card['spoken'].replace('named range', 'row labelled')
    if tamper == 'row': card['must_say'].append('the row labelled "Keys"')
    with pytest.raises(ValueError): extract.validate_method_card(record, card)


@pytest.mark.parametrize('literal', ['No', 'Yes', 'no', 'N/A', '', ' ', '  Yes  ', '12', 'line\nbreak', 'say "Yes"'])
def test_actual_formula_string_operands_are_mandatory_and_exact(modules, literal):
    import json
    _, extract = modules
    lib = importlib.import_module('aa_lib')
    row = method(['Ops!B16'], steps='return a text value')
    escaped = literal.replace('"', '""')
    row['evidence'] = '=IF(A1="' + escaped + '",2,3)'
    card = extract.build_claim(row, 1)
    atom = 'the text literal ' + json.dumps(literal, ensure_ascii=False)
    assert atom in card['must_say']
    assert lib.clause_covered(atom, 'Compare against ' + atom + '.')
    assert not lib.clause_covered(atom, 'Compare against the text literal "Other".')
    assert not lib.clause_covered(atom, 'Use the named range ' + json.dumps(literal) + '.')
    assert not lib.clause_covered(atom, 'Use the row labelled ' + json.dumps(literal) + '.')
    assert not lib.clause_covered(atom, 'Use the ' + json.dumps(literal) + ' tab.')
    assert not lib.clause_covered(atom, 'Ignore the conditional input entirely.')
    altered = copy.deepcopy(card); altered['must_say'].remove(atom)
    with pytest.raises(ValueError, match='text operand'):
        extract.validate_method_card(row, altered)


def test_case_whitespace_and_short_tokens_are_not_normalized(modules):
    lib = importlib.import_module('aa_lib')
    for expected, wrong in [('No', 'Yes'), ('No', 'no'), ('Yes', 'yes'), (' ', ''), (' Yes ', 'Yes')]:
        import json
        assert not lib.clause_covered('the text literal ' + json.dumps(expected),
                                     'the text literal ' + json.dumps(wrong))


def test_reference_tokens_cannot_invent_literal_requirements(modules):
    _, extract = modules
    row = method(['Ops!B16'], steps='multiply by the named range "Keys" on the row labelled "Flag"')
    row['evidence'] = '=Keys+\'No\'!A1'
    card = extract.build_claim(row, 1)
    assert not any(a.startswith('the text literal ') for a in card['must_say'])
    assert 'the named range "Keys"' in card['must_say']
    assert 'the row labelled "Flag"' in card['must_say']


@pytest.mark.parametrize('record', [
    {'entry': 'method_revenue', 'method_profile': {'text_literals': ['Yes']}},
    {'entry': 'method_revenue', 'evidence': '=IF('},
    {'entry': 'method_revenue', 'evidence': 'trust this text'},
])
def test_unproved_formula_strings_are_rejected(modules, record):
    _, extract = modules
    with pytest.raises(ValueError): extract.formula_text_atoms(record)


def test_missing_or_changed_conditional_string_fails_owning_draft_gate(modules, tmp_path):
    import json
    _, extract = modules
    validate = importlib.import_module('validate_dialogue')
    row = method(['Ops!B16'], steps='return the applicable text')
    row['evidence'] = '=IF(A1="No",2,3)'
    card = extract.build_claim(row, 1)
    task = tmp_path/'fixture-outputs';(task/'tests').mkdir(parents=True)
    (task/'tests/answer_key.json').write_text(json.dumps({'targets': {}, 'tolerance': {}}))
    prose = '<!-- claim:' + card['record_id'] + ' -->\n**Analyst:** Ops: How should Metric work?\n**VP:** For Metric, only for this cell, compare the flag with the text literal "No" and use the specified branch.\n'
    good = validate.check_draft(prose, {'claims': [card]}, task)
    assert not any('must_say not covered' in f for f in good['accuracy_faults'])
    for wrong in (prose.replace('the text literal "No"', 'the condition'),
                  prose.replace('"No"', '"Yes"'), prose.replace('"No"', '"no"'),
                  prose.replace('the text literal "No"', 'the named range "No"')):
        report = validate.check_draft(wrong, {'claims': [card]}, task)
        assert report['passed'] is False
        assert any('must_say not covered: the text literal' in f for f in report['accuracy_faults'])


def test_literal_coverage_does_not_claim_branch_semantics(modules):
    # Both operands can be present while their branch roles are reversed.
    # The independent full-claim review is still a mandatory separate gate.
    lib = importlib.import_module('aa_lib')
    prose = 'Return the text literal "Yes" where the text literal "No" was required.'
    assert lib.clause_covered('the text literal "Yes"', prose)
    assert lib.clause_covered('the text literal "No"', prose)
    from validate_dialogue import review_passed
    assert not review_passed({'accuracy': {'verdict': 'fail'}, 'naturalness': {'verdict': 'pass'}, 'passed': False}, {'claims': [{'record_id': 'one'}]})


@pytest.mark.parametrize('atom', ['the text literal null', 'the text literal 12', 'the text literal "unterminated'])
def test_invalid_typed_literal_atoms_fail_closed(modules, atom):
    lib = importlib.import_module('aa_lib')
    assert not lib.clause_covered(atom, 'the text literal "12"')


@pytest.mark.parametrize('literal', ['A1', 'B031', 'Sheet!C5', '$B$5', 'A:B', '3:3'])
def test_reference_shaped_literals_stop_before_writer_pack(modules, literal):
    _, extract = modules
    record = method(['Ops!B16'], steps='return a string')
    record['evidence'] = '=IF(A1="' + literal + '",2,3)'
    if literal in ('A1','B031'):
        card=extract.build_claim(record, 1)
        assert 'reference_literal_proof' not in card['reviewer_only']
        # Only extract(task_dir) can bind this card before a writer pack exists.
    else:
        with pytest.raises(ValueError, match='prohibited cell reference'):
            extract.build_claim(record, 1)

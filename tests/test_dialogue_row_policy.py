"""Row policy identities must not be mistaken for calculation cells."""
import copy

import pytest

from test_dialogue_scope_names import modules, method


def policy(reference="'City Road'!row 86"):
    return dict(entry='row_populated', value='unused', cells=[reference],
                fields={'label': '"Unused expense"'})


def test_unused_row_retains_exact_junior_scope(modules):
    _, extract = modules
    record = policy(); before = copy.deepcopy(record)
    claim = extract.build_claim(record, 1)
    assert record == before
    assert claim['junior_scope'] == ["'City Road'!row 86"]
    assert claim['reviewer_only']['cells'] == claim['junior_scope']
    assert '!row' not in claim['spoken']
    assert 'Unused expense' in claim['spoken']


@pytest.mark.parametrize('reference', ['Ops!row 0', 'Ops!row -1',
    'Ops!row 1048577', 'Ops!row 86:90', '!row 86', 'Ops!row 86 extra'])
def test_malformed_row_identity_is_rejected(modules, reference):
    _, extract = modules
    with pytest.raises(ValueError):
        extract.build_claim(policy(reference), 1)


def test_row_exception_does_not_weaken_calculation_scope(modules):
    _, extract = modules
    with pytest.raises(ValueError):
        extract.build_claim(method(['Ops!row 86']), 1)


def test_multiple_rows_are_not_silently_widened(modules):
    _, extract = modules
    record = policy(); record['cells'].append("'City Road'!row 87")
    with pytest.raises(ValueError):
        extract.build_claim(record, 1)

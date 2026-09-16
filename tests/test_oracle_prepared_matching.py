"""Differential leak-detection tests against the retained public matcher."""
import datetime as dt
from decimal import Decimal

import openpyxl
import pytest

import xl_mcp_oracle as O


VALUES = [None, True, False, 0, 1, -1, 0.25, 25, 1234.5,
          1.0000000005, 1.000000002, float('inf'), float('-inf'), float('nan'),
          Decimal('1.25'), 'TRUE', ' false ', '1', '1.0', '25%', ' 25 % ',
          '1,234.50', '(1,234.50)', '$1,234.50', '-0.25', 'not a date',
          ' Revenue  Growth ', 'revenue growth', '', '2026-09-12',
          '09/12/2026', '12-Sep-2026', 'September 12, 2026',
          '2026-02-30', dt.date(2026, 9, 12), dt.datetime(2026, 9, 12, 3, 4),
          [], {'not': 'a scalar'}]


def test_all_typed_and_displayed_pairs_match_reference():
    targets = [O.ValueTarget(str(i), raw, mcp, '')
               for i, (raw, mcp) in enumerate(zip(VALUES, VALUES[7:] + VALUES[:7]))]
    targets += [O.ValueTarget('same-'+str(i), value, value, '') for i, value in enumerate(VALUES)]
    for target in targets:
        prepared = O._PreparedTarget.prepare(target)
        for value in VALUES:
            assert prepared.matches(O._value_signature(value)) == O.value_matches_target(value, target), (value, target)


@pytest.mark.parametrize('scale', [1, 100, 1000000000])
def test_tolerance_boundary_and_percent_representations(scale):
    target = O.ValueTarget('target', Decimal(scale), Decimal(scale)/100, '%')
    prepared = O._PreparedTarget.prepare(target)
    for offset in ['-0.000000002', '-0.000000001', '0', '0.000000001', '0.000000002']:
        value = Decimal(scale) * (1 + Decimal(offset))
        for form in (value, str(value), str(value)+'%'):
            assert prepared.matches(O._value_signature(form)) == O.value_matches_target(form, target)


def test_full_scan_report_including_allowlist_matches_reference(tmp_path, monkeypatch):
    path = tmp_path/'delivered.xlsx'
    book = openpyxl.Workbook(); sheet = book.active; sheet.title = 'Model'
    values = [True, False, 0.25, '25%', 'TRUE', '2026-09-12',
              dt.datetime(2026, 9, 12), 'Revenue growth', 1234.5, '$1,234.50',
              'unrelated', '=1+2', 1.0000000005, 1.000000002]
    for i, value in enumerate(values, 1): sheet.cell(i, 2, value)
    book.save(path)
    audit = [dict(variable_id=str(i), refs=['Model!A'+str(i+1)],
                  cell_value=value, mcp_value=value, unit='')
             for i, value in enumerate([True, 0.25, '2026-09-12', 'revenue growth', 1234.5, 1])]
    refs = [entry['refs'][0] for entry in audit]
    allowlist = {('Model!B4', '1'): 'approved percent rendering', ('Model!Z99', '1'): 'stale entry'}
    actual = O.check_workbook(path, refs, audit, allowlist)
    class ReferenceTarget:
        def __init__(self, target): self.target = target
        def matches(self, value): return O.value_matches_target(value, self.target)
    monkeypatch.setattr(O._PreparedTarget, 'prepare', ReferenceTarget)
    monkeypatch.setattr(O, '_value_signature', lambda value: value)
    assert actual == O.check_workbook(path, refs, audit, allowlist)
    assert not actual['valid']


def test_parsing_cost_is_per_value_and_target_not_cartesian_product(monkeypatch):
    count = 0; original = O._as_date
    def counted(value):
        nonlocal count
        count += 1
        return original(value)
    monkeypatch.setattr(O, '_as_date', counted)
    targets = [O._PreparedTarget.prepare(O.ValueTarget(str(i), i, i, '')) for i in range(181)]
    values = ['not a date', '25%', 0.25, dt.date(2026, 9, 12)]
    for value in values:
        signature = O._value_signature(value)
        for target in targets: target.matches(signature)
    assert count == 2 * len(targets) + len(values)

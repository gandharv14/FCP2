import pytest

import xl_output_task as task


def test_mcp_instructions_do_not_invent_removed_input_categories_or_provenance():
    # A task may retrieve only historical cash flows supplied by the workbook.
    # The generic scaffold must not claim it instead needs external market,
    # tax, or contractual assumptions, or that no calculation can begin yet.
    scenario = task.scenario_for('0001', 'lbo', [{'name': 'Equity return'}], mcp=True)
    prose = '\n'.join([scenario, task.INPUT_SECTION_MCP, task.RESEARCH_SECTION])
    for unsupported in ('externally-sourced', 'market rates', 'tax rates',
                        'macro assumptions', 'contractual terms', 'before anything can be computed'):
        assert unsupported not in prose
    assert 'Retrieve those inputs through the research data service' in scenario
    assert 'provenance and financial' in task.RESEARCH_SECTION


@pytest.mark.parametrize('sheet,expected', [
    ('Model', 'Model'), ('5Year', "'5Year'"), ('A B', "'A B'"),
    ("O'Brien", "'O''Brien'"), ('Scenario!A', "'Scenario!A'"),
])
def test_target_reference_quotes_preserve_sheet_identity(sheet, expected):
    assert task.quote_sheet(sheet) == expected
    from test_task_disclosure import _load_disclose
    assert _load_disclose().parse_ref(task.quote_sheet(sheet) + '!$B$12', 'Default') == sheet + '!B12'

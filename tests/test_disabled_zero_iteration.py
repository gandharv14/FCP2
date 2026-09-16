"""An unused zero iteration count must not fabricate an iterative budget."""
import pytest
from test_source_health import _workbook
from xl_seg.evaluate import workbook_calculation_metadata
from xl_source_health import inspect_workbook


@pytest.mark.parametrize('iterate', ['', 'iterate="0"', 'iterate="false"'])
def test_disabled_zero_count_preserves_explicit_metadata_and_source(tmp_path, iterate):
    source=tmp_path/'4001.xlsx'
    _workbook(source,calc=f'calcMode="auto" iterateCount="0" {iterate}')
    original=source.read_bytes()
    metadata=workbook_calculation_metadata(source)
    assert metadata.iterate is False
    assert metadata.iterate_count == 0
    assert metadata.iterate_count_origin == 'explicit'
    assert metadata.raw_calc_pr['iterateCount'] == '0'
    assert inspect_workbook(source)['route'] == 'pass'
    assert source.read_bytes() == original


@pytest.mark.parametrize('iterate', ['iterate="1"','iterate="true"','iterate="maybe"'])
def test_zero_does_not_authorize_enabled_or_unknown_iteration(tmp_path, iterate):
    source=tmp_path/'4001.xlsx'
    _workbook(source,calc=f'calcMode="auto" iterateCount="0" {iterate}')
    metadata=workbook_calculation_metadata(source)
    assert metadata.iterate_count is None
    assert metadata.iterate_count_origin == 'unknown'
    assert inspect_workbook(source)['route'] == 'insufficient_evidence'


@pytest.mark.parametrize('count',['-1','1000000000000','nope'])
def test_disabled_iteration_does_not_accept_malformed_counts(tmp_path,count):
    source=tmp_path/'4001.xlsx'
    _workbook(source,calc=f'calcMode="auto" iterateCount="{count}" iterate="false"')
    assert workbook_calculation_metadata(source).iterate_count_origin == 'unknown'
    assert inspect_workbook(source)['route'] == 'insufficient_evidence'

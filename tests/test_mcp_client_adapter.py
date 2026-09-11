from types import SimpleNamespace
from dataclasses import dataclass

import pytest

from xl_mcp_oracle import _tool_data


class StructuredResponse:
    def model_dump(self, *, mode):
        assert mode == 'json'
        return {'rows': [{'value': 7}], 'next_cursor': None}


@dataclass(slots=True)
class Root:
    id: str
    metadata: dict


def test_pydantic_style_tool_response_preserves_structured_data():
    assert _tool_data(SimpleNamespace(data=StructuredResponse(), is_error=False)) == {
        'rows': [{'value': 7}], 'next_cursor': None}
    raw = {'rows': []}
    assert _tool_data(SimpleNamespace(data=raw)) == raw
    assert _tool_data(raw) == raw
    assert _tool_data(SimpleNamespace(data=[StructuredResponse()])) == [
        {'rows': [{'value': 7}], 'next_cursor': None}]
    assert _tool_data(SimpleNamespace(data=[Root('s1', {'payload': StructuredResponse()})])) == [
        {'id': 's1', 'metadata': {'payload': {'rows': [{'value': 7}], 'next_cursor': None}}}]


@pytest.mark.parametrize('field', ['is_error', 'isError'])
def test_error_result_never_unwrapped_as_success(field):
    with pytest.raises(RuntimeError, match='returned an error'):
        _tool_data(SimpleNamespace(data=StructuredResponse(), **{field: True}))


def test_real_pydantic_root_model_when_mcp_runtime_is_configured():
    pydantic = pytest.importorskip('pydantic')
    model = pydantic.RootModel[dict](root={'sources': [{'id': 's1'}]})
    assert _tool_data(SimpleNamespace(data=model)) == {'sources': [{'id': 's1'}]}


@pytest.mark.parametrize('field', ['structured_content', 'structuredContent'])
def test_lossless_protocol_payload_precedes_empty_sdk_models(field):
    @dataclass(slots=True)
    class EmptyRoot:
        pass
    rows = [{'id': 's1', 'metadata': {'name': 'Supplied workbook'}}]
    result = SimpleNamespace(data=[EmptyRoot()], **{field: {'result': rows}})
    assert _tool_data(result) == rows
    result = SimpleNamespace(data={}, **{field: {'rows': rows, 'next_cursor': None}})
    assert _tool_data(result) == {'rows': rows, 'next_cursor': None}
    result = SimpleNamespace(data={}, **{field: {'result': rows, 'other': 1}})
    assert _tool_data(result) == {'result': rows, 'other': 1}

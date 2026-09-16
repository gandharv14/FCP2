from copy import deepcopy
import pytest
from mcp_env.build import source_id, slug, validate_spec
from test_source_profiles import legacy_spec


def test_long_explicit_ids_preserve_distinct_source_definitions():
    spec=legacy_spec();base=deepcopy(spec['variables'][0]);spec['variables']=[]
    prefix='audit-private-vci-sales-subledger-customer-invoices-shipment-records-and-general-ledger'
    for i,suffix in enumerate(('', '-2', '-3')):
        v=deepcopy(base);v.update(id=f'v{i}',period=str(2024+i))
        v['sources'][0].update(id=prefix+suffix,url='internal://supplied/'+prefix+suffix)
        spec['variables'].append(v)
    before=deepcopy(spec);validate_spec(spec)
    ids=[source_id(v['sources'][0])for v in spec['variables']]
    assert len(set(ids))==3 and all(len(x)==72 for x in ids)
    assert spec==before
    assert ids==[source_id(v['sources'][0])for v in deepcopy(spec)['variables']]


@pytest.mark.parametrize('value',['simple-id','Mixed spaces / Symbols','x'*72,'!!!'])
def test_short_explicit_identity_is_unchanged(value):
    assert source_id({'id':value})==slug(value)


def test_implicit_identity_is_unchanged_even_when_long():
    url='https://'+'a'*100+'.example/path'
    assert source_id({'url':url,'name':'source'})==slug('a'*100+'.example')


@pytest.mark.parametrize('identity',['same-id','x'*100])
def test_conflicting_definitions_with_same_full_identity_still_fail(identity):
    spec=legacy_spec();v=spec['variables'][0];v['sources'][0]['id']=identity
    other=deepcopy(v);other.update(id='other',period='2025');other['sources'][0]['url']='https://different.example/'
    spec['variables'].append(other)
    with pytest.raises(ValueError,match='Duplicate explicit source id'):validate_spec(spec)

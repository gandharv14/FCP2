import copy
import pytest
import gen_normalizer as G

def row(vid,source='one'):
    return dict(id=vid,name='Security expense',value=123.0,raw=None,unit='USD',period='2025',basis='reported',entity='Company',scenario='Actual',source_notes='retained note',sources=[{'id':source,'name':source,'url':'https://example.com/'+source,'kind':'document','role':'primary'}],question='What security expense?',cells=['Model!A1'],extra_cells=[])

def test_alias_union_retains_ids_sources_and_both_dispositions():
    a,b=row('z','one'),row('a','two');before=copy.deepcopy([a,b]);merged,included,proof=G.coalesce_same_atom_aliases([a,b],{'draft1':['z'],'draft2':['a']})
    assert [a,b]==before and len(merged)==1 and merged[0]['id']=='a'
    assert {s['id'] for s in merged[0]['sources']}=={'one','two'}
    assert included=={'draft1':['a'],'draft2':['a']}
    assert proof['original_entries']==2 and proof['canonical_atoms']==1
    assert {x['id'] for x in proof['groups'][0]['aliases']}=={'a','z'}
    assert all(len(x['definition_sha256'])==64 for x in proof['groups'][0]['aliases'])

@pytest.mark.parametrize('field,value',[('value',124.0),('period','2024'),('scenario','Budget'),('question','Different question'),('source_notes','Different note'),('extra_cells',['Model!A2']),('unit','USD millions'),('raw','123')])
def test_same_physical_cells_with_conflicting_meaning_fail(field,value):
    a,b=row('a'),row('b');b[field]=value
    with pytest.raises(ValueError,match='conflicting'):G.coalesce_same_atom_aliases([a,b],{'d':['a','b']})

def test_distinct_cells_same_dimensions_are_not_coalesced():
    a,b=row('a'),row('b');b['cells']=['Model!B1']
    merged,included,proof=G.coalesce_same_atom_aliases([a,b],{'d':['a','b']})
    assert len(merged)==2 and not proof['groups']

def test_no_alias_inputs_unchanged_and_duplicate_ids_rejected():
    a=row('a');v,inc,proof=G.coalesce_same_atom_aliases([a],{'d':['a']})
    assert v==[a] and inc=={'d':['a']} and not proof['groups']
    with pytest.raises(ValueError,match='duplicate emitted ID'):G.coalesce_same_atom_aliases([a,a],{'d':['a']})

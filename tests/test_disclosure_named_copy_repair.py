import copy
from types import SimpleNamespace
import pytest
from test_disclosure_named_copy import D, book
from scripts.fleet.disclosure_named_copy_repair import corrected_payload


def fixture():
    gold, cells = book('=Circ+$D$14+F16', '=Circ+$D$14+G16')
    old = SimpleNamespace(copy_lock_note=D._cell_copy_lock_note,
                          agent_records=D.agent_records)
    rec = dict(entry='method_interest', value='out_of_catalogue',
               disposition='disclosed', cell_keys=cells, leak_flag=False,
               fields=dict(label='"Undrawn Fee"', band="'Debt Schedule'!F20:G20",
                           representative="'Debt Schedule'!F20",
                           calculation_kind='copied-column calculation',
                           steps='Original numeric calculation'+old.copy_lock_note(gold,cells)),
               evidence='unchanged source formula', numbers=[1,12,0])
    rows=[rec,dict(entry=None,disposition='omitted',unrelated='retained')]
    return old,gold,dict(records=rows,agent_records=D.agent_records(rows),audit={'untouched':True})


def test_only_source_copy_note_changes_and_original_stays_immutable():
    old,gold,p=fixture(); before=copy.deepcopy(p)
    result,delta=corrected_payload(old,D,gold,p)
    assert p==before and delta[0]['index']==0 and delta[0]['names']==['Circ']
    original=result['records'][0]['fields'].pop('steps')
    assert 'named reference "Circ"' in original
    expected=copy.deepcopy(p['records'][0]);expected['fields'].pop('steps')
    assert result['records'][0]==expected
    assert result['records'][1]==p['records'][1] and result['audit']==p['audit']


@pytest.mark.parametrize('mutation',['changed_note','extra_note','changed_projection','changed_source'])
def test_rejects_foreign_note_projection_or_nonuniform_source(mutation):
    old,gold,p=fixture()
    if mutation=='changed_note':p['records'][0]['fields']['steps']='unrelated wording'
    if mutation=='extra_note':p['records'][0]['fields']['steps']*=2
    if mutation=='changed_projection':p['agent_records']=[]
    if mutation=='changed_source':gold.formula['Debt Schedule!G20']='=Other+$D$14+G16'
    with pytest.raises(ValueError):corrected_payload(old,D,gold,p)

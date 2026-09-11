"""Indexed methods preserve sequential roles and complete assessment occurrences."""
from types import SimpleNamespace
import copy,json
import pytest
from scripts.batch import harbor_method_items as M
from scripts.batch import harbor_method_assessments as A
from scripts.batch.harbor_disclosure_evidence import scoped_disclosure


def inputs():
    def band(row):return dict(sheet='Ops',row=row,pattern='same',label='Metric',cell_keys=[f'Ops!D{row+1}'],values=[1,1.0,None])
    a,b,c=band(12),band(6),band(0)
    gold=SimpleNamespace(row_label=lambda cell:'Metric')
    D=SimpleNamespace(method_roles_for_band=lambda gold,band: (['growth'],['native']) if band['row']==0 else ([],[]))
    return D,gold,[a,b,c,a]


def test_ordered_duplicate_can_gain_role_only_after_intervening_propagation():
    D,gold,bands=inputs();prepared=M.prepare(D,gold,bands)
    assert prepared[0]['roles']==[]
    assert prepared[1]['roles']==['growth']
    assert prepared[2]['role_context']==['native']
    assert prepared[3]['roles']==['growth']
    assert prepared[0]['band']==prepared[3]['band']
    assert len(prepared.bands)==3 and len(prepared)==4


def test_occurrence_state_independent_and_nested_band_metadata_immutable():
    D,gold,bands=inputs();prepared=M.prepare(D,gold,[bands[0],bands[0]])
    prepared[0]['roles']=['growth'];prepared[0]['role_context']=['agent decision']
    assert prepared[1]['roles']==[] and prepared[1]['role_context']==[]
    roles=prepared[0]['roles'];roles.append('outside mutation')
    assert prepared[0]['roles']==['growth']
    with pytest.raises(TypeError):prepared[0]['band']['values'].append(7)
    with pytest.raises(TypeError):prepared[0]['band']['row']=999
    assert json.dumps(prepared[0]['band'])==json.dumps(bands[0])


def test_indexed_transform_preserves_order_and_applies_resolution_to_each_state():
    D,gold,bands=inputs();prepared=M.prepare(D,gold,bands)
    original=[dict(item) for item in prepared]
    def update(item):
        item['roles']=['agent'];item['role_context']=item['role_context']+['resolved']
    for item in original:update(item)
    prepared.transform(update)
    assert [dict(item) for item in prepared]==original


def test_competing_native_roles_remain_unresolved():
    D,gold,bands=inputs();D.method_roles_for_band=lambda gold,band: (['left'],[]) if band['row']==0 else ((['right'],[]) if band['row']==12 else ([],[]))
    prepared=M.prepare(D,gold,bands)
    assert prepared[1]['roles']==[]


@pytest.mark.parametrize('order',[[],[0],[0,0,0],[0,1,2,1,0,2,2,2],[2,1,0],[0,2,0,2,0]])
def test_run_codec_exact_order_numeric_types_missing_and_null(order):
    items=[{'value':1},{'value':1.0,'cached':None},{'value':None}]
    encoded=A.encode(items,iter(order))
    assert json.dumps(list(A.rows(encoded)))==json.dumps([items[i] for i in order])
    assert A.logical_length(encoded)==len(order)
    assert A.expanded_json_bytes(encoded)==len(json.dumps([items[i] for i in order],ensure_ascii=False,separators=(',',':')).encode())
    assert sum(count for row,count in A.weighted(encoded))==len(order)
    assert json.dumps(list(A.rows(A.filtered(encoded,lambda row:row['value'] is not None))))==json.dumps([items[i] for i in order if items[i]['value'] is not None])


@pytest.mark.parametrize('change',['unknown','negative','past_end','bool','count','format','nan'])
def test_malformed_index_rejected_before_scope_filter(change):
    value=A.encode([{'cell_keys':['Ops!C1']}],[0])
    if change=='unknown':value['schema_version']='other'
    elif change=='negative':value['index_runs']=[[-1,0,1]]
    elif change=='past_end':value['index_runs']=[[0,1,2]];value['length']=2
    elif change=='bool':value['index_runs'][0][2]=True
    elif change=='count':value['length']=2
    elif change=='format':value['format']='invented ordering'
    else:value['items'][0]['number']=float('nan')
    with pytest.raises(ValueError):A.filtered(value,lambda row:False)


def test_scoped_evidence_filters_once_per_unique_row_without_expansion():
    items=[{'cell_keys':['Ops!C1'],'status':'structural'},{'cell_keys':['Ops!D1'],'status':'unclassified'}, {'cell_keys':['Ops!row 8'],'status':'unclassified'}]
    value=A.encode(items,[0]*20+[1]*30+[2]*40)
    disclosure={'records':[{'cell_keys':['Ops!C1']}],'agent_records':[{'cell_keys':['Ops!C1']}],'method_assessments':value}
    result=scoped_disclosure(disclosure)['method_assessments']
    assert isinstance(result,dict) and result['length']==60
    assert A.status_counts(result)=={'structural':20,'unclassified':40}
    assert disclosure['method_assessments']==value
    uncertain=copy.deepcopy(disclosure);uncertain['records'][0]['cell_keys']=['Ops!row 1']
    assert scoped_disclosure(uncertain)==uncertain


def test_large_repeated_index_is_compact_and_counted_without_rows_list():
    value=A.encode([{'status':'structural'}],(0 for _ in range(100_000)))
    assert value['index_runs']==[[0,0,100_000]] and len(json.dumps(value))<500
    assert A.status_counts(value)=={'structural':100_000}


def test_detect_keeps_first_record_when_same_dedup_key_has_different_fields():
    D,gold,bands=inputs();bands=bands+[bands[2]]*3
    D.METHOD_ROLE_PATTERNS=['growth']
    D.expand_method_band=lambda gold,delivered,band:band
    def attempt(gold,band,label,base,entry):
        return {'family':'growth','cells':['Ops!X1'],'cell_keys':['Ops!X1'],'fields':{'value':band['row']}}, {**base,'status':'out_of_catalogue'}
    D.try_custom_role=attempt
    # Expanded oracle retains each independently computed occurrence; the
    # existing final detector deduplicates on family/cells, preserving first.
    expanded=M.prepare(D,gold,bands);old_records=[];old_assessments=[]
    for item in expanded:
        band=item['band'];base=dict(band=band.get('band'),cells=band.get('cells',[]),cell_keys=band['cell_keys'],label=item['label'],roles=item['roles'],role_context=item['role_context'])
        if not item['roles']:old_assessments.append({**base,'status':'unclassified','reason':'no_method_role'})
        else:
            rec,assessment=attempt(gold,band,item['label'],base,'growth');old_records.append(rec);old_assessments.append(assessment)
    records,assessments,claimed=M.detect(D,gold,None,bands,set())
    def dedup(rows):
        seen=set();result=[]
        for row in rows:
            key=row['family'],tuple(row['cells'])
            if key not in seen:seen.add(key);result.append(row)
        return result
    assert dedup(records)==dedup(old_records)==[{'family':'growth','cells':['Ops!X1'],'cell_keys':['Ops!X1'],'fields':{'value':6}}]
    assert list(A.rows(assessments))==old_assessments
    assert len(records)<len(old_records) and claimed=={'Ops!X1'}

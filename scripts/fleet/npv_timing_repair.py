"""Source-derived correction of retained, misclassified direct XNPV records.

Only the timing convention changes. Caller must independently bind the rejected
review, preserve its consumed attempt, and obtain a new review before adoption.
"""
import copy


def corrected_payload(renderer, golden, delivered, original, cells, closure, raw_targets):
    cells=set(cells)
    if not cells or cells & set(closure):
        raise ValueError('nongraded rejected XNPV cells required')
    result=copy.deepcopy(original);changed=[]
    for index,old in enumerate(original['records']):
        selected=set(old.get('cell_keys',[]))
        if not selected & cells:continue
        if (selected-cells or old.get('entry')!='npv_timing'
            or old.get('value')!='excel_default_one_period_out'
            or old.get('disposition')!='disclosed' or old.get('source')!='convention_detector'):
            raise ValueError('only retained periodic misclassification may change')
        derived=renderer.detect_npv_timing(golden,selected)
        derived=renderer.apply_ship_when(derived,dict(gold=golden,delivered=delivered,targets=set(closure),raw_targets=set(raw_targets)))
        if len(derived)!=1 or derived[0]['value']!='dated_first_date_origin' or derived[0]['disposition']!='disclosed':
            raise ValueError('direct source XNPV timing must be rederived and independently shippable')
        new=derived[0]
        allowed={'label','note','value','alternatives'}
        if set(new)!=set(old) or any(new[k]!=old[k]for k in old if k not in allowed):
            raise ValueError('source correction changed record scope or provenance')
        result['records'][index]=new;changed.append(index)
    covered={c for i in changed for c in original['records'][i]['cell_keys']}
    if covered!=cells:raise ValueError('rejected XNPV coverage incomplete')
    if 'agent_records'in result:
        result['agent_records']=renderer.agent_records(result['records'])
    return result,changed

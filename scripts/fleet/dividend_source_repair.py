"""Correct rejected dividend-amount disclosures from their exact source formula.

This produces a candidate only. The ordinary source revision consumer must
authenticate the rejected review and obtain a distinct additional review.
"""
import copy


def corrected_payload(renderer,golden,delivered,original,cells,closure,raw_targets):
    cells=set(cells)
    if not cells or cells&set(closure):raise ValueError('nongraded rejected dividend cells required')
    result=copy.deepcopy(original);changed=[]
    for index,old in enumerate(original['records']):
        selected=set(old.get('cell_keys',[]))
        if not selected&cells:continue
        if selected-cells or old.get('entry')!='distribution_policy' or old.get('value')!='payout_ratio' \
                or old.get('disposition')!='disclosed' or old.get('source')!='convention_detector':
            raise ValueError('only rejected dividend ratio misclassification may change')
        derived=renderer.detect_distribution_policy(golden,selected,set(raw_targets))
        derived=renderer.apply_ship_when(derived,dict(gold=golden,delivered=delivered,
            targets=set(closure),raw_targets=set(raw_targets)))
        if len(derived)!=1 or derived[0]['value']!='formula_mechanics' or derived[0]['disposition']!='disclosed':
            raise ValueError('source dividend mechanics must remain independently shippable')
        new=derived[0];refs=new.get('mechanics_references',[])
        if len(refs)!=2 or not any('dividend per share'in golden.row_label(r).lower()for r in refs) \
                or not any('shares'in golden.row_label(r).lower()for r in refs):
            raise ValueError('exact share count and per-share dividend operands required')
        allowed={'label','note','value','fields','mechanics_references'}
        if set(new)-set(old)-allowed or set(old)-set(new)-allowed \
                or any(new.get(k)!=v for k,v in old.items()if k not in allowed):
            raise ValueError('dividend correction changed source scope or provenance')
        result['records'][index]=new;changed.append(index)
    if {c for i in changed for c in original['records'][i]['cell_keys']}!=cells:
        raise ValueError('rejected dividend coverage incomplete')
    if 'agent_records'in result:result['agent_records']=renderer.agent_records(result['records'])
    return result,changed

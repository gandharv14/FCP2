"""Correct only source-proven deleted-reference prose in retained method records.

The original review stays rejected; publication requires a new independent review.
"""
import copy
import re

ERROR_REFERENCE=re.compile(r"(?:(?:'(?:[^']|'')+'|[^\s'!+*/^&=<>(),:;{}\[\]-]+)!)?#REF!",re.I)


def corrected_payload(renderer,golden,delivered,original,cells,closure,raw_targets):
    cells=set(cells)
    if not cells or cells & (set(closure)|set(raw_targets)):
        raise ValueError('nongraded rejected error-reference cells required')
    result=copy.deepcopy(original);changed=[]
    for index,old in enumerate(original['records']):
        selected=set(old.get('cell_keys',[]))
        if not selected & cells:continue
        if (selected-cells or old.get('source')!='custom_method_detector'
                or old.get('disposition')!='disclosed' or old.get('coverage_complete')is not True):
            raise ValueError('only complete retained custom method may change')
        prior=old['method_profile']
        profile=renderer.formula_profile(golden,dict(cell_keys=old['cell_keys'],
            band=prior['band'],label=prior['label'],pattern=prior['pattern']))
        if not profile['complete'] or {k:v for k,v in profile.items()if k!='ast'}!=prior:
            raise ValueError('current source formula profile differs')
        if old['evidence']!=profile['formula'] or any(not golden.formula.get(c)for c in selected):
            raise ValueError('source formula or copied scope missing')
        refs=[raw for raw in profile['raw_references']if ERROR_REFERENCE.fullmatch(raw)]
        if not refs:raise ValueError('no source-proven deleted reference')
        new_fields=renderer.custom_sentence_fields(profile,golden,lock_cells=old['cell_keys'])
        if new_fields.pop('_coverage_complete')is not True:
            raise ValueError('corrected source prose incomplete')
        expected=copy.deepcopy(old['fields'])
        for raw in refs:
            before='the named range '+renderer.q(raw)
            after=renderer.describe_ref(golden,raw,old['cell_keys'][0].rsplit('!',1)[0],prior['label'])
            if before not in expected['steps'] or 'broken reference'not in after:
                raise ValueError('retained deleted-reference misclassification missing')
            expected['steps']=expected['steps'].replace(before,after)
        if expected!=new_fields or expected==old['fields']:
            raise ValueError('source correction changed unrelated calculation prose')
        result['records'][index]['fields']=new_fields;changed.append(index)
    if {c for i in changed for c in original['records'][i]['cell_keys']}!=cells:
        raise ValueError('rejected error-reference coverage incomplete')
    if 'agent_records'in result:result['agent_records']=renderer.agent_records(result['records'])
    return result,changed

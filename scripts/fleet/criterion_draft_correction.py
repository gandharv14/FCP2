"""Explicit deterministic draft correction after a proved criterion-card fix.

The original draft and author Calls remain evidence for the pre-edit text.
This projection makes no semantic approval and consumes no review authority.
"""
import json
from scripts.fleet.criterion_source_cards import require


def corrected_draft(draft, changes):
    require(type(draft) is str and draft.strip() and changes,
            'original draft and source-proved changes required')
    ids=[c['record_id'] for c in changes]
    require(len(set(ids))==len(ids), 'duplicate source correction')
    result=draft;proof=[]
    for change in changes:
        criteria=change['retained_criteria']
        require(len(criteria)==1 and type(criteria[0]) is str and '\n' not in criteria[0],
                'one unambiguous source criterion required')
        criterion=json.dumps(criteria[0],ensure_ascii=False)
        require(change['removed_false_row_atoms']==['the row labelled '+criterion],
                'correction exceeds the false source row identity')
        marker='<!-- claim:'+change['record_id']+' -->'
        require(result.count(marker)==1, 'exact original claim section required')
        start=result.index(marker);end=result.find('<!-- claim:',start+len(marker))
        if end<0:end=len(result)
        section=result[start:end]
        before='the row labelled by the text literal '+criterion
        after='the text literal '+criterion
        require(section.count(before)==1, 'ambiguous or absent criterion phrase in original draft')
        new=section.replace(before,after,1)
        result=result[:start]+new+result[end:]
        proof.append(dict(record_id=change['record_id'],source_cell=change['source_cell'],
            source_formula=change['source_formula'],old_phrase=before,new_phrase=after))
    require(len(proof)==len(changes) and result!=draft, 'complete deterministic criterion correction required')
    return result,proof

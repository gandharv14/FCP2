"""Unknown substance, changed scope, or exposed draft metadata remain blocking."""
import copy
from types import SimpleNamespace
import pytest
from scripts.batch import harbor_dialogue_source_revision_warnings as W


def fixture():
    claims={'claims':[dict(record_id='r%03d'%i,sheet='Source',junior_scope=['Source!A'+str(i)],reviewer_only={'entry':'source_selection'})for i in range(1,26)]}
    chunks=[]
    for i,r in enumerate(claims['claims'],1):
        slot=('<!-- slot:c%03d-junior | ask how to build the row labelled "Amount"; prose only, no new modelling facts -->\n'%i)if i in(13,14)else''
        chunks.append('<!-- claim:'+r['record_id']+' -->\n'+slot+'**Analyst:** Source: For `'+r['junior_scope'][0]+'`:\n\n**VP:** Use the amount.\n')
    text='\n'.join(chunks)
    finding='The frozen junior_scope header is altered by inserting "Source:" before the required scoped-cell prefix.'
    review=dict(agent_model='gpt-5.6-sol-high',round=3,passed=False,accuracy=dict(verdict='fail',claims=[dict(record_id=r['record_id'],must_say='entailed',findings=[finding]if i==0 else[])for i,r in enumerate(claims['claims'])],extras=[],cell_refs_in_senior_turns=[]),naturalness=dict(verdict='fail',findings=[W.STYLE,W.SLOTS]))
    import re
    def parse(t):return re.findall(r'^\*\*[^\n]+',re.sub(r'<!--.*?-->','',t,flags=re.S),re.M)
    v=SimpleNamespace(parse_turns=parse,map_turns_to_claims=lambda t,rows:({r['record_id']:t[i*2:i*2+2]for i,r in enumerate(rows)},[]),junior_scope_text=lambda r:'For `'+r['junior_scope'][0]+'`')
    return review,claims,text,v


def test_exact_frozen_prefix_and_hidden_comments_are_presentation_only():
    r,c,t,v=fixture();before=copy.deepcopy(r)
    proof=W.classify(r,c,t,t,t,v)
    assert proof['entailed']==25 and len(proof['warnings'])==3 and r==before


@pytest.mark.parametrize('change',['contradiction','missing','extra','unknown_style','unknown_finding','sheet','visible_metadata','changed_prefix','lost_comment','duplicate_comment'])
def test_unproved_or_substantive_findings_rejected(change):
    r,c,t,v=fixture();template=t;original=t
    if change=='contradiction':r['accuracy']['claims'][2]['must_say']='contradicted'
    elif change=='missing':r['accuracy']['claims'].pop()
    elif change=='extra':r['accuracy']['extras']=['The senior sign is wrong.']
    elif change=='unknown_style':r['naturalness']['findings'].append('The dialogue changes scope.')
    elif change=='unknown_finding':r['accuracy']['claims'][0]['findings']=['The source operand is wrong.']
    elif change=='sheet':c['claims'][0]['sheet']='Different'
    elif change=='visible_metadata':t=t.replace('<!-- slot:c013-junior','slot:c013-junior')
    elif change=='changed_prefix':t=t.replace('Source: For `Source!A1`:', 'Source: For `Source!B1`:')
    elif change=='lost_comment':t=t.replace('slot:c013-junior','removed:c013-junior')
    else:t=t.replace('<!-- slot:c014-junior','<!-- slot:c013-junior')
    with pytest.raises(ValueError):W.classify(r,c,t,template,original,v)


def test_multiline_claim_identity_preserved_and_duplicate_rejected():
    assert W.blocks('<!-- claim:first\nsecond -->\ntext')[0][0]=='first\nsecond'
    with pytest.raises(ValueError):W.blocks('<!-- claim:r -->one<!-- claim:r -->two')


def test_revalidate_rejects_foreign_schema_before_any_history_read():
    with pytest.raises(ValueError,match='foreign derivative'):
        W.revalidate({'schema_version':'unrelated'}, {}, {}, '', root='unused')

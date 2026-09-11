import copy
from pathlib import Path
import pytest
from scripts.batch import harbor_semantic_readiness as r
from test_harbor_semantic_readiness import disclosure_fixture,bind_json,ROOT
from scripts.batch.harbor_admission import record

def test_multiline_bullets_keep_full_exact_text_and_signs():
    first='- `S!D151`: For row "Operating costs\n     excluding depreciation", multiply (130681+S!D166+5702) by negative1.'
    second='- `S!G151`: Multiply (111712+7119) by negative1.'
    section='## Workbook disclosure\n\nHeader.\n\n'+first+'\n'+second+'\n'
    assert r._disclosure_bullets(section,['- `S!D151`: ','- `S!G151`: '])==[first,second]


def test_label_looking_like_an_unrelated_bullet_does_not_split_the_clause():
    first='- `S!A1`: Label "Costs\n- operating costs" then negate (S!A2).'
    second='- `S!B1`: Other clause.'
    assert r._disclosure_bullets('Header\n'+first+'\n'+second+'\n',['- `S!A1`: ','- `S!B1`: '])==[first,second]

@pytest.mark.parametrize('section,prefixes',[
 ('Header\n- `S!A1`: A\n- `S!A1`: duplicate\n',['- `S!A1`: ']),
 ('Header\n- `S!A1`: A\n',['- `S!B1`: ']),
 ('Header\n- `S!B1`: B\n- `S!A1`: A\n',['- `S!A1`: ','- `S!B1`: ']),
])
def test_ambiguous_missing_or_reordered_anchors_fail_closed(section,prefixes):
    with pytest.raises(ValueError):r._disclosure_bullets(section,prefixes)


def test_manifest_keeps_unchanged_sentence_identity_and_full_record_coverage(tmp_path):
    _,old,args,_,_,_=disclosure_fixture(tmp_path)
    renderer=r.skill_module(ROOT,'task-disclosure','disclose')
    payload=r.read(args['disclosure']);payload['records'][0]['fields']['label']='"Costs\n     excluding depreciation"'
    payload['agent_records']=renderer.agent_records(payload['records'])
    args['disclosure']=bind_json(Path(args['disclosure']['path']),payload)
    instruction=Path(args['instruction']['path']);instruction.write_text('Build this model.\n\n'+renderer.render_section(payload['records']))
    args['instruction']=record(instruction)
    new=r.disclosure_manifest(**args,root=ROOT)
    assert len(new['sentences'])==len(r.read(old)['sentences'])
    assert any('\n' in row['text'] for row in new['sentences'])
    for row in new['sentences']:assert row['text'] in instruction.read_text()
    assert sorted(rid for row in new['sentences'] for rid in row['record_ids'])==sorted(new['agent_record_ids'])
    # Only the modified record's text/identity changes; other units are stable.
    assert new['sentences'][1:]==r.read(old)['sentences'][1:]

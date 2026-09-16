import copy
import pytest
from scripts.batch.harbor_profile_presentation import split_conventions

def test_exact_caveat_word_order_other_fields_and_raw_fragment_preserved():
    text='Exact period and series mapping should be verified against the original workbook context because the page separates historical and current reporting and distinguishes three Class B rate variations.'
    raw={'field_conventions':['unchanged',text,'tail'],'capture':{'url':'original'},'review':{'status':'pending'}};before=copy.deepcopy(raw)
    result,mapping=split_conventions(raw);assert raw==before and result['capture']==raw['capture'] and result['review']==raw['review']
    m=mapping[0];assert ' '.join(result['field_conventions'][m['output_start']:m['output_start']+m['pieces']])==text
    assert result['field_conventions'][0]=='unchanged' and result['field_conventions'][-1]=='tail'
    assert all(len(x)<=160 for x in result['field_conventions'])

@pytest.mark.parametrize('text',['a'*161,'ab  '*70,'ab\tcd '*40,' '+('word '*40)])
def test_ambiguous_whitespace_or_unbreakable_token_refused(text):
    with pytest.raises(ValueError):split_conventions({'field_conventions':[text]})

def test_unicode_and_duplicate_original_items_preserve_multiplicity():
    text=' '.join(['測定値']*60);result,mapping=split_conventions({'field_conventions':[text,text]})
    assert len(mapping)==2
    assert all(' '.join(result['field_conventions'][m['output_start']:m['output_start']+m['pieces']])==text for m in mapping)

def test_no_unnecessary_projection():
    with pytest.raises(ValueError,match='no overlong'):split_conventions({'field_conventions':['short']})

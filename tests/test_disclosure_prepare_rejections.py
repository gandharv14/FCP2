from types import SimpleNamespace
import pytest
from scripts.fleet import disclosure_review_prepare as P


def test_first_round_retains_original_empty_history():
    events=[]
    q=SimpleNamespace(closed_rejection=lambda *a:pytest.fail('no rejection'),
        _primary_history=lambda *a:events.append(a))
    assert P.review_history({},q,SimpleNamespace(campaign='campaign'),'scope')==(1,[])
    assert events==[('campaign','scope',1,[])]


def test_each_consumed_rejection_is_proven_before_history():
    events=[];prior=[{'outcome':'one'},{'outcome':'two'}]
    q=SimpleNamespace(closed_rejection=lambda *a:events.append(('closed',a)),
        _primary_history=lambda *a:events.append(('history',a)))
    assert P.review_history(dict(prior_rejections=prior,next_round=3),q,
        SimpleNamespace(campaign='campaign'),'scope')==(3,prior)
    assert events==[('closed',(prior[0],'scope',1)),('closed',(prior[1],'scope',2)),
        ('history',('campaign','scope',3,prior))]


@pytest.mark.parametrize('ordinal,prior',[(True,[]),(0,[]),(2,[]),(1,[{}]),(2,{}),(3,[{}])])
def test_missing_or_reset_consumption_cannot_prepare(ordinal,prior):
    q=SimpleNamespace(closed_rejection=lambda *a:pytest.fail('invalid input reached proof'))
    with pytest.raises(ValueError):P.review_history(dict(next_round=ordinal,prior_rejections=prior),q,None,None)


def test_uncertain_call_never_reaches_primary_history():
    def fail(*args):raise ValueError('unsealed response')
    q=SimpleNamespace(closed_rejection=fail,_primary_history=lambda *a:pytest.fail('uncertain response admitted'))
    with pytest.raises(ValueError,match='unsealed'):P.review_history(
        dict(next_round=2,prior_rejections=[{}]),q,SimpleNamespace(campaign=None),'scope')

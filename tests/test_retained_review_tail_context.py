from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from scripts.fleet import retained_review_tail_context as R,dialogue_revision_review as V

@pytest.mark.parametrize('changed',['source','workbook','outcome','result','descriptor','blocked','helper'])
def test_second_rejection_cannot_be_swapped(monkeypatch,changed):
    cp={k:k for k in ('source','workbook','outcome','result','descriptor','blocked')}
    p=dict(cp,semantic_context=dict(plan='p',helper='self'))
    U=NS(completed_failure=lambda *a:pytest.fail('foreign rejection reached original validator'))
    monkeypatch.setattr(R,'read',lambda p:cp);monkeypatch.setattr(R,'selected',lambda p:(U,None));monkeypatch.setattr(R,'binding',lambda p:'self')
    if changed=='helper':p['semantic_context']['helper']='foreign'
    else:p[changed]='foreign'
    with pytest.raises(ValueError,match='another completed rejection'):R.completed_failure(p,None,None)


def test_exact_second_rejection_calls_original_validator(monkeypatch):
    cp={k:k for k in ('source','workbook','outcome','result','descriptor','blocked')};p=dict(cp,semantic_context=dict(plan='p',helper='self'));seen=[]
    monkeypatch.setattr(R,'read',lambda p:cp);monkeypatch.setattr(R,'selected',lambda p:(NS(completed_failure=lambda *args:seen.append(args)or 'original'),{'schema_version':'old'}));monkeypatch.setattr(R,'binding',lambda p:'self')
    assert R.completed_failure(p,'I','controls')=='original'and seen==[(p,'I','controls')]

@pytest.mark.parametrize('fails',[True,False])
def test_review_load_restores_registered_retained_scopes(monkeypatch,fails):
    seen=[]
    def inner(row,stack):
        stack.callback(seen.append,'restored')
        if fails:raise ValueError('actual independent validation failed')
        return 'material'
    monkeypatch.setattr(V,'_load',inner)
    if fails:
        with pytest.raises(ValueError):V.load('bound')
    else:assert V.load('bound')=='material'
    assert seen==['restored']

@pytest.mark.parametrize('mechanical',[False,True])
def test_close_preserves_actual_context_manager_choice(mechanical):
    events=[];old=NS(__exit__=lambda *a:events.append('tail'));new=NS(__exit__=lambda *a:events.append('mechanical'))
    d=NS(_semantic_tail_manager=old)
    if mechanical:d._retained_mechanical_review_manager=new
    R.close(d);assert events==['mechanical'if mechanical else'tail']


def test_mechanical_completed_failure_retains_original_guard_validator(monkeypatch):
    cp={k:k for k in ('source','workbook','outcome','result','descriptor','blocked')};p=dict(cp,semantic_context=dict(plan='p',helper='self'));seen=[]
    old=dict(schema_version='fleet-ordinary-mechanical-dialogue/v1',utilities='exact-original-utilities')
    monkeypatch.setattr(R,'read',lambda row:cp);monkeypatch.setattr(R,'selected',lambda p:('M',old));monkeypatch.setattr(R,'binding',lambda path:'self')
    monkeypatch.setattr(R,'module',lambda row:seen.append(row)or NS(completed_failure=lambda *args:seen.append(args)or 'original'))
    assert R.completed_failure(p,'I','controls')=='original'
    assert seen==['exact-original-utilities',(p,'I','controls')]

def test_completed_takeover_projection_receives_only_original_authenticated_rejection(monkeypatch):
    cp={k:k for k in ('source','workbook','outcome','result','descriptor','blocked')};cp['completed_takeover']={'helper':'T'}
    p={k:cp[k]for k in ('source','workbook','outcome','result','descriptor','blocked')};p['semantic_context']={'plan':'p','helper':'self'}
    old={'schema_version':'tail'};seen=[]
    monkeypatch.setattr(R,'read',lambda row:cp);monkeypatch.setattr(R,'selected',lambda q:(NS(completed_failure=lambda *a:('original-value','source','old-lease')),old));monkeypatch.setattr(R,'binding',lambda x:'self')
    monkeypatch.setattr(R,'module',lambda row:NS(project=lambda *a:seen.append(a)or('original-value','source','new-lease')))
    assert R.completed_failure(p,'I','controls')==('original-value','source','new-lease')
    assert seen==[(p,cp['completed_takeover'],'original-value','source','old-lease')]

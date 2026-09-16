from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from scripts.fleet import retained_disclosure_context as M


def fixture(monkeypatch):
    b=lambda x:dict(path=x,sha256=x)
    q=dict(helper=b('/reader'),config=b('/config'),original_snapshot=b('/old-snapshot'),snapshot=b('/snapshot'),workbook='0187',source=b('/source'))
    q.update({k:b('/'+k) for k in ('original_job','outcome','result','descriptor','blocked')})
    selected=dict(helper=q['helper'],plan=b('/plan'),config=q['config'],snapshot=q['snapshot'],projection=dict(environment=3,dispatcher=2,job=1),kind='completed_disclosure')
    config=dict(code=q['original_snapshot'],workbook='0187')
    rows={'/plan':q,'/config':config,'/old-snapshot':dict(root='/old'),'/snapshot':dict(root='/new')}
    monkeypatch.setattr(M,'read',lambda x:rows[x['path']]);monkeypatch.setattr(M,'binding',lambda x:b('/reader'))
    mods=[NS(__file__='/'+root+'/'+str(i))for i,root in enumerate(('old','old','old','new','new'))]
    mods[0].SECONDS=3300;mods[0].MEMORY=8589934592
    env=(*mods,config,Path('/new'),object())
    return selected,env,q,rows


def test_original_config_and_mixed_roots_remain_explicit(monkeypatch):
    s,e,q,rows=fixture(monkeypatch)
    assert M.validate_inventory(s,e,q)==q
    assert e[5]['code']==q['original_snapshot']


@pytest.mark.parametrize('change',['old-root','new-root','module','config','snapshot','source','workbook','projection','guard','guard-binding','extra-field'])
def test_foreign_mixed_context_rejected(monkeypatch,change):
    s,e,q,rows=fixture(monkeypatch)
    p=dict(q)
    if change=='old-root':rows['/old-snapshot']['root']='/foreign'
    elif change=='new-root':e=(*e[:6],Path('/foreign'),e[7])
    elif change=='module':e[3].__file__='/old/worker'
    elif change=='config':rows['/config']['code']=q['snapshot']
    elif change=='snapshot':s['snapshot']={}
    elif change=='source':p['source']={}
    elif change=='workbook':p['workbook']='0102'
    elif change=='projection':s['projection']['job']=9
    elif change=='guard':e[0].SECONDS=6600
    elif change=='guard-binding':p['outcome']={}
    elif change=='extra-field':s['unexpected']=True
    with pytest.raises(ValueError):M.validate_inventory(s,e,p)


def completed_review_prefix(D,outcome,env):
    return D.successful(outcome,env)


def completed_review_context(I,current,value,env):
    W,selected,s,l,D=env[0],None,None,None,None
    W.active(selected,s,l)
    return I._prior(current,value['descriptor'])


@pytest.mark.parametrize('fail',[False,True])
def test_only_exact_terminal_rejection_is_decoded_and_scope_restores(monkeypatch,fail):
    p={'outcome':'actual-rejected-guard','result':'actual-result','descriptor':'actual-descriptor'}
    calls=[];U=NS(completed_failure=lambda p,I,c:(calls.append('original-rejection-validator')or('value','selection','lease')))
    monkeypatch.setattr(M,'read',lambda row:{'utilities':'bound-original-validator'})
    monkeypatch.setattr(M,'module',lambda name,row:U)
    # Names are retained because compilation preserves the original function name.
    selected=NS(completed_review_prefix=completed_review_prefix,completed_review_context=completed_review_context)
    D=NS(successful=lambda out,env:('original',out));I=NS(_prior=lambda bound,desc:('prior',bound));env=(NS(active=lambda *a:None),I,None,None,None,None,None,'controls')
    try:
        with M.completed_rejection_scope(selected,p,{'previous':'old'}):
            assert selected.completed_review_prefix(D,'earlier',env)==('original','earlier')
            assert selected.completed_review_prefix(D,p['outcome'],env)==(p['result'],'value')
            assert selected.completed_review_context(I,'earlier',{'descriptor':'old'},env)==('prior','earlier')
            assert selected.completed_review_context(I,p['result'],{'descriptor':p['descriptor']},env)==('value','selection','lease')
            with pytest.raises(ValueError):selected.completed_review_context(I,p['result'],{'descriptor':'foreign'},env)
            if fail:raise RuntimeError('original history failure')
    except RuntimeError:
        assert fail
    assert selected.completed_review_prefix is completed_review_prefix and selected.completed_review_context is completed_review_context
    assert calls==['original-rejection-validator']*2


def test_current_custody_remains_original_before_semantic_authorization(tmp_path):
    seen=[];W=NS(active=lambda w,s,l:seen.append((w,s,l))or('path','current'))
    worker=NS(run=tmp_path/'worker');worker.run.mkdir();p={'semantic_successor':{'helper':{},'output':str(tmp_path/'next')}}
    assert M.semantic_current(p,env=None,D=None,W=W,worker=worker,selection='actual-selection',lease='actual-lease')==('path','current')
    assert seen==[(worker,'actual-selection','actual-lease')]
    completion=worker.run/'dialogue-semantic-retry-02/complete.json';completion.parent.mkdir();completion.write_text('{}')
    with pytest.raises(ValueError,match='lacks its original handoff'):
        M.semantic_current(p,env=None,D=None,W=W,worker=worker,selection='actual-selection',lease='actual-lease')
    assert len(seen)==1

@pytest.mark.parametrize('change',[None,'earlier','extra-child','previous-phase','descriptor','result','validator'])
def test_only_actual_final_second_review_uses_original_terminal_decoder(tmp_path,monkeypatch,change):
    directories=[tmp_path/'000',tmp_path/'001'];directory=directories[-1]
    previous={'status':'STEP_COMPLETE','next_step':'dialogue-review','descriptor':'actual-descriptor'}
    result={'path':str(directory/'result.json'),'sha256':'exact'}
    value={'descriptor':'actual-descriptor','checkpoint':'actual-blocked'}
    rows={'outcome':{'result':result},str(result):value}
    monkeypatch.setattr(M,'read',lambda b:rows[b if isinstance(b,str)else str(b)])
    monkeypatch.setattr(M,'binding',lambda p:result if p==directory/'result.json'else None)
    calls=[]
    def completed(p,I,c):
        calls.append(p);assert I=='original-I'and c=='controls'
        if change=='validator':raise ValueError('actual guard changed')
        return value,'actual-selection','actual-lease'
    U=NS(completed_failure=completed)
    if change=='earlier':directory=directories[0]
    elif change=='extra-child':directories.append(tmp_path/'002')
    elif change=='previous-phase':previous['next_step']='dialogue-writer'
    elif change=='descriptor':value['descriptor']='foreign'
    elif change=='result':monkeypatch.setattr(M,'binding',lambda p:None)
    if change is None:
        assert M.semantic_terminal(U,'original-I','controls',directory,directories,previous,'outcome')==(result,value,'actual-selection','actual-lease')
        assert calls==[dict(outcome='outcome',result=result,descriptor='actual-descriptor',blocked='actual-blocked')]
    else:
        with pytest.raises(ValueError):M.semantic_terminal(U,'original-I','controls',directory,directories,previous,'outcome')
        assert len(calls)==(1 if change=='validator'else 0)

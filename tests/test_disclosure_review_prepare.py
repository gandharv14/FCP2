from contextlib import contextmanager
from types import SimpleNamespace
import json,sys
from pathlib import Path
import pytest
from scripts.fleet import disclosure_review_prepare as P
from scripts.batch import harbor_disclosure_review_round

@pytest.mark.parametrize('mode',['contextmanager','completed_build','tuple'])
@pytest.mark.parametrize('cached',[False,True])
def test_exact_original_context_shape_and_manager_cleanup(tmp_path,monkeypatch,mode,cached):
    events=[];env=tuple(range(8));lease=object();historical=object() if mode=='completed_build' else lease
    class Manager:
        def __enter__(self):events.append('open');return (None,None,None,env,None,None,historical)
        def __exit__(self,*args):events.append('close')
    manager=Manager();D=SimpleNamespace(_initial_tail_manager=manager)
    original=SimpleNamespace(context=lambda *a,**k:manager,tail_context=lambda *a,**k:(None,None,None,D,env,None,None))
    source={'path':'/source','sha256':'a'*64}
    rounds=SimpleNamespace(completed_build=lambda b:(SimpleNamespace(),SimpleNamespace(content=SimpleNamespace(bindings={'original_raw_source':source},workbook='0001')),lease))
    original_rejection=lambda *args:'original'
    rounds.closed_rejection=original_rejection
    @contextmanager
    def installed(actual,profile):
        assert actual is rounds and profile=={'selected':'proof'}
        events.append('cache-open');actual.closed_rejection=lambda *args:'cached'
        try:yield
        finally:
            assert sys.modules['scripts.batch.harbor_disclosure_review_round']is rounds
            actual.closed_rejection=original_rejection;events.append('cache-close')
    runner=SimpleNamespace(MODULES={'rounds':'harbor_disclosure_review_round'})
    def loader(name,row):
        result=original if name=='prepared_disclosure_original'else runner if name=='prepared_disclosure_runner'else SimpleNamespace(installed=installed)if name=='prepared_completed_rejection_cache'else rounds
        sys.modules[name]=result
        return result
    monkeypatch.setattr(P,'load',loader)
    p=dict.fromkeys(P.FIELDS,None);p.update(schema_version=P.SCHEMA,helper=P.binding(Path(P.__file__).resolve()),source=source,workbook='0001',modules={'rounds':{}},build={})
    if cached:p.update(prior_rejections=[{'original':'rejected'}],next_round=2,rejection_history={'helper':{},'profile':{'selected':'proof'}})
    contextmanager_mode=mode!='tuple'
    p['loader']=dict(helper={},plan={},function='context'if contextmanager_mode else'tail_context',kwargs={'continued':True},mode='contextmanager'if contextmanager_mode else'tuple',environment=3 if contextmanager_mode else 4,lease=6 if mode=='contextmanager'else None,dispatcher=None if contextmanager_mode else 3,close_attribute=None if contextmanager_mode else'_initial_tail_manager')
    path=tmp_path/'profile.json';path.write_text(json.dumps(p));old=sys.modules.get('scripts.batch.harbor_disclosure_review_round')
    with pytest.raises(RuntimeError,match='consumer'):
        with P.context(P.binding(path))as(actual,current):
            assert actual==env and current is lease
            assert sys.modules['scripts.batch.harbor_disclosure_review_round']is rounds
            assert rounds.closed_rejection()==('cached'if cached else'original')
            raise RuntimeError('consumer')
    assert events==(['open']if contextmanager_mode else[])+(['cache-open','cache-close']if cached else[])+['close']
    assert rounds.closed_rejection is original_rejection
    assert sys.modules.get('scripts.batch.harbor_disclosure_review_round')is old

@pytest.mark.parametrize('defect',['function','kwargs','unknown','false_continued'])
def test_unlisted_context_invocations_never_open(tmp_path,monkeypatch,defect):
    p=dict.fromkeys(P.FIELDS,None);p.update(schema_version=P.SCHEMA,helper=P.binding(Path(P.__file__).resolve()))
    p['loader']=dict(helper={},plan={},function='context',kwargs={'continued':True},mode='contextmanager',environment=3,lease=6,dispatcher=None,close_attribute=None)
    if defect=='function':p['loader']['function']='run'
    elif defect=='kwargs':p['loader']['kwargs']['permit']=True
    elif defect=='unknown':p['loader']['unbound']='value'
    path=tmp_path/'profile.json';path.write_text(json.dumps(p));monkeypatch.setattr(P,'load',lambda *a:pytest.fail('unlisted loader invoked'))
    with pytest.raises(ValueError):
        with P.context(P.binding(path),continued=defect!='false_continued'):pytest.fail('opened')

@pytest.mark.parametrize('defect',['extra','missing','no_history','first_round'])
def test_cache_selection_rejects_unbound_or_unconsumed_history(monkeypatch,defect):
    p=dict(rejection_history=dict(helper={},profile={}),prior_rejections=[{}],next_round=2)
    if defect=='extra':p['rejection_history']['skip_validation']=True
    elif defect=='missing':p['rejection_history'].pop('profile')
    elif defect=='no_history':p['prior_rejections']=[]
    else:p['next_round']=1
    monkeypatch.setattr(P,'load',lambda *a:pytest.fail('invalid cache loaded'))
    with pytest.raises(ValueError,match='closed completed rejection history'):
        with P.completed_rejection_history(p,object()):pytest.fail('invalid cache installed')

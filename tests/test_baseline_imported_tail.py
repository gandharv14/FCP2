from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace as N
import pytest
from scripts.fleet import baseline_imported_tail as T

@pytest.mark.parametrize('baseline_status',['STEP_COMPLETE','UNRESOLVED'])
def test_actual_imported_checkpoint_is_guarded_once_before_ordinary_models(tmp_path,monkeypatch,baseline_status):
 events=[];home=tmp_path/'new';ops=tmp_path/'operations';p=dict(schema_version=T.SCHEMA,home=str(home),operations=str(ops));m=dict(descriptor={'original':'descriptor'},checkpoint={'actual':'imported'},config={'actual':'config'})
 def guard(action,args,controls,directory,dispatch):
  events.append(('baseline',action,args,directory,dispatch));return {'guard':'actual'}
 def successful(row,env):
  return {},dict(status=baseline_status,step='baseline')if row=={'guard':'actual'}else dict(status='PUBLICATION_READY')
 def keep(path,value):events.append(('keep',path,value));return {'kept':str(path)}
 D=N(successful=successful,run=lambda job:events.append(('ordinary',job)))
 F=N(active=lambda prior:events.append(('active',prior)),keep=keep,binding=lambda path:{'path':str(path)},checked=lambda row:{'outcome':{'ordinary':'completed'}})
 C=N(run=guard);env=tuple(range(8));job={'original':'job'}
 @contextmanager
 def context(bound,probe):
  assert probe is False;yield p,{'original':'plan'},F,C,D,m,job,env
 monkeypatch.setattr(T,'context',context)
 if baseline_status=='UNRESOLVED':
  with pytest.raises(ValueError,match='baseline held'):T.run({'tail':'plan'})
  assert not any(e[0]=='ordinary'for e in events)
 else:T.run({'tail':'plan'});assert sum(e[0]=='ordinary'for e in events)==1
 baseline=next(e for e in events if e[0]=='baseline');assert baseline[2]==dict(descriptor=m['descriptor'],checkpoint=m['checkpoint'],captures=None)and baseline[4]is False
 assert sum(e[0]=='baseline'for e in events)==1


def test_original_environment_advances_through_baseline_to_first_research_request():
 assert T.progressed(dict(status="PENDING_MODEL",step="research"))
 assert not T.progressed(dict(status="UNRESOLVED",step="research"))
 assert not T.progressed(dict(status="PENDING_MODEL",step="normalization_phase1"))

@pytest.mark.parametrize('changed',['none','dispatch','checkpoint','status','step'])
def test_pending_research_uses_actual_non_dispatching_guard(changed):
 from types import SimpleNamespace as N
 previous={'home':'/old/continuation'};bound={'path':'/old/continuation/baseline/outcome.json'}
 migrated={'descriptor':{'d':'same'},'checkpoint':{'c':'actual-imported'}}
 intent={'action':'step','arguments':dict(descriptor=migrated['descriptor'],checkpoint=migrated['checkpoint'],captures=None),'dispatch':False}
 result=dict(status='PENDING_MODEL',step='research',descriptor=migrated['descriptor'])
 if changed=='dispatch':intent['dispatch']=True
 elif changed=='checkpoint':intent['arguments']['checkpoint']={'foreign':'checkpoint'}
 elif changed=='status':result['status']='UNRESOLVED'
 elif changed=='step':result['step']='normalization_phase1'
 def checked(row):
  if row==bound:return {'resource':{'guard':'r'}}
  if row=={'guard':'r'}:return {'intent':{'intent':'i'}}
  assert row=={'intent':'i'};return intent
 F=N(checked=checked);D=N(successful=lambda b,e:({'current':'pending'},result))
 if changed=='none':assert T.validate_pending({'completed_boundary':bound},previous,F,D,(),migrated)[1]==result
 else:
  with pytest.raises(ValueError):T.validate_pending({'completed_boundary':bound},previous,F,D,(),migrated)

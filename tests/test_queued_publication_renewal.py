from pathlib import Path
import json
import pytest
from scripts.fleet import queued_publication_renewal as N

def fixture(tmp_path,monkeypatch):
    def save(name,v):
        p=tmp_path/name;p.write_text(json.dumps(v));return N.binding(p)
    job=save('job.json',dict(run='/native/worker',result='/native/result.json',driver={'path':'/native/driver.py'}))
    intent=save('intent.json',dict(job=job))
    outcome=save('outcome.json',dict(intent=intent,status='PUBLICATION_READY',returncode=0,automatic_retry=False,result={'path':'/native/result.json'}))
    process=save('process.json',dict(job=job,pid=99999991,start_ticks='41'))
    proc=tmp_path/'proc';proc.mkdir()
    monkeypatch.setattr(N,'Path',lambda value:proc if str(value)=='/proc' else Path(value))
    return dict(process=process,job=job,outcome=outcome),proc,save

def process(proc,pid,argv,start='42',state='S'):
    p=proc/str(pid);p.mkdir();fields=[state]+['0']*21;fields[19]=start
    (p/'stat').write_text(str(pid)+' (fixture) '+' '.join(fields));(p/'cmdline').write_bytes(b'\0'.join(a.encode()for a in argv)+b'\0')

def test_stopped_reader_checks_bound_finished_job(tmp_path,monkeypatch):
    p,proc,_=fixture(tmp_path,monkeypatch);process(proc,5555,['python','unrelated.py']);N.stopped(p)

@pytest.mark.parametrize('which',['run','result','job','old_pid'])
def test_actual_job_or_descendant_identity_refuses_renewal(tmp_path,monkeypatch,which):
    p,proc,_=fixture(tmp_path,monkeypatch)
    needles={'run':'/native/worker/child.py','result':'/native/result.json','job':p['job']['path'],'old_pid':'unrelated.py'}
    process(proc,99999991 if which=='old_pid' else 5555,['python',needles[which]],start='41')
    with pytest.raises(ValueError,match='still active'):N.stopped(p)

def test_mutated_outcome_binding_rejected(tmp_path,monkeypatch):
    p,_,_=fixture(tmp_path,monkeypatch);Path(p['outcome']['path']).write_text('{}')
    with pytest.raises(ValueError,match='input changed'):N.stopped(p)

def test_other_workbook_using_same_frozen_driver_does_not_block(tmp_path,monkeypatch):
    p,proc,_=fixture(tmp_path,monkeypatch)
    process(proc,5555,['python','/native/driver.py','--job','/native/other-workbook.json'])
    N.stopped(p)

def test_historical_survey_uses_original_hash_validator_and_restores_on_failure(tmp_path):
    from scripts.fleet import history as H
    path=tmp_path/'history.json'
    path.write_text(json.dumps(dict(schema_version=H.SCHEMA,roots=['/original'],
        scan_roots=['/original'],known_ids=['book','other'],fresh_ids=['book'],
        excluded={},histories={})))
    selected=N.binding(path);original=H.projected_survey
    with pytest.raises(RuntimeError,match='downstream failure'):
        with N.historical_survey(H,'book',selected):
            assert H.projected_survey((Path('/original'),),{'book','other'})==({}, {})
            with pytest.raises(ValueError,match='namespace or denominator'):
                H.projected_survey((Path('/foreign'),),{'book','other'})
            with pytest.raises(ValueError,match='does not authorize'):
                H.inspect_projection((Path('/original'),),{'book','other'},'other',selected)
            path.write_text('{}')
            with pytest.raises(ValueError,match='changed'):
                H.projected_survey((Path('/original'),),{'book','other'})
            raise RuntimeError('downstream failure')
    assert H.projected_survey is original


def test_legacy_survey_retains_opaque_history_and_restores_original():
 from types import SimpleNamespace
 from scripts.fleet import queued_publication_renewal as Q
 events=[];old=lambda *a:None;row={'path':'/legacy','sha256':'s'}
 def validate(r,**kw):
  assert r==row and kw==dict(workbook='0565',history_roots=['/old'],known_ids={'0565'})
  events.append('validate');return dict(excluded={},histories={'0565':[{'path':'/old-terminal','sha256':'old'}]})
 H=SimpleNamespace(projected_survey=old,validate=validate)
 with Q.historical_survey(H,'0565',row,legacy=True):
  excluded,histories=H.projected_survey(['/old'],{'0565'})
  assert histories['0565'][0]['sha256']=='old' and events==['validate']
 assert H.projected_survey is old

@pytest.mark.parametrize('wrong_checkpoint',[False,True])
@pytest.mark.parametrize('kind',['model_tail','completed_semantic','derivative'])
def test_completed_model_context_opens_before_publication_gate_and_closes(tmp_path,monkeypatch,wrong_checkpoint,kind):
 from types import SimpleNamespace as S
 events=[];plan=dict(code_root=str(tmp_path),model_tail={},workbook='book',ready='ready');ready=dict(job='job',outcome='outcome',checkpoint='cp');values={'plan':plan,'history':{},'ready':ready,'job':{}}
 manager=S(__exit__=lambda *args:events.append('close'))
 env=(None,None,'producer');D=S(_model_publication_manager=manager,setup=lambda job:env,successful=lambda row,e:('wrong'if wrong_checkpoint else'cp',dict(status='PUBLICATION_READY')),publication_request=lambda e,v:'request')
 if kind=='completed_semantic':
  plan.pop('model_tail');plan['semantic_continuation']={'completed_reader':{'path':'/authentic-completed-reader','sha256':'bound'}}
  del D._model_publication_manager
  D._semantic_publication_manager=manager;D._semantic_publication_scope=manager
 if kind=='derivative':
  plan.pop('model_tail');plan['source_revision']={'completed_reader':{'path':'/bound','sha256':'bound'}}
  del D._model_publication_manager
  D._derivative_context_manager=manager;D._source_revision_publication_scope=manager
 def dispatcher(*args):events.append('context');return D
 F=S(ready_value=lambda *args:ready,history_prefix=lambda p:'PREFIX_',selected_history_survey=lambda p:'selected',history_survey=lambda *args:events.append('history'),dispatcher=dispatcher,projected_request=lambda *args:'projected')
 def gate():assert events==['history','context'];events.append('gate')
 pub=S(fresh_publication=F,pinned_dispatcher='pinned',checked=lambda row:{} if isinstance(row,dict) else values[row],history_binding=lambda p:dict(path='/history',sha256='sha'),publication_only=gate)
 old=list(N.sys.path)
 try:
  if wrong_checkpoint:
   with pytest.raises(ValueError,match='queued checkpoint'):N.publication_context(pub,'plan')
  else:
   result=N.publication_context(pub,'plan');assert result[4]=='projected';assert events==['history','context','gate'];result[5].close()
  assert events[-1]=='close'
  assert events.count('close')==(1 if kind=='model_tail'else 2)
 finally:N.sys.path[:]=old

@pytest.mark.parametrize('failure',[False,True])
def test_ordinary_semantic_keeps_original_reader_before_publication_gate(monkeypatch,tmp_path,failure):
 from contextlib import contextmanager
 from types import SimpleNamespace as S
 events=[];plan=dict(code_root=str(tmp_path),workbook='book',ready='ready',history={},semantic_continuation={})
 ready=dict(job='job',outcome='outcome',checkpoint='cp');env=(None,None,'producer');values={'plan':plan,'ready':ready,'job':{},'history':{}}
 manager=S(__exit__=lambda *args:events.append('semantic-close'))
 def setup(job):
  if failure:raise ValueError('original setup failure')
  return env
 D=S(_semantic_publication_manager=manager,setup=setup,successful=lambda *a:('cp',dict(status='PUBLICATION_READY')),publication_request=lambda *a:'request')
 def dispatcher(*args):
  assert events==['history-open','history'];events.append('context');return D
 def gate():assert events[-1]=='context';events.append('gate')
 @contextmanager
 def historical(*args,**kwargs):
  events.append('history-open')
  try:yield
  finally:events.append('history-close')
 monkeypatch.setattr(N,'historical_survey',historical)
 F=S(history_prefix=lambda p:'PREFIX_',history_survey=lambda *a:events.append('history'),dispatcher=dispatcher,projected_request=lambda *a:'projected')
 pub=S(fresh_publication=F,pinned_dispatcher='pinned',checked=lambda r:{}if isinstance(r,dict)else values[r],history_binding=lambda p:dict(path='/history',sha256='sha'),publication_only=gate)
 old=list(N.sys.path)
 try:
  if failure:
   with pytest.raises(ValueError,match='original setup failure'):N.publication_context(pub,'plan')
  else:
   result=N.publication_context(pub,'plan');assert result[4]=='projected';result[5].close()
  assert events==['history-open','history','context','gate','semantic-close','history-close']
 finally:N.sys.path[:]=old

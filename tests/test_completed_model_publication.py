from contextlib import contextmanager,ExitStack
from types import SimpleNamespace
import sys,pytest
from scripts.fleet import fresh_publication as F,preserved_gate_continue as P,retained_instruction_context as A


def test_actual_parent_context_has_exact_publication_and_runtime_sites():
 projected=F.completed_model_projection(P.context,A.function_sha(P.context),lambda *a:None,lambda *a:None,lambda *a:None)
 assert projected.__name__==P.context.__name__
 with pytest.raises(ValueError):F.completed_model_projection(P.context,'0'*64,lambda *a:None,lambda *a:None,lambda *a:None)


def test_original_history_and_runtime_retained_but_current_custody_uses_P():
 seen=[]
 class Worker:
  def active(self,*a):raise AssertionError('queued lease must use P')
 W=Worker();I='original-I';A='original-adoption'
 @contextmanager
 def original(row,*,continued=False,invocation=None):
  with ExitStack()as stack:
   env=(None,I,None,W);worker='original-worker'
   seen.append(('history',row,continued))
   if invocation is None:w,controls,s,l=('old','old','old','old')
   else:raise AssertionError('owning historical fallback')
   W.active(w,s,l)
   yield (w,controls,s,l)
 @contextmanager
 def runtime(w,i,a):
  assert(w,i,a)==(W,I,A);seen.append('runtime-enter')
  try:yield
  finally:seen.append('runtime-exit')
 globals_=sys.modules[original.__module__].__dict__;globals_.update(seen=seen,W=W,I=I,A=A)
 selected=F.completed_model_projection(original,__import__('scripts.fleet.retained_instruction_context',fromlist=['']).function_sha(original),lambda *a:('current','controls','selection','lease'),runtime,lambda *a:seen.append(('P',*a)))
 with selected('exact history',continued=True)as result:assert result==('current','controls','selection','lease')
 assert seen==[('history','exact history',True),'runtime-enter',('P','current','selection','lease'),'runtime-enter','runtime-exit','runtime-exit']


@pytest.mark.parametrize('fault',[None,'source','fresh','reader','schema','no_tail'])
def test_completed_model_authority_is_source_and_history_bound(fault):
 row={'path':'plan','sha256':'a'};reader={'path':'reader','sha256':'b'}
 selected=dict(schema_version='fleet-instruction-unacquired-model-continuation/v1',helper=reader,workbook='0040',source={'sha256':'source'},completed_tail={'actual':'history'})
 plan=dict(workbook='0040',source_sha256='source',model_tail=dict(reader=reader,plan=row))
 if fault=='source':plan['source_sha256']='foreign'
 if fault=='fresh':plan['fresh_authoring']=row
 if fault=='reader':selected['helper']={'foreign':'reader'}
 if fault=='schema':selected['schema_version']='unrelated'
 if fault=='no_tail':selected.pop('completed_tail')
 def require(ok,message):
  if not ok:raise ValueError(message)
 pub=SimpleNamespace(require=require,checked=lambda _:selected,binding=lambda _:reader)
 if fault:
  with pytest.raises(ValueError):F.model_history_binding(pub,plan)
 else:assert F.model_history_binding(pub,plan)==row


def test_actual_publish_selects_original_cold_adapter_for_model_tail(monkeypatch):
 seen=[];request=object();producer=SimpleNamespace(run=lambda *a:pytest.fail('unadapted publication'))
 pub=SimpleNamespace(__file__='/publisher.py',binding=lambda path:{'path':path,'sha256':'p'})
 plan={'model_tail':{'actual':'reader'}};row={'path':'plan','sha256':'r'}
 monkeypatch.setattr(F,'install_validation',lambda *a:None);monkeypatch.setattr(F,'projected_request',lambda p,P,r,q:r)
 monkeypatch.setattr(F,'reader_evidence',lambda *a:['bound reader']);monkeypatch.setattr(F,'publication_arguments',lambda *a:('journal',{'timeout_seconds':3300}))
 def select(P,**kwargs):
  seen.append(kwargs)
  return lambda r,d,**k:(r,d,k)
 result=F.publish(pub,row,plan,(None,None,producer),request,SimpleNamespace(select=select))
 assert result==(request,'journal',{'timeout_seconds':3300})
 assert seen==[dict(reader_plan=row,child_wrapper={'path':'/publisher.py','sha256':'p'},reader_evidence=['bound reader'])]


def test_actual_model_child_reopens_history_before_publication_only(monkeypatch,tmp_path):
 class Stop(Exception):pass
 row={'path':'plan','sha256':'p'};history={'path':'history','sha256':'h'};readyrow={'path':'ready','sha256':'r'};jobrow={'path':'job','sha256':'j'}
 plan=dict(code_root=str(tmp_path),workbook='0040',model_tail={},ready=readyrow);ready=dict(job=jobrow);job={'original':'job'};seen=[]
 def checked(r):return plan if r==row else ready if r==readyrow else job
 def require(ok,message):
  if not ok:raise ValueError(message)
 pub=SimpleNamespace(checked=checked,require=require,history_binding=lambda p:history,publication_only=lambda:seen.append('publication-only'),pinned_dispatcher=None)
 monkeypatch.setattr(sys,'argv',['publisher','publication-child','--plan','plan','--plan-sha256','p','--intent','intent','--intent-sha256','i','--adapter','sidecar'])
 monkeypatch.delenv('HARBOR_FLEET_CONTINUATION_READ_ONLY',raising=False)
 for k,v in [('PATH','history'),('SHA256','h')]:monkeypatch.setenv('HARBOR_FLEET_RETAINED_HISTORY_'+k,v)
 monkeypatch.setenv('HARBOR_FLEET_WORKBOOK','0040');monkeypatch.setattr(F,'history_survey',lambda *a:seen.append('history'))
 def setup(j):seen.append('setup');raise Stop()
 def dispatcher(*a):seen.append('dispatcher');return SimpleNamespace(setup=setup)
 monkeypatch.setattr(F,'dispatcher',dispatcher)
 with pytest.raises(Stop):F.publication_child(pub,None)
 assert seen==['history','dispatcher','publication-only','setup']

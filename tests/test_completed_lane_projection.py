from pathlib import Path
from types import SimpleNamespace
import hashlib,inspect
import pytest
from test_completed_lane_history import case
from test_root_lease_takeover import setup,save
from scripts.batch import harbor_completed_lane_projection as P,harbor_completed_lane_history as H

class Reader:
 def historical_lease(self,worker,before):
  self.observed.append(before)
  return before

@pytest.fixture
def projection(case):
 kw,tracker=case;W=kw['W'];worker=kw['worker'];reader=Reader();reader.observed=[];reader.takeover=kw['T'];reader.authority=save(worker.run/'authority.json',{'exact':True})
 W.declared_namespace={'_source_cards':reader}
 p=dict(schema_version=P.SCHEMA,implementation=P.binding(P.__file__),validator=P.binding(H.__file__),reader=P.binding(__file__),authority=reader.authority,method_sha256=hashlib.sha256(inspect.getsource(Reader.historical_lease).encode()).hexdigest(),worker_run=str(worker.run),initial=P.binding(worker.run/'initial.json'),before=kw['before_binding'],after=kw['after_binding'],completion=kw['completion'],kind='dialogue',schema=P.binding(kw['T'].schema.__file__),lifecycle=P.binding(kw['T'].life.__file__))
 certificate=save(worker.run/'projection.json',p)
 return kw,tracker,reader,certificate

def test_original_completed_cas_then_historical_predecessor_only(projection):
 kw,tracker,reader,cert=projection;raw=tracker.read_bytes();P.install_projection(kw['W'],cert)
 assert reader.historical_lease(kw['worker'],P.read(kw['after_binding']))==P.read(kw['before_binding'])
 assert reader.observed==[P.read(kw['before_binding'])] and tracker.read_bytes()==raw

@pytest.mark.parametrize('change',['current','certificate','method','authority'])
def test_projection_rejects_unbound_history(projection,change):
 kw,tracker,reader,cert=projection;raw=tracker.read_bytes()
 if change=='method':reader.historical_lease=lambda *a:None
 elif change=='authority':reader.authority=save(kw['worker'].run/'other.json',{})
 if change in ('method','authority'):
  with pytest.raises(ValueError):P.install_projection(kw['W'],cert)
 else:
  P.install_projection(kw['W'],cert);current=P.read(kw['after_binding'])
  if change=='current':current['revision']+=1
  else:Path(cert['path']).write_text('{}')
  with pytest.raises(ValueError):reader.historical_lease(kw['worker'],current)
 assert not reader.observed and tracker.read_bytes()==raw

def test_original_gate_can_read_same_historical_authority_without_recursive_commit(projection,monkeypatch):
 from scripts.batch import harbor_lane_notes as N
 kw,tracker,reader,cert=projection;raw=tracker.read_bytes();old=N.check_gate;seen=[]
 def gate(*args,**kwargs):
  seen.append(True)
  assert reader.historical_lease(kw['worker'],P.read(kw['after_binding']))==P.read(kw['before_binding'])
  return old(*args,**kwargs)
 monkeypatch.setattr(N,'check_gate',gate)
 P.install_projection(kw['W'],cert)
 assert reader.historical_lease(kw['worker'],P.read(kw['after_binding']))==P.read(kw['before_binding'])
 assert 1<=len(seen)<=3 and tracker.read_bytes()==raw

def test_failed_outer_gate_clears_scoped_projection_state(projection,monkeypatch):
 from scripts.batch import harbor_lane_notes as N
 kw,tracker,reader,cert=projection;raw=tracker.read_bytes();old=N.check_gate
 def fail(*a,**k):raise ValueError('actual gate failed')
 P.install_projection(kw['W'],cert);monkeypatch.setattr(N,'check_gate',fail)
 with pytest.raises(ValueError,match='actual gate failed'):reader.historical_lease(kw['worker'],P.read(kw['after_binding']))
 assert not reader.observed
 # A second failure must still evaluate the gate rather than use a leaked flag.
 with pytest.raises(ValueError,match='actual gate failed'):reader.historical_lease(kw['worker'],P.read(kw['after_binding']))
 monkeypatch.setattr(N,'check_gate',old)
 assert reader.historical_lease(kw['worker'],P.read(kw['after_binding']))==P.read(kw['before_binding'])
 assert tracker.read_bytes()==raw

@pytest.mark.parametrize('change',['current','certificate'])
def test_nested_read_cannot_use_foreign_certificate_or_tracker(projection,monkeypatch,change):
 from scripts.batch import harbor_lane_notes as N
 kw,tracker,reader,cert=projection;raw=tracker.read_bytes()
 def nested(*a,**k):
  after=P.read(kw['after_binding'])
  if change=='current':after['revision']+=1
  else:Path(cert['path']).write_text('{}')
  reader.historical_lease(kw['worker'],after)
 monkeypatch.setattr(N,'check_gate',nested);P.install_projection(kw['W'],cert)
 with pytest.raises(ValueError):reader.historical_lease(kw['worker'],P.read(kw['after_binding']))
 assert not reader.observed and tracker.read_bytes()==raw

# This exact reader deliberately rereads its live tracker, as the criterion
# provenance reader does. The projection changes only this method's globals.
from scripts.batch.harbor_admission import record as binding,read as checked
class RereadingReader:
 def historical_lease(self,worker,before):
  selected=binding(worker.context.tracker_root/(before['workbook_id']+'.json'))
  assert checked(selected)==before
  self.observed.append(before)
  return before

def test_scoped_original_reread_receives_proven_precommit_without_global_patch(projection):
 from dataclasses import replace
 kw,tracker,reader,cert=projection;p=P.read(cert);new=RereadingReader();new.authority=reader.authority;new.observed=[]
 p.update(schema_version=P.REREAD,live_tracker=str(tracker),reader=P.binding(__file__),method_sha256=hashlib.sha256(inspect.getsource(RereadingReader.historical_lease).encode()).hexdigest())
 cert=save(kw['worker'].run/'reread.json',p);original_binding=RereadingReader.historical_lease.__globals__['binding'];raw=tracker.read_bytes()
 P.install_projection(kw['W'],cert,reader=new)
 assert new.historical_lease(kw['worker'],P.read(kw['after_binding']))==P.read(kw['before_binding'])
 assert RereadingReader.historical_lease.__globals__['binding']is original_binding
 assert checked(binding(tracker))==P.read(kw['after_binding'])and tracker.read_bytes()==raw

@pytest.mark.parametrize('change',['tracker','before','foreign_live'])
def test_scoped_reread_rejects_changed_or_foreign_artifacts(projection,change):
 kw,tracker,reader,cert=projection;p=P.read(cert);new=RereadingReader();new.authority=reader.authority;new.observed=[]
 p.update(schema_version=P.REREAD,live_tracker=str(tracker),reader=P.binding(__file__),method_sha256=hashlib.sha256(inspect.getsource(RereadingReader.historical_lease).encode()).hexdigest())
 if change=='foreign_live':p['live_tracker']='/foreign/tracker.json'
 cert=save(kw['worker'].run/'reread.json',p);P.install_projection(kw['W'],cert,reader=new);after=P.read(p['after'])
 if change=='tracker':tracker.write_text('{}')
 elif change=='before':Path(p['before']['path']).write_text('{}')
 with pytest.raises((ValueError,AssertionError)):new.historical_lease(kw['worker'],after)
 assert not new.observed

def captured_route(reader):
 def old(*args):raise AssertionError('foreign route')
 original=reader.historical_lease
 def routed(value,*args,_old=old,_new=original,**kwargs):
  return _new(value,*args,**kwargs) if value is reader.worker else _old(value,*args,**kwargs)
 return routed

@pytest.mark.parametrize('change',[None,'captured','code','namespace'])
def test_exact_precaptured_historical_route_is_rebound_without_changing_bytecode(projection,monkeypatch,change):
 import sys
 kw,tracker,reader,cert=projection;p=P.read(cert);new=RereadingReader();new.authority=reader.authority;new.observed=[];new.worker=kw['worker'];route=captured_route(new);router=SimpleNamespace(historical_lease=route);name='scripts.batch.fixture_projection_router';monkeypatch.setitem(sys.modules,name,router)
 p.update(schema_version=P.ROUTED,live_tracker=str(tracker),reader=P.binding(__file__),method_sha256=hashlib.sha256(inspect.getsource(RereadingReader.historical_lease).encode()).hexdigest(),routing=dict(module=name,implementation=P.binding(__file__),method_sha256=hashlib.sha256(inspect.getsource(route).encode()).hexdigest()))
 if change=='captured':route.__kwdefaults__['_new']=lambda *a:None
 if change=='code':p['routing']['method_sha256']='0'*64
 if change=='namespace':monkeypatch.setitem(sys.modules,name,SimpleNamespace())
 cert=save(kw['worker'].run/'routed.json',p);original=dict(route.__kwdefaults__)
 if change:
  with pytest.raises(ValueError):P.install_projection(kw['W'],cert,reader=new,router=router)
  assert router.historical_lease is route
 else:
  P.install_projection(kw['W'],cert,reader=new,router=router)
  assert router.historical_lease.__code__ is route.__code__ and router.historical_lease.__closure__ is route.__closure__
  assert route.__kwdefaults__==original
  assert router.historical_lease(kw['worker'],P.read(kw['after_binding']))==P.read(kw['before_binding'])
  assert new.observed==[P.read(kw['before_binding'])]

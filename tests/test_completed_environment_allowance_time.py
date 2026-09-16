import types
from pathlib import Path
import pytest
from scripts.fleet import environment_checkpoint_continue as E
from scripts.batch import harbor_lease_takeover as T
from scripts.batch.harbor_admission import read,record
from test_root_lease_takeover import setup,save


@pytest.mark.parametrize('fault',[None,'completion','time'])
def test_completed_lease_uses_proved_event_time_without_changing_live_clock(setup,tmp_path,fault):
 s,d,before,old=setup
 lease,done=T.replace_expired(s,d,clock=lambda:120)
 complete=read(done);stop=read(complete['stopped_owner_proof'])
 if fault=='time':
  stop['observed_at']=121;complete['stopped_owner_proof']=save(tmp_path/'forged-stop.json',stop);done=save(tmp_path/'forged-complete.json',complete)
 elif fault=='completion':
  complete['lease']['token']='changed';done=save(tmp_path/'forged-complete.json',complete)
 profile=save(tmp_path/'profile.json',dict(schema_version='harbor-allowance-takeover-direct/v1',selection=T._selection_value(s)))
 runtime=types.ModuleType('original_runtime');runtime.checked=lambda row:Path(row['path']).read_bytes() if record(row['path'])==row else (_ for _ in ()).throw(ValueError('changed'))
 runtime.T=T;runtime.s=s;runtime.time=types.SimpleNamespace(time_ns=lambda:999)
 exec("def direct(p):\n checked(p['selection']['tracker'])\n assert 100 <= time.time_ns() < 200, 'historical allowance expired'\n return p,T,s,{}\n",runtime.__dict__)
 raw=Path(s.tracker['path']).read_bytes()
 if fault:
  with pytest.raises(ValueError):E.takeover_context(runtime,profile,done,current=False)
 else:
  assert E.takeover_context(runtime,profile,done,current=False)[2]==s
  assert T.validate_completion(done)[2]==lease
 assert runtime.time.time_ns()==999 and Path(s.tracker['path']).read_bytes()==raw

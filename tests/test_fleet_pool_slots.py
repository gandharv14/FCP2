from pathlib import Path
import ast,fcntl,os,sys,threading,time
import pytest
from scripts.fleet import pool_v2 as pool
from scripts.fleet.coordinator import Coordinator,serve

def configuration(tmp_path,count):
 repo=Path(pool.__file__).resolve().parents[2];rows=[]
 for i in range(count):
  rows.append(pool.persist(tmp_path/(str(i)+'-job.json'),dict(workbook=f'{i:04}',cwd=str(repo),memory_limit_bytes=8*1024**3,minimum_disk_bytes=12*1024**3),immutable=True))
 token=tmp_path/'token';token.write_text('w'*32)
 cfg=dict(schema_version=pool.SCHEMA,host='hhu-server',code_root=str(repo),python=sys.executable,worker=pool.binding(repo/'scripts/fleet/worker.py'),url='http://127.0.0.1:1',token_file=str(token),jobs=rows)
 return cfg

def test_only_start_and_slot_entry_guards_changed():
 old=ast.parse(Path(pool.__file__).with_name('pool.py').read_text());new=ast.parse(Path(pool.__file__).read_text())
 def functions(t):return {n.name:ast.dump(n,include_attributes=False) for n in t.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name not in {'start','run_slot'}}
 assert functions(old)==functions(new)

@pytest.mark.parametrize('count,expected',[(0,[]),(1,[0]),(2,[0,1]),(3,[0,1])])
def test_spawn_count_matches_explicit_allowlist(tmp_path,monkeypatch,count,expected):
 cfg=configuration(tmp_path,count);bound=pool.persist(tmp_path/'pool.json',cfg,immutable=True);commands=[]
 class Process:
  pid=123
 def spawn(command,**kwargs):commands.append(command);return Process()
 monkeypatch.setattr(pool.subprocess,'Popen',spawn);monkeypatch.setattr(pool,'process_identity',lambda pid:dict(pid=pid,start='fixture'))
 result=pool.start(bound,tmp_path/'run')
 assert [x['slot'] for x in result['slots']]==expected
 assert [int(x[-1]) for x in commands]==expected
 assert (tmp_path/'run/current.json').is_file()

@pytest.mark.parametrize('index',[-1,1,2,True])
def test_unassigned_slot_rejected_before_lock_or_network(tmp_path,index):
 cfg=configuration(tmp_path,1);bound=pool.persist(tmp_path/'pool.json',cfg,immutable=True)
 with pytest.raises(ValueError,match='not assigned'):pool.run_slot(bound,tmp_path/'never-created','missing-generation',index)
 assert not (tmp_path/'never-created').exists()

def test_actual_single_supervisor_leaves_second_lifetime_slot_unheld(tmp_path):
 c=Coordinator(tmp_path/'test.sqlite');server=serve(c,'127.0.0.1',0,'w'*32,'a'*32);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
 cfg=configuration(tmp_path,1);cfg['url']=f'http://127.0.0.1:{server.server_port}';bound=pool.persist(tmp_path/'pool.json',cfg,immutable=True);root=tmp_path/'run';record=None
 try:
  record=pool.start(bound,root);assert [x['slot'] for x in record['slots']]==[0]
  deadline=time.monotonic()+10
  while time.monotonic()<deadline:
   state=pool.status(root)
   if state['slots'][0]['state']=='IDLE':break
   time.sleep(.05)
  assert state['slots'][0]['state']=='IDLE';assert not (root/'slot-1.lock').exists()
  fd=os.open(root/'slot-0.lock',os.O_RDWR)
  try:
   with pytest.raises(BlockingIOError):fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
  finally:os.close(fd)
  assert state['dispatch_count']==0
  pool.stop(root)
  deadline=time.monotonic()+10
  while pool.process_identity(record['slots'][0]['identity']['pid'])==record['slots'][0]['identity'] and time.monotonic()<deadline:time.sleep(.05)
  assert pool.process_identity(record['slots'][0]['identity']['pid'])!=record['slots'][0]['identity']
 finally:
  if record:
   for item in record['slots']:
    if pool.process_identity(item['identity']['pid'])==item['identity']:
     import signal
     pool.signal_exact(item['identity'],signal.SIGKILL)
  server.shutdown();server.server_close();thread.join()

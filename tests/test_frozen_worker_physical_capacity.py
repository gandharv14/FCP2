"""Real cold selected workers propagate policy into two guarded transport children.

Coordinator and resource observations are local fixtures; no HTTP is performed.
Actual policy validation, guardian, process boundaries, and flock gates run.
"""
from pathlib import Path
import json,os,socket,subprocess,sys,time
from scripts.fleet.migration import binding
ROOT=Path(__file__).resolve().parents[1]
F=ROOT/'diagnostics/vm-fleet-20260912/control/source-pool-staged-pipeline-001/implementation-001'

def test_cold_selected_workers_overlap_two_and_hold_third_without_replay(tmp_path):
 code=json.loads((F/'snapshot.json').read_text())['root'];source=tmp_path/'source';source.write_text('test source');auth=tmp_path/'authorization';auth.write_text('bounded local lock regression');policy=tmp_path/'policy.json'
 policy.write_text(json.dumps(dict(schema_version='fleet-physical-model-capacity/v1',authorization=binding(auth),hosts=[socket.gethostname()],slots_per_host=2,slot_root=str(tmp_path/'physical'),publication_limit=1,per_workbook_limit=1,unknown_response_policy='hold-never-replay',max_wait_seconds=1,revision=1)))
 release=tmp_path/'release';driver=tmp_path/'transport_child.py'
 driver.write_text("""import sys,json,time,os
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import xl_transport
assert os.environ['HARBOR_MODEL_SLOTS']=='1'
with xl_transport._local_model_slot():
 Path(sys.argv[2]).write_text(json.dumps(dict(policy=os.environ['HARBOR_PHYSICAL_MODEL_POLICY_SHA256'],pid=os.getpid(),at=time.time())))
 while not Path(sys.argv[3]).exists():time.sleep(.02)
 Path(sys.argv[4]).write_text(json.dumps(dict(status='PUBLICATION_READY')))
""")
 bootstrap="""import sys,json
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,sys.argv[1])
from scripts.fleet import worker as W
from scripts.fleet.migration import binding
j=json.loads(Path(sys.argv[2]).read_text())
class LocalFixture:
 def call(self,action,**kw):
  if action=='claim':return dict(workbook=j['workbook'],source_sha256=j['source']['sha256'],owner=kw['owner'],token='local-fixture-only',fence=1)
  assert action in {'heartbeat','finish'}
  return {}
W.shutil.disk_usage=lambda p:SimpleNamespace(free=100*1024**3)
W._process_group_rss_bytes=lambda p:0
W._process_group_alive=lambda p:False
print(json.dumps(W.execute(binding(sys.argv[2]),client=LocalFixture(),owner='local-fixture-'+j['workbook'],url='http://unused.invalid',token_file='/unused.fixture')))
"""
 env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','HARBOR_MODEL_SLOT_ROOT':str(tmp_path/'legacy'),'HARBOR_MODEL_SLOTS':'1'}
 processes=[];jobs=[]
 try:
  for i in range(3):
   wb=f'{i+1:04d}';run=tmp_path/('worker-'+wb);result=tmp_path/(wb+'-result.json');job=dict(schema_version='harbor-fleet-worker-job/v1',workbook=wb,source=binding(source),version='a'*64,driver=binding(driver),inputs=[],command=[sys.executable,'-I','-B',str(driver),code,str(tmp_path/(wb+'-entered')),str(release),str(result)],cwd=code,run=str(run),result=str(result),memory_limit_bytes=8*1024**3,timeout_seconds=30,minimum_disk_bytes=12*1024**3,physical_model_policy=binding(policy));p=tmp_path/(wb+'-job.json');p.write_text(json.dumps(job));jobs.append(p)
   processes.append(subprocess.Popen([sys.executable,'-I','-B','-c',bootstrap,code,str(p)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,text=True))
   if i<2:
    deadline=time.monotonic()+15
    while not (tmp_path/(wb+'-entered')).exists()and time.monotonic()<deadline:
     if processes[-1].poll()is not None:break
     time.sleep(.03)
    assert (tmp_path/(wb+'-entered')).exists(),processes[-1].communicate(timeout=2)
  third=processes[2];stdout,stderr=third.communicate(timeout=10);assert third.returncode==0,(stdout,stderr)
  outcome=json.loads((tmp_path/'worker-0003/outcome.json').read_text());assert outcome['status']=='RECONCILIATION_REQUIRED'and outcome['returncode']!=0
  assert not(tmp_path/'0003-entered').exists()
  assert all(p.poll()is None for p in processes[:2])
  assert (tmp_path/'legacy/capacity').read_bytes()==b'1\n'and not(tmp_path/'legacy/slot-0.lock').exists()
  assert all(json.loads((tmp_path/(wb+'-entered')).read_text())['policy']==binding(policy)['sha256']for wb in ('0001','0002'))
  release.write_text('release')
  for process in processes[:2]:
   stdout,stderr=process.communicate(timeout=10);assert process.returncode==0,(stdout,stderr)
  for wb in ('0001','0002'):assert json.loads((tmp_path/('worker-'+wb)/'outcome.json').read_text())['status']=='PUBLICATION_READY'
  old=(tmp_path/'worker-0003/intent.json').read_bytes();repeat=subprocess.run([sys.executable,'-I','-B','-c',bootstrap,code,str(jobs[2])],capture_output=True,text=True,env=env,timeout=10)
  assert repeat.returncode!=0 and 'never replay'in repeat.stderr and (tmp_path/'worker-0003/intent.json').read_bytes()==old
 finally:
  release.write_text('release')
  for process in processes:
   if process.poll()is None:
    try:process.communicate(timeout=5)
    except subprocess.TimeoutExpired:process.kill();process.communicate()

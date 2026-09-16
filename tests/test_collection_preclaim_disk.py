from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import ast,copy,json,shutil,time
import pytest
from scripts.fleet import collection_disk_policy as D,collection_recovery as R,collection_launch as C,publish as P
from scripts.fleet.migration import binding
ROOT=Path(__file__).resolve().parents[1];H=ROOT/'diagnostics/vm-fleet-20260912/returned/0411-qualified-001'
def put(root,name,v):
 p=root/name;p.write_text(json.dumps(v));return binding(p)
@pytest.fixture
def selected(tmp_path):
 old_original=binding(H/'root-collection-001/profile.json');p=json.loads(Path(old_original['path']).read_text());s=json.loads(Path(p['staged']['path']).read_text());plan=json.loads(Path(s['plan']['path']).read_text())
 # Build a closed private preclaim history. Production collection may now be complete.
 plan.update(journal=str(tmp_path/'collection-operation'),output_root=str(tmp_path/'outputs'))
 s['plan']=put(tmp_path,'original-plan.json',plan);p['staged']=put(tmp_path,'staged.json',s);old=put(tmp_path,'original-profile.json',p)
 journal=tmp_path/'original-launch';journal.mkdir();original_journal=H/'root-collection-001/launch-001'
 intent=json.loads((original_journal/'intent.json').read_text());intent['profile']=old;intent_binding=put(journal,'intent.json',intent)
 ready=json.loads((original_journal/'ready.json').read_text());ready['reason']=json.dumps(intent_binding,sort_keys=True);put(journal,'ready.json',ready)
 command=json.loads((original_journal/'command.json').read_text());command[command.index('--plan')+1]=s['plan']['path'];command[command.index('--plan-sha256')+1]=s['plan']['sha256'];put(journal,'command.json',command)
 (journal/'response.log').write_bytes((original_journal/'response.log').read_bytes());uncertain=json.loads((original_journal/'uncertain.json').read_text());uncertain['intent']=intent_binding;put(journal,'uncertain.json',uncertain)
 helper=binding(Path(D.__file__).resolve());now=time.time_ns();policy=dict(schema_version=D.SCHEMA,helper=helper,authorization=binding(ROOT/'diagnostics/bulk-production/authorization-20260913.md'),workbook=plan['workbook'],source_sha256=plan['source_sha256'],manifest=plan['manifest'],archive=binding(plan['archive']),reserve_bytes=2*1024**3,allocation_factor=3,metadata_bytes=64*1024**2,created_at_ns=now,expires_at_ns=now+3600*10**9)
 plan={**plan,'schema_version':'harbor-fleet-collection-plan/v2','disk_policy':put(tmp_path,'policy.json',policy)}
 collector=tmp_path/'collector.py';collector.write_text(R.collector_source(Path(s['collector']['path']).read_text()));derivation=put(tmp_path,'derivation.json',dict(schema_version='harbor-collection-only-successor/v1',original=s['collector'],collector=binding(collector),disk_helper=helper));recovery=put(tmp_path,'recovery.json',dict(schema_version='harbor-collection-preclaim-disk-failure/v1',profile=old,journal=str(journal),files={x.name:binding(x)for x in journal.iterdir()}));p.update(schema_version=C.SCHEMA2,collector=binding(collector),collection_plan=put(tmp_path,'plan.json',plan),collector_derivation=derivation,preclaim_recovery=recovery)
 return p,s,plan,policy,tmp_path

def test_actual_archive_bound_disk_measurement_and_full_preclaim_history(selected):
 p,s,plan,policy,tmp=selected;m=D.requirements(plan)
 assert m['expanded_bytes']>0 and m['preclaim_bytes']>=2*1024**3+64*1024**2 and m['preclaim_bytes']<3*1024**3
 with patch.object(D.shutil,'disk_usage',return_value=SimpleNamespace(free=4*1024**3)):
  assert D.check(m)['free_bytes']==4*1024**3
  assert C.load(put(tmp,'profile.json',p))[2]==plan
 assert R.preclaim(p,s,plan)['state']=='ready'

def small_policy(selected,**changes):
 p,s,plan,policy,tmp=selected
 policy.update(schema_version=D.SMALL_SCHEMA,reserve_bytes=768*1024**2)
 policy.update(changes);plan['disk_policy']=put(tmp,'small-policy.json',policy)
 return plan

def test_small_authenticated_archive_fits_one_gib_without_changing_old_policy(selected):
 old=copy.deepcopy(selected[2]);plan=small_policy(selected)
 with patch.object(D.shutil,'disk_usage',return_value=SimpleNamespace(free=1024**3)):
  assert D.check(D.requirements(plan))['required_bytes']<1024**3
  with pytest.raises(ValueError,match='disk reserve'):D.check(D.requirements(old))

@pytest.mark.parametrize('change',[{'reserve_bytes':0},{'allocation_factor':1},{'metadata_bytes':0},{'expires_at_ns':time.time_ns()+7200*10**9}])
def test_small_policy_remains_bounded(selected,change):
 with pytest.raises(ValueError):D.requirements(small_policy(selected,**change))

def test_small_policy_rejects_authenticated_oversized_archive(selected):
 import io,tarfile,hashlib
 plan=small_policy(selected);tmp=selected[-1];archive=tmp/'large.tgz'
 # More than the policy limit even after authenticating every actual byte.
 payload=b'x'*(64*1024**2+1)
 with tarfile.open(archive,'w:gz')as f:
  info=tarfile.TarInfo('payload');info.size=len(payload);f.addfile(info,io.BytesIO(payload))
 manifest=put(tmp,'large-manifest.json',dict(workbook=plan['workbook'],archive_sha256=binding(archive)['sha256'],files={'payload':dict(size_bytes=len(payload),sha256=hashlib.sha256(payload).hexdigest())}))
 plan.update(archive=str(archive),manifest=manifest);policy=json.loads(Path(plan['disk_policy']['path']).read_text());policy.update(archive=binding(archive),manifest=manifest);plan['disk_policy']=put(tmp,'large-policy.json',policy)
 with pytest.raises(ValueError,match='allocation exceeds'):D.requirements(plan)

def test_prepare_explicit_small_policy_retains_original_selection(selected):
 p,s,plan,policy,tmp=selected;prior=json.loads(Path(p['preclaim_recovery']['path']).read_text())['profile'];before=Path(prior['path']).read_bytes()
 result=R.prepare(prior,tmp/'small-selection',collector=p['collector'],disk_helper=policy['helper'],authorization=policy['authorization'],small_archive=True)
 successor=json.loads(Path(result['path']).read_text());next_plan=json.loads(Path(successor['collection_plan']['path']).read_text());disk=json.loads(Path(next_plan['disk_policy']['path']).read_text())
 assert disk['schema_version']==D.SMALL_SCHEMA and disk['expires_at_ns']-disk['created_at_ns']==3600*10**9
 assert Path(prior['path']).read_bytes()==before and D.requirements(next_plan)['reserve_bytes']==768*1024**2

@pytest.mark.parametrize('key,value',[('reserve_bytes',0),('reserve_bytes',True),('allocation_factor',1),('metadata_bytes',0),('workbook','9999'),('source_sha256','0'*64),('expires_at_ns',1),('authorization',{'path':'/wrong','sha256':'0'*64})])
def test_policy_scope_and_finite_margin_cannot_relax(selected,key,value):
 p,s,plan,policy,tmp=selected;policy[key]=value;plan['disk_policy']=put(tmp,'policy-changed.json',policy)
 with pytest.raises((ValueError,FileNotFoundError)):D.requirements(plan)

def test_disk_reserve_checked_before_original_collector_journal_or_claim(selected,tmp_path):
 p,s,plan,policy,tmp=selected;plan={**plan,'journal':str(tmp/'new-collector-journal'),'output_root':str(tmp/'outputs')};row=put(tmp,'boundary-plan.json',plan)
 with patch.object(D.shutil,'disk_usage',return_value=SimpleNamespace(free=1*1024**3)),pytest.raises(ValueError,match='disk reserve'):
  P.controlled(row,action='collect',url='http://unused',token_file='/unused',owner='unused',version='v')
 assert not Path(plan['journal']).exists()

def test_default_remote_and_legacy_guards_remain_unchanged(selected,tmp_path):
 p,s,plan,policy,tmp=selected;plan={k:v for k,v in plan.items()if k!='disk_policy'};plan.update(schema_version='harbor-fleet-collection-plan/v1',journal=str(tmp/'legacy-journal'))
 with patch.object(P.shutil,'disk_usage',return_value=SimpleNamespace(free=4*1024**3)),pytest.raises(ValueError,match='publication disk reserve unavailable before claim'):
  P.controlled(put(tmp,'legacy-plan.json',plan),action='collect',url='unused',token_file='unused',owner='unused',version='v')
 assert not Path(plan['journal']).exists()

@pytest.mark.parametrize('kind',['output','journal','wrong_owner','changed_source','bad_collector'])
def test_preclaim_recovery_rejects_any_consumed_or_changed_boundary(selected,tmp_path,kind):
 p,s,plan,policy,tmp=selected
 if kind=='output':
  # A private fixture represents a crossed output boundary; no production output is touched.
  plan={**plan,'output_root':str(tmp/'outputs')};(Path(plan['output_root'])/(plan['workbook']+'-outputs')).mkdir(parents=True)
 if kind=='journal':plan={**plan,'journal':str(tmp/'existing')};Path(plan['journal']).mkdir()
 if kind=='wrong_owner':p={**p,'owner':'local:collection:foreign'}
 if kind=='changed_source':plan={**plan,'source_sha256':'0'*64}
 if kind=='bad_collector':s={**s,'collector':{'path':'/foreign','sha256':'0'*64}}
 with pytest.raises((ValueError,FileNotFoundError)):R.preclaim(p,s,plan,check_process=False)

def test_live_or_unreadable_local_process_inventory_blocks(selected):
 p,s,plan,_,_=selected
 with patch.object(R.subprocess,'run',return_value=SimpleNamespace(stdout='123 '+p['python']+' -I -B '+s['collector']['path']+' collect\n')),pytest.raises(ValueError,match='still running'):R.preclaim(p,s,plan)
 with patch.object(R.subprocess,'run',side_effect=PermissionError),pytest.raises(PermissionError):R.preclaim(p,s,plan)

def test_ready_central_is_exact_and_never_projects_a_hold(selected):
 p,s,plan,_,_=selected;ready=R.preclaim(p,s,plan,check_process=False);op=dict(workbook=plan['workbook'],kind='publication',state='confirmed',fence=p['expected_fence'],owner=p['expected_owner'],evidence=json.dumps(s['remote_result']));status=dict(at=100,workbooks=[ready],operations=[op]);capacity=SimpleNamespace(validate_occupancy=lambda v:None)
 with patch.object(R,'local_stopped',return_value={}):
  assert R.ready_central(status,p,s,plan,capacity,now=100)==ready and ready['owner']is None
  for change in ({'state':'hold'},{'fence':ready['fence']+1},{'reason':'foreign'}):
   altered=copy.deepcopy(status);altered['workbooks'][0].update(change)
   with pytest.raises(ValueError):R.ready_central(altered,p,s,plan,capacity,now=100)
  for kind,wb in [('publication','other'),('model',plan['workbook'])]:
   altered=copy.deepcopy(status);altered['operations'].append(dict(kind=kind,workbook=wb,state='active'))
   with pytest.raises(ValueError):R.ready_central(altered,p,s,plan,capacity,now=100)

def test_original_collector_unchanged_except_declared_disk_branch(selected):
 p,s,_,_,_=selected;old=ast.parse(Path(s['collector']['path']).read_text());new=ast.parse(Path(p['collector']['path']).read_text());functions=lambda t:{x.name:ast.dump(x,include_attributes=False)for x in t.body if isinstance(x,(ast.FunctionDef,ast.AsyncFunctionDef))}
 a,b=functions(old),functions(new);assert set(a)==set(b)
 assert all(a[k]==b[k]for k in a if k not in {'controlled','main'})
 assert '12*1024**3'in R.collector_source(Path(s['collector']['path']).read_text())and '8*1024**3'in R.collector_source(Path(s['collector']['path']).read_text())

def test_successor_launch_consumes_ready_without_reconciling_or_replaying(tmp_path):
 """Exercise the owning root path; READY recovery must never call reconcile."""
 from contextlib import nullcontext
 from scripts.fleet import collection_launch as launch
 archive_output=tmp_path/'outputs';archive_output.mkdir();(archive_output/'a.txt').write_text('retained qualified bytes')
 files={'a.txt':{'sha256':binding(archive_output/'a.txt')['sha256'],'size_bytes':24}}
 job_journal=tmp_path/'collector';root_journal=tmp_path/'root'
 receipt=put(tmp_path,'receipt.json',dict(url='unused',private_directory=str(tmp_path)))
 p=dict(preclaim_recovery={},coordinator_receipt=receipt,capacity_policy={},publisher={},owner='local:collection:0411:001',expected_fence=3,python='python',native_python='python',collector={'path':'selected-collector','sha256':'new'},collection_plan={'path':'selected-plan','sha256':'new'})
 s=dict(remote_result={},remote_plan={},remote_version='v',collector={'path':'original-collector'},plan={'path':'original-plan'})
 plan=dict(workbook='0411',source_sha256='source',journal=str(job_journal));ready=dict(workbook='0411',state='ready',fence=3,owner=None)
 complete=dict(workbook='0411',state='complete',fence=4,owner=p['owner'],version='v',source_sha256='source');calls=[];finished=False
 class Client:
  def __init__(self,*a):pass
  def call(self,action,**kw):
   calls.append(action);assert action=='status','recovery may not reconcile a consumed READY transition'
   return {'workbooks':[complete if finished else ready]}
 def run(command,**kw):
  nonlocal finished
  assert command[3:6]==['selected-collector','collect','--plan']and command[6]=='selected-plan'
  assert json.loads((root_journal/'ready.json').read_text())==ready
  job_journal.mkdir();put(job_journal,'collection.json',dict(status='COLLECTED',workbook='0411',local_byte_integrity_verified=True,output=str(archive_output),files=files));finished=True
  return SimpleNamespace(returncode=0)
 selected=put(tmp_path,'selected.json',{})
 with patch.object(launch,'load',return_value=(p,s,plan,{})),patch.object(launch,'Client',Client),patch.object(launch,'lock',return_value=nullcontext()),patch.object(launch,'central',return_value=ready),patch.object(launch.P,'ssh',return_value='stopped'),patch.object(launch.P,'module',side_effect=lambda name,row:SimpleNamespace(validate_occupancy=lambda s:None)if name=='collection_capacity'else SimpleNamespace(files=lambda path:files)),patch.object(launch.subprocess,'run',side_effect=run):
  result=launch.launch(selected,root_journal,True)
  assert json.loads(Path(result['path']).read_text())['status']=='COLLECTED'
  assert calls==['status','status','status']
  with pytest.raises(ValueError,match='journal consumed'):launch.launch(selected,root_journal,True)

def test_generic_ordinary_v2_selection_preserves_staging_without_failure_projection(selected):
 p,s,plan,policy,tmp=selected;prior=json.loads(Path(p['preclaim_recovery']['path']).read_text())['profile'];before=Path(prior['path']).read_bytes()
 result=R.prepare(prior,tmp/'ordinary-selection',collector=p['collector'],disk_helper=policy['helper'],authorization=policy['authorization'])
 v=json.loads(Path(result['path']).read_text());assert 'preclaim_recovery'not in v
 assert Path(prior['path']).read_bytes()==before and v['staged']==p['staged']
 assert R.successor(v,s,json.loads(Path(s['plan']['path']).read_text()))['journal']==plan['journal']
 with pytest.raises(ValueError,match='already exists'):R.prepare(prior,tmp/'ordinary-selection',collector=p['collector'],disk_helper=policy['helper'],authorization=policy['authorization'])

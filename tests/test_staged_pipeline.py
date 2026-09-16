from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import copy,json,os,time
import pytest
from scripts.fleet import staged_pipeline as P
from scripts.fleet.migration import binding
ROOT=Path(__file__).resolve().parents[1]
def put(root,name,v):
 p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v));return binding(p)
@pytest.fixture
def flow(tmp_path):
 source=dict(next_attempt=2,home=str(tmp_path/'source'),helper=dict(path=str(ROOT/'scripts/fleet/source_first.py')),seconds=7200,memory_bytes=16*1024**3,snapshot=put(tmp_path,'snapshot.json',{'root':str(ROOT),'fingerprint':'v'}),legacy_research=put(tmp_path,'old.json',{}))
 template=dict(home=str(tmp_path/'authoring'),code_root=str(ROOT),reconstruction=dict(mode='existing',handoff=None,generated_curation=None))
 p=dict(schema_version=P.SCHEMA,workbook='0001',home=str(tmp_path/'pipeline'),source_plan=put(tmp_path,'source-plan.json',source),author_template=put(tmp_path,'template.json',template),total_seconds=86400,author_memory_bytes=8*1024**3,admission_policy={},automatic_retry=False)
 old=dict(schema_version=P.S.MCP_SCHEMA,staged_history=dict(naturalization_reservations_upper_bound=2,dialogue_rounds_upper_bound=1),original={'path':'unchanged','sha256':'a'*64})
 selected=put(tmp_path,'pipeline.json',p)
 return p,source,old,template,selected,tmp_path

def complete_source(p,source):
 r=Path(source['home']);r.mkdir();build=dict(state='VERIFIED',source_generation_id='a'*64,segmentation_generation_id='b'*64)
 ledger=put(r,'corpus/workbooks/0001/ledger.json',dict(build=build))
 for folder,g in [('sources','a'*64),('segments','b'*64)]:put(r,'corpus/'+folder+'/0001/generations/'+g+'/generation-manifest.json',{'generation_id':g})
 return put(r,'result.json',dict(status='RECONSTRUCTION_VERIFIED',plan=p['source_plan'],legacy_research=source['legacy_research'],ledger=ledger,models_dispatched=0,cumulative_reconstruction_ordinal=2,automatic_retry=False))

def test_whole_job_runs_source_then_same_claim_author_and_never_replays(flow,monkeypatch):
 p,source,old,template,selected,tmp=flow;phases=[];validations=[]
 monkeypatch.setenv('HARBOR_FLEET_WORKBOOK','0001');monkeypatch.setenv('HARBOR_FLEET_FENCE','1')
 def phase(p,name,command,seconds,memory_bytes,models):
  phases.append((name,seconds,memory_bytes,models));put(Path(p['home']),name+'-intent.json',{})
  if name=='source':complete_source(p,source)
  else:
   author={'path':command[command.index('--plan')+1],'sha256':command[command.index('--plan-sha256')+1]}
   assert P.actual_phase(selected)=='AUTHORING'
   put(Path(template['home']),'result.json',dict(status='PUBLICATION_READY',plan=author,workbook='0001',publication_performed=False))
  return put(Path(p['home']),name+'-resource.json',{'returncode':0})
 def inspect(row):
  if Path(p['home']).exists():raise ValueError('already consumed')
 with patch.object(P,'inspect',side_effect=inspect),patch.object(P,'load',return_value=(p,source,old,template)),patch.object(P,'guarded_phase',side_effect=phase),patch.object(P,'wait_author_admission')as wait,patch.object(P.L,'validate',side_effect=lambda row:validations.append(row)),patch.object(P.L,'source_handoff')as handoff,patch.object(P.fresh,'inspect')as fresh:
  result=P.run(selected);assert result==binding(Path(template['home'])/'result.json');assert len(phases)==2
  assert phases[0]==('source',7650,16*1024**3,False)
  assert phases[1][0]=='author'and 0<phases[1][1]<86400 and phases[1][2:]==(8*1024**3,True)
  assert wait.call_count==handoff.call_count==fresh.call_count==1
  authority=json.loads(Path(validations[0]['path']).read_text());assert authority['original']==old['original']
  assert authority['authoring']['cumulative_nat_upper_bound']==4 and authority['authoring']['cumulative_dialogue_reviews_upper_bound']==3
  assert authority['authoring']['source_result']==binding(Path(source['home'])/'result.json')
  assert P.actual_phase(selected)=='PUBLICATION_READY'
  with pytest.raises(ValueError,match='already consumed'):P.run(selected)
  assert len(phases)==2

@pytest.mark.parametrize('status,models,ordinal',[('RECONSTRUCTION_HELD',0,2),('RECONSTRUCTION_VERIFIED',1,2),('RECONSTRUCTION_VERIFIED',0,1)])
def test_unqualified_or_recounted_source_never_enters_author(flow,status,models,ordinal):
 p,source,old,template,selected,tmp=flow;row=complete_source(p,source);v=json.loads(Path(row['path']).read_text());v.update(status=status,models_dispatched=models,cumulative_reconstruction_ordinal=ordinal);Path(row['path']).write_text(json.dumps(v))
 with pytest.raises(ValueError,match='source completion'):P.source_handoff(p,source)
 assert not Path(template['home']).exists()

def test_source_guard_failure_preserves_intent_and_does_not_prepare_authors(flow,monkeypatch):
 p,source,old,template,selected,tmp=flow;monkeypatch.setenv('HARBOR_FLEET_WORKBOOK','0001');monkeypatch.setenv('HARBOR_FLEET_FENCE','1')
 with patch.object(P,'inspect'),patch.object(P,'load',return_value=(p,source,old,template)),patch.object(P,'guarded_phase',side_effect=RuntimeError('source failed')),patch.object(P,'prepare_author')as author:
  with pytest.raises(RuntimeError):P.run(selected)
  author.assert_not_called();assert (Path(p['home'])/'intent.json').exists()and P.actual_phase(selected)=='HELD'

@pytest.mark.parametrize('fault',['wrong_owner','wrong_version','wrong_source','expired','own_operation'])
def test_admission_keeps_exact_custody_without_claim_or_reconcile(flow,monkeypatch,fault):
 from scripts.fleet import coordinator
 p,source,old,template,selected,tmp=flow;source['source']={'sha256':'s'};p['source_plan']=put(tmp,'new-source.json',source);monkeypatch.setenv('HARBOR_FLEET_URL','unused');monkeypatch.setenv('HARBOR_FLEET_TOKEN_FILE','unused');monkeypatch.setenv('HARBOR_FLEET_OWNER','host:owner');monkeypatch.setenv('HARBOR_FLEET_FENCE','1')
 row=dict(workbook='0001',state='active',owner='host:owner',fence=1,source_sha256='s',version='v',expires=200);status=dict(at=100,workbooks=[row],operations=[])
 if fault=='wrong_owner':row['owner']='foreign'
 if fault=='wrong_version':row['version']='foreign'
 if fault=='wrong_source':row['source_sha256']='foreign'
 if fault=='expired':row['expires']=99
 if fault=='own_operation':status['operations']=[dict(workbook='0001',state='active')]
 class Client:
  def __init__(self,*a):pass
  def call(self,action,**kw):assert action=='status'and not kw;return status
 with patch.object(coordinator,'Client',Client),patch.object(P.fresh,'load_module',return_value=SimpleNamespace(validate_occupancy=lambda s:None,model_admission_barriers=lambda *a:[])),pytest.raises(ValueError):P.wait_author_admission(p,time.monotonic()+10)

def test_original_child_resource_guard_is_used_and_recorded(flow):
 from scripts.batch import hardened_runner
 p,source,old,template,selected,tmp=flow;Path(p['home']).mkdir()
 with patch.object(hardened_runner,'_run_in_process_group',return_value=0)as guard:
  result=P.guarded_phase(p,'source',['python','-I','-B','original-source'],7650,16*1024**3,models=False)
  assert guard.call_args.kwargs['memory_limit_bytes']==16*1024**3 and guard.call_args.kwargs['timeout_seconds']==7650
  assert json.loads(Path(result['path']).read_text())['returncode']==0
  with pytest.raises(FileExistsError):P.guarded_phase(p,'source',['python'],7650,16*1024**3,models=False)
  assert guard.call_count==1

def test_source_phase_is_observed_but_author_capacity_is_reserved(flow):
 from scripts.fleet import pool_v5 as pool
 p,source,old,template,selected,tmp=flow;job=dict(driver={'path':str(ROOT/'scripts/fleet/staged_pipeline.py')},inputs=[selected],workbook='0001',cwd=str(ROOT))
 assert pool.job_phase(job)=='author'
 assert pool.observed_job_phase(job)==dict(capacity_reservation='author',actual_phase='NOT_STARTED')
 Path(p['home']).mkdir();put(Path(p['home']),'source-intent.json',{})
 assert pool.observed_job_phase(job)['actual_phase']=='SOURCE_RECONSTRUCTION'
 put(Path(p['home']),'source-verified.json',{})
 assert pool.observed_job_phase(job)['actual_phase']=='WAITING_FOR_AUTHOR_ADMISSION'
 assert pool.job_phase(job)=='author'
 job['workbook']='foreign'
 with pytest.raises(ValueError,match='detached'):pool.observed_job_phase(job)

@pytest.fixture
def closed_selection(tmp_path):
 old=json.loads((ROOT/'diagnostics/vm-fleet-20260912/control/source-pool-staged-pipeline-001/0032-history-002.json').read_text());old['home']=str(tmp_path/'workbook')
 inventory=put(tmp_path,'inventory.json',{'sources':[{'workbook_id':'0032',**old['original']}]});source=dict(schema_version='fleet-source-first/v3',workbook='0032',home=old['home']+'/source',models=0,next_attempt=2,cumulative_reconstruction_limit=2,legacy_research=put(tmp_path,'history.json',old),inventory=inventory,history_roots=old['history_roots'],source={'workbook_id':'0032',**old['original']},seconds=7200)
 queue=put(tmp_path,'queue.json',{'items':[dict(workbook_id='0032',original_source=source['source'],history_class='legacy_model_artifacts',preserved_history=old['histories']['0032'],preserved_curations=old['curations'])]})
 template=dict(schema_version=P.fresh.SCHEMA,workbook='0032',queue=queue,inventory=inventory,history_roots=old['history_roots'],code_root=str(ROOT),home=old['home']+'/authoring',preparer=binding(ROOT/'scripts/fleet/fresh.py'),dispatcher=binding(ROOT/'scripts/fleet/advance_authoring_v8.py'),taxonomy=binding(ROOT/'scripts/fleet/fresh.py'),env_file=None,batch='test',owner='test',publication_root=str(tmp_path/'publication'),output_root=str(tmp_path/'output'),reconstruction=dict(mode='existing',handoff=None,generated_curation=None))
 p=dict(schema_version=P.SCHEMA,workbook='0032',source_plan=put(tmp_path,'source.json',source),author_template=put(tmp_path,'template.json',template),home=old['home']+'/pipeline',admission_policy=binding(ROOT/'scripts/fleet/capacity_policy.py'),total_seconds=86400,author_memory_bytes=8*1024**3,automatic_retry=False)
 return p,source,old,template,tmp_path

@pytest.mark.parametrize('defect',[None,'extra_field','wrong_workbook','source_models','counter_reset','template_home','template_source','template_code','template_handoff','guard_bool','retry'])
def test_pipeline_selection_is_closed_and_keeps_original_author_scope(closed_selection,defect):
 p,source,old,template,tmp=closed_selection
 if defect=='extra_field':p['unbounded']=True
 if defect=='wrong_workbook':p['workbook']='foreign'
 if defect=='source_models':source['models']=1
 if defect=='counter_reset':source['next_attempt']=1
 if defect=='template_home':template['home']='/foreign'
 if defect=='template_source':template['inventory']={}
 if defect=='template_code':template['code_root']='/foreign'
 if defect=='template_handoff':template['reconstruction']['handoff']={}
 if defect=='guard_bool':p['author_memory_bytes']=True
 if defect=='retry':p['automatic_retry']=True
 p['source_plan']=put(tmp,'selected-source.json',source);p['author_template']=put(tmp,'selected-template.json',template);selected=put(tmp,'selected.json',p)
 with patch.object(P.source_first,'load',return_value=(source,{'root':str(ROOT)},None,None)),patch.object(P.S,'validate',return_value=old):
  if defect:
   with pytest.raises((ValueError,KeyError)):P.load(selected)
  else:assert P.load(selected)==(p,source,old,template)

@pytest.mark.parametrize('actual_phase',['SOURCE_RECONSTRUCTION','AUTHORING'])
def test_reserve_counts_two_source_phase_pipelines_as_two_authors(tmp_path,actual_phase):
 from scripts.fleet import pool_v5 as pool
 from scripts.fleet.coordinator import Coordinator
 c=Coordinator(tmp_path/'coordinator.sqlite',clock=lambda:1000.)
 for wb in ('0001','0002','0003'):
  c.enroll(wb,'a'*64,'selected source');c.reconcile(wb,'ready')
 for wb in ('0001','0002'):c.claim(wb,'hhu-server-3:slot-'+wb+':pipeline','id-'+wb,'v',lease_seconds=180)
 root=tmp_path/'pool';(root/'dispatches').mkdir(parents=True)
 job=dict(workbook='0003',source={'sha256':'a'*64},version='v',driver={'path':str(ROOT/'scripts/fleet/retained_warning_continue.py')},inputs=[],run=str(tmp_path/'unused'),cwd=str(ROOT),memory_limit_bytes=8*1024**3,minimum_disk_bytes=1)
 selected=put(tmp_path,'job.json',job)
 census={wb:dict(phase='author',actual_phase=actual_phase,capacity_reservation='author',rss_bytes=100)for wb in ('0001','0002')}
 admission=SimpleNamespace(validate_occupancy=lambda s:None,model_admission_barriers=lambda *a:[])
 policy=dict(max_total_workers=4,max_source_workers=2,max_author_workers=2,headroom_bytes=4*1024**3)
 with patch.object(pool,'admission_module',return_value=admission),patch.object(pool,'native_census',return_value=census),patch.object(pool,'resources')as resources:
  assert pool.reserve(root,[(selected,job)],c.status(),0,'hhu-server-3',policy,{})is None
  resources.assert_not_called()
 assert not list((root/'dispatches').iterdir())
 assert [r['state']for r in c.status()['workbooks']if r['workbook']=='0003']==['ready']

def test_two_existing_sources_block_a_new_pipeline_despite_free_author_capacity(tmp_path):
 from scripts.fleet import pool_v5 as pool
 from scripts.fleet.coordinator import Coordinator
 c=Coordinator(tmp_path/'coordinator.sqlite',clock=lambda:1000.)
 for wb in ('0001','0002','0003'):
  c.enroll(wb,'a'*64,'selected source');c.reconcile(wb,'ready')
 for wb in ('0001','0002'):c.claim(wb,'hhu-server-3:slot-'+wb+':source','id-'+wb,'v',lease_seconds=180)
 root=tmp_path/'pool';(root/'dispatches').mkdir(parents=True)
 job=dict(workbook='0003',source={'sha256':'a'*64},version='v',driver={'path':str(ROOT/'scripts/fleet/staged_pipeline.py')},inputs=[],run=str(tmp_path/'unused'),cwd=str(ROOT),memory_limit_bytes=16*1024**3,minimum_disk_bytes=1)
 selected=put(tmp_path,'job.json',job);census={wb:dict(phase='source',rss_bytes=100)for wb in ('0001','0002')}
 policy=dict(max_total_workers=4,max_source_workers=2,max_author_workers=2,headroom_bytes=4*1024**3)
 with patch.object(pool,'admission_module',return_value=SimpleNamespace(validate_occupancy=lambda s:None,model_admission_barriers=lambda *a:[])),patch.object(pool,'native_census',return_value=census),patch.object(pool,'resources')as resources:
  assert pool.reserve(root,[(selected,job)],c.status(),0,'hhu-server-3',policy,{})is None
  resources.assert_not_called()
 assert not list((root/'dispatches').iterdir())

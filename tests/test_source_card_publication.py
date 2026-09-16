from pathlib import Path
import ast,json
import pytest
from scripts.fleet import source_card_publication as P,build_retained_warning_publication as B

def fixture():
 values={}
 def add(path,value):
  row={'path':path,'sha256':'a'*64};values[path]=value;return row
 helper=add('/helper.py',{});config=add('/config.json',{});prior=add('/prior.json',{})
 runtime=add('/runtime.json',{});oldjob=add('/oldjob.json',{'config':config})
 tail=add('/tail.json',dict(schema_version='fleet-source-card-completed-tail/v1',helper=helper,workbook='0200',source={'sha256':'s'},version='version',prior_plan=prior,runtime_profile=runtime,output='/tail',operations='/ops',job=oldjob))
 ready=add('/tail/result.json',dict(schema_version='fleet-source-card-completed-tail/v1',status='PUBLICATION_READY',plan=tail,workbook='0200',runtime=runtime,models_dispatched=0,publication_performed=False,outcome=add('/ops/000/outcome.json',{})))
 job=add('/job.json',dict(inputs=[tail],driver=helper,result=ready['path'],source={'sha256':'s'},version='version'))
 root=add('/launch-plan.json',{'job':job});intent=add('/intent.json',{'plan':root});launch=add('/launched.json',dict(intent=intent,remote={'job':job,'root_intent':intent},automatic_retry=False))
 plan=dict(workbook='0200',source_sha256='s',code_root='/code/version',ready=ready,source_card_history=dict(tail_plan=tail,tail_helper=helper,root_launch=launch,job=job,config=config,queued_transaction=add('/transaction.json',{})))
 def require(ok,message):
  if not ok:raise ValueError(message)
 return plan,values,dict(checked=lambda row:values[row['path']],binding=lambda path:dict(path=path,sha256='a'*64),require=require)

def test_actual_enqueue_linked_to_job_and_source():
 p,v,k=fixture();assert P.history_binding(p,**k)==p[P.KEY]['tail_plan']

@pytest.mark.parametrize('change',[
 lambda p,v:v['/tail/result.json'].update(status='STEP_COMPLETE'),
 lambda p,v:v['/tail/result.json'].update(models_dispatched=1),
 lambda p,v:v['/tail/result.json'].update(publication_performed=True),
 lambda p,v:v['/tail/result.json'].update(runtime={}),
 lambda p,v:v['/tail/result.json'].update(outcome={'path':'/ops/001/outcome.json'}),
 lambda p,v:v['/job.json'].update(version='foreign'),
 lambda p,v:v['/oldjob.json'].update(config={}),
 lambda p,v:v['/launched.json']['remote'].update(root_intent={}),
 lambda p,v:p[P.KEY].update(unreviewed={}),
])
def test_partial_or_detached_publication_refused(change):
 p,v,k=fixture();change(p,v)
 with pytest.raises((ValueError,KeyError)):P.history_binding(p,**k)

def test_explicit_reader_keeps_owning_publisher_functions_exact(tmp_path):
 old=Path('diagnostics/vm-fleet-20260912/control/0029-publication-003/collector.py').resolve();reader=Path(P.__file__).resolve();B.build(tmp_path/'pub',old,reader_source=reader)
 manifest=json.loads((tmp_path/'pub/manifest.json').read_text());assert Path(manifest['dependencies']['reader']['path']).read_bytes()==reader.read_bytes()
 def funcs(p):return {n.name:ast.dump(n,include_attributes=False)for n in ast.parse(p.read_text()).body if isinstance(n,ast.FunctionDef)}
 before=funcs(old);after=funcs(tmp_path/'pub/publish.py');assert {n for n in before if before[n]!=after[n]}=={'history_binding','publication_environment','remote_child'}
 for name in ('controlled','pack','collect'):assert before[name]==after[name]

def actual_queue(tmp_path,monkeypatch):
 from types import SimpleNamespace
 from test_harbor_publish import setup
 from scripts import harbor_publish as owning
 from scripts.batch import harbor_ready_transitions as ready
 request,intent,tracker,queued,journal,_=setup(tmp_path,monkeypatch)
 transaction=owning.read(queued['journal'])
 api=SimpleNamespace(require=owning.require,checked=owning.read,binding=owning.record)
 plan={'workbook':'0245',P.KEY:{'queued_transaction':queued['journal']},'journal':str(tmp_path/'selected')}
 projection={'after':transaction['before']}
 pubjournal=Path(plan['journal'])/'owning-publication';pubjournal.mkdir(parents=True)
 owning._keep(pubjournal/'intent.json',owning.read(intent))
 return api,plan,projection,request,owning,ready,tracker,queued,journal

def test_actual_original_queue_rederived_then_original_publication_lease_accepted(tmp_path,monkeypatch):
 api,plan,projection,r,p,ready,path,queued,journal=actual_queue(tmp_path,monkeypatch)
 raw=path.read_bytes();after=P.queued_ancestry(api,plan,projection,r,p,ready,proof=True)
 P.publication_tracker(api,plan,p,r,after,after);assert path.read_bytes()==raw
 acquired=p._lease_step(path,journal,after,'acquire','publication',p.OWNER)
 raw=path.read_bytes();P.publication_tracker(api,plan,p,r,after,acquired);assert path.read_bytes()==raw

@pytest.mark.parametrize('change',['same_workbook_commit','same_workbook_evidence','foreign_publication_intent','wrong_original_commit'])
def test_same_workbook_unrelated_tracker_or_commit_rejected(tmp_path,monkeypatch,change):
 import copy
 api,plan,projection,r,p,ready,path,queued,journal=actual_queue(tmp_path,monkeypatch)
 after=P.queued_ancestry(api,plan,projection,r,p,ready,proof=True)
 if change=='wrong_original_commit':
  wrong=copy.deepcopy(p.read(projection['after']));wrong['revision']+=1
  projection['after']=p._keep(tmp_path/'wrong-commit.json',wrong)
  with pytest.raises(ValueError,match='detached'):P.queued_ancestry(api,plan,projection,r,p,ready,proof=True)
  return
 acquired=p._lease_step(path,journal,after,'acquire','publication',p.OWNER)
 if change=='same_workbook_commit':acquired['last_gate']='different_commit'
 elif change=='same_workbook_evidence':acquired['bindings']['staged_bundle']['sha256']='f'*64
 else:
  row=Path(plan['journal'])/'owning-publication/intent.json';value=p.read(p.record(row));value['code']={};row.write_bytes(p.lifecycle.canonical(value))
 with pytest.raises(ValueError):P.publication_tracker(api,plan,p,r,after,acquired)

def test_forged_queue_with_recomputed_file_hash_rejected_by_original_after(tmp_path,monkeypatch):
 api,plan,projection,r,p,ready,path,queued,journal=actual_queue(tmp_path,monkeypatch)
 from dataclasses import replace
 d=p.read(plan[P.KEY]['queued_transaction']);after=p.read(d['after']);after['last_gate']='forged'
 afterpath=Path(d['after']['path']);afterpath.write_bytes(p.lifecycle.canonical(after));d['after']=p.record(afterpath)
 tx=Path(plan[P.KEY]['queued_transaction']['path']);tx.write_bytes(p.lifecycle.canonical(d));plan[P.KEY]['queued_transaction']=p.record(tx)
 (tx.parent/'complete.json').write_bytes(p.lifecycle.canonical(dict(tracker_sha256=d['after']['sha256'],notes_sha256=d['notes']['sha256'])))
 r=replace(r,lease=replace(r.lease,sha256=d['after']['sha256']))
 with pytest.raises(ValueError,match='rederived'):P.queued_ancestry(api,plan,projection,r,p,ready,proof=True)

def test_actual_original_promotion_and_released_tracker_retain_exact_queue_ancestry(tmp_path,monkeypatch):
 api,plan,projection,r,p,ready,path,queued,journal=actual_queue(tmp_path,monkeypatch)
 # The original owning executor writes the same selected journal whose lineage
 # the publication-only reader checks; no synthetic promotion is asserted.
 selected=Path(plan['journal'])/'owning-publication'
 intent=p.record(selected/'intent.json');p.execute(intent)
 after=P.queued_ancestry(api,plan,projection,r,p,ready,proof=True)
 current=p.read(p.record(path));raw=path.read_bytes()
 P.publication_tracker(api,plan,p,r,after,current)
 promoted=p.read(p.read(p.record(selected/'promoted.json'))['tracker'])
 P.publication_tracker(api,plan,p,r,after,promoted)
 assert path.read_bytes()==raw
 current['bindings']['staged_bundle']['sha256']='f'*64
 with pytest.raises(ValueError):P.publication_tracker(api,plan,p,r,after,current)

def recovered_history_fixture():
 p,v,k=fixture();tail=v['/tail.json'];tail.update(owner='exact',runtime={'path':'/rt.py','sha256':'a'*64})
 rp={'path':'/recovery.json','sha256':'a'*64};helper={'path':'/recovery.py','sha256':'a'*64}
 v[rp['path']]=dict(schema_version='fleet-prepared-enqueue-recovery/v1',tail_plan=p[P.KEY]['tail_plan'],tail_helper=p[P.KEY]['tail_helper'],helper=helper,transaction=p[P.KEY]['queued_transaction'],output='/recovered',runtime_profile=tail['runtime_profile'],**{key:tail[key]for key in ('workbook','owner','source','version','runtime')})
 p[P.KEY]['recovery_plan']=rp;v['/recovered/result.json']={**v['/tail/result.json'],'schema_version':'fleet-prepared-enqueue-recovery/v1','plan':rp};p['ready']={'path':'/recovered/result.json','sha256':'a'*64}
 v['/job.json'].update(driver=helper,inputs=[rp],result=p['ready']['path'])
 return p,v,k

def test_prepared_recovery_is_explicit_not_relabelled_failed_step():
 p,v,k=recovered_history_fixture();assert P.history(p,**k)[3]['plan']==p[P.KEY]['recovery_plan']

@pytest.mark.parametrize('field',['tail_plan','tail_helper','transaction','source','owner','version'])
def test_recovery_cannot_replace_original_history(field):
 p,v,k=recovered_history_fixture();v['/recovery.json'][field]={}
 with pytest.raises((ValueError,KeyError)):P.history(p,**k)

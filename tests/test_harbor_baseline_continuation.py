import copy,json
from pathlib import Path
import pytest
from scripts.batch import harbor_baseline_continuation as B,harbor_source_only_continuation as S
from scripts.batch.harbor_admission import record,AdmissionError


def keep(p,value):
 p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value));return record(p)


def failed(tmp):
 home=tmp/'home';b=home/'bootstrap/baseline';b.mkdir(parents=True);(b/'command.log').write_text('numeric value interpreted as time')
 keep(b/'result.json',dict(returncode=1,log=record(b/'command.log')));keep(b/'intent.json',{'old':'command'});keep(home/'bootstrap/prepared.json',{})
 keep(home/'environment/selection.json',{});error=keep(home/'environment/unresolved-baseline-123.json',dict(status='UNRESOLVED',step='baseline',automatic_retry=False,source_invalidity_claimed=False))
 tracker=dict(lane_state={lane:dict(state='passed',attempt=1,confidence_history=[{}],current_confidence=1)for lane in B.PASSED})
 tracker['lane_state']['research']=dict(state='pending',attempt=0,confidence_history=[],current_confidence=None)
 return home,tracker,error


def test_source_only_default_stays_strict_but_explicit_baseline_keeps_both_passed_lanes(tmp_path):
 home,tracker,error=failed(tmp_path)
 with pytest.raises(AdmissionError):S.pristine(home,tracker)
 assert B.pristine(home,tracker)==error
 S.pristine(home,tracker,baseline=keep(tmp_path/'explicit.json',{'schema_version':B.BOUNDARY}))


@pytest.mark.parametrize('kind',['completed','uncertain','audit','model','instruction','lane','extra-error','segmentation'])
def test_no_completed_or_uncertain_downstream_work_can_be_reset(tmp_path,kind):
 home,t,error=failed(tmp_path)
 if kind=='completed':keep(home/'bootstrap/baseline/complete.json',{})
 elif kind=='uncertain':keep(home/'bootstrap/baseline/result.json',dict(returncode=None,log=record(home/'bootstrap/baseline/command.log')))
 elif kind=='audit':(home/'bootstrap/audit').mkdir()
 elif kind in ('model','instruction'):(home/('model-campaign'if kind=='model'else'instruction')).mkdir()
 elif kind=='lane':t['lane_state']['research']['attempt']=1
 elif kind=='segmentation':t['lane_state']['harbor-segmentation-analyst']['state']='pending'
 else:keep(home/'environment/unresolved-baseline-456.json',{})
 with pytest.raises(AdmissionError):B.pristine(home,t)


def test_failed_attempt_inventory_detects_changes_and_symlinks(tmp_path):
 home,_,_=failed(tmp_path);path=home/'bootstrap/baseline';before=B.inventory(path)
 (path/'command.log').write_text('changed');assert B.inventory(path)!=before
 (path/'alias').symlink_to(path/'command.log')
 with pytest.raises(AdmissionError):B.inventory(path)


def test_actual_guarded_mask_proof_requires_selected_bytes_and_workbook(tmp_path):
 mask=tmp_path/'xl_input_mask.py';mask.write_text('mask code');probe=tmp_path/'probe.py';probe.write_text('original CLI proof')
 result=dict(status='PASS',workbook='0012',code=record(mask),models=0,claims=0,original_failure_preserved=True)
 guard=dict(returncode=0,failure=None,models=0,lease_mutations=0,seconds=900,memory_bytes=8*1024**3,probe=record(probe),log='original checks\n'+json.dumps(result)+'\n')
 row=keep(tmp_path/'native.json',dict(returncode=0,stdout=json.dumps(guard)))
 B.validated_mask(row,'0012',record(mask))
 with pytest.raises(AdmissionError):B.validated_mask(row,'0065',record(mask))
 guard['returncode']=None;row=keep(tmp_path/'native.json',dict(returncode=0,stdout=json.dumps(guard)))
 with pytest.raises(AdmissionError):B.validated_mask(row,'0012',record(mask))


def test_code_delta_cannot_change_pipeline_quality_or_model_limits(tmp_path):
 oldroot=tmp_path/'old';newroot=tmp_path/'new';oldroot.mkdir();newroot.mkdir()
 (oldroot/'xl_input_mask.py').write_text('old');(newroot/'xl_input_mask.py').write_text('corrected')
 old=keep(tmp_path/'old.json',dict(root=str(oldroot),files=[dict(relative='xl_input_mask.py',sha256=record(oldroot/'xl_input_mask.py')['sha256']),dict(relative='quality.py',sha256='same')]))
 new=keep(tmp_path/'new.json',dict(root=str(newroot),files=[dict(relative='xl_input_mask.py',sha256=record(newroot/'xl_input_mask.py')['sha256']),dict(relative='quality.py',sha256='same')]))
 assert B.code_delta(old,new,record(newroot/'xl_input_mask.py'))==['xl_input_mask.py']
 v=json.loads(Path(new['path']).read_text());v['files'][1]['sha256']='changed';new=keep(Path(new['path']),v)
 with pytest.raises(AdmissionError,match='unrelated'):B.code_delta(old,new,record(newroot/'xl_input_mask.py'))


def test_selected_code_checks_running_root_full_inventory_and_actual_bytes(tmp_path,monkeypatch):
 from scripts import harbor_campaign as C
 root=tmp_path/'code';root.mkdir();(root/'xl_input_mask.py').write_text('corrected')
 files={'xl_input_mask.py':record(root/'xl_input_mask.py')['sha256']}
 keep(root/'.pipeline-code.json',files)
 rows=[dict(relative=k,sha256=v)for k,v in files.items()]+[dict(relative='.pipeline-code.json',sha256=record(root/'.pipeline-code.json')['sha256'])]
 snapshot=keep(tmp_path/'snapshot.json',dict(root=str(root),files=rows))
 monkeypatch.setattr(C,'ROOT',root);B.selected_code(snapshot)
 monkeypatch.setattr(C,'ROOT',tmp_path/'foreign')
 with pytest.raises(AdmissionError,match='selected code'):B.selected_code(snapshot)
 monkeypatch.setattr(C,'ROOT',root);(root/'xl_input_mask.py').write_text('tampered')
 with pytest.raises(ValueError,match='changed'):B.selected_code(snapshot)


def test_history_allows_new_migrated_campaign_but_preserves_old_failure(tmp_path,monkeypatch):
 home,t,error=failed(tmp_path)
 t.update(workbook_id='0012',bindings={'original_raw_source':{'path':'/original.xlsx','sha256':'original'}},generation_ids={'source':'source-gen','segmentation':'seg-gen'})
 tracker=keep(home/'trackers/workbooks/0012.json',t);archive=keep(tmp_path/'before.json',t)
 oldcode=keep(tmp_path/'old-code.json',{});newcode=keep(tmp_path/'new-code.json',{})
 config=keep(home/'config.json',dict(run_root=str(home),code=oldcode,workbook='0012'))
 descriptor=keep(home/'descriptor.json',{})
 checkpoint=keep(home/'checkpoint.json',dict(descriptor=descriptor,status='UNRESOLVED',step='baseline',publication_ready=False,evidence=[error]))
 witness=keep(tmp_path/'witness.json',dict(schema_version=B.BOUNDARY,workbook='0012',config=config,descriptor=descriptor,checkpoint=checkpoint,tracker=tracker,tracker_archive=archive,old_code=oldcode,new_code=newcode,mask={},native_validation={},failed_files=B.inventory(home/'bootstrap/baseline'),failure=error,source=t['bindings']['original_raw_source'],generation_ids=t['generation_ids']))
 # Independent code/probe validation has focused tests; this is the actual
 # historical boundary across later writes in the retained campaign directory.
 monkeypatch.setattr(B,'selected_code',lambda *a:None)
 monkeypatch.setattr(B,'code_delta',lambda *a:None)
 monkeypatch.setattr(B,'validated_mask',lambda *a:None)
 args=dict(config=config,descriptor=descriptor,checkpoint=checkpoint,tracker=tracker,new_code=newcode)
 B.validate(witness,**args)
 (home/'model-campaign').mkdir()
 with pytest.raises(AdmissionError,match='namespace'):B.validate(witness,**args)
 B.validate(witness,**args,require_pristine=False)
 (home/'bootstrap/baseline/command.log').write_text('changed')
 with pytest.raises(AdmissionError,match='bytes'):B.validate(witness,**args,require_pristine=False)


def test_tracker_import_preserves_actual_lifecycle_bytes_and_lease_hash(tmp_path):
 from types import SimpleNamespace
 value={'revision':3,'workbook_id':'0012','leases':{'task_agent':{'owner':'owner','token':'private'}}}
 raw=S.C.lifecycle.canonical(value);lease=SimpleNamespace(sha256=S.C.executor.sha(raw))
 saved=S.snapshot_initial_tracker(tmp_path,value,lease)
 assert Path(saved['path']).read_bytes()==raw and saved['sha256']==lease.sha256
 assert S.C.executor.canonical(value)!=raw
 wrong=tmp_path/'wrong';wrong.mkdir()
 with pytest.raises(AdmissionError,match='serialization'):
  S.snapshot_initial_tracker(wrong,value,SimpleNamespace(sha256='0'*64))
 assert not list(wrong.iterdir())

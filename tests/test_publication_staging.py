"""A fresh native staging manifest must not omit root proof or publisher custody."""
from pathlib import Path
import json,subprocess,sys
import pytest
from scripts.fleet.migration import retain,binding
from scripts.fleet import publication_staging as S

@pytest.fixture
def case(tmp_path):
 def put(name,value):return retain(tmp_path/name,value)
 dependency=put('reader.json',{'fixed':'reader'})
 manifest=put('manifest.json',{'dependencies':{'fresh':dependency}})
 p={key:put(key+'.json',{'name':key}) for key in ('plan','publisher','proof','guard','capacity_policy','process_copy')}
 p.update(implementation=manifest,worker_outcome={'path':'/native/owned/outcome.json','sha256':'a'*64},process={'path':'/native/owned/process.json','sha256':'b'*64})
 return tmp_path,put('profile.json',p),p,dependency

def test_stages_root_proof_manifest_reader_and_process_copy(case):
 root,profile,p,dependency=case;out=root/'stage';out.mkdir();r=S.prepare(profile,out);files=json.loads(Path(r['path']).read_bytes())
 for row in (profile,p['proof'],p['guard'],p['implementation'],p['process_copy'],dependency):assert row['path'] in files
 assert p['worker_outcome']['path'] not in files and p['process']['path'] not in files
 completed=subprocess.run([sys.executable,'-I','-B',files[-1]],capture_output=True)
 assert completed.returncode==0 and b'PUBLICATION_ROOT_FILES_VERIFIED'in completed.stdout
 Path(p['proof']['path']).unlink()
 assert subprocess.run([sys.executable,'-I','-B',files[-1]],capture_output=True).returncode!=0

def test_capture_and_report_are_included(case):
 root,profile,p,dependency=case
 capture=retain(root/'capture.json',{'raw':True});report=retain(root/'qualification.json',{'checks':[]})
 p['qualification_capture']=retain(root/'capture-binding.json',{'capture':capture,'qualification':report});new=retain(root/'profile2.json',p)
 assert all(x in S.rows(new)for x in (capture,report,p['qualification_capture']))


def test_completed_admission_archive_is_staged_without_current_tracker(case):
 root,profile,p,dependency=case
 archive=retain(root/'archived-tracker.json',{'revision':2})
 current={'path':'/native/live/tracker.json','sha256':'a'*64}
 history=retain(root/'input-history.json',dict(schema_version='fleet-completed-tracker-input/v1',
  input=current,archive=archive,proof=p['proof'],ready={'path':'/native/ready.json','sha256':'b'*64}))
 selected=retain(root/'history-profile.json',dict(p,input_history=history))
 assert archive in S.rows(selected) and current not in S.rows(selected)
 Path(archive['path']).write_text('{"revision":3}')
 with pytest.raises(ValueError):S.rows(selected)

def test_changed_dependency_rejected_before_staging(case):
 root,profile,p,dependency=case;Path(dependency['path']).write_text('{}')
 with pytest.raises(ValueError):S.rows(profile)

def test_preparation_never_rewrites_existing_selection(case):
 root,profile,p,dependency=case;out=root/'stage';out.mkdir();S.prepare(profile,out);before=(out/'stage-bindings.json').read_bytes()
 with pytest.raises((ValueError,FileExistsError)):S.prepare(profile,out)
 assert (out/'stage-bindings.json').read_bytes()==before


def test_probe_checks_child_selection_before_disabling_original_run():
 text='before=True\nP.run=P.execute=P._lease_step=P.schema.cas_mutate=no\nafter=True\n'
 result=S.child_selection_probe(text)
 assert result.index('reader_evidence(pub,plan)')<result.index('publication_child_adapter.select')<result.index('P.run=P.execute')
 assert 'physical_context'not in result  # Owning dispatch selects the lineage; probe does not fabricate it.
 with pytest.raises(ValueError):S.child_selection_probe('different boundary')


def test_preowning_stages_exact_prior_proof_and_settlement_without_owned_files(case):
 root,profile,p,dependency=case
 settled_intent=retain(root/'settled-intent.json',{'effect':'abandoned'})
 settled=retain(root/'settled.json',{'intent':settled_intent})
 edge=retain(root/'edge.json',{'schema_version':'fleet-publication-semantic-evidence-recovery/v1','prior_profile':profile,'root_intent':retain(root/'old-root-intent.json',{}),'failure_proof':retain(root/'stopped-proof.json',{}),'reconciliation':settled})
 successor=retain(root/'successor.json',dict(p,preowning_recovery=edge))
 selected=S.rows(successor)
 assert edge in selected and profile in selected and settled_intent in selected
 assert p['worker_outcome'] not in selected


def test_direct_probe_checks_actual_adapter_before_qualification():
 from types import SimpleNamespace
 calls=[]
 def run():pass
 def selected():pass
 selected.declared_runner=run
 owning=SimpleNamespace(run=selected)
 evidence=[{'path':'/retained/actual.json','sha256':'a'*64}]
 def select(p,**kwargs):
  assert p is owning and kwargs==dict(reader_plan={'path':'/plan'},child_wrapper={'path':'/publisher'},reader_evidence=evidence)
  calls.append('selected');return selected
 pub=SimpleNamespace(publication_child_adapter=SimpleNamespace(select=select),warning_publication=SimpleNamespace(evidence=lambda plan:evidence))
 def capture(*args):calls.append('qualification');return 'captured'
 text="if True:\n start=time.monotonic();result=Q.capture_publication()\n"
 scope=dict(pub=pub,env=[None,None,owning],row={'path':'/plan'},PUBLISHER={'path':'/publisher'},plan={'retained_warning':True},time=SimpleNamespace(monotonic=lambda:0),Q=SimpleNamespace(capture_publication=capture))
 exec(S.direct_child_selection_probe(text),scope)
 assert calls==['selected','qualification']
 def fail(*args,**kwargs):raise ValueError('missing declared evidence')
 pub.publication_child_adapter.select=fail;calls.clear()
 with pytest.raises(ValueError,match='missing declared evidence'):exec(S.direct_child_selection_probe(text),scope)
 assert not calls
 with pytest.raises(ValueError):S.direct_child_selection_probe('unknown capture boundary')

def test_version_custody_stages_local_bytes_without_native_original(case):
 root,profile,p,dependency=case
 local=retain(root/'copied-native-job.json',{'exact':'retained'})
 native=dict(path='/native/owned/original-job.json',sha256=local['sha256'])
 custody=retain(root/'version-custody.json',dict(schema_version='fleet-publication-version-custody/v1',files=[dict(original=native,local=local)]))
 selected=S.rows(retain(root/'version-profile.json',dict(p,version_custody=custody)))
 assert custody in selected and local in selected and native not in selected

@pytest.mark.parametrize('schema',['fleet-publication-owning-recovery/v1','fleet-publication-preexecute-recovery/v1'])
def test_owning_resume_stages_root_intent_and_authority_not_native_journal(case,schema):
 root,profile,p,dependency=case
 put=lambda n,v:retain(root/n,v)
 original_intent={'path':'/native/owning/intent.json','sha256':'f'*64}
 copied=put('native-intent-copy',{})
 settled_intent=put('settled-intent',{});settled=put('settled',{'intent':settled_intent})
 failure=put('failure',{});auth=put('authorization',{});policy=put('policy',{'authorization':auth})
 resume=put('resume',dict(prior_plan=p['plan'],failure_proof=failure,reconciliation=settled,policy=policy,intent=original_intent))
 root_intent=put('root-intent',{})
 edge=put('owning-edge',dict(schema_version=schema,prior_profile=profile,root_intent=root_intent,failure_proof=failure,reconciliation=settled,resume=resume,guard_implementation=p['implementation'],copies=[dict(original=original_intent,local=copied)]))
 selected=S.rows(put('resume-profile',dict(p,owning_recovery=edge)))
 assert all(x in selected for x in (root_intent,resume,settled_intent,auth,copied))
 assert original_intent not in selected and p['worker_outcome']not in selected

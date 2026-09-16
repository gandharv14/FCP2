"""New work preserves mismatched old artifacts, counters and all target choices."""
from pathlib import Path
import copy,json,time
import pytest
from scripts.batch import harbor_legacy_research_history as L,harbor_legacy_opaque_history as O
from scripts.fleet.build_opaque_history import prepare
ROOT=Path(__file__).resolve().parents[1]
AUTH=L.binding(ROOT/'diagnostics/bulk-production/authorization-20260913.md')

def put(root,name,value):
 p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(value if isinstance(value,bytes)else json.dumps(value).encode());return L.binding(p)

def generation(root,kind,source_sha,curation):
 gid=('a'if kind=='source'else'b')*64;base=root/kind/'0001/generations'/gid
 rows={'curation.toml':put(base,'curation.toml',curation)}if kind=='segmentation'else{'raw.xlsx':put(base,'raw.xlsx',b'old raw')}
 value=dict(generation_id=gid,bindings=dict(source_sha256=source_sha),curated_output_cells=['S!B2'],artifacts={n:dict(path=n,sha256=r['sha256'],size_bytes=Path(r['path']).stat().st_size)for n,r in rows.items()})
 return put(base,'generation-manifest.json',value)

@pytest.fixture
def fixture(tmp_path):
 raw=put(tmp_path,'raw/0001.xlsx',b'original');curation=b'[[targets]]\ncell="S!B2"\n';source=generation(tmp_path,'source',raw['sha256'],curation);segment=generation(tmp_path,'segmentation',raw['sha256'],curation);cur=L.binding(Path(segment['path']).parent/'curation.toml')
 baseline=put(tmp_path,'baseline/0001-inputs.xlsx',b'current changed baseline');sidecar=put(tmp_path,'baseline/0001-inputs.segmentation.json',{})
 audit=tmp_path/'history/0001-variable-sources';put(audit,'0001.metadata.json',dict(api_calls=1,status='complete',finish_reasons=['stop']));put(audit,'failed-verdict.json',dict(passed=False,faults=['historical rejection']))
 package=tmp_path/'history/0001-outputs';put(package,'pipeline_bindings.json',dict(source_generation_id='other-generation'));put(package,'instruction.md',b'original instructions')
 tracker=put(tmp_path,'history/workbooks/0001.json',dict(workbook_id='0001',source_candidate=raw['path'],source_health={'source_sha256':raw['sha256']},source_generation_id='a'*64,segmentation_generation_id='b'*64,baseline_inputs={**baseline,'sha256':'0'*64},curation=dict(sha256_before=cur['sha256'],sha256_after=cur['sha256']),status='blocked_old_stage'))
 old=dict(schema_version=L.PACKAGE_SCHEMA,authorization=AUTH,workbook='0001',original=raw,tracker=tracker,curations=[cur],audit_directory=str(audit),audit_files=L.inventory(audit),source_manifest=source,segmentation_manifest=segment,baseline={**baseline,'sha256':'0'*64},baseline_sidecar=sidecar,history_roots=[str(tmp_path/'history')],known_ids=['0001'],excluded={'0001':[str(package)]},histories={'0001':[tracker,*L.inventory(audit).values()]},retained_opaque_research_calls=1,new_source_reconstruction_ordinal=2,new_research_max_calls=16,new_research_max_batches=8,new_research_attempts_per_batch=2,home=str(tmp_path/'old/0001'),created_at_ns=time.time_ns(),expires_at_ns=time.time_ns()+86400*10**9)
 previous=put(tmp_path,'previous.json',old);row=prepare(previous,str(tmp_path/'new/0001'),tmp_path/'authority.json');return tmp_path,old,row

def changed(root,row,mutate):
 p=copy.deepcopy(L.read(row));mutate(p);return put(root,'changed.json',p)

def test_mismatched_old_baseline_and_package_remain_opaque_and_exact(fixture):
 root,old,row=fixture;p=L.validate(row)
 assert L.read(p['tracker'])['baseline_inputs']['sha256']=='0'*64
 assert p['baseline']['sha256']!='0'*64
 assert L.read(p['opaque_history']['prior_selection'])==old
 assert p['opaque_history']['historical_quality_adopted']is False
 assert p['new_source_reconstruction_ordinal']>2
 assert p['retained_opaque_research_calls']>=1
 with pytest.raises(ValueError,match='source-only'):L.author_survey(p)

@pytest.mark.parametrize('fault',['omit_namespace','omit_file','reset_research','reset_source','reset_nat','adopt_quality','source_change','target_change','new_baseline_path','expired','extra_field'])
def test_closed_custody_rejects_tampering_and_resets(fixture,fault):
 root,old,row=fixture
 def mutate(p):
  h=p['opaque_history']
  if fault=='omit_namespace':del h['namespaces'][p['excluded']['0001'][0]]
  if fault=='omit_file':h['namespaces'][p['audit_directory']].pop('failed-verdict.json')
  if fault=='reset_research':p['retained_opaque_research_calls']=0
  if fault=='reset_source':p['new_source_reconstruction_ordinal']=2
  if fault=='reset_nat':h['consumption_upper_bounds']['naturalization_attempts']=0
  if fault=='adopt_quality':h['historical_quality_adopted']=True
  if fault=='source_change':p['original']['sha256']='0'*64
  if fault=='target_change':p['curations'][0]['sha256']='0'*64
  if fault=='new_baseline_path':p['baseline']['path']=str(root/'other.xlsx')
  if fault=='expired':p['expires_at_ns']=0
  if fault=='extra_field':p['trust_old_quality']=True
 with pytest.raises(ValueError):L.validate(changed(root,row,mutate))

def test_added_history_file_invalidates_exact_inventory(fixture):
 root,old,row=fixture;put(Path(old['audit_directory']),'new-request.json',{'status':'submitted'})
 with pytest.raises(ValueError,match='namespace changed'):L.validate(row)

def test_pending_metadata_cannot_be_reclassified_as_opaque_complete(fixture):
 root,old,row=fixture;audit=Path(old['audit_directory']);put(audit,'0001.metadata.json',dict(api_calls=1,status='submitted',finish_reasons=[]))
 with pytest.raises(ValueError,match='unsettled'):O.minimum_consumption({str(audit):L.inventory(audit)})

def test_actual_0277_and_0488_full_custody_still_validate():
 for wb in ('0277','0488'):
  path=ROOT/'diagnostics/bulk-production/opaque-history-003'/(wb+'-history.json')
  if not path.exists():pytest.skip('private retained corpus unavailable')
  p=L.validate(L.binding(path));assert p['opaque_history']['historical_quality_adopted']is False
  assert L.read(p['tracker'])['workbook_id']==wb

def author_successor(root,row):
 p=L.read(row);home=Path(p['home'])/'source';source_plan=put(root,'source-plan.json',dict(legacy_research=row,home=str(home),source=p['original']))
 result=put(home,'result.json',dict(status='RECONSTRUCTION_VERIFIED',plan=source_plan,legacy_research=row,models_dispatched=0,automatic_retry=False,cumulative_reconstruction_ordinal=p['new_source_reconstruction_ordinal']))
 counts=p['opaque_history']['consumption_upper_bounds'];snap=put(root,'snapshot.json',dict(root=str(root/('c'*64)),fingerprint='c'*64))
 a=dict(source_authority=row,source_result=result,snapshot=snap,new_nat_maximum_attempts=2,cumulative_nat_upper_bound=counts['naturalization_attempts']+2,new_dialogue_reviews=2,cumulative_dialogue_reviews_upper_bound=counts['dialogue_reviews']+2)
 return put(root,'author.json',{**p,'schema_version':O.AUTHOR_SCHEMA,'authoring':a})

def test_only_completed_new_source_grants_additive_author_allowance(fixture):
 root,old,row=fixture;author=author_successor(root,row);p=L.validate(author);excluded,histories=L.author_survey(p)
 assert '0001'not in excluded and histories==p['histories']
 assert p['authoring']['new_nat_maximum_attempts']==p['authoring']['new_dialogue_reviews']==2
 for key in ('cumulative_nat_upper_bound','cumulative_dialogue_reviews_upper_bound'):
  with pytest.raises(ValueError,match='consumption'):L.validate(changed(root,author,lambda x:x['authoring'].__setitem__(key,2)))
 result=L.read(p['authoring']['source_result']);result['status']='RECONSTRUCTION_HELD';new=put(root,'failed-source.json',result)
 with pytest.raises(ValueError,match='qualification'):L.validate(changed(root,author,lambda x:x['authoring'].__setitem__('source_result',new)))

@pytest.mark.parametrize('status',[None,'incomplete','pending','unknown'])
def test_executor_uncertainty_stays_held_even_with_no_live_process(tmp_path,status):
 root=tmp_path/'call';put(root,'plan.json',dict(schema_version='harbor-model-execution/v1'));put(root,'request.json',{});put(root,'submitted.json',dict(attempt_id='id',request={}))
 put(root,'transport-00.started.json',dict(attempt_id='id'))
 if status is not None:put(root,'outcome.json',dict(schema_version='harbor-model-execution/v1',status=status,semantic_approval=False,evidence={}))
 with pytest.raises(ValueError,match='incomplete or unsealed'):O.settled_history({str(root):L.inventory(root)})

def test_terminal_executor_rejection_retains_exact_seal_and_no_quality(tmp_path):
 root=tmp_path/'call';put(root,'plan.json',dict(schema_version='harbor-model-execution/v1'));put(root,'request.json',{});put(root,'submitted.json',dict(attempt_id='id',request={}))
 evidence={name:r['sha256']for name,r in L.inventory(root).items()};put(root,'outcome.json',dict(schema_version='harbor-model-execution/v1',status='http_rejected',semantic_approval=False,evidence=evidence))
 assert O.settled_history({str(root):L.inventory(root)})
 put(root,'request.json',{'changed':True})
 with pytest.raises(ValueError,match='seal'):O.settled_history({str(root):L.inventory(root)})

@pytest.mark.parametrize('status',['staged','pending','writing','unknown'])
def test_pending_naturalization_is_not_cleared_by_new_allowance(tmp_path,status):
 put(tmp_path,'state.json',dict(version='naturalize-recovery-v1',status=status,attempts=[],attempt_limit=2))
 with pytest.raises(ValueError,match='pending or unknown'):O.settled_history({str(tmp_path):L.inventory(tmp_path)})

def test_naturalization_requires_terminal_attempt_validation(tmp_path):
 state=dict(version='naturalize-recovery-v1',status='initialized',attempts=[],attempt_limit=2);put(tmp_path,'state.json',state)
 assert O.settled_history({str(tmp_path):L.inventory(tmp_path)})
 state.update(status='exhausted',attempts=[dict(attempt=1,valid=False),dict(attempt=2,valid=False)]);put(tmp_path,'state.json',state)
 with pytest.raises(ValueError,match='completed validation'):O.settled_history({str(tmp_path):L.inventory(tmp_path)})
 for ordinal in (1,2):put(tmp_path,'attempt-%02d/validation.json'%ordinal,dict(attempt=ordinal,valid=False))
 assert O.settled_history({str(tmp_path):L.inventory(tmp_path)})

def test_nonstate_naturalization_artifact_does_not_impersonate_current_state(tmp_path):
 put(tmp_path,'state.json',dict(version='naturalize-recovery-v1',status='initialized',attempts=[],attempt_limit=2))
 put(tmp_path,'semantic-review.json',dict(version='naturalize-recovery-v1',valid=True))
 put(tmp_path,'apply-transaction/journal.json',dict(version='naturalize-recovery-v1',committed=True))
 assert O.settled_history({str(tmp_path):L.inventory(tmp_path)})
 put(tmp_path,'state.json',dict(version='naturalize-recovery-v1',status='writing',attempts=[],attempt_limit=2))
 with pytest.raises(ValueError,match='pending or unknown'):O.settled_history({str(tmp_path):L.inventory(tmp_path)})

def test_unknown_or_missing_nat_state_schema_stays_held(tmp_path):
 root=tmp_path/'0001-instruction-naturalization';put(root,'state.json',dict(version='unknown',status='applied'))
 with pytest.raises(ValueError,match='unknown original NAT state schema'):O.settled_history({str(root):L.inventory(root)})

def test_opaque_upper_bounds_are_not_reported_as_observed_calls(fixture,monkeypatch):
 import xl_variable_source_audit as audit
 root,old,row=fixture;author=author_successor(root,row);p=L.read(author)
 monkeypatch.setattr(audit,'_split_chunks',lambda inventory:[('header',[])])
 result=L.budget(author,p['histories']['0001'],{},workbook='0001',inherited_budget=None)
 assert result['legacy_calls_opaque_is_upper_bound']is True
 assert result['legacy_recorded_api_calls']==1
 assert result['legacy_calls_opaque']>1 and result['new_maximum_calls']==2

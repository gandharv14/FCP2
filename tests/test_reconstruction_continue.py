import copy,pytest
from scripts.fleet.reconstruction_continue import validate_boundary

def boundary():
 source=dict(workbook_id='0010',sha256='a'*64);p=dict(workbook='0010',source=source,expected_failure='GenerationValidationError:actual certificate refused',generation=None,previous_attempt=1,next_attempt=2,cumulative_reconstruction_limit=2,seconds=7200,memory_bytes=16*1024**3,models=0,automatic_retry=False);ledger=dict(workbook_id='0010',source=source,state='STOPPED',failure='source_policy_unsupported',reason_codes=['data_tables_present'],build=dict(state='STOPPED',stage='presentation_tables',exit_code=1,failure=p['expected_failure'],attempts=1),build_history=[]);policy=dict(limits=dict(stage_seconds=7200,memory_bytes=16*1024**3));return p,ledger,policy

def test_exact_pre_source_failure_retains_counter():validate_boundary(*boundary())

@pytest.mark.parametrize('mutation',['completed_source','source_generation','counter','history','failure','source','route','models','memory','seconds','limit'])
def test_retained_pre_source_boundary_cannot_reset_or_generalize(mutation):
 p,l,a=boundary()
 if mutation=='completed_source':l['build']['source_checkpoint']={'state':'complete'}
 if mutation=='source_generation':l['build']['source_generation_id']='abc'
 if mutation=='counter':l['build']['attempts']=2
 if mutation=='history':l['build_history']=[{}]
 if mutation=='failure':l['build']['failure']='another reason'
 if mutation=='source':l['source']=dict(sha256='b'*64)
 if mutation=='route':l['reason_codes']=['external_links_present']
 if mutation=='models':p['models']=1
 if mutation=='memory':p['memory_bytes']+=1
 if mutation=='seconds':p['seconds']+=1
 if mutation=='limit':p['cumulative_reconstruction_limit']=1
 with pytest.raises(ValueError):validate_boundary(p,l,a)


def test_explicit_restricted_source_boundary_retains_completed_normalization():
 p,l,a=boundary();p['restriction_inventory']={'path':'/inventory','sha256':'d'*64};p['expected_failure']='RecalculationError:restricted pass requires an approved batch inventory';l['build'].update(stage='source',failure=p['expected_failure'],normalization={'candidate':'/original-candidate'})
 validate_boundary(p,l,a)

@pytest.mark.parametrize('fault',['inventory_missing','different_failure','normalization_missing','already_has_source'])
def test_restricted_boundary_does_not_generalize_source_failures(fault):
 p,l,a=boundary();p['restriction_inventory']={'path':'/inventory','sha256':'d'*64};p['expected_failure']='RecalculationError:restricted pass requires an approved batch inventory';l['build'].update(stage='source',failure=p['expected_failure'],normalization={'candidate':'/original-candidate'})
 if fault=='inventory_missing':p['restriction_inventory']=None
 elif fault=='different_failure':p['expected_failure']=l['build']['failure']='source failed'
 elif fault=='normalization_missing':l['build'].pop('normalization')
 else:l['build']['source_checkpoint']={'state':'complete'}
 with pytest.raises(ValueError):validate_boundary(p,l,a)


def test_corpus_clone_preserves_internal_generation_pointers(tmp_path):
 from scripts.fleet.reconstruction_continue import corpus_inventory,clone
 root=tmp_path/'old';g=root/'generations/g';g.mkdir(parents=True);(g/'proof.json').write_text('{"passed":true}');(root/'current').symlink_to('generations/g');(root/'proof.json').symlink_to('current/proof.json')
 closure=dict(corpus=str(root),**corpus_inventory(root));assert len(closure['files'])==1 and len(closure['links'])==2
 target=clone({'home':str(tmp_path/'new')},closure)
 assert (target/'proof.json').read_bytes()==(g/'proof.json').read_bytes()
 assert corpus_inventory(root)=={k:closure[k]for k in ('files','links')}
 assert (target/'current').readlink()==(root/'current').readlink()


@pytest.mark.parametrize('fault',['escape','absolute','dangling'])
def test_corpus_generation_pointer_cannot_escape_or_disappear(tmp_path,fault):
 from scripts.fleet.reconstruction_continue import corpus_inventory
 root=tmp_path/'corpus';root.mkdir();outside=tmp_path/'outside';outside.write_text('evidence')
 target={'escape':'../outside','absolute':str(outside),'dangling':'missing'}[fault];(root/'link').symlink_to(target)
 with pytest.raises((ValueError,FileNotFoundError)):corpus_inventory(root)


def test_changed_relative_pointer_fails_exact_closure(tmp_path):
 from scripts.fleet.reconstruction_continue import corpus_inventory,closure_matches
 root=tmp_path/'corpus';root.mkdir();(root/'one').write_text('one');(root/'two').write_text('two');(root/'current').symlink_to('one');closure=corpus_inventory(root);(root/'current').unlink();(root/'current').symlink_to('two')
 assert not closure_matches(dict(corpus=str(root),restriction_inventory={'path':'bound'}),closure)

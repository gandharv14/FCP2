from pathlib import Path
import pytest
from test_baseline_precommand import case,put
from scripts.batch import harbor_baseline_continuation as B,harbor_baseline_precommand as P,harbor_source_only_continuation as M

def profile(case):
 h,t,q,o=case;validated=P.validate(h,t,q,o);tracker=put(h/'tracker.json',t)
 proof=put(h/'native.json',dict(status='ACTUAL_PRECOMMAND_BOUNDARY_VERIFIED',authoring_files_unchanged=True,models=0,claims=0,module_sha256=P.record(P.__file__)['sha256'],tracker=tracker,result=validated))
 return h,t,dict(schema_version=B.PRECOMMAND,precommand_validator=P.record(P.__file__),native_validation=proof,failed_outcome=o,checkpoint=q,failure=validated['failure'],tracker=tracker)

def test_explicit_precommand_uses_original_validator_then_preserves_historical_absence(case):
 h,t,p=profile(case);bound=put(h/'selected.json',p)
 assert B.precommand(p,h,t)==p['failure'];M.pristine(h,t,baseline=bound)
 with pytest.raises(Exception):B.pristine(h,t)
 (h/'model-campaign').mkdir()
 with pytest.raises(Exception):B.precommand(p,h,t)
 assert B.precommand(p,h,t,require_pristine=False)==p['failure']
 (h/'bootstrap/baseline').mkdir()
 with pytest.raises(Exception):B.precommand(p,h,t,require_pristine=False)

@pytest.mark.parametrize('field',['module_sha256','tracker','result','models','authoring_files_unchanged'])
def test_native_precommand_proof_never_substitutes_foreign_or_changed_boundary(case,field):
 h,t,p=profile(case);proof=P.read(p['native_validation'])
 if field=='module_sha256':proof[field]='0'*64
 elif field=='tracker':proof[field]={'path':'/foreign','sha256':'f'*64}
 elif field=='result':proof[field]={}
 elif field=='models':proof[field]=1
 else:proof[field]=False
 p['native_validation']=put(h/'native-bad.json',proof)
 with pytest.raises(Exception):B.precommand(p,h,t)

def test_precommand_delta_preserves_mask_and_all_quality_code(tmp_path):
 root=tmp_path/'new';path=root/'scripts/batch/harbor_baseline_precommand.py';path.parent.mkdir(parents=True);path.write_bytes(Path(P.__file__).read_bytes());v=P.record(path)
 old=put(tmp_path/'old.json',{'root':'/old','files':[{'relative':'xl_input_mask.py','sha256':'same'}]})
 new=put(tmp_path/'new.json',{'root':str(root),'files':[{'relative':'xl_input_mask.py','sha256':'same'},{'relative':'scripts/batch/harbor_baseline_precommand.py','sha256':v['sha256']}]})
 assert B.precommand_delta(old,new,v)==['scripts/batch/harbor_baseline_precommand.py']
 x=P.read(new);x['files'][0]['sha256']='changed';new=put(tmp_path/'changed.json',x)
 with pytest.raises(Exception):B.precommand_delta(old,new,v)

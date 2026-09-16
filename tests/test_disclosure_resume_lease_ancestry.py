from pathlib import Path
import json,pytest
from scripts.fleet import disclosure_review_continue as M


def fixture(tmp):
 def keep(path,v):
  path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(v));return M.binding(path)
 oldhome=tmp/'original';home=tmp/'cache'
 base=dict(helper={'path':'/old','sha256':'a'*64},owner='host:old',fence=3,output=str(oldhome),source={'path':'/source','sha256':'b'*64},round=2,limit=2,code_snapshots=['old'])
 baseb=keep(tmp/'base.json',base);lease={'path':'/lease-complete','sha256':'c'*64};before={'path':'/before','sha256':'d'*64};after={'path':'/after','sha256':'e'*64}
 proof=dict(schema_version='fleet-disclosure-initial-cache-failure/v1',plan=baseb,worker_outcome={},process={},authority={},retained_lease=lease,lease_before=before,lease_after=after)
 proofb=keep(tmp/'proof.json',proof);old=dict(base,helper={'path':'/new','sha256':'f'*64},owner='host:new',fence=4,output=str(home),code_snapshots=['new'],reserved_round=proofb,round_reader={'profile':None,'runtime':{}})
 r=dict(prior_plan=keep(tmp/'old.json',old),lease_plan=baseb,lease_completion=lease,lease_before=before,lease_after=after)
 keep(home/'continued.json',proofb);keep(home/'reserved.json',{'plan':'exact-original'});keep(oldhome/'reserved.json',{'plan':'exact-original'})
 return old,r,keep


def test_completed_renewal_original_authority_preserved(tmp_path):
 old,r,_=fixture(tmp_path);assert M.resume_lease_plan({},old,r)==r['lease_plan']
 assert M.resume_lease_plan({},old,{'prior_plan':r['prior_plan']})==r['prior_plan']

@pytest.mark.parametrize('fault',['before','completion','reservation','continued','scope','fence','reader'])
def test_changed_ancestry_rejected(tmp_path,fault):
 old,r,keep=fixture(tmp_path)
 if fault=='before':r['lease_before']={}
 elif fault=='completion':r['lease_completion']={}
 elif fault=='reservation':keep(Path(old['output'])/'reserved.json',{})
 elif fault=='continued':keep(Path(old['output'])/'continued.json',{})
 elif fault=='scope':old['source']={}
 elif fault=='fence':old['fence']=5
 else:old['round_reader']['profile']={}
 with pytest.raises(ValueError):M.resume_lease_plan({},old,r)

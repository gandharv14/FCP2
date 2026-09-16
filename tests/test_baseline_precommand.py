import json
from pathlib import Path
import pytest
from scripts.batch import harbor_baseline_precommand as P

def put(p,v):
 p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v));return P.record(p)

@pytest.fixture
def case(tmp_path):
 h=tmp_path/'task';put(h/'environment/selection.json',{});(h/'bootstrap').mkdir()
 f=put(h/'environment/unresolved-baseline-timeout.json',dict(status='UNRESOLVED',step='baseline',automatic_retry=False,source_invalidity_claimed=False,error_type='TimeoutError'))
 passed=dict(state='passed');pending=dict(state='pending',attempt=0,confidence_history=[],current_confidence=None)
 t=dict(lane_state={'harbor-source-warden':passed,'harbor-segmentation-analyst':passed,'author':pending})
 descriptor={'path':'/descriptor','sha256':'d'};lease={'retained':'same'}
 prior=put(h/'steps/001/result.json',dict(status='STEP_COMPLETE',step='segmentation',descriptor=descriptor,lease=lease))
 i=put(h/'steps/002/intent.json',dict(action='step',arguments=dict(descriptor=descriptor,checkpoint=prior)))
 log=put(h/'steps/002/step.log',{})
 r=put(h/'steps/002/resource.json',dict(intent=i,returncode=0,failure=None,log=log))
 q=put(h/'steps/002/result.json',dict(status='UNRESOLVED',step='baseline',publication_ready=False,evidence=[f],descriptor=descriptor,lease=lease))
 o=put(h/'steps/002/outcome.json',dict(status='COMPLETED_OPERATION',resource=r,result=q,publication_performed=False))
 return h,t,q,o

@pytest.mark.parametrize('bad',[None,'baseline','model','instruction','lane','guard','lease'])
def test_only_original_completed_precommand_failure_can_continue(case,bad):
 h,t,q,o=case
 if bad in ('baseline','model','instruction'):(h/{'baseline':'bootstrap/baseline','model':'model-campaign','instruction':'instruction'}[bad]).mkdir()
 if bad=='lane':t['lane_state']['author']['attempt']=1
 if bad=='guard':
  outcome=P.read(o);r=P.read(outcome['resource']);r['returncode']=None
  outcome['resource']=put(Path(outcome['resource']['path']),r);o=put(Path(o['path']),outcome)
 if bad=='lease':
  value=P.read(q);value['lease']={'new':'lease'};q=put(Path(q['path']),value)
  outcome=P.read(o);outcome['result']=q;o=put(Path(o['path']),outcome)
 if bad:
  with pytest.raises(Exception):P.validate(h,t,q,o)
 else:
  assert P.validate(h,t,q,o)['status']=='STOPPED_BEFORE_BASELINE_COMMAND'
  assert not (h/'bootstrap/baseline').exists()

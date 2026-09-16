import json
from pathlib import Path
import pytest
from scripts.fleet import retained_warning_continue as W


def fixture(tmp_path, *, outer=False, rc=0, failure=None, result_status='DIALOGUE_REPAIR_REQUIRED', next_step='dialogue-review', wrong_descriptor=False):
    def put(name,value):
        p=tmp_path/name;p.write_text(json.dumps(value));return W.binding(p)
    descriptor=put('descriptor.json',{'identity':'original'})
    blocked=put('blocked.json',{'status':'DIALOGUE_REPAIR_REQUIRED'})
    result=put('result.json',dict(descriptor=descriptor,checkpoint=blocked,status=result_status,next_step=next_step))
    intent=put('intent.json',dict(action='step',arguments={'descriptor':{} if wrong_descriptor else descriptor}))
    log=put('step.log',{'retained':'original'})
    resource=put('resource.json',dict(returncode=rc,failure=failure,intent=intent,log=log))
    outcome=put('outcome.json',dict(status='RECONCILIATION_REQUIRED' if outer else 'COMPLETED_OPERATION',resource=resource,result=result,automatic_retry=False))
    return dict(outcome=outcome,result=result,descriptor=descriptor,blocked=blocked)


def test_actual_completed_guard_is_read_only(tmp_path):
    p=fixture(tmp_path);before={x.name:x.read_bytes()for x in tmp_path.iterdir()}
    assert W.original_review_guard(p)['result']==p['result']
    assert before=={x.name:x.read_bytes()for x in tmp_path.iterdir()}


@pytest.mark.parametrize('change',[{'outer':True},{'rc':1},{'failure':'TimeoutError'},{'result_status':'STEP_COMPLETE'},{'next_step':'dialogue-paraphraser'},{'wrong_descriptor':True}])
def test_uncertain_or_wrong_boundary_rejected(tmp_path,change):
    with pytest.raises(ValueError):W.original_review_guard(fixture(tmp_path,**change))


def test_changed_completed_log_rejected(tmp_path):
    p=fixture(tmp_path);(tmp_path/'step.log').write_text('changed')
    with pytest.raises(ValueError):W.original_review_guard(p)

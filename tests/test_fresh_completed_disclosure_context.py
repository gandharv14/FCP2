from copy import deepcopy
from types import SimpleNamespace
import pytest
from scripts.fleet import fresh_completed_disclosure_context as C


@pytest.mark.parametrize('defect',[None,'selection','descriptor','predecessor','publication','status','checkpoint'])
def test_failed_review_must_be_exact_successor_of_real_build(monkeypatch,tmp_path,defect):
    w=SimpleNamespace(run=tmp_path)
    cp={'path':str(tmp_path/'steps/disclosure-review/blocked.json'),'sha256':'c'*64}
    build={'descriptor':{'descriptor':1},'result':{'result':1},'outcome':{'outcome':1}}
    built={'descriptor':build['descriptor'],'selection':{'source':1}}
    value=dict(status='MODEL_RECONCILIATION_REQUIRED',next_step='disclosure-review',publication_performed=False,
        checkpoint=cp,descriptor=build['descriptor'],selection=built['selection'],evidence=[build['result']])
    blocked=dict(status=value['status'],step='disclosure-review',selection=deepcopy(value['selection']))
    if defect=='selection':value['selection']={}
    elif defect=='descriptor':value['descriptor']={}
    elif defect=='predecessor':value['evidence']=[{}]
    elif defect=='publication':value['publication_performed']=True
    elif defect=='status':value['status']='STEP_COMPLETE'
    elif defect=='checkpoint':cp={'path':'/wrong','sha256':'b'*64}
    p=dict(build=build,stopped_result={'stopped':1},stopped_outcome={'stopped_outcome':1})
    monkeypatch.setattr(C,'read',lambda b:value if b==p['stopped_result']else blocked)
    monkeypatch.setattr(C,'binding',lambda path:cp)
    d=SimpleNamespace(successful=lambda b:(build['result'],built)if b==build['outcome']else(p['stopped_result'],value))
    i=SimpleNamespace(selected=lambda b:(w,None,None))
    if defect:
        with pytest.raises(ValueError):C.stopped(p,d,i)
    else:assert C.stopped(p,d,i)==value

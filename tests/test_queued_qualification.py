import hashlib
import json
from types import SimpleNamespace
import pytest
from scripts.fleet.queued_qualification import capture


@pytest.fixture
def case(tmp_path):
    tracker=tmp_path/'0001.json';tracker.write_text('{"revision":4}')
    def binding(path):return dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    def checked(row):
        from pathlib import Path
        assert binding(Path(row['path']))==row
        return json.loads(Path(row['path']).read_bytes())
    def require(ok,message):
        if not ok:raise ValueError(message)
    pub=SimpleNamespace(binding=binding,checked=checked,require=require)
    calls=[];report=dict(schema_version='harbor-complete-readiness/v1',workbook='0001',stage_sha256='stage',publication_ready=True,blockers=[],checks=[{'actual':'retained'}])
    def proof(before,selection):
        assert before=={'revision':4};calls.append('original proof');return report
    original=lambda *a:None
    P=SimpleNamespace(run=original,execute=original,_lease_step=original,schema=SimpleNamespace(cas_mutate=original),_paths=original,_checkpoint=original,ready=SimpleNamespace(_proof=proof),encode=lambda r:{'checkpoint':'original'})
    request=SimpleNamespace(tracker_root=tmp_path,output=tmp_path/'output',publication_root=tmp_path/'published',selection=SimpleNamespace(content=SimpleNamespace(stage_sha256='stage')))
    plan=dict(workbook='0001',journal=str(tmp_path/'journal'));out=tmp_path/'diagnostic'
    return pub,P,request,plan,{},out,tracker,report,calls,original


def test_retains_fresh_original_report_without_relabeling_previous_result(case):
    pub,P,r,p,row,out,tracker,report,calls,original=case
    result=capture(pub,P,r,p,row,out)
    assert calls==['original proof']
    value=pub.checked(result);assert pub.checked(value['qualification'])==report
    assert json.loads(tracker.read_text())=={'revision':4}
    assert out.stat().st_mode&0o777==0o700
    assert (out/'proof.json').stat().st_mode&0o777==0o600
    assert P.execute is original and P.schema.cas_mutate is original
    with pytest.raises(ValueError,match='fresh'):capture(pub,P,r,p,row,out)
    assert len(calls)==1


@pytest.mark.parametrize('defect',['false','blocker','foreign','drift','mutation'])
def test_no_pass_artifact_on_failed_proof_drift_or_owning_write(case,defect):
    pub,P,r,p,row,out,tracker,report,calls,original=case
    if defect=='false':report['publication_ready']=False
    elif defect=='blocker':report['blockers']=['numerical mismatch']
    elif defect=='foreign':report['workbook']='9999'
    else:
        def proof(*a):
            if defect=='mutation':P.schema.cas_mutate()
            tracker.write_text('{"revision":5}')
            return report
        P.ready._proof=proof
    with pytest.raises((ValueError,RuntimeError)):capture(pub,P,r,p,row,out)
    assert not out.exists()
    assert P.execute is original and P.schema.cas_mutate is original

@pytest.mark.parametrize('defect',[None,'unchecked','missing','no_evidence','publisher','plan'])
def test_publication_metadata_uses_same_full_original_checks_once(case,defect):
    from scripts.fleet.queued_qualification import capture_publication
    pub,P,r,p,row,out,tracker,report,calls,original=case
    code=out.parent/'publisher.py';code.write_text('# exact original publisher\n');pub.__file__=str(code)
    publisher=pub.binding(code)
    ready=out.parent/'ready.json';ready.write_text('{"status":"PUBLICATION_READY"}')
    p['ready']=pub.binding(ready)
    plan=out.parent/'plan.json';plan.write_text(json.dumps(p));row=pub.binding(plan)
    report['checks']={'original_gate':{'checked':True}};report['evidence']=[p['ready']]
    if defect=='unchecked':report['checks']['original_gate']['checked']=False
    elif defect=='missing':report['checks']={}
    elif defect=='no_evidence':report['evidence']=[]
    elif defect=='publisher':publisher={**publisher,'sha256':'foreign'}
    elif defect=='plan':p['journal']+='/foreign'
    if defect:
        with pytest.raises(ValueError):capture_publication(pub,P,r,p,row,out,publisher)
        assert not (out/'publication-proof.json').exists()
    else:
        result=pub.checked(capture_publication(pub,P,r,p,row,out,publisher))
        assert result['checks']==1 and result['evidence']==1
        assert pub.checked(result['qualification'])==report
        assert result['tracker']==pub.binding(tracker) and result['ready']==p['ready']
    assert len(calls)==(0 if defect in ('publisher','plan') else 1)
    assert P.execute is original and P.schema.cas_mutate is original

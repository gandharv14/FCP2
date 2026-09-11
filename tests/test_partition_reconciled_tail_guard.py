"""Current-reader authority must preserve the failed finalizer as failed."""
from pathlib import Path
import importlib.util,json
import pytest
ROOT=Path(__file__).resolve().parents[1]
@pytest.mark.parametrize('fault',[None,'prior_failure','prior_log','prior_original','consume_initial','consume_capability','consume_log'])
def test_reconciled_final_guard_failed_consume_ancestry(tmp_path,monkeypatch,fault):
    path=ROOT/'diagnostics/requested-fixes-20260910/continue_0184_dialogue_partition_final_v7.py'
    spec=importlib.util.spec_from_file_location('final_guard_v7',path);d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)
    def bind(name,value):
        p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(value if isinstance(value,str) else json.dumps(value));return d.binding(p)
    aggregate=bind('aggregate.json',{'passed':True})
    initial=bind('canonical-result.json',{'checkpoint':{} if fault=='checkpoint' else aggregate})
    snapshot={'path':'/exact/snapshot','sha256':'a'*64}
    executable=bind('reader.py','# reviewed reader')
    oldintent=bind('old/intent.json',{'action':'finalize'})
    oldlog=bind('old/step.log','original retained exception')
    oldresource=bind('old/resource.json',dict(returncode=0 if fault=='old_rc' else 1,failure=None,intent=oldintent,log=oldlog))
    oldoutcome=bind('old/outcome.json',dict(status='RECONCILIATION_REQUIRED',resource=oldresource,result=None,automatic_retry=False))
    oldcanonical=bind('old/canonical-result.json',{'checkpoint':aggregate})
    priorintent=bind('prior/intent.json',{'snapshot':snapshot})
    priorlog=bind('prior/step.log','prior success')
    prior=bind('prior/result.json',dict(status='COMPLETED_AGGREGATE_RECONCILED',completed_aggregate=aggregate,canonical_result=oldcanonical,
        old_failed_resource={} if fault=='prior_original' else oldresource,old_failed_outcome=oldoutcome,old_canonical=oldcanonical))
    priorresource=bind('prior/resource.json',dict(returncode=1 if fault=='prior_failure' else 0,failure=None,intent=priorintent,log=priorlog))
    prioroutcome=bind('prior/outcome.json',dict(status='COMPLETED_OPERATION',automatic_retry=False,resource=priorresource,result=prior))
    consumeintent=bind('consume/intent.json',dict(initial={} if fault=='consume_initial' else oldcanonical,action='initial-aggregate-consume',models=fault=='consume_capability',commands=False,containers=False))
    consumelog=bind('consume/step.log','unchanged failed reader')
    consumeresource=bind('consume/resource.json',dict(returncode=1,failure=None,intent=consumeintent,log=consumelog))
    consumeoutcome=bind('consume/outcome.json',dict(resource=consumeresource,result=None,automatic_retry=False))
    intent=bind('intent.json',dict(driver=executable,snapshot=snapshot))
    log=bind('step.log','read-only checked')
    result=bind('result.json',dict(status='COMPLETED_AGGREGATE_RECONCILED',canonical_result=initial,
        snapshot={} if fault=='snapshot' else snapshot,driver=executable,model_calls=0,finalization_replayed=False,
        tracker_mutated=False,canonical_outer_completed=False,old_failed_resource=oldresource,
        old_failed_outcome=oldoutcome,old_failed_log=oldlog,old_canonical=oldcanonical,completed_aggregate=aggregate,previous_reconciliation=prior,previous_reconciliation_resource=priorresource,previous_reconciliation_outcome=prioroutcome,failed_consume_resource=consumeresource,failed_consume_outcome=consumeoutcome,failed_consume_log=consumelog,failed_consume_created_worker_intent=False))
    resource=bind('resource.json',dict(returncode=0,failure=None,intent=intent,log=log,result={} if fault=='resource_result' else result))
    outcome=bind('outcome.json',dict(status='RECONCILIATION_REQUIRED' if fault=='outer_status' else 'COMPLETED_OPERATION',resource=resource,result=result,automatic_retry=False))
    for k,v in dict(INITIAL=initial,INITIAL_RESOURCE=resource,INITIAL_OUTCOME=outcome,SNAPSHOT=snapshot).items():monkeypatch.setattr(d,k,v)
    if fault=='prior_log':Path(priorlog['path']).write_text('changed prior log')
    if fault=='consume_log':Path(consumelog['path']).write_text('changed failed log')
    if fault is None:
        actual,evidence=d.finalizer_guard();assert actual['checkpoint']==aggregate and oldresource in evidence and oldlog in evidence
        assert d.checked(oldoutcome)['status']=='RECONCILIATION_REQUIRED'
        assert prior in evidence and consumeresource in evidence
    else:
        with pytest.raises(AssertionError):d.finalizer_guard()

"""Completed steps must retain their actual stopped owner and checkpoint chain."""
import json
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from scripts.fleet import pre_dispatch_continue as M


def history(tmp_path,monkeypatch,fault=None):
    def put(path,value):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return M.binding(path)
    code=M.binding(__file__);root=tmp_path/'snapshot';root.mkdir()
    source=put(tmp_path/'source.json',{})
    fresh=put(tmp_path/'fresh.json',dict(code_root=str(root)))
    old=put(tmp_path/'old-worker.json',dict(inputs=[fresh]))
    admitted=put(tmp_path/'admitted.json',dict(status='ADMITTED'))
    original=dict(driver=code,outcome=admitted,operations=str(tmp_path/'first'))
    q={k:None for k in M.FIELDS}
    q.update(schema_version=M.SCHEMA,helper=code,workbook='0289',source=source,
        original_worker_job=old,original_job=put(tmp_path/'original-job.json',original),
        home=str(tmp_path/'continuation'),operations=str(tmp_path/'later'),history_reader=code,
        owner='actual-owner',fence=3,failed=put(tmp_path/'failed.json',{}))
    prior=put(tmp_path/'prior.json',q)
    put(Path(q['home'])/'intent.json',dict(plan=prior,failed=q['failed'],retained_admission=admitted,automatic_retry=False))
    job=put(Path(q['home'])/'authoring-job.json',dict(original,operations=q['operations']))
    run=tmp_path/'worker';worker=put(tmp_path/'worker-job.json',dict(run=str(run),driver=code,inputs=[prior],source=source,workbook='0289',version=root.name))
    wi=put(run/'intent.json',dict(job=worker,owner=q['owner'],claim_id='claim'))
    claim=dict(owner=q['owner'],claim_id='claim',fence=3,source_sha256=source['sha256'],workbook='0289',version=root.name)
    if fault=='claim':claim['fence']=4
    put(run/'claim.json',claim)
    outer=dict(intent=wi,status='RECONCILIATION_REQUIRED',returncode=1,failure_type=None,
        result={'unexpected':'completion'}if fault=='result'else None,automatic_retry=False,publication_performed=False)
    stopped=put(run/'outcome.json',outer)
    value=dict(status='STEP_COMPLETE',checkpoint={'actual':'worker checkpoint'},selection={'source':'actual'},lease={'consumed':'retained'},descriptor=code,next_step='nat-commit')
    cp=put(tmp_path/'completed.json',value)
    held=dict(value,evidence=[{'foreign':'checkpoint'}]if fault=='prefix'else[cp])
    if fault=='next_step':held['next_step']='nat-init'
    env=(None,)*5+('unchanged-run','unchanged-policy','unchanged-controls')
    calls=[]
    D=NS(setup=lambda j:env,successful=lambda row,e:(admitted,dict(status='ADMITTED'))if row==admitted else(cp,value))
    readers={'completed_pre_dispatch_source_history':NS(select_history=lambda *a,**k:calls.append('source')),
        'completed_pre_dispatch_original':NS(failed_guard=lambda *a:calls.append('original-failed-guard')),
        'completed_pre_dispatch_original_dispatcher':D,'completed_pre_dispatch_selected_dispatcher':D}
    monkeypatch.setattr(M,'module',lambda row,name:readers[name])
    monkeypatch.setattr(M,'renewal_dispatcher',lambda *a:calls.append('dispatcher'))
    monkeypatch.setattr(M,'renewal_setup_guard',lambda *a:(cp,held))
    p=dict(schema_version='fleet-completed-pre-dispatch-history/v1',helper=M.binding(M.__file__),workbook='0289',source=source,
        previous=prior,original_worker_job=worker,original_job=job,outer=stopped,failed=code,completed=code,last_step=code,dispatcher=code)
    if fault=='source':p['source']=code
    row=put(tmp_path/'history.json',p)
    return row,cp,calls


def test_completed_history_preserves_original_checkpoint_without_running(tmp_path,monkeypatch):
    row,cp,calls=history(tmp_path,monkeypatch)
    assert M.context(row)[-1]==cp
    assert calls==['source','original-failed-guard','dispatcher']
    with pytest.raises(ValueError,match='cannot run without'):M.run(row)
    assert not (tmp_path/'new-lease').exists()


@pytest.mark.parametrize('fault',['source','claim','result','prefix','next_step'])
def test_changed_source_owner_or_completed_boundary_is_rejected(tmp_path,monkeypatch,fault):
    row,_,_=history(tmp_path,monkeypatch,fault)
    with pytest.raises(ValueError):M.context(row)

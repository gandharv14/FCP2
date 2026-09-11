"""Tiny real-generation bootstrap tests; no actual corpus/network/container work."""
import copy
import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from test_harbor_admission import setup
from test_harbor_lane_notes import case
from scripts.batch import harbor_workbook_bootstrap as b
from scripts.batch import harbor_admission as a
from scripts.batch import harbor_tracker_lifecycle as life
from scripts.corpus import inventory


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return a.record(path)

@pytest.fixture
def selected(setup,monkeypatch):
    import xl_input_mask
    monkeypatch.setattr(b,'ROOT',Path(xl_input_mask.__file__).parent)
    old,policy,history,original,tmp=setup
    facade,path,ready,token,source,seg=case(setup)
    for n in range(2,51):(original.parent/f'{n:04}.xlsx').write_bytes(b'Other uninspected original '+str(n).encode())
    inv=tmp/'frozen-inventory.json';inventory([original.parent],inv,50);rows=a.read(a.record(inv))['sources']
    source_manifest=a.read(source.manifest);seg_manifest=a.read(seg.manifest);cur=a.record(Path(seg.manifest['path']).parent/'curation.toml')
    original_row=next(r for r in rows if r['workbook_id']=='0001')
    ledger=write(tmp/'retained-corpus/0001/ledger.json',dict(schema_version='corpus-workbook/v1',workbook_id='0001',source=original_row,build=dict(state='VERIFIED',bindings={'curation_sha256':cur['sha256']},source_generation_id=source_manifest['generation_id'],source_manifest_sha256=source.manifest['sha256'],segmentation_generation_id=seg_manifest['generation_id'],segmentation_manifest_sha256=seg.manifest['sha256'])))
    survey=write(tmp/'survey.json',dict(inventory=a.record(inv),scanned_roots=[str(history)],unmapped_prior_artifacts=[]))
    chosen=[dict(original=r,exclusion_evidence=[],prior_audit_metadata=[]) for r in rows]
    chosen[0].update(ledger=ledger,retained_targets=seg_manifest['curated_output_cells'],retained_curation=cur)
    manifest=write(tmp/'candidate.json',dict(schema_version='new50-qualified-task-candidate-proposal/v1',target_new_qualified_tasks=50,denominator_for_original_rebuild=50,survey=survey,selected_candidates=chosen))
    trackers=tmp/'new-fleet/workbooks';trackers.mkdir(parents=True)
    p=a.policy_from_candidate_manifest(manifest,batch='new-fixture',tracker_root=trackers,original_inventory=a.record(inv),dependency=policy.dependency,dependencies=policy.dependencies,history_roots=(history,),expected_original_count=50)
    selection=b.Selection(manifest,p,'0001',source.manifest,seg.manifest,tmp/'bootstrap',b.Policy('fixture-project'))
    return selection,history,tmp


def acquire(selection,result):
    path=selection.policy.tracker_root/'0001.json'
    def authority(operation,target,before,request):
        return operation=='acquire' and target==str(path) and before['revision']==result['revision'] and request['owner']=='root-test' and request['scope']=='workbook:0001'
    result=life.lease_operation(path,authority=life.RootAuthority('harbor-orchestrator',10**12,authority),operation='acquire',lease_kind='task_agent',owner='root-test',scope='workbook:0001',duration_ns=10**12,expected_revision=result['revision'],expected_sha256=result['sha256'])
    return b.Lease('root-test',result['tracker']['leases']['task_agent']['token'],result['revision'],result['sha256'])


def complete(selection,prepared):
    admitted=b.admit(selection,prepared);lease=acquire(selection,admitted)
    for kind in ('source','segmentation'):
        result=b.complete_lane(selection,prepared,kind,lease);lease=dataclasses.replace(lease,revision=result['revision'],sha256=result['sha256'])
    return lease,result


def test_real_admission_source_segmentation_keeps_original_targets_and_budgets(selected):
    s,history,tmp=selected;ledger=Path(a.read(s.candidate_manifest)['selected_candidates'][0]['ledger']['path']);prior=ledger.read_bytes()
    prepared=b.prepare(s);assert not (s.policy.tracker_root/'0001.json').exists()
    lease,result=complete(s,prepared);tracker=result['tracker']
    assert tracker['lane_state']['harbor-source-warden']['state']=='passed' and tracker['lane_state']['harbor-segmentation-analyst']['state']=='passed'
    assert all(row['attempt']==0 for row in tracker['lane_state'].values())
    assert tracker['promotion_ids']['task_generation_id'] is None and ledger.read_bytes()==prior
    assert b.read(prepared)['validated']['targets']==a.read(s.candidate_manifest)['selected_candidates'][0]['retained_targets']
    argv=b.baseline_arguments(s,tmp/'baseline-out')
    assert argv[argv.index('--source-generation-root')+1]==str(Path(s.source_manifest['path']).parents[3])
    assert argv[argv.index('--seg-dir')+1]==str(Path(s.segmentation_manifest['path']).parents[3])
    assert '--mask-cells' not in argv and '--segmentation-mode' in argv


@pytest.mark.parametrize('change',['ledger_changed','targets','curation','source_manifest','foreign_id','unproved_normalization'])
def test_frozen_identity_and_proofs_reject_before_admission(selected,change):
    s,history,tmp=selected;manifest=a.read(s.candidate_manifest);row=manifest['selected_candidates'][0]
    if change=='ledger_changed':Path(row['ledger']['path']).write_text('{}')
    elif change=='source_manifest':s=dataclasses.replace(s,source_manifest={**s.source_manifest,'sha256':'0'*64})
    elif change=='curation':Path(row['retained_curation']['path']).write_text('changed')
    elif change=='foreign_id':s=dataclasses.replace(s,workbook='9999')
    elif change=='targets':
        row['retained_targets']=['Sheet!A1'];binding=write(tmp/'wrong-targets.json',manifest);p=dataclasses.replace(s.policy,campaign_evidence=tuple(binding if x==s.candidate_manifest else x for x in s.policy.campaign_evidence));s=dataclasses.replace(s,candidate_manifest=binding,policy=p)
    else:
        ledger=a.read(row['ledger']);ledger['build']['normalization']={'original_sha256':'0'*64};row['ledger']=write(tmp/'wrong-ledger.json',ledger);binding=write(tmp/'wrong-normalization.json',manifest);p=dataclasses.replace(s.policy,campaign_evidence=tuple(binding if x==s.candidate_manifest else x for x in s.policy.campaign_evidence));s=dataclasses.replace(s,candidate_manifest=binding,policy=p)
    with pytest.raises((ValueError,KeyError)):b.prepare(s)
    assert not (s.policy.tracker_root/(s.workbook+'.json')).exists()


def test_ledger_refresh_is_explicit_and_preserves_original_contract(selected):
    s,history,tmp=selected;old=a.read(s.candidate_manifest)['selected_candidates'][0]['ledger'];value=a.read(old);value['diagnostic_note']='new nonfinancial evidence';fresh=write(tmp/'refreshed-ledger.json',value)
    refreshed=dataclasses.replace(s,refresh=b.LedgerRefresh(old,fresh));assert b.validate(refreshed)['ledger']==fresh
    bad=dataclasses.replace(refreshed,refresh=b.LedgerRefresh(fresh,fresh))
    with pytest.raises(ValueError,match='previous'):b.validate(bad)


def baseline(selected,monkeypatch):
    s,history,tmp=selected;prepared=b.prepare(s);lease,result=complete(s,prepared)
    prior=b.subprocess.run
    def run(argv,**kwargs):
        if len(argv)>1 and argv[1]==str(b.ROOT/'xl_input_mask.py'):
            import xl_input_mask
            code=xl_input_mask.main(argv[2:]);return SimpleNamespace(returncode=code or 0)
        return prior(argv,**kwargs)
    monkeypatch.setattr(b.subprocess,'run',run)
    bindings,receipt=b.run_baseline(s,prepared,lease)
    return s,prepared,lease,bindings,receipt


def test_owned_baseline_and_pinned_audit_plan_do_not_dispatch_models(selected,monkeypatch):
    s,prepared,lease,bindings,receipt=baseline(selected,monkeypatch)
    plan=b.audit_plan(s,prepared,receipt,lease)
    assert plan['actual_model_calls']==0 and plan['requires_root_model_slot'] and plan['inherited_budget'] is None
    assert plan['argv'][plan['argv'].index('--endpoint')+1]=='https://litellm.labelbox.com'
    assert all(n==0 for n in plan['batch_consumed'].values())
    assert not (s.run/'audit').exists()
    with pytest.raises(ValueError,match='already attempted'):b.run_baseline(s,prepared,lease)


def test_interrupted_baseline_retains_intent_and_does_not_restart(selected,monkeypatch):
    s,history,tmp=selected;prepared=b.prepare(s);lease,result=complete(s,prepared);prior=b.subprocess.run
    def run(argv,**kwargs):
        if len(argv)>1 and argv[1]==str(b.ROOT/'xl_input_mask.py'):raise b.subprocess.TimeoutExpired(argv,900)
        return prior(argv,**kwargs)
    monkeypatch.setattr(b.subprocess,'run',run)
    with pytest.raises(b.subprocess.TimeoutExpired):b.run_baseline(s,prepared,lease)
    assert (s.run/'baseline/intent.json').exists() and not (s.run/'baseline/complete.json').exists()
    with pytest.raises(ValueError,match='already attempted'):b.run_baseline(s,prepared,lease)


def test_fresh_audit_history_cannot_be_silently_ignored(selected,monkeypatch):
    s,prepared,lease,bindings,receipt=baseline(selected,monkeypatch)
    history=s.policy.history_roots[0];write(history/'0001-variable-sources/0001.metadata.json',{'workbook':'0001','api_calls':1})
    with pytest.raises(ValueError,match='history evidence changed'):b.audit_plan(s,prepared,receipt,lease)


def test_root_selected_policy_or_lease_cannot_change_after_preparation(selected):
    s,history,tmp=selected;prepared=b.prepare(s);wrong=dataclasses.replace(s,model_policy=b.Policy('another-project'))
    with pytest.raises(ValueError,match='configuration changed'):b.admit(wrong,prepared)
    lease,result=complete(s,prepared)
    with pytest.raises(ValueError):b.run_baseline(s,prepared,dataclasses.replace(lease,token='forged'))
    assert not (s.run/'baseline').exists()


def test_original_or_generation_trees_cannot_be_used_for_bootstrap_journal(selected):
    s,history,tmp=selected;run=Path(s.source_manifest['path']).parent/'new-journal'
    with pytest.raises(ValueError,match='disjoint'):b.prepare(dataclasses.replace(s,run=run))
    assert not run.exists()


@pytest.mark.parametrize('spent',[1,2])
def test_exact_prior_audit_batches_preserve_remaining_budget(selected,monkeypatch,spent):
    import xl_variable_source_audit as audit
    s,history,tmp=selected;validated=b.validate(s);sg=Path(s.source_manifest['path']).parent;gg=Path(s.segmentation_manifest['path']).parent
    segment,meta=audit.resolve_segmentation_directory(gg.parents[2],s.workbook,segmentation_generation_id=gg.name,source_generation_root=sg.parents[2],source_generation_id=sg.name)
    inventory=audit.build_inventory(s.workbook,validated['bindings']['source_file']['path'],segment,segmentation=meta)
    batches=audit._split_chunks(inventory);assert len(batches)==1
    header,rows=batches[0];user='Workbook metadata:\n'+json.dumps(inventory['metadata'])+'\n\nInventory batch 1 of 1 (NDJSON):\n'+'\n'.join(rows)
    directory=history/'0001-variable-sources';attempts=[]
    for n in range(spent):
        req=write(directory/f'request-{n}.json',dict(model=s.model_policy.model,endpoint=audit.PROD_ENDPOINT,messages=[dict(role='system',content='Synthetic original audit'),dict(role='user',content=user)]))
        response=write(directory/f'response-{n}.json',dict(content='Synthetic retained audit response',finish_reason='stop'));attempts.append(dict(request=req,response=response))
    write(directory/'0001.metadata.json',dict(workbook='0001',api_calls=spent))
    inherited=write(tmp/'inherited.json',dict(schema_version='audit-inherited-budget/v1',workbook='0001',attempts=attempts))
    s,prepared,lease,bindings,receipt=baseline(selected,monkeypatch)
    if spent==2:
        with pytest.raises(ValueError,match='budget exhausted'):b.audit_plan(s,prepared,receipt,lease,inherited_budget=inherited)
    else:
        plan=b.audit_plan(s,prepared,receipt,lease,inherited_budget=inherited)
        assert list(plan['batch_consumed'].values())==[1] and plan['inherited_budget']==inherited
        assert plan['argv'][plan['argv'].index('--inherited-budget-sha256')+1]==inherited['sha256']
    assert not (s.run/'audit').exists()


@pytest.mark.parametrize('change',['baseline_bytes','intent','plan'])
def test_audit_dispatch_recheck_rejects_changed_baseline_command_or_plan(selected,monkeypatch,change):
    s,prepared,lease,bindings,receipt=baseline(selected,monkeypatch);plan=b.audit_plan(s,prepared,receipt,lease)
    assert b.revalidate_audit_plan(s,prepared,receipt,lease,plan)==plan
    if change=='baseline_bytes':Path(bindings['baseline_inputs']['path']).write_bytes(b'changed')
    elif change=='intent':(s.run/'baseline/intent.json').write_text('{}')
    else:plan['argv'].append('--refresh')
    with pytest.raises(ValueError):b.revalidate_audit_plan(s,prepared,receipt,lease,plan)
    assert not (s.run/'audit').exists()


def test_new_prior_task_blocks_admission_without_mutating_original_ledger(selected):
    s,history,tmp=selected;prepared=b.prepare(s);saved=(s.run/'ledger.snapshot.json').read_bytes()
    directory=history/'0001-outputs';directory.mkdir();(directory/'instruction.md').write_text('Existing task evidence')
    with pytest.raises(ValueError,match='prior task'):b.admit(s,prepared)
    assert not (s.policy.tracker_root/'0001.json').exists() and (s.run/'ledger.snapshot.json').read_bytes()==saved

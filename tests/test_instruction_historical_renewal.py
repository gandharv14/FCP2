from dataclasses import replace
from pathlib import Path
import pytest
from scripts.batch import harbor_instruction_lease_renewal as R,harbor_instruction_worker as W
from scripts.batch.harbor_admission import read,record
from test_instruction_lease_renewal import boundary,case,instruction_setup


def frozen(tmp_path):
    root=tmp_path/'frozen'
    names=['scripts/batch/harbor_instruction_worker.py','scripts/batch/harbor_role_producer.py',
        'scripts/batch/harbor_instruction_producer.py','scripts/batch/hardened_runner.py',
        'scripts/batch/process_guardian.py','scripts/harbor_pretransport_recovery.py',
        'scripts/harbor_instruction_code_applicability.py','scripts/batch/harbor_example.py']
    for name in names:
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text('This is deliberately not executable Python.\n')
    value={'root':str(root),'files':[dict(relative=name,sha256=record(root/name)['sha256']) for name in names]}
    snap=W.keep(tmp_path/'frozen.json',value)
    return root,snap,{str(root/name):record(root/name)['sha256'] for name in names}


@pytest.mark.parametrize('fault',[None,'changed','extra','missing','foreign','duplicate','symlink'])
def test_historical_inventory_is_exact_and_does_not_execute_old_files(tmp_path,fault):
    root,snap,expected=frozen(tmp_path)
    from types import SimpleNamespace
    worker=SimpleNamespace(root=root)
    if fault=='changed':(root/'scripts/batch/harbor_example.py').write_text('changed')
    elif fault=='extra':(root/'extra.py').write_text('extra')
    elif fault=='missing':(root/'scripts/batch/harbor_example.py').unlink()
    elif fault=='foreign':worker.root=tmp_path
    elif fault=='duplicate':
        value=read(snap);value['files'].append(value['files'][0]);snap=W.keep(tmp_path/'duplicate.json',value)
    elif fault=='symlink':(root/'link').symlink_to(root/'scripts/batch/harbor_example.py')
    if fault:
        with pytest.raises((ValueError,FileNotFoundError)):R._historical_inventory(worker,snap)
    else:assert R._historical_inventory(worker,snap)==expected


def test_historical_receipt_preserves_real_cas_checks_without_current_code_mix(boundary,tmp_path,monkeypatch):
    worker,selection,lease,checkpoint,_,tracker=boundary
    root,snap,expected=frozen(tmp_path)
    worker=replace(worker,root=root)
    monkeypatch.setattr(W,'code',lambda worker:expected)
    complete=R.authorize(worker,selection,lease,checkpoint,snap,duration_ns=200,clock=lambda:30)
    worker=replace(worker,lease_renewal=complete)
    monkeypatch.setattr(W,'code',lambda worker:{'current-module':'different'})
    before=tracker.read_bytes()
    with pytest.raises(ValueError,match='implementation'):R.validate(worker,read(record(tracker)))
    result=R.validate_historical_integrity(worker,read(record(tracker)),snap)
    assert result['checkpoint']==checkpoint and tracker.read_bytes()==before
    saved=read(complete)
    Path(saved['tracker']['path']).write_text('{}')
    with pytest.raises(ValueError):R.validate_historical_integrity(worker,read(record(tracker)),snap)


def test_manifest_revision_checkpoint_takes_priority_over_its_old_renewal(boundary,monkeypatch):
    from dataclasses import asdict
    from scripts.batch import harbor_disclosure_revision as D,harbor_disclosure_manifest_revision as M,harbor_transition_codec as codec
    worker,selection,lease,checkpoint,snapshot,tracker=boundary
    renewal=R.authorize(worker,selection,lease,checkpoint,snapshot,duration_ns=200,clock=lambda:30)
    old_permit=read(read(renewal)['permit'])
    from scripts.batch.harbor_workbook_bootstrap import Lease
    lease=Lease(**read(renewal)['lease'])
    value=dict(schema_version='harbor-disclosure-manifest-revision/v1',initial=record(worker.run/'initial.json'),
        predecessor=renewal,selection=codec.encode(selection),lease=asdict(lease),prior_continuations={'lease_renewal':renewal})
    revision=W.keep(worker.run/'manifest-revision/permit.json',value)
    revised=replace(worker,lease_renewal=renewal,disclosure_revision=revision)
    monkeypatch.setattr(D,'validate',lambda worker,before:value)
    monkeypatch.setattr(M,'historical_lease',lambda worker,before:old_permit)
    with pytest.raises(ValueError,match='preceding checkpoint'):
        W.step(revised,selection,lease,checkpoint=renewal)
    result=W.step(revised,selection,lease,checkpoint=revision)
    assert read(result.evidence)['step']=='disclosure-review'
    assert read(read(result.evidence)['intent'])['previous']==revision


def test_revised_warning_codec_keeps_exact_prior_and_later_renewal_chain(tmp_path):
    from scripts import harbor_instruction_campaign as C
    revision=W.keep(tmp_path/'revision.json',{'revision':2})
    old=W.keep(tmp_path/'old-renewal.json',{'old':True})
    warning=W.keep(tmp_path/'warning.json',{'schema_version':'harbor-dialogue-warning-requalification/v1',
        'prior_continuations':{'disclosure_revision':revision,'lease_renewal':old}})
    C._warning_history(None,None,warning,revision,None,old)
    permit=W.keep(tmp_path/'new-permit.json',{'schema_version':R.SCHEMA,'prior_continuations':{'warning_requalification':warning,'lease_renewal':old}})
    later=W.keep(tmp_path/'new-renewal.json',{'schema_version':R.SCHEMA,'permit':permit})
    C._warning_history(None,None,warning,revision,None,later)
    with pytest.raises(ValueError):C._warning_history(None,None,warning,revision,None,None)
    with pytest.raises(ValueError):C._warning_history(None,None,warning,old,None,later)

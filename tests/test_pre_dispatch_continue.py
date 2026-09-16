import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.fleet import pre_dispatch_continue as P


def fixture(tmp_path):
    def keep(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value)); return P.binding(path)
    root = tmp_path / 'ops'; directory = root / '000'; directory.mkdir(parents=True)
    code = tmp_path / 'campaign.py'; code.write_text('original')
    base = SimpleNamespace(__file__=str(code)); cp = keep(tmp_path / 'admission.json', {'status': 'ADMITTED'})
    descriptor = keep(tmp_path / 'descriptor.json', {})
    job = {'operations': str(root)}; keep(root / 'job.json', job)
    intent = keep(directory / 'intent.json', dict(action='step', arguments=dict(descriptor=descriptor, checkpoint=cp, captures=None), code=P.binding(code), dispatch=True))
    log = directory / 'step.log'; log.write_text("credential=read_env_key(controls.env_file)\nscripts.batch.harbor_admission.AdmissionError: configured model credential unavailable\n")
    resource = keep(directory / 'resource.json', dict(returncode=1, failure=None, intent=intent, log=P.binding(log)))
    keep(directory / 'dependency.json', {})
    failed = keep(directory / 'outcome.json', dict(status='RECONCILIATION_REQUIRED', resource=resource, automatic_retry=False, terminal_verdict=None, source_invalidity_claimed=False))
    keep(root / 'hold.json', dict(outcome=failed))
    return dict(failed=failed), job, cp, dict(descriptor=descriptor), base, keep


def test_exact_credential_failure_retains_admission(tmp_path):
    p, job, cp, value, base, keep = fixture(tmp_path)
    assert P.failed_guard(p, job, cp, value, base) is None


@pytest.mark.parametrize('fault', ['extra_result', 'next_child', 'uncertain', 'different_error', 'different_checkpoint'])
def test_no_replay_of_uncertain_or_advanced_guard(tmp_path, fault):
    p, job, cp, value, base, keep = fixture(tmp_path)
    root = Path(job['operations']); directory = root / '000'
    if fault == 'extra_result': keep(directory / 'result.json', {})
    elif fault == 'next_child': (root / '001').mkdir()
    elif fault == 'different_checkpoint': cp = keep(tmp_path / 'other.json', {})
    else:
        failed = P.read(p['failed']); guard = P.read(failed['resource'])
        if fault == 'uncertain': guard['failure'] = 'TimeoutError'; guard['returncode'] = None
        else:
            Path(guard['log']['path']).write_text('different failure\n'); guard['log'] = P.binding(guard['log']['path'])
        failed['resource'] = keep(directory / 'resource.json', guard)
        p['failed'] = keep(directory / 'outcome.json', failed); keep(root / 'hold.json', dict(outcome=p['failed']))
    with pytest.raises(ValueError): P.failed_guard(p, job, cp, value, base)


def setup_case(tmp_path):
    def keep(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value)); return P.binding(path)
    home = tmp_path / 'fresh'; home.mkdir()
    plan = keep(tmp_path / 'plan.json', {})
    old = dict(inputs=[plan]); job = dict(operations=str(home / 'operations'))
    result = dict(status='RECONCILIATION_REQUIRED', workbook='0307', plan=plan,
                  failure_type='ModuleNotFoundError', publication_performed=False, automatic_retry=False)
    failed = keep(home / 'result.json', result)
    log = tmp_path / 'worker.log'
    log.write_text("dispatcher.successful(outcome, dispatcher.setup(job)); dispatcher.run(job_binding)\nfrom scripts.fleet.snapshot_inventory import equivalent\nModuleNotFoundError: No module named 'scripts.fleet.snapshot_inventory'\n")
    outer = dict(result=failed, log=P.binding(log))
    return dict(failed=failed, workbook='0307'), old, job, outer, dict(home=str(home)), keep


def test_initial_setup_failure_preserves_completed_admission(tmp_path):
    p, old, job, outer, fresh, _ = setup_case(tmp_path)
    P.setup_dependency_guard(p, old, job, outer, fresh)


@pytest.mark.parametrize('fault', ['operation_exists', 'different_error', 'result_changed', 'uncertain', 'different_plan'])
def test_initial_setup_does_not_replay_uncertain_or_advanced_work(tmp_path, fault):
    p, old, job, outer, fresh, keep = setup_case(tmp_path)
    if fault == 'operation_exists': Path(job['operations']).mkdir()
    elif fault == 'different_error':
        Path(outer['log']['path']).write_text('ModuleNotFoundError: different dependency\n')
        outer['log'] = P.binding(outer['log']['path'])
    else:
        result = P.read(p['failed'])
        if fault == 'result_changed': result['status'] = 'ADMITTED'
        elif fault == 'uncertain': result['failure_type'] = 'TimeoutError'
        else: result['plan'] = keep(tmp_path / 'other-plan.json', {})
        p['failed'] = keep(Path(p['failed']['path']), result); outer['result'] = p['failed']
    with pytest.raises(ValueError): P.setup_dependency_guard(p, old, job, outer, fresh)


def test_dependency_selection_retains_original_dispatcher_and_checks_bytes(tmp_path):
    original = tmp_path / 'original.py'
    original.write_text('def setup():\n    from scripts.fleet.snapshot_inventory import equivalent\n    return equivalent\n')
    dep = tmp_path / 'snapshot_inventory.py'; dep.write_text('def equivalent(a,b): return a == b\n')
    selected = P.binding(dep); before = original.read_bytes()
    code = P.dependency_dispatcher_source(P.binding(original), selected)
    assert 'spec_from_file_location' in code and original.read_bytes() == before
    dep.write_text('changed')
    with pytest.raises(ValueError): P.dependency_dispatcher_source(P.binding(original), selected)

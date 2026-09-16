from pathlib import Path
import importlib.util
import pytest
from scripts.fleet import prepare_queue_successor as P
from scripts.fleet.migration import binding,checked
spec=importlib.util.spec_from_file_location('original_queue_tests',Path(__file__).with_name('test_fleet_prepare_queue.py'));fixtures=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixtures)


def test_explicit_dispatcher_is_in_every_plan_and_transfer(tmp_path):
    root=tmp_path.resolve()/'repo';bundle,handoff,history=fixtures.fixture(root)
    selected=root/'future_dispatcher.py';selected.write_text('# independently versioned continuation dispatcher\n');d=binding(selected)
    result=P.prepare(root,'f'*64,bundle,handoff,history,tmp_path.resolve()/'plans',dispatcher=d);v=checked(result)
    jobs=[checked(b) for batch in v['assignments'].values() for b in batch]
    assert len(jobs)==9 and v['claims']==v['workers_started']==0 and v['dispatcher']==d
    assert all(checked(j['inputs'][0])['dispatcher']==d and d in j['inputs'] for j in jobs)
    assert any(x['path']==d['path'] and x['sha256']==d['sha256'] for x in checked(v['plan_manifest'])['files'])
    assert (root/'diagnostics/authoring-resume-20260910/runner/advance_authoring_v7.py').read_text()=='# selected test fixture\n'


def test_altered_dispatcher_or_consumed_task_cannot_generate_plans(tmp_path):
    root=tmp_path.resolve()/'repo';bundle,handoff,history=fixtures.fixture(root)
    selected=root/'future_dispatcher.py';selected.write_text('# future\n');d=binding(selected);selected.write_text('# changed\n')
    with pytest.raises(ValueError,match='dispatcher changed'):
        P.prepare(root,'f'*64,bundle,handoff,history,tmp_path.resolve()/'plans',dispatcher=d)
    assert not (tmp_path/'plans').exists()
    d=binding(selected);prior=root/'diagnostics/authoring-queue-extension-20260911T185027Z/reconstruction-0085-attempt-001';prior.mkdir()
    with pytest.raises(ValueError,match='prior reconstruction consumed'):
        P.prepare(root,'f'*64,bundle,handoff,history,tmp_path.resolve()/'plans',dispatcher=d)


def test_dispatcher_in_release_requires_exact_manifest_hash(tmp_path):
    from scripts.fleet.migration import retain
    root=tmp_path.resolve()/'repo';bundle,handoff,history=fixtures.fixture(root)
    manifest=checked(bundle);manifest['files']['scripts/fleet/advance_authoring_v8.py']={'sha256':'c'*64};b=retain(root/'bundle-with-dispatcher.json',manifest)
    d={'path':str(P.REMOTE/'releases'/('f'*64)/'code/scripts/fleet/advance_authoring_v8.py'),'sha256':'c'*64}
    result=P.prepare(root,'f'*64,b,handoff,history,tmp_path.resolve()/'plans',dispatcher=d)
    value=checked(result);job=checked(value['assignments']['hhu-server'][0]);assert checked(job['inputs'][0])['dispatcher']==d
    assert not any(row['path']==d['path'] for row in checked(value['plan_manifest'])['files'])
    with pytest.raises(ValueError,match='differs from release'):
        P.prepare(root,'f'*64,b,handoff,history,tmp_path.resolve()/'plans-bad',dispatcher={**d,'sha256':'d'*64})

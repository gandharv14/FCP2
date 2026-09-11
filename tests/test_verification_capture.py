"""Inventory mechanics only; financial gates are explicit mocked boundaries."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import harbor_verification_capture as capture
from scripts.batch.harbor_admission import record


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) if isinstance(value, dict) else value)
    return path


def fixture(tmp_path, monkeypatch):
    root = tmp_path / 'code'
    write(root / 'scripts/harbor_verification_capture.py', '# selected verifier\n')
    write(root / '.cursor/skills/task-disclosure/SKILL.md', 'Required current instructions')
    write(root / 'scripts/batch/archive/executor.py.txt', 'Exact historical code bytes')
    write(root / 'requirements-lock.txt', 'openpyxl==3.1.5')
    write(root / 'verification_manifests/approved_source_inventories.v2.json', {'registry': 'current'})
    write(root / 'diagnostics/not-code.py', 'Not selected')
    write(root / 'tests/not-code.py', 'Not selected')
    write(root / '.env', 'SECRET=not-selected')
    monkeypatch.setattr(capture, 'ROOT', root)
    original = write(tmp_path / 'originals/0001.xlsx', 'original workbook')
    write(original.parent / '0002.xlsx', 'unrelated original')
    source = {'workbook_id': '0001', **record(original)}
    anchor = write(tmp_path / 'trust/public.pem', 'public key fixture')
    source_gen = tmp_path / 'source/generations/s1'
    predecessor = write(tmp_path / 'external/predecessor.xlsx', 'actual signed request source')
    write(source_gen / 'request.json', {'schema_version': 'source-recalc-request/v2', 'source': record(predecessor)})
    write(source_gen / 'source/0001.xlsx', 'recalculated workbook')
    write(source_gen / 'ast/0001/nodes.csv', 'node\n1\n')
    write(source_gen / 'ast/0001/edges.csv', 'from,to\n')
    seg_gen = tmp_path / 'segmentation/generations/g1'
    write(seg_gen / 'generation-manifest.json', {'id': 'g1'})
    stage = tmp_path / 'stage/0001-outputs'
    write(stage / 'instruction.md', 'task')
    write(stage / 'tests/answer_key.json', {'targets': {'S!A1': 42}})
    normalization = tmp_path / 'normalization/0001-variable-sources'
    write(normalization / 'normalized.json', {'variables': []})
    naturalization = tmp_path / 'naturalization'
    write(naturalization / 'state.json', {'status': 'applied'})
    observation = tmp_path / 'runtime'
    execution = write(observation / 'execution.json', {'valid': True})
    (observation / 'empty-directory').mkdir()
    write(observation / 'main-build.iid', 'sha256:fixture')
    task_root = tmp_path / 'published/tasks'
    final_task = task_root / 'task-generations/t1'
    write(final_task / 'instruction.md', 'task')
    publication_root = tmp_path / 'published/releases'
    manifest = write(publication_root / 'releases/r1/release-manifest.json', {'release_id': 'r1'})
    pointer = write(publication_root / 'current-release.json', {'release_id': 'r1'})
    tracker_path = tmp_path / 'trackers/0001.json'
    notes = write(tracker_path.parent / '0001.md', 'synchronized notes')
    write(tracker_path, {'workbook_id': '0001', 'promotion_ids': {'release_id': 'r1', 'task_generation_id': 't1'},
                         'agent_notes': record(notes)})
    transaction_root = tracker_path.parent / '0001-notes-transactions'
    before = write(transaction_root / 'promoted/before.json', {'workbook_id': '0001'})
    write(transaction_root / 'promoted/transaction.json', {
        'schema_version': capture.transitions.SCHEMA, 'action': 'promoted', 'before': record(before),
        'selection': 'selected', 'published': 'published'})
    write(transaction_root / 'older/transaction.json', {'action': 'other-transition'})
    write(transaction_root / 'promoted/complete.json', {'completed': True})
    content = SimpleNamespace(stage=stage, normalization_run=normalization, naturalization_root=naturalization,
        bindings={'source_generation': {'path': str(source_gen)}, 'segmentation_generation': {'path': str(seg_gen)}},
        mcp_build_a=None, mcp_build_b=None, pre_dialogue_witness=None, normalization_records=())
    selection = SimpleNamespace(content=content, observation=SimpleNamespace(execution=record(execution)))
    published = SimpleNamespace(release_id='r1', task_root=task_root, publication_root=publication_root,
                               pointer=record(pointer), manifest=record(manifest))
    monkeypatch.setattr(capture, 'decode', lambda key: {'selected': selection, 'published': published}[key])
    calls = []
    def qualify(*args, **kwargs):
        calls.append('native-qualification')
        return {'state': 'PROMOTED', 'verified_promoted': True, 'revalidation_required': False, 'release_id': 'r1'}
    monkeypatch.setattr(capture.promotion, 'validate', qualify)
    proof = {'publication_ready': True, 'blockers': [], 'checks': {'fixture-boundary': {'checked': True}},
             'evidence': [record(stage / 'instruction.md')]}
    def ready(*args, **kwargs):
        calls.append('canonical-proof')
        return proof
    monkeypatch.setattr(capture.transitions, '_proof', ready)
    identity = capture.runtime.RuntimeProducerIdentity('/producer/python', '/producer/docker')
    monkeypatch.setattr(capture.runtime, 'capture_runtime_producer_identity', lambda: identity)
    return SimpleNamespace(source=source, tracker=tracker_path, anchor=anchor, selection=selection,
                           proof=proof, calls=calls, root=root, predecessor=predecessor,
                           transaction_root=transaction_root, final_task=final_task, published=published)


def collect(f):
    return capture.collect(f.source, f.tracker, str(f.anchor))


def test_capture_preserves_exact_closure_and_never_collects_unrelated_workbooks(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    result = collect(f)
    files = {row['path']: row for row in result['files']}
    assert f.calls == ['native-qualification', 'canonical-proof']
    assert result['schema_version'] == capture.SCHEMA
    assert result['producer_identity'] == {'python_executable': '/producer/python', 'docker_executable': '/producer/docker'}
    for path in (f.predecessor, f.anchor, f.tracker, f.transaction_root / 'older/transaction.json',
                 f.root / '.cursor/skills/task-disclosure/SKILL.md', f.root / 'scripts/batch/archive/executor.py.txt',
                 f.root / 'verification_manifests/approved_source_inventories.v2.json',
                 f.root / 'scripts/harbor_verification_capture.py', f.final_task / 'instruction.md'):
        assert files[str(path)] == record(path)
    assert not any('/0002.xlsx' in name or name.endswith('/.env') or '/not-code.py' in name for name in files)
    assert str(tmp_path / 'runtime/empty-directory') in result['directories']
    assert str(tmp_path / 'originals') in result['protected_roots']
    assert str(f.final_task) in result['protected_roots']
    assert str(f.root) not in result['protected_roots']
    assert str(f.root / 'scripts/harbor_verification_capture.py') in result['protected_files']
    assert str(f.anchor) in result['protected_files']
    assert str(f.transaction_root / 'older/transaction.json') in result['protected_files']
    assert set(result['scratch_roots']) == {str(f.selection.content.stage), str(f.selection.content.normalization_run),
        str(f.selection.content.naturalization_root), str(tmp_path / 'runtime')}
    assert str(tmp_path) not in result['scratch_roots']
    assert all(row == record(row['path']) for row in result['files'])
    assert result['files'] == sorted(result['files'], key=lambda row: row['path'])


@pytest.mark.parametrize('change', ['stale', 'flag', 'revalidation'])
def test_noncurrent_promotion_rejects_before_proof(tmp_path, monkeypatch, change):
    f = fixture(tmp_path, monkeypatch)
    state = {'state': 'PROMOTED', 'verified_promoted': True, 'revalidation_required': False}
    state[{'stale': 'state', 'flag': 'verified_promoted', 'revalidation': 'revalidation_required'}[change]] = {
        'stale': 'STALE', 'flag': False, 'revalidation': True}[change]
    monkeypatch.setattr(capture.promotion, 'validate', lambda *a, **k: state)
    with pytest.raises(ValueError, match='current complete promotion'):
        collect(f)
    assert f.calls == []


@pytest.mark.parametrize('change', ['ready', 'blockers', 'unchecked'])
def test_canonical_proof_cannot_be_replaced_by_promotion_flag(tmp_path, monkeypatch, change):
    f = fixture(tmp_path, monkeypatch)
    if change == 'ready':
        f.proof['publication_ready'] = False
    elif change == 'blockers':
        f.proof['blockers'] = ['missing-evidence']
    else:
        f.proof['checks']['fixture-boundary']['checked'] = False
    with pytest.raises(ValueError, match='canonical complete readiness'):
        collect(f)


def test_ambiguous_journal_rejects_even_if_mocked_financial_boundary_passes(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    source = f.transaction_root / 'promoted/transaction.json'
    write(f.transaction_root / 'duplicate/transaction.json', source.read_text())
    with pytest.raises(ValueError, match='ambiguous'):
        collect(f)


def test_nested_metadata_dependencies_are_followed_and_rechecked(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    run = f.selection.content.naturalization_root
    leaf = write(run / 'retained-body.txt', 'exact response')
    parent = write(run / 'nested.json', {'body': record(leaf)})
    write(run / 'state.json', {'evidence': record(parent)})
    result = collect(f)
    assert record(leaf) in result['files']
    leaf.write_text('altered response')
    with pytest.raises(ValueError, match='hash changed'):
        collect(f)


def test_missing_hash_bound_dependency_is_not_silently_omitted(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    run = f.selection.content.naturalization_root
    write(run / 'state.json', {'body': {'path': str(run / 'missing.txt'), 'sha256': 'a' * 64}})
    with pytest.raises(ValueError, match='missing declared verification dependency'):
        collect(f)


def test_missing_request_source_outside_generation_rejects(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    f.predecessor.unlink()
    with pytest.raises(ValueError, match='missing declared verification dependency'):
        collect(f)


def test_missing_normalization_lineage_dependency_rejects(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    checkpoint = write(tmp_path / 'lineage/checkpoint.json', {'candidate': str(tmp_path / 'lineage/missing.xlsx'),
                                                          'candidate_sha256': 'a' * 64})
    f.selection.content.normalization_records = (record(checkpoint),)
    with pytest.raises(ValueError, match='missing declared verification dependency'):
        collect(f)


def test_selected_tree_symlink_is_rejected(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    (f.selection.content.stage / 'linked.txt').symlink_to(f.predecessor)
    with pytest.raises(ValueError):
        collect(f)


@pytest.mark.parametrize('mutation', ['bytes', 'membership'])
def test_changes_during_capture_fail_before_return(tmp_path, monkeypatch, mutation):
    f = fixture(tmp_path, monkeypatch)
    original = capture._Inventory.close_json
    def changed(inventory):
        original(inventory)
        write(f.selection.content.stage / ('instruction.md' if mutation == 'bytes' else 'new.txt'), 'changed')
    monkeypatch.setattr(capture._Inventory, 'close_json', changed)
    with pytest.raises(ValueError, match='changed during capture'):
        collect(f)


def test_json_size_limit_fails_explicitly(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(capture, 'MAX_JSON_BYTES', 1)
    with pytest.raises(ValueError, match='exceeds supported closure inspection bound'):
        collect(f)


@pytest.mark.parametrize('kind', ['source_generation', 'original_parent', 'protected_file'])
def test_scratch_candidates_with_protected_overlap_are_excluded(tmp_path, monkeypatch, kind):
    f = fixture(tmp_path, monkeypatch)
    if kind == 'source_generation':
        f.selection.content.naturalization_root = Path(f.selection.content.bindings['source_generation']['path'])
    elif kind == 'original_parent':
        f.selection.content.naturalization_root = Path(f.source['path']).parent
    else:
        f.selection.content.bindings['curation'] = record(f.selection.content.naturalization_root / 'state.json')
    result = collect(f)
    assert str(f.selection.content.naturalization_root) not in result['scratch_roots']


@pytest.mark.parametrize('filename', ['/tmp/../private/file.json', '/tmp//file.json', '/tmp/.env', '/tmp/.env.production'])
def test_unsafe_or_private_path_rejected(filename):
    with pytest.raises(ValueError):
        capture._path(filename)


def test_explicit_runtime_producer_replays_both_owning_gates_without_native_capture(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    native = collect(f)
    identity = capture.runtime.RuntimeProducerIdentity('/producer/python', '/producer/docker')
    calls = []
    qualification, proof = capture.promotion.validate, capture.transitions._proof
    def qualify(*args, **kwargs):
        calls.append(('promotion', kwargs))
        return qualification(*args, **kwargs)
    def ready(*args, **kwargs):
        calls.append(('proof', kwargs))
        return proof(*args, **kwargs)
    def forbidden():
        raise AssertionError('isolated explicit producer must not inspect native Docker locator')
    monkeypatch.setattr(capture.runtime, 'capture_runtime_producer_identity', forbidden)
    monkeypatch.setattr(capture.promotion, 'validate', qualify)
    monkeypatch.setattr(capture.transitions, '_proof', ready)
    isolated = capture.collect(f.source, f.tracker, str(f.anchor), runtime_producer=identity)
    assert isolated == native
    assert calls == [('promotion', {'trusted_public_key':str(f.anchor), 'runtime_producer':identity}),
                     ('proof', {'runtime_producer':identity})]


@pytest.mark.parametrize('producer', [True, {}, SimpleNamespace(python_executable='/producer/python', docker_executable='/producer/docker')])
def test_explicit_producer_requires_closed_type_before_gates(tmp_path, monkeypatch, producer):
    f = fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='typed runtime producer'):
        capture.collect(f.source, f.tracker, str(f.anchor), runtime_producer=producer)
    assert f.calls == []


def test_mutated_typed_producer_paths_are_revalidated_before_capture(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    identity = capture.runtime.RuntimeProducerIdentity('/producer/python', '/producer/docker')
    object.__setattr__(identity, 'python_executable', 'relative/python')
    with pytest.raises(ValueError, match='canonical and absolute'):
        capture.collect(f.source, f.tracker, str(f.anchor), runtime_producer=identity)
    assert f.calls == []


def test_explicit_producer_change_during_capture_rejected(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    identity = capture.runtime.RuntimeProducerIdentity('/producer/python', '/producer/docker')
    original = capture._Inventory.close_json
    def changed(inventory):
        original(inventory)
        object.__setattr__(identity, 'docker_executable', '/another/docker')
    monkeypatch.setattr(capture._Inventory, 'close_json', changed)
    with pytest.raises(ValueError, match='explicit verification context changed'):
        capture.collect(f.source, f.tracker, str(f.anchor), runtime_producer=identity)


def test_default_capture_preserves_native_gate_keywords(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    seen = []
    qualification, proof = capture.promotion.validate, capture.transitions._proof
    def qualify(*args, **kwargs):
        seen.append(('promotion', kwargs))
        return qualification(*args, **kwargs)
    def ready(*args, **kwargs):
        seen.append(('proof', kwargs))
        return proof(*args, **kwargs)
    monkeypatch.setattr(capture.promotion, 'validate', qualify)
    monkeypatch.setattr(capture.transitions, '_proof', ready)
    collect(f)
    assert seen == [('promotion', {'trusted_public_key':str(f.anchor)}), ('proof', {})]

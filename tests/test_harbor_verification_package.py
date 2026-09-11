"""Synthetic custody protocol tests; mocked capture/replay is not financial proof."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import harbor_retention as retention
from scripts import harbor_verification_capture as capture
from scripts import harbor_verification_package as package

IMAGE = 'sha256:' + '1' * 64


def binding(path):
    return {'path': str(path), 'sha256': package.digest(path)}


@pytest.fixture
def custody(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    scratch, raw, final, code = [root/n for n in ('scratch', 'raw', 'final', 'code')]
    for path in (scratch, raw, final, code):
        path.mkdir()
    first, second = scratch/'nodes.csv', scratch/'old-nodes.csv'
    first.write_bytes(b'exact duplicated synthetic graph\n')
    second.write_bytes(first.read_bytes())
    source = raw/'source.xlsx'; source.write_bytes(b'synthetic-source-not-an-excel-proof')
    task = final/'instruction.md'; task.write_text('Synthetic fixture task, no financial proof.')
    key = raw/'trusted.pem'; key.write_bytes(b'synthetic-key')
    tracker = raw/'tracker.json'; tracker.write_text('{"fixture":"tracker"}')
    pointer = raw/'release.json'; pointer.write_text('{"fixture":"release"}')
    manifest = raw/'generation.json'; manifest.write_text('{"fixture":"generation"}')
    verifier = code/'verifier.py'; verifier.write_text('# synthetic installed code fixture\n')
    captured = dict(schema_version=capture.SCHEMA, source=binding(source), files=[binding(p) for p in
                    (first, second, source, tracker, pointer, manifest, verifier, key, task)],
                    directories=[str(final)], final_task_root=str(final), scratch_roots=[str(scratch)],
                    verifier_root=str(code), protected_roots=[str(raw), str(final), str(code)],
                    protected_files=sorted([str(tracker), str(pointer), str(manifest), str(verifier), str(key)]),
                    tracker=binding(tracker), release_pointer=binding(pointer),
                    release_manifest=binding(manifest), release_id='fixture-release',
                    producer_identity={'python_executable':'/producer/python','docker_executable':'/producer/docker'})
    captured['files'] = sorted(captured['files'], key=lambda row: row['path'])
    calls = []
    def collect(*args, **kwargs):
        calls.append(args)
        return deepcopy(captured)
    # Explicit fixture substitutions: no owning capture or financial validator runs.
    monkeypatch.setattr(capture, 'collect', collect)
    monkeypatch.setattr(package, 'ROOT', code)
    monkeypatch.setattr(package, '_code_files', lambda: [verifier])
    monkeypatch.setattr(package, '_CAPABILITIES', {})
    # The protocol suite is hermetic; native APFS operation has dedicated tests.
    monkeypatch.setattr(package, '_clone_file', lambda *args: False)
    bundle = root/'private-package'
    expected = package.build_package(binding(source), tracker, key, bundle)
    return SimpleNamespace(root=root, scratch=scratch, raw=raw, final=final, code=code,
                           first=first, second=second, source=source, key=key, task=task,
                           tracker=tracker, pointer=pointer, release_manifest=manifest,
                           verifier=verifier, bundle=bundle, expected=expected,
                           captured=captured, capture_calls=calls)


def selected_plan(custody):
    h = package.digest(custody.first)
    return retention.plan_cleanup([
        {'path': str(custody.first), 'retained_path': str(custody.bundle/'objects'/h)}],
        disposable_roots=[str(custody.scratch)], protected_roots=[str(custody.raw)],
        retained_roots=[str(custody.bundle)])


def docker_stub(monkeypatch, *, outcome=None, image_id=IMAGE, returncode=0):
    calls = []
    if outcome is None:
        outcome = {'state': 'PROMOTED', 'verified_promoted': True, 'release_id': 'fixture-release', 'revalidation_required':False}
    def run(argv, **kwargs):
        calls.append((list(argv), kwargs))
        if argv[1:3] == ['image', 'inspect']:
            return SimpleNamespace(returncode=0, stdout=json.dumps([{'Id': image_id, 'Os':'linux'}]), stderr='')
        assert argv[1] == 'run'
        return SimpleNamespace(returncode=returncode, stdout=json.dumps(outcome), stderr='synthetic rejection')
    monkeypatch.setattr(package.shutil, 'which', lambda tool: '/fixture/docker')
    monkeypatch.setattr(package.subprocess, 'run', run)
    return calls


def rewrite_manifest(custody, value):
    path = custody.bundle/'manifest.json'
    path.write_bytes(package.canonical(value))
    return package.digest(path)


def test_build_deduplicates_exact_bytes_and_materializes_independent_copies(custody):
    value = package.inspect_package(custody.bundle, custody.expected)
    assert len(custody.capture_calls) == 1
    assert len(value['files']) == len(value['objects']) + 1
    mappings = {row['path']: row for row in value['files']}
    assert mappings[str(custody.first)]['sha256'] == mappings[str(custody.second)]['sha256']
    namespace = custody.root/'namespace'
    package.materialize_package(custody.bundle, custody.expected, namespace)
    for row in value['files']:
        copied = namespace / Path(row['path']).relative_to('/')
        assert package.digest(copied) == row['sha256']
        assert copied.stat().st_ino != (custody.bundle/'objects'/row['sha256']).stat().st_ino
    edited = namespace/custody.first.relative_to('/')
    edited.chmod(0o600); edited.write_bytes(b'edited restored bytes')
    package.inspect_package(custody.bundle, custody.expected)
    assert custody.first.read_bytes() == b'exact duplicated synthetic graph\n'
    assert (namespace/custody.final.relative_to('/')).is_dir()


@pytest.mark.parametrize('bad', [None, '', '0'*64, True, 'a'*63])
def test_manifest_requires_external_exact_digest(custody, bad):
    with pytest.raises(package.PackageError):
        package.inspect_package(custody.bundle, bad)


@pytest.mark.parametrize('mutation', ['changed', 'missing', 'extra', 'symlink'])
def test_object_tampering_is_rejected(custody, mutation):
    h = package.digest(custody.first)
    obj = custody.bundle/'objects'/h
    if mutation == 'changed':
        obj.chmod(0o600); obj.write_bytes(b'wrong')
    elif mutation == 'missing': obj.unlink()
    elif mutation == 'extra': (obj.parent/'unexpected').write_bytes(b'extra')
    else:
        obj.unlink(); obj.symlink_to(custody.first)
    with pytest.raises(package.PackageError):
        package.inspect_package(custody.bundle, custody.expected)


@pytest.mark.parametrize('kind', ['bundle', 'manifest', 'objects', 'destination'])
def test_symlink_custody_paths_rejected(custody, kind):
    if kind == 'destination':
        destination = custody.root/'namespace-link'; destination.symlink_to(custody.raw, target_is_directory=True)
        with pytest.raises(package.PackageError):
            package.materialize_package(custody.bundle, custody.expected, destination)
        return
    target = custody.bundle if kind == 'bundle' else custody.bundle/('manifest.json' if kind == 'manifest' else kind)
    actual = target.with_name(target.name+'-actual')
    target.rename(actual); target.symlink_to(actual, target_is_directory=actual.is_dir())
    with pytest.raises(package.PackageError):
        package.inspect_package(custody.bundle, custody.expected)


@pytest.mark.parametrize('path', ['relative/file', '/tmp/../escape', '/tmp//double', '/tmp/new\nline'])
@pytest.mark.parametrize('field', ['files', 'directories'])
def test_namespace_traversal_and_noncanonical_paths_rejected(custody, path, field):
    value = package.inspect_package(custody.bundle, custody.expected)
    if field == 'files': value['files'][0]['path'] = path
    else: value['directories'].append(path)
    expected = rewrite_manifest(custody, value)
    with pytest.raises(package.PackageError):
        package.inspect_package(custody.bundle, expected)


@pytest.mark.parametrize('mutation', ['duplicate_path', 'duplicate_object', 'unreferenced_object', 'file_parent'])
def test_closed_namespace_membership_rejected(custody, mutation):
    value = package.inspect_package(custody.bundle, custody.expected)
    if mutation == 'duplicate_path': value['files'].append(deepcopy(value['files'][0]))
    elif mutation == 'duplicate_object': value['objects'].append(deepcopy(value['objects'][0]))
    elif mutation == 'unreferenced_object':
        raw=b'unreferenced'; h=hashlib.sha256(raw).hexdigest()
        (custody.bundle/'objects'/h).write_bytes(raw)
        value['objects'].append({'sha256':h,'size':len(raw)})
    else: value['directories'].append(value['files'][0]['path']+'/child')
    expected = rewrite_manifest(custody, value)
    with pytest.raises(package.PackageError): package.inspect_package(custody.bundle, expected)


@pytest.mark.parametrize('anchor', ['tracker', 'release_pointer', 'release_manifest'])
def test_publication_anchor_must_be_packaged(custody, anchor):
    value = package.inspect_package(custody.bundle, custody.expected)
    value[anchor]['sha256'] = '0'*64
    expected = rewrite_manifest(custody, value)
    with pytest.raises(package.PackageError, match='anchor'):
        package.inspect_package(custody.bundle, expected)


def test_docker_replay_is_pinned_isolated_and_never_mounts_host_originals(custody, monkeypatch):
    calls = docker_stub(monkeypatch)
    result = package.verify_package(custody.bundle, custody.expected, IMAGE)
    assert result['isolated'] is True and result['manifest_sha256'] == custody.expected
    assert len(calls) == 2
    command, kwargs = calls[1]
    assert kwargs['timeout'] == 900 and kwargs['check'] is False
    for flag, value in [('--pull','never'),('--network','none'),('--cap-drop','ALL'),
                        ('--security-opt','no-new-privileges'),('--memory','8g'),
                        ('--entrypoint','/usr/local/bin/python')]:
        assert command[command.index(flag)+1] == value
    assert '--read-only' in command and command[-3] == str(custody.code/'scripts/harbor_verification_package.py')
    assert command[-4] == IMAGE and command[-2:] == ['--replay', custody.expected]
    mounts = [command[i+1] for i, value in enumerate(command) if value == '--mount']
    assert mounts and all(value.endswith(',readonly') for value in mounts)
    assert any(f'src={custody.bundle},dst=/custody,readonly' in value for value in mounts)
    for mount in mounts:
        source = next(p[4:] for p in mount.split(',') if p.startswith('src='))
        if source != str(custody.bundle):
            assert '/namespace/' in source
            assert not source.startswith(str(custody.root)+'/')
        assert 'docker.sock' not in mount and ',dst=/,' not in mount


@pytest.mark.parametrize('image', ['latest', 'python:3.12', 'repo@sha256:'+'1'*64, True])
def test_unpinned_image_rejected_before_transport(custody, monkeypatch, image):
    calls = docker_stub(monkeypatch)
    with pytest.raises(package.PackageError, match='pinned'):
        package.verify_package(custody.bundle, custody.expected, image)
    assert calls == []


def test_inspected_image_identity_mismatch_rejected(custody, monkeypatch):
    calls = docker_stub(monkeypatch, image_id='sha256:'+'2'*64)
    with pytest.raises(package.PackageError, match='identity'):
        package.verify_package(custody.bundle, custody.expected, IMAGE)
    assert len(calls) == 1


@pytest.mark.parametrize('outcome', [
    {'state':'PROMOTED','verified_promoted':False,'release_id':'fixture-release'},
    {'state':'PROMOTED','verified_promoted':True,'release_id':'foreign-release'},
    {'state':'STOPPED','verified_promoted':True,'release_id':'fixture-release'},
])
def test_nonqualifying_replay_cannot_issue_capability(custody, monkeypatch, outcome):
    docker_stub(monkeypatch, outcome=outcome)
    with pytest.raises(package.PackageError):
        package.authorize_cleanup(custody.bundle, custody.expected, IMAGE, selected_plan(custody))
    assert package._CAPABILITIES == {}


def test_changed_bundled_code_rejected_even_with_rehashed_manifest(custody, monkeypatch):
    value = package.inspect_package(custody.bundle, custody.expected)
    old = package.digest(custody.verifier); raw = b'# changed verifier code\n'
    new = hashlib.sha256(raw).hexdigest()
    (custody.bundle/'objects'/old).unlink(); (custody.bundle/'objects'/new).write_bytes(raw)
    for row in value['files'] + value['objects'] + value['verifier_files']:
        if row['sha256'] == old:
            row['sha256'] = new
            if 'size' in row: row['size'] = len(raw)
    expected = rewrite_manifest(custody, value)
    calls = docker_stub(monkeypatch)
    with pytest.raises(package.PackageError, match='installed verifier differs'):
        package.verify_package(custody.bundle, expected, IMAGE)
    assert calls == []


def test_capability_is_process_issued_identity_and_exact_plan(custody, monkeypatch):
    docker_stub(monkeypatch)
    plan = selected_plan(custody)
    auth = package.authorize_cleanup(custody.bundle, custody.expected, IMAGE, plan)
    assert package.validate_cleanup_authorization(auth, plan)['isolated'] is True
    for forged in (True, {'token':auth.token}, package.CleanupAuthorization(auth.token),
                   package.CleanupAuthorization('made-up')):
        with pytest.raises(package.PackageError): package.validate_cleanup_authorization(forged, plan)
    changed = deepcopy(plan); changed['logical_bytes'] += 1
    with pytest.raises(package.PackageError, match='plan changed'):
        package.validate_cleanup_authorization(auth, changed)


@pytest.mark.parametrize('anchor', ['tracker', 'pointer', 'release_manifest'])
def test_current_publication_anchor_change_revokes_capability(custody, monkeypatch, anchor):
    docker_stub(monkeypatch)
    plan = selected_plan(custody)
    auth = package.authorize_cleanup(custody.bundle, custody.expected, IMAGE, plan)
    getattr(custody, anchor).write_bytes(b'changed publication')
    with pytest.raises(package.PackageError, match='bound file changed'):
        package.validate_cleanup_authorization(auth, plan)
    assert custody.first.exists()


@pytest.mark.parametrize('root', ['bin', 'usr', 'proc', 'dev', 'etc'])
def test_unsupported_system_namespace_mount_rejected(tmp_path, root):
    (tmp_path/root).mkdir()
    if root == 'etc': (tmp_path/root/'passwd').write_bytes(b'not allowed')
    with pytest.raises(package.PackageError, match='unsupported'):
        package._mounts(tmp_path)


@pytest.mark.parametrize('where', ['bundle', 'objects'])
def test_materialization_never_changes_custody_tree(custody, where):
    destination = custody.bundle/'namespace' if where == 'bundle' else custody.bundle/'objects'/'namespace'
    with pytest.raises(package.PackageError):
        package.materialize_package(custody.bundle, custody.expected, destination)
    assert not destination.exists()
    package.inspect_package(custody.bundle, custody.expected)


@pytest.mark.parametrize('where', ['scratch', 'raw', 'final', 'code'])
def test_materialization_stays_outside_original_selected_roots(custody, where):
    destination = getattr(custody, where)/'restored-namespace'
    with pytest.raises(package.PackageError):
        package.materialize_package(custody.bundle, custody.expected, destination)
    assert not destination.exists()


def test_custody_mount_path_cannot_inject_docker_mount_options(custody, monkeypatch):
    moved = custody.bundle.with_name('private-package,readonly=false')
    custody.bundle.rename(moved)
    calls = docker_stub(monkeypatch)
    with pytest.raises(package.PackageError, match='comma'):
        package.verify_package(moved, custody.expected, IMAGE)
    assert calls == []


@pytest.mark.parametrize('qualified', [True, False])
def test_internal_replay_reenters_owning_validator(custody, monkeypatch, capsys, qualified):
    from scripts.batch import harbor_promotion_evidence as promotion
    from scripts.batch import harbor_runtime_readiness as readiness
    value = package.inspect_package(custody.bundle, custody.expected)
    inspections, calls = [], []
    identity = object()
    def inspect(path, expected):
        inspections.append((path, expected))
        return value
    def validate(*args, **kwargs):
        calls.append((args, kwargs))
        return {'state':'PROMOTED' if qualified else 'STOPPED',
                'verified_promoted':qualified, 'release_id':'fixture-release'}
    monkeypatch.setattr(package, 'inspect_package', inspect)
    # Explicit mocked owning validator; exercises dispatch, not financial validity.
    monkeypatch.setattr(promotion, 'validate', validate)
    monkeypatch.setattr(readiness, 'RuntimeProducerIdentity', lambda **kwargs: identity)
    if qualified:
        package._replay(custody.expected)
        assert json.loads(capsys.readouterr().out)['verified_promoted'] is True
    else:
        with pytest.raises(package.PackageError, match='owning verifier'):
            package._replay(custody.expected)
    assert inspections == [(Path('/custody'), custody.expected)]
    assert len(calls) == 1
    assert calls[0][0] == (value['source'], Path(value['tracker']['path']))
    assert calls[0][1] == {'trusted_public_key':value['trusted_public_key'], 'runtime_producer':identity}


@pytest.mark.parametrize('mutation', ['task', 'source', 'verifier'])
def test_live_release_changes_revoke_issued_capability(custody, monkeypatch, mutation):
    docker_stub(monkeypatch)
    plan = selected_plan(custody)
    auth = package.authorize_cleanup(custody.bundle, custody.expected, IMAGE, plan)
    getattr(custody, mutation).write_bytes(b'changed after isolated replay')
    with pytest.raises(package.PackageError):
        package.validate_cleanup_authorization(auth, plan)
    assert custody.first.exists()


@pytest.mark.parametrize('operation', ['authorize', 'qualify'])
def test_live_final_task_tamper_blocks_private_qualification(custody, monkeypatch, operation):
    docker_stub(monkeypatch)
    custody.task.write_bytes(b'changed final task')
    with pytest.raises(package.PackageError, match='live final task'):
        if operation == 'authorize':
            package.authorize_cleanup(custody.bundle, custody.expected, IMAGE, selected_plan(custody))
        else:
            package.qualify_retained_release(custody.bundle, custody.expected, IMAGE,
                source=custody.captured['source'], tracker_path=custody.tracker,
                trusted_public_key=custody.key)
    assert package._CAPABILITIES == {}


def test_private_qualification_requires_exact_requested_source(custody, monkeypatch):
    docker_stub(monkeypatch)
    wrong = dict(custody.captured['source'], sha256='0'*64)
    with pytest.raises(package.PackageError, match='another release'):
        package.qualify_retained_release(custody.bundle, custody.expected, IMAGE,
            source=wrong, tracker_path=custody.tracker, trusted_public_key=custody.key)


def test_added_installed_code_revokes_capability(custody, monkeypatch):
    docker_stub(monkeypatch)
    plan = selected_plan(custody)
    auth = package.authorize_cleanup(custody.bundle, custody.expected, IMAGE, plan)
    added = custody.code/'new_verifier.py'; added.write_text('# added installed verifier dependency\n')
    monkeypatch.setattr(package, '_code_files', lambda: [custody.verifier, added])
    with pytest.raises(package.PackageError):
        package.validate_cleanup_authorization(auth, plan)


def test_live_trust_anchor_change_revokes_capability(custody, monkeypatch):
    docker_stub(monkeypatch)
    plan = selected_plan(custody)
    auth = package.authorize_cleanup(custody.bundle, custody.expected, IMAGE, plan)
    custody.key.write_bytes(b'changed trusted public key')
    with pytest.raises(package.PackageError):
        package.validate_cleanup_authorization(auth, plan)


@pytest.mark.parametrize('mutation', ['top_level_list', 'missing_anchor', 'files_null', 'anchor_null', 'object_bool_size'])
def test_malformed_manifest_never_runs_replay_or_issues_capability(custody, monkeypatch, mutation):
    value = package.inspect_package(custody.bundle, custody.expected)
    if mutation == 'top_level_list': value = []
    elif mutation == 'missing_anchor': value.pop('release_pointer')
    elif mutation == 'files_null': value['files'] = None
    elif mutation == 'anchor_null': value['tracker'] = None
    else: value['objects'][0]['size'] = True
    expected = rewrite_manifest(custody, value)
    calls = docker_stub(monkeypatch)
    # Wrong schema shapes may be rejected by closed-field checks or typed access;
    # neither is permission to continue replay or produce a cleanup capability.
    with pytest.raises((ValueError, KeyError, TypeError, AttributeError)):
        package.authorize_cleanup(custody.bundle, expected, IMAGE, selected_plan(custody))
    assert calls == [] and package._CAPABILITIES == {}
    assert custody.first.exists()


def test_trust_anchor_change_after_capture_cannot_be_rebound(custody, monkeypatch):
    frozen = deepcopy(custody.captured)
    def raced_capture(*args):
        custody.key.write_text('a changed trust anchor')
        return frozen
    monkeypatch.setattr(capture, 'collect', raced_capture)
    destination = custody.root / 'raced-package'
    with pytest.raises(package.PackageError, match='trusted key changed'):
        package.build_package(binding(custody.source), custody.tracker, custody.key, destination)
    assert not destination.exists()


def test_retained_qualification_identifies_live_task_not_deleted_stage(custody, monkeypatch):
    import xl_release_publication as release
    outcome = dict(state='PROMOTED', verified_promoted=True, revalidation_required=False,
                   release_id='fixture-release', output_path=str(custody.scratch),
                   output_sha256='a' * 64)
    monkeypatch.setattr(package, 'verify_package', lambda *args: {'qualification':outcome})
    custody.first.unlink(); custody.second.unlink(); custody.scratch.rmdir()
    result = package.qualify_retained_release(custody.bundle, custody.expected, IMAGE,
        source=custody.captured['source'], tracker_path=custody.tracker, trusted_public_key=custody.key)
    assert result['output_path'] == str(custody.final)
    assert result['output_sha256'] == release._tree_hash(custody.final)[0]
    assert result['original_stage'] == {'path':str(custody.scratch),'sha256':'a'*64}
    assert result['verified_promoted'] is True


@pytest.mark.parametrize('mutation', ['scratch_roots', 'protected_roots', 'protected_files',
                                      'directories', 'extra_file', 'missing_file'])
def test_qualifying_financial_stub_cannot_authorize_forged_custody_scope(custody, monkeypatch, mutation):
    """Mock finance succeeds; independent capture must still reject forged scope."""
    import contextlib
    import io
    from scripts.batch import harbor_promotion_evidence as promotion
    value = package.inspect_package(custody.bundle, custody.expected)
    if mutation == 'scratch_roots': value['scratch_roots'] = [str(custody.raw)]
    elif mutation == 'protected_roots': value['protected_roots'] = []
    elif mutation == 'protected_files': value['protected_files'] = []
    elif mutation == 'directories': value['directories'].append(str(custody.raw/'invented-directory'))
    elif mutation == 'extra_file':
        original = next(row for row in value['files'] if row['path'] == str(custody.first))
        value['files'].append({**original, 'path':str(custody.raw/'unrelated.xlsx')})
        value['files'].sort(key=lambda row: row['path'])
    else:
        value['files'] = [row for row in value['files'] if row['path'] != str(custody.first)]
    expected = rewrite_manifest(custody, value)
    financial_calls, capture_calls = [], []
    def financial(*args, **kwargs):
        financial_calls.append((args, kwargs))
        return {'state':'PROMOTED','verified_promoted':True,'revalidation_required':False,
                'release_id':'fixture-release'}
    # This frozen owning-capture result is independent of the forged manifest.
    def collect(*args, **kwargs):
        capture_calls.append((args, kwargs))
        return deepcopy(custody.captured)
    monkeypatch.setattr(promotion, 'validate', financial)
    monkeypatch.setattr(capture, 'collect', collect)
    original_inspect = package.inspect_package
    def inspected(path, h):
        return original_inspect(custody.bundle if Path(path) == Path('/custody') else path, h)
    monkeypatch.setattr(package, 'inspect_package', inspected)
    docker_stub(monkeypatch)
    transport = package.subprocess.run
    def replay(argv, **kwargs):
        if argv[1:3] == ['image', 'inspect']:
            return transport(argv, **kwargs)
        assert argv[1] == 'run'
        output = io.StringIO()
        try:
            with contextlib.redirect_stdout(output):
                package._replay(expected)
        except package.PackageError as error:
            return SimpleNamespace(returncode=1, stdout=output.getvalue(), stderr=str(error))
        return SimpleNamespace(returncode=0, stdout=output.getvalue(), stderr='')
    monkeypatch.setattr(package.subprocess, 'run', replay)
    with pytest.raises(package.PackageError, match='owning capture'):
        package.authorize_cleanup(custody.bundle, expected, IMAGE, selected_plan(custody))
    assert len(financial_calls) == 1 and len(capture_calls) == 1
    assert capture_calls[0][1]['runtime_producer'] is financial_calls[0][1]['runtime_producer']
    assert package._CAPABILITIES == {} and custody.first.exists()


def test_exact_scope_replay_compares_all_capture_metadata(custody, monkeypatch, capsys):
    from scripts.batch import harbor_promotion_evidence as promotion
    value = package.inspect_package(custody.bundle, custody.expected)
    monkeypatch.setattr(package, 'inspect_package', lambda *args: value)
    calls = []
    def qualify(*args, **kwargs):
        calls.append(kwargs)
        return {'state':'PROMOTED','verified_promoted':True,'revalidation_required':False,
                'release_id':'fixture-release'}
    monkeypatch.setattr(promotion, 'validate', qualify)
    package._replay(custody.expected)
    assert json.loads(capsys.readouterr().out)['verified_promoted'] is True
    assert len(calls) == 1

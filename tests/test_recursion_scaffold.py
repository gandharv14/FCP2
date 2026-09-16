"""Exercise the Docker and direct-OCI paths without publishing images."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import recursion_scaffold as scaffold
import recursion_registry_image as registry


def context(tmp_path):
    build = tmp_path / 'build'
    research = build / 'tasks/0006/mcp-server/runtime'
    research.mkdir(parents=True)
    (research / 'records.jsonl').write_bytes(b'{"source":"research"}\n')
    (build / 'tasks/0006/0006-inputs.xlsx').write_bytes(b'unchanged workbook')
    (build / 'fcp-inactivity-watchdog').write_text('#!/bin/sh\nexit 0\n')
    return build


def test_installed_runtime_keeps_inputs_and_research_private(tmp_path):
    build = context(tmp_path)
    before = {p.relative_to(build): p.read_bytes() for p in (build / 'tasks').rglob('*') if p.is_file()}
    manifest = scaffold.install(build)
    assert len(manifest['files']) == 8
    assert all((build / p).read_bytes() == data for p, data in before.items())
    assert (build / 'tasks').stat().st_mode & 0o777 == 0o700
    assert all((build / p).stat().st_mode & 0o777 == 0o600 for p in before)
    assert 'COPY --chown=0:0 isolation-rootfs/ /' in (build / 'Dockerfile').read_text()
    assert 'fcp-isolated-runtime opencode' in (build / 'fcp-opencode-wrapper').read_text()


@pytest.mark.parametrize('defect', ['legacy', 'wrapper', 'runtime', 'symlink'])
def test_unsafe_context_cannot_be_published(tmp_path, defect):
    build = context(tmp_path)
    if defect != 'legacy':
        scaffold.install(build)
        if defect == 'wrapper':
            (build / 'fcp-wrapper').write_text('legacy root wrapper')
        elif defect == 'runtime':
            (build / 'isolation-rootfs/usr/local/bin/fcp-isolated-runtime').write_text('tampered')
        else:
            (build / 'tasks/0006/link').symlink_to(build / 'tasks/0006/0006-inputs.xlsx')
    with pytest.raises(AssertionError):
        scaffold.validate(build)


def test_direct_oci_layer_enforces_same_ownership_and_runtime(tmp_path):
    build = context(tmp_path)
    manifest = scaffold.install(build)
    files = [{'path': p.relative_to(build).as_posix(), 'bytes': p.stat().st_size,
              'sha256': scaffold.sha(p)} for p in build.rglob('*') if p.is_file()]
    (tmp_path / 'registry-approval.json').write_text(json.dumps({'status': 'approved', 'files': files}))
    archive, _ = registry.layer(tmp_path)
    with tarfile.open(archive) as tf:
        assert len(tf.getnames()) == len(set(tf.getnames()))
        for member in tf.getmembers():
            assert member.uid == member.gid == 0
            if member.name.startswith('opt/fcp/tasks'):
                assert member.mode == (0o700 if member.isdir() else 0o600)
        for entry in manifest['files']:
            assert tf.getmember(entry['destination']).mode == entry['mode']
            assert hashlib.sha256(tf.extractfile(entry['destination']).read()).hexdigest() == entry['sha256']
        assert tf.extractfile('opt/fcp/tasks/0006/0006-inputs.xlsx').read() == b'unchanged workbook'
        assert tf.extractfile('opt/fcp/tasks/0006/mcp-server/runtime/records.jsonl').read() == b'{"source":"research"}\n'


@pytest.mark.parametrize('copied_helper', [False, True])
def test_prepare_build_context_installs_isolation(tmp_path, copied_helper):
    source = ROOT.parent / 'run_recursion_eval.py'
    if copied_helper:
        copied = tmp_path / 'runner_support.py'
        copied.write_bytes(source.read_bytes())
        source = copied
    spec = importlib.util.spec_from_file_location('scaffold_test_helper', source)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    task = tmp_path / 'source-task'
    research = task / 'environment/mcp-server/runtime'
    research.mkdir(parents=True)
    (task / 'environment/0006-inputs.xlsm').write_bytes(b'macro workbook')
    (research / 'records.jsonl').write_bytes(b'research corpus')
    helper.prepare_build_context(tmp_path / 'build', {'0006': task})
    scaffold.validate(tmp_path / 'build')
    assert (tmp_path / 'build/tasks/0006/0006-inputs.xlsm').read_bytes() == b'macro workbook'


def test_parent_runner_rejects_legacy_before_docker(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('scaffold_guard_helper', ROOT.parent / 'run_recursion_eval.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    monkeypatch.setattr(helper, 'shell', lambda *a, **kw: pytest.fail('Unsafe context reached Docker'))
    with pytest.raises(AssertionError, match='Legacy build context'):
        helper.build_runner_image({}, context(tmp_path), 'base', 'repo', 'tag', 'project')


def test_runtime_change_changes_image_fingerprint(monkeypatch):
    spec = importlib.util.spec_from_file_location('scaffold_digest_helper', ROOT.parent / 'run_recursion_eval.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    monkeypatch.setattr(helper, 'isolation_scaffold', lambda: scaffold)
    before = helper.task_set_digest({})
    original = scaffold.runtime_files()
    monkeypatch.setattr(scaffold, 'runtime_files', lambda: original + [('test/helper', b'changed', 0o755)])
    assert helper.task_set_digest({}) != before

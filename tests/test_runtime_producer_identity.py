"""Producer argv replay is explicit, closed and independent of verifier tools."""
import dataclasses
import json
from pathlib import Path

import pytest

from scripts.batch import harbor_runtime_readiness as r
from test_harbor_runtime_readiness import capture, reseal


def forbidden(*args, **kwargs):
    raise AssertionError('observation replay must not probe or execute producer tools')


def test_explicit_identity_replays_observation_without_host_tools(tmp_path, monkeypatch):
    s, root, run, obs, calls, _ = capture(tmp_path, monkeypatch)
    identity = r.capture_runtime_producer_identity()
    assert r.validate_runtime(s, obs, root=root)['valid']
    before = r.tree(run)
    count = len(calls)
    monkeypatch.setattr(r.sys, 'executable', '/different-host/python')
    monkeypatch.setattr(r.shutil, 'which', forbidden)
    monkeypatch.setattr(r, '_command', forbidden)
    result = r.validate_runtime(s, obs, root=root, producer_identity=identity)
    assert result['valid'] and result['exact_grader_targets'] == 1
    assert len(calls) == count and r.tree(run) == before


@pytest.mark.parametrize('changed', ['python', 'docker', 'missing_docker'])
def test_default_recheck_remains_bound_to_native_tools(tmp_path, monkeypatch, changed):
    s, root, run, obs, _, _ = capture(tmp_path, monkeypatch)
    if changed == 'python':
        monkeypatch.setattr(r.sys, 'executable', '/different-host/python')
    else:
        monkeypatch.setattr(r.shutil, 'which', lambda _: None if changed == 'missing_docker' else '/different-host/docker')
    with pytest.raises(ValueError):
        r.validate_runtime(s, obs, root=root)


@pytest.mark.parametrize('field', ['python_executable', 'docker_executable'])
def test_explicit_wrong_tool_identity_rejects(tmp_path, monkeypatch, field):
    s, root, run, obs, _, _ = capture(tmp_path, monkeypatch)
    identity = dataclasses.replace(r.capture_runtime_producer_identity(), **{field: '/wrong/tool'})
    with pytest.raises(ValueError):
        r.validate_runtime(s, obs, root=root, producer_identity=identity)


@pytest.mark.parametrize('artifact', [
    'mcp-build.command.json', 'mcp-build-inspect.command.json',
    'mcp-start.command.json', 'mcp-inspect.command.json',
    'main-build.command.json', 'main-build-inspect.command.json',
    'main-startup.command.json', 'oracle.command.json', 'grader.command.json',
    'mcp-inspect-after.command.json', 'mcp-cleanup.command.json', 'launch-intent.json',
])
def test_resealed_changed_command_or_launch_identity_rejects(tmp_path, monkeypatch, artifact):
    s, root, run, obs, _, _ = capture(tmp_path, monkeypatch)
    identity = r.capture_runtime_producer_identity()
    path = run / artifact
    document = json.loads(path.read_text())
    if artifact == 'launch-intent.json':
        document['docker'] = '/wrong/docker'
    else:
        document['argv'][0] = '/wrong/tool'
    path.write_text(json.dumps(document))
    obs = reseal(run, obs)
    with pytest.raises(ValueError):
        r.validate_runtime(s, obs, root=root, producer_identity=identity)


@pytest.mark.parametrize('artifact', ['oracle.json', 'reward/reward-details.json', 'inputs/0001-outputs/environment/0001-inputs.xlsx'])
def test_explicit_identity_does_not_relax_observation_checks(tmp_path, monkeypatch, artifact):
    s, root, run, obs, _, _ = capture(tmp_path, monkeypatch)
    identity = r.capture_runtime_producer_identity()
    path = run / artifact
    if artifact.endswith('.xlsx'):
        path.write_bytes(b'changed workbook')
    else:
        document = json.loads(path.read_text())
        if artifact == 'oracle.json':
            document['mcp_checks']['exact_resolutions'] = 0
        else:
            document['score'] = 0.5
        path.write_text(json.dumps(document))
    obs = reseal(run, obs)
    with pytest.raises(ValueError):
        r.validate_runtime(s, obs, root=root, producer_identity=identity)


@pytest.mark.parametrize('value', [None, 1, True, Path('/usr/bin/python'), '', 'python', '/',
                                  '//usr/bin/python', '/usr//bin/python', '/usr/./bin/python',
                                  '/usr/../bin/python', '/usr/bin/python/', '/usr/bin/python\n',
                                  '/usr/bin/py\x00thon', '/usr/bin/py\tthon', '/usr/bin/py\\thon',
                                  '/usr/bin/python\x85', '/usr/bin/python\u2028', '/usr/bin/python\u2029'])
@pytest.mark.parametrize('field', ['python_executable', 'docker_executable'])
def test_tool_identity_is_strict_canonical_absolute_lexical_path(value, field):
    fields = {'python_executable': '/usr/bin/python', 'docker_executable': '/usr/bin/docker'}
    fields[field] = value
    with pytest.raises(ValueError):
        r.RuntimeProducerIdentity(**fields)


def test_identity_allows_spaces_without_shell_interpretation_and_is_frozen():
    identity = r.RuntimeProducerIdentity('/opt/Python Runtime/python', '/opt/Docker CLI/docker')
    with pytest.raises(dataclasses.FrozenInstanceError):
        identity.python_executable = '/other/python'


def test_capture_uses_host_locators_and_rejects_unavailable_docker(monkeypatch):
    monkeypatch.setattr(r.sys, 'executable', '/opt/native/python')
    probes = []
    monkeypatch.setattr(r.shutil, 'which', lambda tool: probes.append(tool) or '/opt/native/docker')
    assert r.capture_runtime_producer_identity() == r.RuntimeProducerIdentity('/opt/native/python', '/opt/native/docker')
    assert probes == ['docker']
    monkeypatch.setattr(r.shutil, 'which', lambda _: None)
    with pytest.raises(ValueError, match='Docker executable unavailable'):
        r.capture_runtime_producer_identity()


@pytest.mark.parametrize('kind', ['dict', 'subclass', 'mutated'])
def test_replay_requires_exact_validated_dataclass(tmp_path, monkeypatch, kind):
    s, root, run, obs, _, _ = capture(tmp_path, monkeypatch)
    identity = r.capture_runtime_producer_identity()
    if kind == 'dict':
        identity = dataclasses.asdict(identity)
    elif kind == 'subclass':
        class OtherIdentity(r.RuntimeProducerIdentity):
            pass
        identity = OtherIdentity(identity.python_executable, identity.docker_executable)
    else:
        object.__setattr__(identity, 'docker_executable', '/unsafe/../docker')
    with pytest.raises(ValueError):
        r.validate_runtime(s, obs, root=root, producer_identity=identity)

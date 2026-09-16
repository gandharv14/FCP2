import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import recursion_content_successor_verify as verify


def fixture(model='opus', stage='research'):
    config = {'id': 'new-config', 'config': {'harnessImageUrl': 'pinned-image'}}
    request = {'config': config['config'], 'probe': {'prompt': verify.STAGES[stage] + '-inputs.xlsx'}}
    checks = dict.fromkeys(verify.REQUIRED, True)
    checks.update({'plain_task_no_mcp_required': True} if stage == 'plain' else
                  {'mcp_sources_callable': True, 'mcp_tools_available': True})
    identity = {'harness': 'claude', 'model': 'claude-opus-5', 'effort': 'max'} if model == 'opus' else {
        'harness': 'opencode', 'model': 'google-vertex/gemini-3.8-flash', 'effort': None}
    value = {'isolation_passed': True, 'checks': checks, 'identity': {'uid': 60100, 'gid': 60100},
             'model_identity': identity, 'python_install': {'passed': True, 'offline': True, 'cleanupComplete': True}}
    if model == 'opus':
        events = [{'data': {'message': {'content': [{'type': 'tool_use', 'id': 'call1', 'name': 'Bash',
                  'input': {'command': '/usr/local/bin/fcp-isolation-probe'}}]}}},
                  {'data': {'message': {'content': [{'type': 'tool_result', 'tool_use_id': 'call1',
                  'content': json.dumps(value)}]}}}]
    else:
        events = [{'data': {'type': 'tool_use', 'part': {'tool': 'bash', 'state': {'status': 'completed',
                  'input': {'command': '/usr/local/bin/fcp-isolation-probe'}, 'metadata': {'exit': 0},
                  'output': json.dumps(value)}}}}]
    status = {'id': 'probe-id', 'runConfigVersionId': 'new-config', 'status': 'succeeded',
              'result': {'passed': True, 'exitCode': 0, 'transcript': json.dumps(events)}}
    return status, request, config


@pytest.mark.parametrize('model', ['opus', 'gemini'])
@pytest.mark.parametrize('stage', ['research', 'plain', 'named'])
def test_actual_tool_output_required_for_each_model_stage(model, stage):
    args = fixture(model, stage)
    result = verify.verify_probe(*args, model, stage)
    assert result['allChecksPassed'] and result['runConfigVersionId'] == 'new-config'


@pytest.mark.parametrize('mutation', ['wrong-config', 'wrong-task', 'failed-result', 'forged-command', 'missing-check', 'wrong-model'])
def test_failure_and_provenance_do_not_become_success(mutation):
    status, request, config = fixture()
    if mutation == 'wrong-config':
        status['runConfigVersionId'] = 'old-config'
    elif mutation == 'wrong-task':
        request['probe']['prompt'] = 'other-inputs.xlsx'
    elif mutation == 'failed-result':
        status['result']['passed'] = False
    else:
        events = json.loads(status['result']['transcript'])
        if mutation == 'forged-command':
            events[0]['data']['message']['content'][0]['input']['command'] = 'echo fabricated-result'
        else:
            item = events[1]['data']['message']['content'][0]
            output = json.loads(item['content'])
            if mutation == 'missing-check':
                del output['checks']['no_platform_credentials']
            else:
                output['model_identity']['model'] = 'wrong-model'
            item['content'] = json.dumps(output)
        status['result']['transcript'] = json.dumps(events)
    with pytest.raises(AssertionError):
        verify.verify_probe(status, request, config, 'opus', 'research')


def test_opencode_completed_timeout_is_rejected():
    status, request, config = fixture('gemini')
    events = json.loads(status['result']['transcript'])
    events[0]['data']['part']['state']['metadata']['exit'] = None
    status['result']['transcript'] = json.dumps(events)
    with pytest.raises(AssertionError):
        verify.verify_probe(status, request, config, 'gemini', 'research')


def test_shell_wrapper_requires_independent_exact_status_and_request_review():
    status, request, config = fixture()
    command = '/usr/local/bin/fcp-isolation-probe; echo "EXIT=$?"'
    events = json.loads(status['result']['transcript'])
    events[0]['data']['message']['content'][0]['input']['command'] = command
    status['result']['transcript'] = json.dumps(events)
    with pytest.raises(AssertionError):
        verify.verify_probe(status, request, config, 'opus', 'research')
    review = {'status': 'approved', 'scope': 'exact-probe-shell-command', 'reviewer': 'independent agent',
              'probeRunId': status['id'], 'runConfigVersionId': status['runConfigVersionId'],
              'statusContentSha256': verify.content_digest(status),
              'requestContentSha256': verify.content_digest(request), 'commands': [command]}
    commands = verify.reviewed_probe_commands(review, status, request)
    assert verify.verify_probe(status, request, config, 'opus', 'research', commands)['allChecksPassed']
    altered = copy.deepcopy(status)
    altered['result']['transcript'] += ' '
    with pytest.raises(AssertionError):
        verify.reviewed_probe_commands(review, altered, request)
    with pytest.raises(AssertionError):
        verify.reviewed_probe_commands(review, status, {**request, 'new': 'changed request'})


def test_registry_verification_preserves_every_base_layer_and_config(monkeypatch):
    base = {'mediaType': 'application/vnd.oci.image.manifest.v1+json', 'config': {'digest': 'baseconfig'},
            'layers': [{'digest': 'base1', 'size': 10}, {'digest': 'base2', 'size': 20}]}
    config = {'architecture': 'amd64', 'os': 'linux', 'config': {'Env': ['MODEL=locked']},
              'rootfs': {'diff_ids': ['diff1', 'diff2']}}
    layer = {'diffId': 'diff3', 'digest': 'layer3', 'size': 3}
    successor, config_bytes = verify.image.append_layer(base, config, layer)
    digest = verify.registry.digest(verify.registry.canonical(successor))
    class Client:
        def __init__(self, repo):
            self.repo = repo
        def manifest(self, ref):
            return copy.deepcopy(base if ref == 'sha256:base' else successor)
        def blob(self, ref):
            return json.dumps(config).encode() if ref == 'baseconfig' else config_bytes
    monkeypatch.setattr(verify.registry, 'Registry', Client)
    prefix = verify.registry.HOST + '/lb-ml-dev/fde-runners/runner-fcp-test@'
    assert verify.verify_registry_pair(prefix + 'sha256:base', prefix + digest, layer)['allBaseLayersPreserved']
    successor['layers'][0]['digest'] = 'modified-base'
    with pytest.raises(AssertionError):
        verify.verify_registry_pair(prefix + 'sha256:base', prefix + digest, layer)

"""Finalize successor proofs from six real probe results and registry GETs.

No remote mutations. Missing/failed probes or changed configuration fail closed.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path

import recursion_content_successor_image as image
import recursion_registry_image as registry

STAGES = {'research': '0006', 'plain': '0004', 'named': 'SampleACLtd_model'}
REQUIRED = {'uid_unprivileged', 'gid_unprivileged', 'saved_uids_unprivileged',
            'saved_gids_unprivileged', 'no_capabilities', 'no_new_privileges',
            'no_platform_credentials', 'restrictions_survive_exec', 'workbook_readable',
            'output_writable', 'model_cli_identified', 'model_matches_requested',
            'model_effort_matches_requested', 'offline_pip_install_supported'}


def decoded_probe_output(output):
    """Read either exact JSON or the trusted probe's previously observed display."""
    for pos, char in enumerate(output):
        if char != '{':
            continue
        try:
            value, _ = json.JSONDecoder().raw_decode(output[pos:])
        except ValueError:
            continue
        if isinstance(value, dict) and isinstance(value.get('checks'), dict) and 'isolation_passed' in value:
            return value
    if 'isolation_passed: True' in output:
        result = {'isolation_passed': True}
        for key in ('identity', 'model_identity', 'python_install'):
            match = re.search(r'^' + key + r': (.*)$', output, re.M)
            if match:
                result[key] = json.loads(match[1])
        result['checks'] = {key: value == 'True' for key, value in
                            re.findall(r'^\s+(?:OK|FAIL)\s+(.*?) = (True|False)$', output, re.M)}
        return result
    raise AssertionError('No actual trusted probe output found')


def content_digest(value):
    return registry.digest(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).split(':')[1]


def reviewed_probe_commands(review, status, request):
    """Only a separate, exact-transcript-bound review can allow a shell wrapper."""
    assert review['status'] == 'approved' and review.get('reviewer')
    assert review['scope'] == 'exact-probe-shell-command'
    assert review['probeRunId'] == status['id']
    assert review['runConfigVersionId'] == status['runConfigVersionId']
    assert review['statusContentSha256'] == content_digest(status)
    assert review['requestContentSha256'] == content_digest(request)
    assert review['commands'] and all(isinstance(command, str) for command in review['commands'])
    return frozenset(review['commands'])


def verify_probe(status, request, config, model, stage, reviewed_commands=frozenset()):
    assert status['status'] == 'succeeded' and status['runConfigVersionId'] == config['id']
    result = status['result']
    assert result['passed'] is True and result['exitCode'] == 0
    assert request['config'] == config['config']
    assert STAGES[stage] + '-inputs.xlsx' in request['probe']['prompt']
    events = result['transcript']
    if isinstance(events, str):
        events = json.loads(events)
    commands, outputs = {}, []
    for event in events:
        data = event.get('data', {})
        message = data.get('message', {})
        content = message.get('content', []) if isinstance(message, dict) else []
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get('type') == 'tool_use' and item.get('name', '').lower() == 'bash':
                    commands[item['id']] = item.get('input', {}).get('command', '').strip()
                if item.get('type') == 'tool_result' and commands.get(item.get('tool_use_id')) in ({'/usr/local/bin/fcp-isolation-probe'} | reviewed_commands):
                    assert not item.get('is_error')
                    value = item.get('content', '')
                    outputs.append(value if isinstance(value, str) else '\n'.join(x.get('text', '') for x in value))
        if data.get('type') == 'tool_use':
            part = data.get('part', {})
            state = part.get('state', {})
            if part.get('tool', '').lower() == 'bash' and state.get('input', {}).get('command', '').strip() in ({'/usr/local/bin/fcp-isolation-probe'} | reviewed_commands):
                assert state['status'] == 'completed'
                assert state.get('metadata', {}).get('exit', 0) == 0
                outputs.append(state.get('output', ''))
    assert outputs, 'Probe must be executed directly; shell wrappers need separate review'
    value = decoded_probe_output(outputs[-1])
    assert value['isolation_passed'] is True
    checks = value['checks']
    assert REQUIRED <= checks.keys() and all(x is True for x in checks.values())
    if stage == 'plain':
        assert checks.get('plain_task_no_mcp_required') is True
    else:
        assert checks.get('mcp_sources_callable') is True and checks.get('mcp_tools_available') is True
    assert value['identity']['uid'] == value['identity']['gid'] == 60100
    expected = ('claude', 'claude-opus-5', 'max') if model == 'opus' else ('opencode', 'google-vertex/gemini-3.8-flash', None)
    actual = value['model_identity']
    assert (actual['harness'], actual['model'], actual.get('effort')) == expected
    assert value['python_install']['passed'] is True and value['python_install']['offline'] is True
    assert value['python_install']['cleanupComplete'] is True
    return {'probeRunId': status['id'], 'runConfigVersionId': config['id'], 'status': 'succeeded',
            'stage': stage, 'taskId': STAGES[stage], 'actualToolCheckCount': len(checks),
            'modelIdentity': actual, 'offlinePipPassed': True, 'allChecksPassed': True}


def verify_registry_pair(base_image, successor_image, layer):
    prefix = registry.HOST + '/'
    assert base_image.startswith(prefix) and successor_image.startswith(prefix)
    base_repo, base_digest = base_image.removeprefix(prefix).split('@')
    new_repo, new_digest = successor_image.removeprefix(prefix).split('@')
    assert base_repo == new_repo
    client = registry.Registry(base_repo)
    base = client.manifest(base_digest)
    successor = client.manifest(new_digest)
    base_config = json.loads(client.blob(base['config']['digest']))
    successor_config = client.blob(successor['config']['digest'])
    expected, expected_config = image.append_layer(base, base_config, layer)
    assert successor == expected and successor_config == expected_config
    assert successor['layers'][:-1] == base['layers']
    assert registry.digest(registry.canonical(expected)) == new_digest
    return {'baseImage': base_image, 'successorImage': successor_image,
            'baseLayerCount': len(base['layers']), 'successorLayerCount': len(successor['layers']),
            'allBaseLayersPreserved': True, 'onlyWorkbookLayerAppended': True,
            'allRuntimeImageConfigPreserved': True, 'registryDigestVerified': True}


def finalize(work):
    work = Path(work)
    folder = work / 'content-successor/images'
    for name in ('payload-proof.json', 'runtime-manifest.json'):
        assert not (folder / name).exists(), 'Preserve finalized evidence'
    layer, _ = image.verify_payload(folder)
    prepared = image.read(folder / 'payload-proof.prepared.json')
    assert prepared['baseAssetsSha256'] == layer['baseAssets']['sha256']
    assert prepared['baseRuntimeManifestSha256'] == layer['runtimeManifest']['sha256']
    assert not prepared['probeResults']
    for path, digest in prepared['evidenceHashes'].items():
        assert image.sha(path) == digest
    descriptor = image.read(prepared['nonWorkbookDescriptorPath'])
    descriptor_digest = registry.digest(json.dumps(descriptor, sort_keys=True, separators=(',', ':')).encode()).split(':')[1]
    assert image.sha(prepared['nonWorkbookDescriptorPath']) == prepared['nonWorkbookDescriptorFileSha256']
    assert descriptor_digest == prepared['nonWorkbookPayloadBaseSha256'] == prepared['nonWorkbookPayloadSuccessorSha256']
    assets = image.read(work / 'final-assets.json')
    patches = {row['taskId']: row for row in image.read(layer['patchManifest']['path'])}
    expected_books = {row['taskId']: {'baseSha256': row['workbookSha256'],
                      'successorSha256': patches.get(row['taskId'], row)['workbookSha256']} for row in assets}
    assert prepared['workbooks'] == expected_books
    proof = copy.deepcopy(prepared)
    proof.update(status='verified', probeResults={}, registryResults={})
    normalized = {'baseAssetsSha256': prepared['baseAssetsSha256'],
                  'baseRuntimeManifestSha256': prepared['baseRuntimeManifestSha256'], 'models': {}}
    for model in ('opus', 'gemini'):
        old_path = work / 'final-images/configs' / model / 'verified.json'
        config_path = folder / 'configs' / model / 'verified.json'
        old, config = image.read(old_path), image.read(config_path)
        published = image.read(folder / (model + '-image.json'))
        assert old['id'] != config['id'] and old['runConfigId'] != config['runConfigId']
        assert config['lockedAt'] and config['verifiedAt']
        assert config['config']['harnessImageUrl'] == published['image']
        assert published['parent'] == old['config']['harnessImageUrl'] == layer['baseImages'][model]['image']
        assert {k: v for k, v in config['config'].items() if k != 'harnessImageUrl'} == {k: v for k, v in old['config'].items() if k != 'harnessImageUrl'}
        stages, hashes = [], {}
        for stage in STAGES:
            status_path = config_path.parent / (stage + '-status.json')
            request_path = config_path.parent / (stage + '-update.request.json')
            status = image.read(status_path)
            response = image.read(config_path.parent / (stage + '-probe.response.json'))
            assert status['id'] == response['probeRunId']
            request = image.read(request_path)
            commands = frozenset()
            review_path = config_path.parent / (stage + '-shell-review.json')
            if review_path.exists():
                commands = reviewed_probe_commands(image.read(review_path), status, request)
                hashes[str(review_path.resolve())] = image.sha(review_path)
            stages.append(verify_probe(status, request, config, model, stage, commands))
            for path in (status_path, request_path):
                hashes[str(path.resolve())] = image.sha(path)
        proof['probeResults'][model] = {'status': 'succeeded', 'runConfigVersionId': config['id'], 'stages': stages}
        proof['registryResults'][model] = verify_registry_pair(published['parent'], published['image'], layer)
        for path in (old_path, config_path, folder / (model + '-image.json')):
            hashes[str(path.resolve())] = image.sha(path)
        proof['evidenceHashes'].update(hashes)
        normalized['models'][model] = {'configPath': str(config_path.resolve()), 'configSha256': image.sha(config_path),
                                       'image': published['image'], 'evidenceHashes': hashes}
    proof_path = folder / 'payload-proof.json'
    image.save_new(proof_path, proof)
    normalized.update(payloadProofPath=str(proof_path.resolve()), payloadProofSha256=image.sha(proof_path))
    image.save_new(folder / 'runtime-manifest.json', normalized)
    return normalized


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True)
    args = parser.parse_args()
    finalize(args.work)

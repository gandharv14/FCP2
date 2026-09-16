"""Publication must validate the Docker path recorded by its isolated runtime."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest
from scripts.fleet import publish as p


@pytest.mark.parametrize('action', ['remote', 'collect'])
def test_ssh_path_cannot_replace_recorded_runtime_wrapper(tmp_path, monkeypatch, action):
    runtime = tmp_path / 'release' / 'venv' / 'bin'
    system = tmp_path / 'system' / 'bin'
    for directory, output in ((runtime, 'isolated-runtime'), (system, 'system-runtime')):
        directory.mkdir(parents=True)
        wrapper = directory / 'docker'
        wrapper.write_text('#!/bin/sh\nprintf "%s\\n" ' + output + '\n')
        wrapper.chmod(0o700)
    # A normal venv Python is a symlink; its lexical bin path must be preserved.
    python = runtime / 'python'
    python.symlink_to(Path(p.sys.executable).resolve())
    monkeypatch.setattr(p.sys, 'executable', str(python))
    monkeypatch.setenv('PATH', str(system))
    monkeypatch.setenv('HARBOR_FLEET_MODEL_JOURNAL', '/obsolete/journal')
    monkeypatch.setenv('LITELLM_API_KEY', 'synthetic-unused-test-value')
    history = p.retain(tmp_path / 'history.json', dict(
        schema_version='fleet-central-history/v1', roots=[], scan_roots=[],
        known_ids=['0029'], fresh_ids=['0029'], excluded={}, histories={}))
    plan = dict(workbook='0029', code_root=str(tmp_path), history=history)
    auth = dict(workbook='0029', owner='publication-test', token='synthetic', fence=1)
    env = p.publication_environment(plan, auth, action=action,
                                    url='http://127.0.0.1:1', token_file=tmp_path/'unused')
    assert shutil.which('docker', path=env['PATH']) == str(runtime/'docker')
    result = subprocess.run(['docker'], env=env, text=True, capture_output=True, check=True)
    assert result.stdout.strip() == 'isolated-runtime'
    assert os.environ['PATH'] == str(system)
    assert 'HARBOR_FLEET_MODEL_JOURNAL' not in env and 'LITELLM_API_KEY' not in env

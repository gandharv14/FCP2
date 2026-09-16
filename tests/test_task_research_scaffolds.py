import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import harden_task_research_scaffolds as harden


def task(root, research=True):
    task = root / '0006-outputs'
    env = task / 'environment'
    env.mkdir(parents=True)
    (task / 'task.toml').write_text('schema_version = "1.4"\n')
    (env / '0006-inputs.xlsm').write_bytes(b'workbook bytes')
    (env / 'Dockerfile').write_text('FROM python:3.12-slim\nCOPY 0006-inputs.xlsm /app/0006-inputs.xlsm\n')
    (task / 'tests').mkdir()
    (task / 'tests/answer_key.json').write_text('{"expected": 42}')
    if research:
        mcp = env / 'mcp-server'
        (mcp / 'runtime').mkdir(parents=True)
        (mcp / 'runtime/records.jsonl').write_text('{"research": 42}\n')
        (mcp / 'server.py').write_text('print("research service")\n')
        (mcp / 'Dockerfile').write_text('FROM python:3.12-slim\nCOPY server.py /srv/server.py\nCOPY runtime /srv/runtime\n')
        (env / 'docker-compose.yaml').write_text('services:\n  mcp-server:\n    build:\n      context: ./mcp-server\n')
    return task


@pytest.mark.parametrize('research', [False, True])
def test_all_assets_preserved_and_private_runner_packages_only_inputs(tmp_path, research):
    source = task(tmp_path / 'tasks', research)
    before = harden.snapshot(source)
    report = tmp_path / 'report'
    plan = harden.prepare([tmp_path / 'tasks'], report)
    harden.apply(plan)
    harden.apply(plan)  # resumable without new writes or changed assets
    result = harden.verify(plan, report)
    assert result['tasks'] == 1 and result['researchTasks'] == int(research)
    after = harden.snapshot(source)
    assert all(after[k] == v for k, v in before.items())
    assert set(after) - set(before) == set(plan['tasks'][0]['updates'])
    assert (source / 'environment/.dockerignore').read_text().splitlines()[1:] == ['**', '!Dockerfile', '!.dockerignore', '!0006-inputs.xlsm']
    # Replanning already-hardened tasks must also work.
    harden.prepare([tmp_path / 'tasks'], tmp_path / 'second-report')


def test_concurrent_source_change_prevents_all_writes(tmp_path):
    source = task(tmp_path / 'tasks')
    plan = harden.prepare([tmp_path / 'tasks'], tmp_path / 'report')
    (source / 'environment/0006-inputs.xlsm').write_bytes(b'changed by other work')
    with pytest.raises(ValueError, match='changed since planning'):
        harden.apply(plan)
    assert not (source / 'environment/.dockerignore').exists()


@pytest.mark.parametrize('defect', ['broad_copy', 'custom_ignore', 'override_ignore', 'shared_mount'])
def test_unsafe_or_custom_context_requires_review(tmp_path, defect):
    source = task(tmp_path / 'tasks')
    env = source / 'environment'
    if defect == 'broad_copy':
        (env / 'Dockerfile').write_text('FROM python:3.12-slim\nCOPY . /app/\n')
    elif defect == 'custom_ignore':
        (env / '.dockerignore').write_text('custom policy\n')
    elif defect == 'override_ignore':
        (env / 'Dockerfile.dockerignore').write_text('override policy\n')
    else:
        with (env / 'docker-compose.yaml').open('a') as stream:
            stream.write('    volumes:\n      - ./mcp-server:/research\n')
    with pytest.raises(ValueError):
        harden.prepare([tmp_path / 'tasks'], tmp_path / 'report')
    assert not (env / 'mcp-server/.dockerignore').exists()

import dataclasses
import io
import json
import time
from pathlib import Path
import pytest

from scripts.batch import harbor_role_producer as roles
from scripts.batch import harbor_instruction_producer as instruction
from scripts.batch import harbor_model_executor as executor
from scripts.batch.harbor_admission import record
from test_harbor_instruction_producer import setup as instruction_setup


@pytest.fixture
def case(instruction_setup, monkeypatch):
    task, campaign, _, calls = instruction_setup
    tracker = Path(task.tracker['path']); value = json.loads(tracker.read_text())
    value['lane_state'][instruction.DISCLOSURE]['state'] = 'pending'
    tracker.write_text(json.dumps(value)); task = dataclasses.replace(task, tracker=record(tracker))
    renderer = instruction.skill_module(task.root, 'task-disclosure', 'disclose')
    directory = task.run / 'disclosure-build' / task.stage.name
    directory.mkdir(parents=True)
    value = dict(schema_version='1.0', task=task.stage.name, case_count=2, band_count=2,
                 cases=[dict(case_id=str(i), label=label, roles=['revenue', 'expense'],
                             band_count=1, role_context=[], triggered_by={}, questions={}, sample_bands=[])
                        for i, label in enumerate(['Sales expenses', 'Other'])])
    instruction.put(directory / 'bands.json', {'synthetic_fixture': True})
    binding = instruction.put(directory / 'ambiguous_roles.json', value)
    monkeypatch.setattr(roles, 'cases', lambda _: (renderer, directory, binding, value))
    def respond(request, text):
        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *args): self.close()
        body = dict(model=task.policy.model, id='role-'+str(len(calls)), object='chat.completion',
                    created=time.time_ns()//10**9, choices=[dict(finish_reason='stop', message=dict(role='assistant', content=text))])
        def opened(req, timeout): calls.append(req); return Response(json.dumps(body).encode())
        monkeypatch.setattr(executor, '_open', opened)
        return instruction.dispatch(task, request, campaign, credential='synthetic-token')
    valid = dict(agent_model='gpt-5.6-sol-high', resolutions=[
        dict(case_id='0', label='Sales expenses', chosen='expense', reason='Own label names an expense.'),
        dict(case_id='1', label='Other', chosen=None, reason='The generic label does not establish a role.')])
    return task, campaign, respond, calls, valid, directory


def test_complete_arbitration_and_abstention_use_owning_loader(case):
    task, campaign, respond, calls, valid, directory = case
    request = roles.prepare(task, campaign)
    body = json.loads(instruction.read(request.prepared.request)['messages'][1]['content'])
    assert set(body) == {'cases'} and request.prepared.ordinal == 1
    respond(request, json.dumps(valid))
    result = roles.consume(task, request, campaign, apply=True)
    assert result['passed'] and result['applied'] and len(calls) == 1
    normalized = json.loads((directory/'role_resolutions.json').read_text())
    assert normalized['validated'] and normalized['resolutions'][1]['chosen'] is None
    with pytest.raises(ValueError, match='successful arbitration'):
        roles.prepare(task, campaign, previous=request)


@pytest.mark.parametrize('defect', ['omitted', 'duplicate', 'unknown', 'label', 'reason', 'model', 'malformed'])
def test_invalid_arbitration_is_not_applied_and_has_only_one_bound_repair(case, defect):
    task, campaign, respond, calls, valid, directory = case
    bad = json.loads(json.dumps(valid))
    if defect == 'omitted': bad['resolutions'].pop()
    elif defect == 'duplicate': bad['resolutions'].append(bad['resolutions'][0])
    elif defect == 'unknown': bad['resolutions'][0]['chosen'] = 'invented'
    elif defect == 'label': bad['resolutions'][0]['label'] = 'Sales'
    elif defect == 'reason': bad['resolutions'][0]['reason'] = ''
    elif defect == 'model': bad['agent_model'] = 'wrong'
    first = roles.prepare(task, campaign)
    respond(first, 'not JSON' if defect == 'malformed' else json.dumps(bad))
    result = roles.consume(task, first, campaign)
    assert not result['passed'] and result['errors']
    assert not (directory/'role_resolutions.json').exists()
    second = roles.prepare(task, campaign, previous=first)
    body = json.loads(instruction.read(second.prepared.request)['messages'][1]['content'])
    assert body['validation_errors'] == result['errors']
    assert body['previous_response'] == instruction.text(result['raw'])
    respond(second, json.dumps(valid))
    assert roles.consume(task, second, campaign, apply=True)['passed']
    with pytest.raises(ValueError, match='only one role-format repair'):
        roles.prepare(task, campaign, previous=second)
    assert len(calls) == 2


def test_changed_prior_raw_response_cannot_authorize_repair(case):
    task, campaign, respond, calls, valid, directory = case
    first = roles.prepare(task, campaign); respond(first, 'invalid')
    result = roles.consume(task, first, campaign)
    second = roles.prepare(task, campaign, previous=first)
    respond(second, json.dumps(valid))
    Path(result['raw']['path']).write_text('changed')
    with pytest.raises(ValueError): roles.consume(task, second, campaign, apply=True)
    assert not (directory/'role_resolutions.json').exists()

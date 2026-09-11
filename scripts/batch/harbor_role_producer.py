"""Bounded role arbitration using only independently reconstructed case metadata."""
from dataclasses import asdict
from pathlib import Path
import json

from scripts.batch import harbor_instruction_producer as instruction
from scripts.batch.harbor_admission import read, record, require
from scripts.batch.harbor_model_review_provenance import Call, validate_call


def cases(task):
    before, _ = instruction.lane_context(task, instruction.DISCLOSURE)
    renderer = instruction.skill_module(task.root, 'task-disclosure', 'disclose')
    directory = task.run / 'disclosure-build' / task.stage.name
    selected = read(record(directory / 'bands.json'))
    golden = record(Path(selected['golden']))
    require(golden == {k: before['bindings']['source_file'][k] for k in ('path', 'sha256')},
            'role cases must derive from the admitted source')
    book = renderer.Book(Path(golden['path']))
    derived = renderer.collect_ambiguous_role_cases(renderer.prepare_method_items(book, selected.get('bands', [])))
    binding = record(directory / 'ambiguous_roles.json')
    value = read(binding)
    expected = dict(schema_version='1.0', task=task.stage.name, cases=derived,
                    case_count=len(derived), band_count=sum(c['band_count'] for c in derived))
    require(value == expected, 'role case metadata differs from current workbook evidence')
    require(derived, 'zero collisions require no arbitration call')
    return renderer, directory, binding, value


def _validation(renderer, path, value):
    normalized, errors, warnings = renderer.validate_role_resolutions(path, value['cases'])
    if normalized is not None:
        expected = {c['case_id']: c for c in value['cases']}
        actual = {r['case_id']: r for r in normalized['resolutions']}
        if set(actual) != set(expected):
            errors.append('Every case_id must be covered exactly once.')
        for key, row in actual.items():
            if key in expected and row.get('label') != expected[key]['label']:
                errors.append('Role label differs from its case: ' + key)
            if not isinstance(row.get('reason'), str) or not row['reason'].strip():
                errors.append('A nonempty reason is required: ' + key)
        if normalized.get('agent_model') != 'gpt-5.6-sol-high':
            errors.append('The required arbitration model stamp is missing.')
    return normalized, errors, warnings


def prepare(task, campaign, *, previous=None):
    renderer, directory, binding, value = cases(task)
    prompt = instruction.skill_prompt(task.root, 'task-disclosure',
                                     'You are arbitrating finance-row roles')
    prompt += '\nReturn the requested JSON as text in your response; do not access files or tools.'
    body = {'cases': value}
    evidence = [binding, record(directory / 'bands.json'), record(Path(__file__))]
    ordinal = 1
    if previous is not None:
        instruction.validate_request(task, previous, 'disclosure-role-arbitration')
        require(previous.prepared.ordinal == 1, 'only one role-format repair is permitted')
        first = consume(task, previous, campaign, apply=False)
        require(not first['passed'] and first['errors'], 'a successful arbitration cannot be regenerated')
        body.update(previous_response=instruction.text(first['raw']), validation_errors=first['errors'])
        evidence.extend([first['receipt'], first['raw']])
        ordinal = 2
    return instruction._request(task, 'disclosure-role-arbitration', instruction.DISCLOSURE,
                                ordinal, 2, body, prompt, evidence, mode='text')


def consume(task, request, campaign, *, apply=False):
    instruction.validate_request(task, request, 'disclosure-role-arbitration')
    renderer, directory, binding, value = cases(task)
    call = instruction.observed(request, campaign)
    checked = validate_call(call, read(request.prepared.request), policy=task.policy, json_response=False)
    payload = json.loads(read(call.request)['messages'][1]['content'])
    require(payload.get('cases') == value, 'arbitrator saw different case metadata')
    expected_keys = {'cases'} if request.prepared.ordinal == 1 else {'cases', 'previous_response', 'validation_errors'}
    require(set(payload) == expected_keys, 'arbitrator received unexpected context')
    if request.prepared.ordinal == 2:
        first_path = task.run / 'role-arbitration' / 'attempt-01'
        prior = read(record(first_path / 'result.json'))
        prior_call = Call(**prior['call'])
        prior_checked = validate_call(prior_call, read(prior_call.request), policy=task.policy, json_response=False)
        require(json.loads(read(prior_call.request)['messages'][1]['content']) == {'cases': value}, 'prior arbitration context changed')
        require(prior_checked['review'] == instruction.text(prior['raw']) == payload['previous_response'], 'repair source changed')
        _, prior_errors, _ = _validation(renderer, Path(prior['raw']['path']), value)
        require(prior_errors and prior_errors == prior['errors'] == payload['validation_errors'], 'repair errors differ from owning validation')
    output = task.run / 'role-arbitration' / ('attempt-%02d' % request.prepared.ordinal)
    raw = instruction.put(output / 'response.txt', checked['review'].encode('utf-8'))
    normalized, errors, warnings = _validation(renderer, Path(raw['path']), value)
    result = dict(passed=normalized is not None and not errors, errors=errors, warnings=warnings,
                  raw=raw, call=asdict(call), cases=binding, producer=record(Path(__file__)),
                  ordinal=request.prepared.ordinal, publication_ready=False)
    receipt = instruction.put(output / 'result.json', result)
    if apply:
        require(result['passed'], 'arbitration failed; only its bounded format repair may continue')
        instruction.put(directory / 'role_resolutions.json', normalized)
        require(renderer.load_role_resolutions(directory / 'role_resolutions.json') is not None, 'owning role loader rejected arbitration')
    return {**result, 'receipt': receipt, 'applied': apply}

"""Fresh writer-body restoration; no semantic approval or historical adaptation."""
from __future__ import annotations

import json
import re
from pathlib import Path

import freeze_protected_spans as freezer
from instruction_spans import InstructionSpanError, assemble_instruction, scan_instruction, sha256_bytes

SCHEMA = 'naturalization-frozen-submission/v1'
INPUTS = ('writer_source.md', 'frozen_spans.json')


def json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode('utf-8')


def read_inputs(root):
    result = {}
    for name in INPUTS:
        path = root / name
        if path.is_symlink():
            raise ValueError('frozen submission input is a symlink: ' + name)
        result[name] = path.read_bytes() if path.exists() else None
    return result


def input_identity(inputs):
    return {name: sha256_bytes(raw) if raw is not None else None for name, raw in inputs.items()}


def derive(source, preamble, input_body, inputs):
    """Return deterministic artifacts, proof, and only a zero-drift candidate."""
    files = {name: raw for name, raw in inputs.items() if raw is not None}
    errors, drift = [], []
    candidate = b''
    spans = {}
    try:
        writer, spans = freezer.freeze(source.decode('utf-8'))
        expected_map = {'source_sha256': sha256_bytes(source), 'spans': spans}
        if inputs['writer_source.md'] != writer.encode('utf-8'):
            errors.append('writer source differs from regenerated immutable source freeze')
        try:
            supplied_map = json.loads(inputs['frozen_spans.json'])
        except (TypeError, UnicodeError, ValueError):
            supplied_map = None
        if supplied_map != expected_map:
            errors.append('span map differs from regenerated immutable source freeze')
        marked = assemble_instruction(source, scan_instruction(source), preamble, input_body)
        files['candidate.marked.md'] = marked
        restored, restore_errors, drift = freezer.restore(marked.decode('utf-8'), spans)
        files['candidate.restored.md'] = restored.encode('utf-8')
        errors.extend(restore_errors)
        if re.sub(r'\[\[/?F\d+\]\]', '', marked.decode('utf-8')) != restored:
            errors.append('restoration changes bytes beyond canonical marker wrappers')
        # A malformed shorter/spacing variant is still transport residue.
        if re.search(r'\[\[\s*/?F\d', restored):
            errors.append('frozen marker residue remains')
        if not errors and not drift:
            candidate = restored.encode('utf-8')
    except (InstructionSpanError, UnicodeError, freezer.SpanError) as exc:
        errors.append(str(exc))
    implementation = {
        name: sha256_bytes((Path(__file__).parent / name).read_bytes())
        for name in ('frozen_submission.py', 'freeze_protected_spans.py', 'instruction_spans.py')
    }
    report = {
        'schema_version': SCHEMA,
        'source_sha256': sha256_bytes(source),
        'preamble_submission_sha256': sha256_bytes(preamble),
        'input_submission_sha256': sha256_bytes(input_body),
        'freeze_input_sha256': input_identity(inputs),
        'files': {name: sha256_bytes(raw) for name, raw in sorted(files.items())},
        'implementation_sha256': implementation,
        'span_count': len(spans), 'errors': errors, 'drift': drift,
        'restored': not errors and not drift,
        'candidate_sha256': sha256_bytes(candidate) if candidate else None,
    }
    return files, report, candidate


def validate_attempt(api, root, state, item):
    attempt = root / ('attempt-%02d' % item['attempt'])
    mode = item.get('submission_mode', 'plain')
    if mode != 'frozen':
        if mode != 'plain' or item.get('frozen_submission') or (attempt / 'restore.json').exists():
            raise api.RecoveryError('frozen_submission_changed', 'frozen submission mode/evidence detached')
        return
    try:
        report_raw = (attempt / 'validation.json').read_bytes()
        report = json.loads(report_raw)
        binding = item['frozen_submission']
        if report.get('submission_mode') != 'frozen' or report.get('frozen_submission') != binding:
            raise ValueError('validation mode/restoration binding changed')
        if sha256_bytes(report_raw) != item['validation_sha256']:
            raise ValueError('validation bytes changed')
        source = (root / 'source.snapshot.md').read_bytes()
        if sha256_bytes(source) != state['source_sha256'] or (attempt / 'source.snapshot.md').read_bytes() != source:
            raise ValueError('source bytes changed')
        preamble = (attempt / 'preamble_body.md').read_bytes()
        body = (attempt / 'input_body.md').read_bytes()
        if sha256_bytes(preamble) != item['preamble_submission_sha256'] or sha256_bytes(body) != item['input_submission_sha256']:
            raise ValueError('writer bodies changed')
        files, restored, candidate = derive(source, preamble, body, read_inputs(attempt))
        raw = (attempt / 'restore.json').read_bytes()
        if binding != {'schema_version': SCHEMA, 'restore_sha256': sha256_bytes(raw)} or raw != json_bytes(restored):
            raise ValueError('restoration evidence changed')
        for name, expected in files.items():
            if (attempt / name).is_symlink() or (attempt / name).read_bytes() != expected:
                raise ValueError('restoration artifact changed: ' + name)
        if item.get('candidate_sha256') != (sha256_bytes(candidate) if candidate else None):
            raise ValueError('restored candidate binding changed')
        if candidate and (attempt / 'candidate.md').read_bytes() != candidate:
            raise ValueError('restored candidate changed')
        for field in ('attempt', 'valid', 'reason_codes', 'preamble_submission_sha256', 'input_submission_sha256'):
            if item.get(field) != report.get(field):
                raise ValueError('attempt record changed: ' + field)
        if item.get('valid'):
            if not restored['restored']:
                raise ValueError('invalid restoration selected')
            current = api._load_validator().validate(source, candidate, api._verified_answer_key(root, state))
            if any(report.get(k) != value for k, value in current.items()):
                raise ValueError('current full validation differs')
            if state.get('status') in {'validated', 'applied'} and state['attempts'][-1]['attempt'] == item['attempt']:
                if (state.get('final_candidate') != 'attempt-%02d/candidate.md' % item['attempt']
                        or state.get('final_instruction_sha256') != sha256_bytes(candidate)):
                    raise ValueError('selected restored candidate binding changed')
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise api.RecoveryError('frozen_submission_changed', str(exc)) from exc

"""Canonicalize only scanner-owned body boundaries; old frozen mode is unchanged."""
import json,re
from pathlib import Path
import frozen_submission as original
from instruction_spans import sha256_bytes

SCHEMA='naturalization-boundary-submission/v1'
MODE='frozen-boundary-v1'
read_inputs=original.read_inputs
input_identity=original.input_identity
json_bytes=original.json_bytes


def canonical_body(raw, *, leading):
    # The span scanner excludes the newline separators surrounding editable
    # bodies. Never strip the preamble's leading bytes or internal whitespace.
    begin=re.match(rb'(?:[ \t]*(?:\r\n|\n|\r))+',raw).end() if leading and re.match(rb'(?:[ \t]*(?:\r\n|\n|\r))+',raw) else 0
    match=re.search(rb'(?:[ \t]*(?:\r\n|\n|\r))+\Z',raw[begin:])
    end=begin+match.start() if match else len(raw)
    return raw[begin:end],dict(start=begin,end=end,raw_sha256=sha256_bytes(raw),prefix_sha256=sha256_bytes(raw[:begin]),suffix_sha256=sha256_bytes(raw[end:]))


def derive(source,preamble,input_body,inputs):
    a,a_slice=canonical_body(preamble,leading=False)
    b,b_slice=canonical_body(input_body,leading=True)
    files,inner,candidate=original.derive(source,a,b,inputs)
    files.update({'preamble.canonical.md':a,'input.canonical.md':b})
    report=dict(schema_version=SCHEMA,source_sha256=sha256_bytes(source),preamble_submission_sha256=sha256_bytes(preamble),input_submission_sha256=sha256_bytes(input_body),freeze_input_sha256=input_identity(inputs),boundary_slices={'preamble':a_slice,'input':b_slice},inner=inner,implementation_sha256=sha256_bytes(Path(__file__).read_bytes()),files={name:sha256_bytes(raw) for name,raw in sorted(files.items())},restored=inner['restored'],errors=inner['errors'],drift=inner['drift'],candidate_sha256=sha256_bytes(candidate) if candidate else None)
    return files,report,candidate


def validate_attempt(api, root, state, item):
    attempt = root / ('attempt-%02d' % item['attempt'])
    mode = item.get('submission_mode', 'plain')
    if mode != MODE:
        raise api.RecoveryError('boundary_submission_changed', 'wrong boundary submission mode')
    try:
        report_raw = (attempt / 'validation.json').read_bytes()
        report = json.loads(report_raw)
        binding = item['frozen_submission']
        if report.get('submission_mode') != MODE or report.get('frozen_submission') != binding:
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

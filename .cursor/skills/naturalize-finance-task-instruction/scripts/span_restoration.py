"""Proposal: exact marker removal from a retained submitted writer attempt.

This operation selects mechanical output only. It does not approve semantics or
make model calls. Root must independently approve the applicability receipt.
"""
from pathlib import Path
import copy
import hashlib
import json
import os
import re
import tempfile

SCHEMA = 'naturalization-submitted-span-restoration/v1'
DIRECTORY = 'submitted-span-restoration'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(root, relative):
    p = Path(relative)
    require(not p.is_absolute() and '..' not in p.parts, 'unsafe restoration path')
    target = root / p
    require(target.is_file() and not any(x.is_symlink() for x in [target, *target.parents]), 'unsafe restoration artifact')
    return target.read_bytes()


def semantic_evidence(request_bytes, response_bytes, source, marked):
    request, response = json.loads(request_bytes), json.loads(response_bytes)
    require(request.get('model') == 'openai/gpt-5.6-sol' and request.get('reasoning_effort') == 'high', 'review model configuration changed')
    messages = request.get('messages', [])
    require(len(messages) == 2 and messages[0]['role'] == 'system' and messages[1]['role'] == 'user', 'review context shape changed')
    require(json.loads(messages[1]['content']) == {'source': source, 'candidate': marked}, 'review context differs from original candidate')
    require('fresh independent semantic reviewer' in messages[0]['content'] and 'clause by clause' in messages[0]['content'], 'review contract is not independent clause review')
    require(len(response.get('choices', [])) == 1 and response['choices'][0]['finish_reason'] == 'stop', 'review response incomplete')
    review = json.loads(response['choices'][0]['message']['content'])
    require(set(review) == {'valid', 'blocking_findings', 'clause_checks'} and review['valid'] is True and review['blocking_findings'] == [], 'review did not pass')
    clauses = review['clause_checks']
    require(isinstance(clauses, list) and bool(clauses), 'review has no clause coverage')
    for clause in clauses:
        require(set(clause) == {'source_claim', 'candidate_claim', 'preserved'} and clause['preserved'] is True, 'review clause failed')
        require(all(isinstance(clause[k], str) and clause[k].strip() for k in ['source_claim', 'candidate_claim']), 'review clause empty')
        require(not re.search(r'\[\[/?F\d+', clause['candidate_claim']), 'review semantic claim contains transport markers')
    return review


def derive(api, root, state, request_relative, response_relative):
    import freeze_protected_spans as freezer
    source = read(root, 'source.snapshot.md')
    require(digest(source) == state['source_sha256'], 'source snapshot changed')
    api._verify_attempt_evidence(root, state)
    number = state['attempts'][-1]['attempt']
    attempt = 'attempt-%02d' % number
    marked = read(root, attempt + '/candidate.md')
    require(digest(marked) == state['attempts'][-1]['candidate_sha256'], 'original candidate changed')
    assembled = api.assemble_instruction(source, api.scan_instruction(source), read(root, attempt + '/preamble_body.md'), read(root, attempt + '/input_body.md'))
    require(assembled == marked, 'original submitted bodies changed')
    writer, spans = freezer.freeze(source.decode('utf-8'))
    require(read(root, 'writer_source.md') == writer.encode(), 'writer source changed')
    require(json.loads(read(root, 'frozen_spans.json')) == {'source_sha256': digest(source), 'spans': spans}, 'frozen span map changed')
    restored, errors, drift = freezer.restore(marked.decode('utf-8'), spans)
    require(not errors and not drift and bool(spans), 'restore has errors or noncanonical spans')
    require(restored != marked.decode() and re.sub(r'\[\[/?F\d+\]\]', '', marked.decode()) == restored, 'restoration is not wrapper removal only')
    result = restored.encode()
    report = api._load_validator().validate(source, result, api._verified_answer_key(root, state))
    request = read(root, request_relative)
    response = read(root, response_relative)
    review = semantic_evidence(request, response, source.decode(), marked.decode())
    files = {'source.snapshot.md', 'spans.json', 'writer_source.md', 'frozen_spans.json', request_relative, response_relative}
    if state.get('answer_key_sha256'):
        files.add('answer_key.snapshot.json')
    for item in state['attempts']:
        prefix = 'attempt-%02d/' % item['attempt']
        files.update(prefix + name for name in ['source.snapshot.md', 'candidate.md', 'preamble_body.md', 'input_body.md', 'validation.json'])
    evidence = {relative: digest(read(root, relative)) for relative in sorted(files)}
    return result, report, {'original_evidence': evidence, 'original_review_request': request_relative, 'original_review_response': response_relative, 'clauses_reviewed': len(review['clause_checks']), 'source_sha256': digest(source), 'original_candidate_sha256': digest(marked), 'restored_candidate_sha256': digest(result), 'span_count': len(spans), 'zero_drift': True, 'wrapper_removal_only': True}


def validate(api, root, state):
    binding = state.get('span_restoration')
    if binding is None:
        require(state.get('final_candidate', '').split('/')[0] != DIRECTORY, 'restoration selection lacks receipt')
        return None
    require(set(binding) == {'path', 'sha256'}, 'invalid restoration binding')
    require(binding['path'] == DIRECTORY + '/receipt.json', 'invalid restoration receipt path')
    raw = read(root, binding['path'])
    require(digest(raw) == binding['sha256'], 'restoration receipt changed')
    receipt = json.loads(raw)
    require(receipt['schema_version'] == SCHEMA, 'invalid restoration schema')
    before_raw = read(root, DIRECTORY + '/state.before.json')
    require(digest(before_raw) == receipt['state_before_sha256'], 'original state changed')
    before = json.loads(before_raw)
    for key in ['attempts', 'inherited_attempts', 'budget_inheritance', 'attempt_limit', 'source_sha256', 'answer_key_sha256', 'endpoint']:
        require(state.get(key) == before.get(key), 'restoration changed financial evidence or budget: ' + key)
    result, report, proof = derive(api, root, before, receipt['proof']['original_review_request'], receipt['proof']['original_review_response'])
    require(proof == receipt['proof'], 'restoration dependencies changed')
    require(read(root, DIRECTORY + '/candidate.md') == result, 'restored candidate changed')
    original_report = read(root, DIRECTORY + '/validation.json')
    require(digest(original_report) == receipt['validation_sha256'], 'restoration validation report changed')
    require(json.loads(original_report) == report, 'restoration validation changed')
    require(state.get('final_candidate') == DIRECTORY + '/candidate.md' and state.get('final_instruction_sha256') == digest(result), 'selected restoration changed')
    accepted = state.get('accepted_semantic_review')
    if accepted:
        require(accepted.get('accepted') is True and accepted.get('candidate_sha256') == proof['restored_candidate_sha256'] and accepted.get('source_sha256') == proof['source_sha256'] and accepted.get('attempt') == before['attempts'][-1]['attempt'], 'persisted semantic approval identity changed')
        require(state.get('semantic_reviews') == [*before.get('semantic_reviews', []), accepted], 'persisted semantic review history changed')
        persisted = json.loads(read(root, 'attempt-%02d/semantic-review.json' % accepted['attempt']))
        require(persisted == accepted, 'persisted semantic approval differs from state')
        require(accepted.get('review_kind') == 'prior_review_exact_marker_restoration', 'restored candidate requires explicit prior-review applicability')
        review, expected = check_review_applicability(root, accepted.get('applicability_review', {}), receipt)
        require(all(accepted.get(k) == value for k, value in expected.items()), 'persisted semantic applicability changed')
        require(accepted.get('reviewer') == review['reviewer'] and accepted.get('message') == review['reason'], 'persisted reviewer provenance changed')
    return receipt


def restore(api, root, *, expected_state_sha256, request_relative, response_relative, failpoint=None):
    root = Path(root).resolve()
    with api.recovery_lock(root):
        raw = read(root, 'state.json')
        state = json.loads(raw)
        if state.get('span_restoration'):
            receipt = validate(api, root, state)
            require(receipt['state_before_sha256'] == expected_state_sha256, 'restoration replay state mismatch')
            require((request_relative, response_relative) == (receipt['proof']['original_review_request'], receipt['proof']['original_review_response']), 'restoration replay evidence mismatch')
            return receipt
        require(digest(raw) == expected_state_sha256, 'state conflict')
        require(state.get('status') == 'validated' and state.get('attempts') and not state.get('accepted_semantic_review') and not state.get('semantic_reviews') and not state.get('staged_attempt'), 'restoration requires unapproved validated submission')
        api._verified_snapshot(root, state)
        api._check_live_source(state)
        result, report, proof = derive(api, root, state, request_relative, response_relative)
        report_bytes = api._json_bytes(report)
        receipt = {'schema_version': SCHEMA, 'state_before_sha256': digest(raw), 'proof': proof, 'validation_sha256': digest(report_bytes), 'writer_attempts_used': api._attempts_used(state), 'new_writer_calls': 0, 'new_semantic_reviews': 0, 'semantic_approval': 'requires_independent_applicability_review'}
        payloads = {'state.before.json': raw, 'candidate.md': result, 'validation.json': report_bytes, 'receipt.json': api._json_bytes(receipt)}
        destination = root / DIRECTORY
        if destination.exists():
            require({p.name for p in destination.iterdir()} == set(payloads), 'interrupted restoration has unexpected files')
            require(all(read(root, DIRECTORY + '/' + name) == value for name, value in payloads.items()), 'interrupted restoration differs')
        else:
            temporary = Path(tempfile.mkdtemp(prefix='.span-restoration-', dir=root))
            try:
                for name, value in payloads.items():
                    api.atomic_write_bytes(temporary / name, value, mode=0o400)
                api._fsync_directory(temporary)
                if failpoint:
                    failpoint('before_directory_publish')
                os.rename(temporary, destination)
                api._fsync_directory(root)
            finally:
                if temporary.exists():
                    import shutil
                    shutil.rmtree(temporary)
        if failpoint:
            failpoint('after_directory_publish')
        updated = copy.deepcopy(state)
        updated['span_restoration'] = {'path': DIRECTORY + '/receipt.json', 'sha256': digest(payloads['receipt.json'])}
        updated['final_candidate'] = DIRECTORY + '/candidate.md'
        updated['final_instruction_sha256'] = digest(result)
        validate(api, root, updated)
        require(read(root, 'state.json') == raw, 'precommit state conflict')
        api.atomic_write_json(root / 'state.json', updated)
        if failpoint:
            failpoint('after_state_publish')
        return receipt


def check_review_applicability(root, binding, receipt):
    require(set(binding) == {'path', 'sha256'}, 'invalid semantic applicability binding')
    raw = read(root, binding['path'])
    require(digest(raw) == binding['sha256'], 'semantic applicability review changed')
    review = json.loads(raw)
    keys = {'schema_version', 'reviewer', 'restoration_receipt_sha256', 'original_request_sha256', 'original_response_sha256', 'source_sha256', 'restored_candidate_sha256', 'all_changed_prose_covered', 'canonical_span_text_only', 'approved', 'reason'}
    require(set(review) == keys and review['schema_version'] == 'naturalization-span-review-applicability/v1', 'invalid semantic applicability schema')
    require(all(review[k] is True for k in ['all_changed_prose_covered', 'canonical_span_text_only', 'approved']), 'semantic applicability not approved')
    require(all(isinstance(review[k], str) and review[k].strip() for k in ['reviewer', 'reason']), 'semantic applicability needs independent reviewer/reason')
    proof = receipt['proof']
    expected = {'restoration_receipt_sha256': digest(read(root, DIRECTORY + '/receipt.json')), 'original_request_sha256': proof['original_evidence'][proof['original_review_request']], 'original_response_sha256': proof['original_evidence'][proof['original_review_response']], 'source_sha256': proof['source_sha256'], 'restored_candidate_sha256': proof['restored_candidate_sha256']}
    require(all(review[k] == value for k, value in expected.items()), 'semantic applicability context changed')
    return review, expected


def accept_review(api, root, *, expected_state_sha256, applicability_path, applicability_sha256):
    """Record separately authored independent applicability, never a new review."""
    root = Path(root).resolve()
    with api.recovery_lock(root):
        raw = read(root, 'state.json')
        require(digest(raw) == expected_state_sha256, 'semantic applicability state conflict')
        state = json.loads(raw)
        api._verified_budget(root, state)
        require(state.get('status') == 'validated' and state.get('span_restoration') and not state.get('accepted_semantic_review'), 'requires unapproved restored candidate')
        receipt = validate(api, root, state)
        binding = {'path': applicability_path, 'sha256': applicability_sha256}
        review, expected = check_review_applicability(root, binding, receipt)
        approval = {'version': api.RECOVERY_VERSION, 'accepted': True, 'attempt': state['attempts'][-1]['attempt'], 'reviewer': review['reviewer'], 'message': review['reason'], 'source_sha256': state['source_sha256'], 'candidate_sha256': state['final_instruction_sha256'], 'review_kind': 'prior_review_exact_marker_restoration', 'applicability_review': binding, **expected}
        # The original model response and the original attempted candidate are
        # immutable. This file records applicability, not a second model review.
        path = root / ('attempt-%02d' % approval['attempt']) / 'semantic-review.json'
        if path.exists():
            require(path.read_bytes() == api._json_bytes(approval), 'interrupted semantic applicability differs')
        else:
            api.atomic_write_json(path, approval)
        updated = copy.deepcopy(state)
        updated['accepted_semantic_review'] = approval
        updated['semantic_reviews'] = [*state.get('semantic_reviews', []), approval]
        validate(api, root, updated)
        require(read(root, 'state.json') == raw, 'semantic applicability precommit conflict')
        api.atomic_write_json(root / 'state.json', updated)
        return approval

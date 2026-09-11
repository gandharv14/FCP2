"""Explicit zero-disclosure applicability; no call or mutation is manufactured."""
from pathlib import Path
from scripts.batch import harbor_readiness as static
from scripts.batch.harbor_admission import read, record, require
from scripts.batch.harbor_tracker import validate_tracker
from scripts.batch.harbor_tracker_lifecycle import validate_lifecycle, checked_record


def validate_content(s, *, root=static.ROOT):
    require(s.dialogue_noop is not None, 'explicit empty-dialogue observation required')
    proof = read(s.dialogue_noop)
    require(set(proof) == {'schema_version', 'before_tracker', 'stage', 'stage_tree', 'claims'}, 'invalid empty-dialogue evidence schema')
    require(proof['schema_version'] == 'harbor-empty-dialogue/v1', 'unknown empty-dialogue evidence')
    before = read(proof['before_tracker'])
    validate_tracker(before)
    validate_lifecycle(before)
    require(before['workbook_id'] == s.workbook, 'empty-dialogue task differs')
    for key in ('original_raw_source', 'source_file', 'source_generation', 'segmentation_generation'):
        require(before['bindings'][key] == s.bindings[key], 'empty-dialogue source context differs')
    row = before['lane_state']['harbor-dialogue-author']
    require(row['state'] == 'pending' and not row.get('attempt') and not row.get('evidence_paths') and not row.get('confidence_history'), 'prior dialogue context cannot be downgraded to zero calls')
    require(before['lane_state']['harbor-independent-verifier']['state'] == 'passed', 'empty-dialogue observation must follow independent verification')
    require(before['promotion_failure'] is None, 'unresolved failure cannot authorize empty dialogue')
    require(proof['stage'] == str(s.stage) and proof['stage_tree'] == static.tree(s.stage), 'empty dialogue must leave every staged byte unchanged')
    require(proof['claims'] == s.dialogue_claims, 'empty claim binding differs')
    for name in ('environment/additional-assumptions.md', 'tests/dialogue-applied.json'):
        require(not (s.stage/name).exists(), 'empty dialogue cannot contain invented dialogue artifacts')
    require(s.dialogue_review is None and s.dialogue_reviewed_draft is None, 'empty dialogue cannot invent a review or draft')
    payload = read(record(s.stage/'tests/disclosure.json'))
    require(payload.get('agent_records') == [], 'disclosure records are absent, malformed, or nonempty')
    extractor = static.skill_module(root, 'additional-assumptions-dialogue', 'extract_claims')
    options = {}
    if s.scope_evidence:
        checked_record(s.scope_evidence)
        options = dict(source_workbook=s.bindings['source_file']['path'], scope_evidence=s.scope_evidence['path'])
    fresh = extractor.extract(s.stage, **options)
    require(fresh == read(s.dialogue_claims) and fresh['empty'] is True and fresh['claims'] == [] and fresh['writer_pack']['claims'] == [], 'current full extraction is not exactly empty')
    recovery = static.skill_module(root, 'naturalize-finance-task-instruction', 'naturalize_recovery')
    state = recovery._read_state(s.naturalization_root)
    require(state['status'] == 'applied', 'empty dialogue requires completed naturalization')
    candidate = s.naturalization_root/state['final_candidate']
    require(record(candidate)['sha256'] == state['final_instruction_sha256'] and candidate.read_bytes() == (s.stage/'instruction.md').read_bytes(), 'empty dialogue changed accepted naturalized instruction')
    recovery.verify_naturalizer_metadata(s.stage/'instruction.md', s.stage/'task.toml')
    evidence = [s.dialogue_noop, proof['before_tracker'], s.dialogue_claims, record(candidate), record(s.naturalization_root/'state.json')]
    evidence += [record(s.stage/name) for name in proof['stage_tree']]
    return dict(valid=True, round=0, disposition='not_applicable', reason='no_agent_records', evidence=evidence)


def validate(selection, *, root=static.ROOT):
    p = selection.semantic
    require(p is not None and p.dialogue_call is None and p.dialogue_reviewed_claims is None and p.dialogue_writer_calls == () and p.historical_dialogue is None, 'only explicitly empty dialogue permits zero calls')
    return validate_content(selection.content, root=root)

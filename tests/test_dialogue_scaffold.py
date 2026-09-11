import copy
import hashlib
import importlib
import json
from pathlib import Path

import pytest


def test_shared_source_links_require_formula_period_and_complete_coverage(tmp_path, monkeypatch):
    from datetime import datetime
    from openpyxl import Workbook
    scripts = Path(__file__).resolve().parents[1] / '.cursor/skills/additional-assumptions-dialogue/scripts'
    monkeypatch.syspath_prepend(str(scripts))
    extractor = importlib.import_module('extract_claims')
    scope = importlib.import_module('claim_scope')
    composer = importlib.import_module('compose_draft')
    validator = importlib.import_module('validate_dialogue')
    lib = importlib.import_module('aa_lib')
    book = Workbook(); book.active.title = 'Covenants'; book.create_sheet('BS')
    claims, rows = [], []
    for index, (target_col, source_col, month) in enumerate([('E', 'H', 3), ('F', 'K', 6)], 1):
        day = 31 if month == 3 else 30
        period = datetime(2020, month, day)
        target, origin = f'Covenants!{target_col}25', f'BS!{source_col}28'
        book['Covenants'][f'{target_col}7'] = period
        book['BS'][f'{source_col}7'] = period
        book['Covenants'][f'{target_col}25'] = '=' + origin
        claims.append(extractor.build_claim({'entry': 'source_selection', 'value': 'source',
            'cells': [target], 'fields': {'label': '"Revolver"',
            'ingredient': f'cell {origin} on the row labelled "Line of Credit"'}}, index))
        rows.append(dict(target_reference=target, source_reference=origin,
            target_period_header=f'Covenants!{target_col}7', source_period_header=f'BS!{source_col}7',
            original_formula='=' + origin, period=period.strftime('%Y-%m-%d')))
    source = tmp_path / 'source.xlsx'; book.save(source)
    disclosure = tmp_path / 'disclosure.json'; disclosure.write_text('{}')
    evidence = tmp_path / 'scope.json'
    payload = dict(source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        disclosure_sha256=hashlib.sha256(disclosure.read_bytes()).hexdigest(), rows=rows)
    evidence.write_text(json.dumps(payload))
    proved = copy.deepcopy(claims)
    shared = scope.prove_groups(proved, disclosure, source, evidence)
    assert shared['groups'] == [[claim['record_id'] for claim in claims]]
    document = dict(claims=proved, shared_source_links=shared, junior_titles=['Analyst'], senior_titles=['VP'])
    template, slots = composer.compose(document, document)
    assert len(slots['slots']) == 2
    draft = composer.assemble_slots(template, slots, {'schema_version': 'dialogue-slot-prose/v1', 'slots': {
        'c001-junior': 'Which source should I use?',
        'c001-senior': 'Use the Line of Credit row on BS for Revolver, matching each period.'}})
    mapped, faults = validator.map_turns_to_claims(lib.parse_turns(composer.strip_slot_scaffold(draft)), proved)
    assert not faults and all(len(turns) == 2 for turns in mapped.values())
    changed = copy.deepcopy(document); changed['shared_source_links']['groups'][0].pop()
    with pytest.raises(ValueError, match='every original claim'):
        composer.compose(changed, changed)
    payload['rows'][1]['period'] = '2020-09-30'; evidence.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match='matching periods'):
        scope.prove_groups(copy.deepcopy(claims), disclosure, source, evidence)
    payload['rows'][1]['period'] = '2020-06-30'
    payload['rows'][1]['source_reference'] = 'BS!H28'; evidence.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match='exact original direct'):
        scope.prove_groups(copy.deepcopy(claims), disclosure, source, evidence)
    disclosure.write_text('{"changed": true}')
    with pytest.raises(ValueError, match='hashes changed'):
        scope.prove_groups(copy.deepcopy(claims), disclosure, source, evidence)


@pytest.mark.parametrize('sheet,encoded', [('BS', 'BS'),
    ("2026 O'Brien! $ Plan", "'2026 O''Brien! $ Plan'")])
def test_source_link_speech_preserves_relationship_and_sheet(monkeypatch, sheet, encoded):
    scripts = Path(__file__).resolve().parents[1] / '.cursor/skills/additional-assumptions-dialogue/scripts'
    monkeypatch.syspath_prepend(str(scripts))
    extract = importlib.import_module('extract_claims')
    claim = extract.build_claim({'entry': 'source_selection', 'value': 'source',
        'cells': ['Covenants!E25'], 'fields': {'label': '"Revolver"',
        'ingredient': f'cell {encoded}!$H$28 on the row labelled "Line of Credit"'}}, 1)
    assert 'takes its value from the row labelled "Line of Credit"' in claim['spoken']
    assert f'on the {sheet} tab' in claim['spoken']
    assert f'the {sheet} tab' in claim['must_say']
    assert '$H$28' not in claim['spoken']
    assert "next period" not in claim['spoken']
    speak = importlib.import_module('spoken_formula')
    with pytest.raises(ValueError, match='complete named source'):
        speak.speak_record({'entry': 'source_selection', 'fields': {'ingredient': 'cell BS!H28'}})


@pytest.mark.parametrize('round_number', [1, 2])
def test_review_cli_requires_naturalness_in_every_round(tmp_path, monkeypatch, round_number):
    scripts = Path(__file__).resolve().parents[1] / '.cursor/skills/additional-assumptions-dialogue/scripts'
    monkeypatch.syspath_prepend(str(scripts))
    validator = importlib.import_module('validate_dialogue')
    claims = tmp_path / 'claims.json'
    claims.write_text(json.dumps({'claims': [{'record_id': 'one', 'must_say': ['prior period']}]}))
    review = tmp_path / 'review.json'
    payload = {'agent_model': 'gpt-5.6-sol-high', 'round': round_number,
        'accuracy': {'verdict': 'pass', 'claims': [{'record_id': 'one', 'must_say': 'entailed'}],
                     'extras': [], 'cell_refs_in_senior_turns': []},
        'naturalness': {'verdict': 'fail', 'findings': ['repetitive checklist']}, 'passed': False}
    review.write_text(json.dumps(payload))
    report = tmp_path / 'report.json'
    args = ['check-review', '--claims', str(claims), '--review', str(review),
            '--round', str(round_number), '--report', str(report)]
    assert validator.main(args) == 2
    assert json.loads(report.read_text())['passed'] is False
    payload['naturalness'] = {'verdict': 'pass', 'findings': []}
    payload['passed'] = True
    review.write_text(json.dumps(payload))
    assert validator.main(args) == 0


def test_scaffold_migration_preserves_prose_and_rejects_changed_claims(monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / '.cursor/skills/additional-assumptions-dialogue/scripts'
    monkeypatch.syspath_prepend(str(scripts))
    compose = importlib.import_module('compose_draft')
    claims = {'junior_titles': ['Analyst'], 'senior_titles': ['VP'], 'claims': [
        {'record_id': 'one', 'sheet': 'CalcA', 'row_label': 'Revenue',
         'must_say': ['last period']}], 'empty': False}
    pack = {'claims': [{'record_id': 'one'}]}
    template, slots = compose.compose(claims, pack)
    old = template.replace('**Analyst:** CalcA:', '**Analyst:**').replace(
        '**VP:**', '**VP:** This one lives on the CalcA tab.')
    fills = {'c001-junior': 'How should revenue work?',
             'c001-senior': 'Revenue uses last period’s value.'}
    filled = old
    for key, value in fills.items():
        filled = filled.replace(compose.slot_token(key), value)
    migrated, proof = compose.migrate_scaffold(old, filled, slots, template, slots)
    assert proof['slot_prose_sha256'] == {key: hashlib.sha256(value.encode()).hexdigest()
                                         for key, value in fills.items()}
    assert compose.strip_slot_scaffold(migrated).startswith('<!-- claim:one -->\n**Analyst:** CalcA: How')
    assert '**VP:** Revenue uses last period’s value.' in compose.strip_slot_scaffold(migrated)
    assert 'This one lives' not in migrated
    payload = {'schema_version': 'dialogue-slot-prose/v1', 'slots': fills}
    assert compose.assemble_slots(template, slots, payload) == migrated
    assert migrated.endswith('\n')
    with pytest.raises(ValueError, match='every template slot'):
        compose.assemble_slots(template, slots, {'schema_version': 'dialogue-slot-prose/v1', 'slots': {}})
    with pytest.raises(ValueError, match='invalid'):
        compose.assemble_slots(template, slots, {'schema_version': 'dialogue-slot-prose/v1',
            'slots': {**fills, 'c001-senior': '**Director:** Invented extra turn'}})
    with pytest.raises(ValueError, match='duplicate JSON key'):
        compose._unique_json_object([('same', 1), ('same', 2)])
    changed = copy.deepcopy(slots)
    changed['slots']['c001-senior']['must_say'] = ['next period']
    with pytest.raises(ValueError, match='claim content'):
        compose.migrate_scaffold(old, filled, changed, template, slots)
    with pytest.raises(ValueError, match='previous filled template is invalid'):
        compose.migrate_scaffold(old, filled.replace('**VP:**', '**Director:**'), slots, template, slots)

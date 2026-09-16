"""Exact source proof for 0190's retained first dialogue review.

This classifies three presentation findings. It grants no review, lease,
continuation, or publication and never changes the rejected review.
"""
from pathlib import Path
import hashlib
import json

SCHEMA = 'fleet-0190-dialogue-source-warning/v1'
SOURCE = 'c6e9acb47959d5bcd2c6abac3f3111932a6e305025f59fe1881a03fe9f4292da'
HASHES = {
    'review': '702eb92d6397c0293088ad246af7592f5ac1da872107a4a39305a2624245d527',
    'claims': '7efc8a6988beeae3fd3cc1a940d6bf21042692611f4bdde3234155fdd72d1eef',
    'draft': '155b42729a1f1e07387a6eac269300474cf10283192fb0a53935d777eee9cf03',
    'template': '8f6922e1663429191a62fa22d5b23d91bbfa04f133f3d4e6b5084c9d75646aef',
    'writer': '8c6a5a8f26ac300c1a1226c842592228292127df8c05a81b652293bc30dc6206',
    'paraphraser': '43941213862de5282597d1550ee639f19349d6e6f8be50e942cae344b298d8ae',
}
ROWS = (
    ('G30', 'Loan Count - Purchased', 'Trades', 'D9', 'Total', 'B9'),
    ('G31', 'Loan Cost Basis - Purchased', 'Trades', 'E9', 'Total', 'B9'),
    ('G33', 'Loan Cost Basis - Liquidated', 'Loans', 'E66', 'Total Non-Active Loans', 'B66'),
)
ACCURACY = 'The frozen junior scoped-cell header is altered by inserting "Summary:" before "For `Summary!G30`:".'
STYLE = [
    'The opening "Summary: For `Summary!G30`:" is redundant and does not read like natural Slack dialogue.',
    'The senior answers are near-copies of the cards\' spoken lines, with only mechanical substitutions such as "set ... equal to" or "feed ... from".',
]
def require(value, message):
    if not value:
        raise ValueError(message)


def checked_bytes(row, expected):
    p = Path(row['path'])
    require(not any(x.is_symlink() for x in [p, *p.parents]), 'warning evidence symlink')
    raw = p.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == row['sha256'] == expected,
            '0190 warning evidence differs')
    return raw


def validate(review, claims, draft, template, writer_filled, paraphraser_filled,
             source, *, task_dir, root):
    from scripts.batch import harbor_instruction_producer as producer
    import openpyxl
    bindings = [review, claims, draft, template, writer_filled, paraphraser_filled]
    raw = {k: checked_bytes(row, HASHES[k]) for k, row in zip(HASHES, bindings)}
    checked_bytes(source, SOURCE)
    r, payload = json.loads(raw['review']), json.loads(raw['claims'])
    require(payload['workbook'] == '0190' and payload['task'] == '0190-outputs'
            and not payload.get('shared_source_links'), '0190 claim identity differs')
    rows = payload['claims']
    require(len(rows) == 3, '0190 complete clause count differs')
    checks = r['accuracy']['claims']
    require(len(checks) == 3 and r['accuracy']['extras'] == []
            and r['accuracy']['cell_refs_in_senior_turns'] == []
            and r['accuracy']['verdict'] == 'fail' and r['passed'] is False
            and r['naturalness'] == {'verdict': 'fail', 'findings': STYLE},
            '0190 review finding set differs')
    book = openpyxl.load_workbook(source['path'], read_only=True, data_only=False)
    formulas = []
    try:
        for i, (cell, label, sheet, origin, origin_label, label_cell) in enumerate(ROWS):
            identity = 'source_selection::Summary::%s::%03d' % (label, i+1)
            claim = rows[i]
            require(claim['record_id'] == identity and claim['sheet'] == 'Summary'
                    and claim['row_label'] == label and claim['junior_scope'] == ['Summary!'+cell]
                    and claim['must_say'] == ['the row labelled "'+label+'"',
                        'the row labelled "'+origin_label+'"', 'the '+sheet+' tab'],
                    '0190 source selection clause differs')
            require(checks[i] == {'record_id': identity, 'must_say': 'entailed',
                    'findings': [ACCURACY] if i == 0 else []}, '0190 review entailment differs')
            formula = '='+sheet+'!'+origin
            require(book['Summary'][cell].value == formula
                    and book['Summary']['A'+cell[1:]].value == label
                    and book[sheet][label_cell].value == origin_label,
                    '0190 direct source formula or label differs')
            formulas.append(dict(cell='Summary!'+cell, formula=formula,
                                 source_label=origin_label, source_cell=sheet+'!'+origin))
    finally:
        book.close()
    validator = producer.skill_module(Path(root), 'additional-assumptions-dialogue', 'validate_dialogue')
    text, frozen = raw['draft'].decode(), raw['template'].decode()
    writer, para = raw['writer'].decode(), raw['paraphraser'].decode()
    require(validator.fill_faults(frozen, writer) == []
            and validator.fill_faults(frozen, para) == []
            and validator.strip_slot_scaffold(para) == text,
            '0190 original scaffold or sealed draft differs')
    from scripts.batch.harbor_dialogue_source_warnings import _juniors
    require(_juniors(writer) == _juniors(para), '0190 protected junior bytes differ')
    require('**Analyst:** Summary: For `Summary!G30`:\n' in frozen,
            '0190 redundant prefix is not original frozen template')
    mechanics = validator.check_draft(text, payload, Path(task_dir), source_workbook=source)
    require(mechanics['passed'], '0190 owning mechanics failed')
    warnings = [dict(code='frozen_junior_prefix', record_id=rows[0]['record_id'], finding=ACCURACY)]
    warnings.extend(dict(code='dialogue_style_only', finding=x) for x in STYLE)
    return dict(schema_version=SCHEMA, workbook='0190', claim_count=3, warnings=warnings,
                evidence=bindings+[source], source_formulas=formulas, mechanics=mechanics,
                root=str(Path(root).resolve()), task_dir=str(Path(task_dir).resolve()))


def revalidate(proof, review_payload, claims_payload, draft_text, *, root):
    require(proof['schema_version'] == SCHEMA and proof['root'] == str(Path(root).resolve()),
            '0190 warning profile/root differs')
    b = proof['evidence']
    require(len(b) == 7 and json.loads(checked_bytes(b[0], HASHES['review'])) == review_payload
            and json.loads(checked_bytes(b[1], HASHES['claims'])) == claims_payload
            and checked_bytes(b[2], HASHES['draft']).decode() == draft_text,
            '0190 owning warning inputs differ')
    require(validate(*b, task_dir=proof['task_dir'], root=root) == proof,
            '0190 warning proof differs')
    return proof

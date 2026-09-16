"""Read-only provenance for a completed rejection whose consumer failed.

This is a typed historical predecessor, never a replacement worker checkpoint.
No function in this module acquires custody, renews a lease, or calls a model.
"""
from pathlib import Path
import hashlib
import importlib.util
import json
import re
import sys

SCHEMA = 'fleet-retained-rejected-reader/v1'
FIELDS = {'schema_version', 'helper', 'reader', 'rejection', 'revision',
          'workbook', 'source', 'snapshot'}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def binding(path):
    p = Path(path)
    require(p.is_absolute() and p.is_file()
            and not any(x.is_symlink() for x in (p, *p.parents)),
            'canonical retained rejection input required')
    return dict(path=str(p), sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def read(row):
    require(set(row) == {'path', 'sha256'} and binding(row['path']) == row,
            'retained rejection bytes changed')
    return json.loads(Path(row['path']).read_bytes())


def module(row):
    require(binding(row['path']) == row, 'retained rejection reader changed')
    spec = importlib.util.spec_from_file_location(
        'retained_rejection_reader_' + row['sha256'][:16], row['path'])
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


def sum_references(tree):
    """Only the subtracted SUM of a bounded set of same-column cells."""
    from openpyxl.utils.cell import range_boundaries, get_column_letter
    if tree.kind == 'prefix' and tree.name == '+' and len(tree.args) == 1:
        tree = tree.args[0]
    require(tree.kind == 'func' and tree.name == 'MAX' and len(tree.args) == 2,
            'MAX source boundary required')
    expression, floor = tree.args
    require(floor.kind == 'const' and floor.value == '0'
            and expression.kind == 'infix' and expression.name == '-'
            and len(expression.args) == 2, 'subtraction before zero floor required')
    total = expression.args[1]
    require(total.kind == 'func' and total.name == 'SUM' and total.args,
            'whole SUM must be subtracted')
    refs = []
    for item in total.args:
        require(item.kind == 'ref' and not item.args
                and re.fullmatch(r'[A-Z]{1,3}[1-9][0-9]*(?::[A-Z]{1,3}[1-9][0-9]*)?', item.name),
                'literal same-sheet SUM cells required')
        first_col, first_row, last_col, last_row = range_boundaries(item.name)
        require(first_col == last_col and 0 < last_row - first_row + 1 <= 64,
                'bounded vertical SUM range required')
        refs.extend(get_column_letter(first_col) + str(row)
                    for row in range(first_row, last_row + 1))
    require(1 < len(refs) <= 64 and len(refs) == len(set(refs))
            and len({re.match('[A-Z]+', r).group() for r in refs}) == 1,
            'distinct same-column SUM operands required')
    return refs


def sentence_pair(labels):
    require(len(labels) >= 3 and len(labels) == len(set(labels))
            and all(isinstance(x, str) and x and '\n' not in x for x in labels),
            'distinct source labels required')
    rows = ['the row labelled ' + json.dumps(x) for x in labels]
    old = 'Next, minus ' + rows[0] + ' plus ' + ', '.join(rows[1:-1]) + ', and ' + rows[-1] + '.'
    new = 'Next, subtract the combined sum of ' + ' plus '.join(rows) + ' from the preceding result.'
    return old, new


def revision(proof, rejected, validator, stage, root):
    """Recompute the existing single-sentence source proof; do not rewrite it."""
    from xl_ast_graph import parse_formula
    from openpyxl.formula.translate import Translator
    require(proof['schema_version'] == 'proposed-source-sum-grouping-correction/v1'
            and proof['independent_review_pass'] is False
            and proof['model_calls_added'] == 0
            and proof['publication_authorized'] is False,
            'source correction is evidence, never independent acceptance')
    require(proof['original_draft'] == rejected['draft']
            and proof['original_review'] == rejected['call']['parsed'],
            'source correction detached from real rejected response')
    claims = read(proof['claims'])
    require(claims == read(rejected['claims']), 'original claim bytes differ')
    for row in (proof['original_draft'], proof['corrected_draft'], proof['source']):
        require(binding(row['path']) == row, 'source correction input changed')
    rid = proof['record_id']
    ids = [c['record_id'] for c in claims['claims']]
    judged = rejected['review']['accuracy']['claims']
    require(len(ids) == len(set(ids))
            and len(judged) == len(ids)
            and {c['record_id'] for c in judged} == set(ids)
            and [(c['record_id'], c['must_say']) for c in judged
                 if c['must_say'] != 'entailed'] == [(rid, 'contradicted')],
            'exact complete rejected claim set required')
    claim = next(c for c in claims['claims'] if c['record_id'] == rid)
    formula = claim['reviewer_only']['evidence']
    refs = sum_references(parse_formula(formula))
    renderer = validator.disclose
    book = renderer.Book(Path(proof['source']['path']))
    sheet = claim['sheet']
    labels = [book.row_label(renderer.parse_ref("'" + sheet.replace("'", "''") + "'!" + r, ''))
              for r in refs]
    require(all('the row labelled ' + json.dumps(label) in claim['must_say'] for label in labels),
            'source operands differ from retained claim')
    formulas = []
    cells = claim['reviewer_only']['cells']
    require(cells and cells == claim['junior_scope'], 'exact original formula family required')
    origin = renderer.parse_ref(cells[0], '').rsplit('!', 1)[1]
    for raw in cells:
        key = renderer.parse_ref(raw, '')
        require(key.rsplit('!', 1)[0] == renderer.parse_ref(cells[0], '').rsplit('!', 1)[0],
                'foreign formula-family sheet')
        actual = book.formula[key]
        require(actual == Translator(formula, origin=origin).translate_formula(key.rsplit('!', 1)[1]),
                'source formula family changed')
        formulas.append(dict(cell=raw, formula=actual))
    require(proof['source_ast'] == dict(root='MAX', left_operation='subtract', right_argument='SUM',
                                      sum_refs=refs, labels=labels, formulas=formulas),
            'saved source AST proof differs')
    old, new = sentence_pair(labels)
    draft = Path(proof['original_draft']['path']).read_text()
    fixed = Path(proof['corrected_draft']['path']).read_text()
    require(proof['old_sentence'] == old and proof['new_sentence'] == new
            and draft.count(old) == 1 and fixed == draft.replace(old, new, 1),
            'correction must change exactly the source-proved sentence')
    mapped, faults = validator.map_turns_to_claims(validator.parse_turns(draft), claims['claims'])
    remapped, new_faults = validator.map_turns_to_claims(validator.parse_turns(fixed), claims['claims'])
    unchanged = [i for i in ids if i != rid]
    require(not faults and not new_faults and old in mapped[rid][1]['text']
            and all(mapped[i] == remapped[i] for i in unchanged)
            and proof['unchanged_record_ids'] == unchanged
            and proof['unchanged_count'] == len(unchanged), 'other authored claims changed')
    mechanics = validator.check_draft(fixed, claims, stage)
    require(mechanics['passed'] is True and mechanics == proof['mechanical_report'],
            'corrected draft mechanical proof changed')
    from scripts.batch.harbor_dialogue_operand_evidence import project
    operands = project(claims, fixed, source=proof['source'],
                       answer_key=binding(stage / 'tests/answer_key.json'), task_dir=stage, root=root)
    require(operands == read(proof['operand_evidence']), 'complete source operand projection changed')
    return dict(claims=len(ids), unchanged_claims=len(unchanged), corrected_claim=rid,
                mechanics=mechanics, source=proof['source'], independent_review_pass=False)


def inspect(row):
    p = read(row)
    require(set(p) == FIELDS and p['schema_version'] == SCHEMA
            and p['helper'] == binding(__file__), 'closed rejected-reader selection required')
    expected = read(p['rejection'])
    require(expected['schema_version'] == 'harbor-completed-review-consumption-proof/v1'
            and expected['reader'] == p['reader'] and expected['snapshot'] == p['snapshot']
            and expected['passed'] is False and expected['round'] == 2
            and expected['model_calls_executed'] == 0 and expected['publication_ready'] is False,
            'actual completed second-review rejection required')
    reader = module(p['reader'])
    require(reader.inspect() == expected, 'recomputed historical rejection proof differs')
    root, base, campaign, worker_module, producer, repair, codec, worker, selected, lease = reader.setup()
    require(selected.content.workbook == p['workbook']
            and selected.content.bindings['original_raw_source'] == p['source'],
            'retained rejection workbook/source differs')
    proof = read(p['revision'])
    require(proof['source'] == selected.content.bindings['source_file'], 'selected source derivative differs')
    validator = producer.skill_module(root, 'additional-assumptions-dialogue', 'validate_dialogue')
    correction = revision(proof, expected, validator, selected.content.stage, root)
    return dict(status='REJECTED_READER_AND_SOURCE_CORRECTION_VERIFIED', selection=row,
                rejection=p['rejection'], revision=p['revision'], correction=correction,
                original_review_attempts=2, model_calls=0, task_mutations=0,
                canonical_worker_checkpoint=False, publication_ready=False)

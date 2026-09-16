"""Pure source-card correction for string criteria swallowed as row labels.

This derives new public cards. It does not approve a retained draft, create an
authoring permit, change a budget, or execute a model request.
"""
from copy import deepcopy
import json


def require(ok, message):
    if not ok:
        raise ValueError(message)


def corrected_claims(original, old_renderer, new_renderer, extractor, formulas):
    claims = original['claims']
    require(claims and len({c['record_id'] for c in claims}) == len(claims),
            'complete unique original claim inventory required')
    keep = ('record_id', 'sheet', 'row_label', 'junior_scope', 'question',
            'must_say', 'alternatives', 'chosen_alternative', 'spoken')
    require([{k: c[k] for k in keep if k in c} for c in claims] ==
            original['writer_pack']['claims'], 'writer pack differs from original claims')
    body = original['disclosure_body']
    require(original['writer_pack']['disclosure_body'] == body,
            'disclosure copies differ')
    corrected = deepcopy(original)
    changes = []
    for index, old in enumerate(claims):
        source = old['reviewer_only']
        if source.get('source') != 'custom_method_detector':
            continue
        cells = source['cells']
        if len(cells) != 1:
            continue
        args = (source['steps'], cells[0], source['evidence'], old['sheet'],
                old['row_label'], 'only for this cell')
        before, after = old_renderer.speak_steps(*args), new_renderer.speak_steps(*args)
        if before == after:
            continue
        require(before == old['spoken'], 'old card is not its original renderer output')
        require(formulas.get(cells[0]) == source['evidence'], 'actual source formula changed')
        tree = extractor.disclose._parse_formula_ast(source['evidence'])
        require(tree is not None, 'original formula cannot be parsed')
        criteria = []
        for node in extractor.disclose.walk_ast(tree):
            if node.kind == 'func' and node.name.upper() == 'AVERAGEIF' and len(node.args) in (2, 3):
                criterion = node.args[1]
                if criterion.kind == 'const' and criterion.name == 'text':
                    criteria.append(str(criterion.shape))
        require(criteria, 'source does not contain an AVERAGEIF string criterion')
        prior_phrases = old_renderer.collect_phrases(source['steps'])
        next_phrases = new_renderer.collect_phrases(source['steps'])
        require(len(prior_phrases) == len(next_phrases), 'source reference count changed')
        removed = []
        for prior, current in zip(prior_phrases, next_phrases):
            require({k: v for k, v in prior.items() if k not in ('text', 'labels')} ==
                    {k: v for k, v in current.items() if k not in ('text', 'labels')},
                    'source reference identity changed')
            require(prior['labels'][:len(current['labels'])] == current['labels'],
                    'actual row label changed')
            removed.extend(prior['labels'][len(current['labels']):])
        require(removed and all(value in criteria for value in removed),
                'removed row identity is not a formula criterion')
        record = dict(entry=source['entry'], evidence=source['evidence'])
        atoms = extractor.must_say_atoms(after, old['sheet'], old['row_label'])
        atoms += [atom for atom in extractor.formula_text_atoms(record) if atom not in atoms]
        false_atoms = {'the row labelled ' + old_renderer.quote_label(v) for v in removed}
        require(set(old['must_say']) - set(atoms) == false_atoms and
                not set(atoms) - set(old['must_say']), 'requirements changed beyond false criterion labels')
        require(all('the text literal ' + json.dumps(v, ensure_ascii=False) in atoms for v in removed),
                'formula string criterion was lost')
        corrected['claims'][index].update(spoken=after, must_say=atoms)
        corrected['writer_pack']['claims'][index].update(spoken=after, must_say=deepcopy(atoms))
        require(body.count(before) == 1, 'ambiguous disclosure card')
        body = body.replace(before, after, 1)
        changes.append(dict(record_id=old['record_id'], source_cell=cells[0],
                            source_formula=source['evidence'], removed_false_row_atoms=sorted(false_atoms),
                            retained_criteria=removed, before=before, after=after))
    require(changes, 'no source-proved criterion correction')
    corrected['disclosure_body'] = body
    corrected['writer_pack']['disclosure_body'] = body
    return corrected, changes

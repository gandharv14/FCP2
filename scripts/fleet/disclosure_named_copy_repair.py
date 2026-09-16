"""Derive only copied-reference wording from an unchanged source band.

This creates candidate data, never a semantic pass or a review reservation.
Both renderers must independently agree on the entire old copy note. All
other record fields, source formulas, numerical operands and scope survive.
"""
import copy


def corrected_payload(original_renderer, selected_renderer, golden, payload):
    result = copy.deepcopy(payload)
    changes = []
    for index, record in enumerate(payload['records']):
        if record.get('disposition') != 'disclosed':
            continue
        cells = record.get('cell_keys') or []
        names = selected_renderer._copied_named_operands(golden, cells)
        if not names:
            continue
        before = original_renderer.copy_lock_note(golden, cells)
        after = selected_renderer.copy_lock_note(golden, cells)
        if not before or before == after:
            continue
        if selected_renderer._cell_copy_lock_note(golden, cells) != before:
            raise ValueError('selected renderer changed original cell-copy semantics')
        steps = record.get('fields', {}).get('steps')
        if not isinstance(steps, str) or not steps.endswith(before) or steps.count(before) != 1:
            raise ValueError('source copy note is absent, changed or ambiguous')
        result['records'][index]['fields']['steps'] = steps[:-len(before)] + after
        changes.append(dict(index=index, cells=list(cells), names=names,
                            original_note=before, corrected_note=after))
    if not changes:
        raise ValueError('no source-proved named-copy correction')
    if original_renderer.agent_records(payload['records']) != payload['agent_records']:
        raise ValueError('original rendered record projection changed')
    result['agent_records'] = selected_renderer.agent_records(result['records'])
    return result, changes

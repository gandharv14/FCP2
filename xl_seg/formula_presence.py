"""Prospective closed COUNTA proof; no source edits, approval or task writes.

A formula occupies its cell regardless of its result (including empty text or
errors). The current evaluator values map stores computed state, not cell deletion.
Explicit override mappings conservatively disable this proof.
Only a complete, expanded static range containing ordinary formula cells is
recognized. Primitive, absent, opaque and array cells retain the old path.
"""
import re
from xl_seg import model

LIMIT = 512
REFERENCE = re.compile(r"(?:(?:'((?:[^']|'')+)'|([A-Za-z_][A-Za-z0-9_ .-]*))!)?(\$?[A-Za-z]{1,3}\$?[1-9][0-9]*):(\$?[A-Za-z]{1,3}\$?[1-9][0-9]*)")

def projection(e, node):
    if node.kind != 'op' or node.op != 'COUNTA' or str(node.arity) != '1' or node.range_truncated:
        return None
    if not (node.expr or '').startswith('COUNTA(') or not node.expr.endswith(')'):
        return None
    expression = node.expr[7:-1]
    match = REFERENCE.fullmatch(expression)
    if match is None:
        return None
    sheet = (match[1].replace("''", "'") if match[1] else match[2]) or node.sheet
    start, end = match[3].replace('$', ''), match[4].replace('$', '')
    a, b = model.split_ref(sheet+'!'+start), model.split_ref(sheet+'!'+end)
    if a is None or b is None or not (1 <= a[1] <= b[1] <= 1048576 and 1 <= a[2] <= b[2] <= 16384):
        return None
    if (b[1]-a[1]+1)*(b[2]-a[2]+1) > LIMIT:
        return None
    refs = tuple(model.range_members(sheet, start+':'+end))
    overrides = getattr(e, 'overrides', {})
    if not isinstance(overrides, dict) or any(ref in overrides for ref in refs):
        return None
    edges = list(e.graph.in_edges.get(node.id, ()))
    if len(edges) != len(refs) or {x.source for x in edges} != set(refs):
        return None
    if any(x.arg_index != 0 or x.target != node.id or x.via_range != expression or x.ref != expression for x in edges):
        return None
    specs = e._arg_specs(node)
    if len(specs) != 1 or specs[0][0] != 'span' or tuple(specs[0][1]) != refs:
        return None
    for ref in refs:
        cell = e.graph.nodes.get(ref)
        if (cell is None or cell.kind != 'formula' or not cell.formula.startswith('=')
                or cell.parse_status != 'ok' or cell.parse_error or cell.range_truncated
                or cell.array_formula or cell.array_ref or cell.array_anchor or cell.array_span
                or e.graph.root_of(ref) is None):
            return None
    return {'schema_version': 'static-formula-counta-presence/v1', 'operation': node.id,
            'source_range': expression, 'formula_cells': list(refs), 'count': len(refs),
            'value_dependencies': [], 'cached_values_used': False, 'lane_approval': False}

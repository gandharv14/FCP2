"""Original primitive types and bounded static range endpoints stay faithful."""
import pytest
from xl_seg import evaluate as candidate, project
from xl_seg.model import Node, Edge, Graph
import xl_ast_graph as candidate_parser

def node(key, kind, value="", value_type="", op=""):
    owner = "Sheet!B1" if kind == "op" else ""
    return Node(id=key, kind=kind, sheet="Sheet", coordinate=key.split("!")[-1],
        row=None if owner else 1, col=None if owner else (2 if kind == "formula" else 1),
        owner=owner, op=op, op_kind="func" if op == "SUM" else "infix",
        arity="", expr="", label="", formula="=SUM(A1,A2)" if kind == "formula" else "",
        value=value, in_cycle=False, value_type=value_type)


def run(module, values, op):
    primitives = [node(f"Sheet!A{i+1}", "label" if typ == "str" else "input", raw, typ)
                  for i, (raw, typ) in enumerate(values)]
    operation = node("Sheet!B1#0:op", "op", op=op)
    target = node("Sheet!B1", "formula", "99999", "int")
    edges = [Edge(source=n.id, target=operation.id, role="arg", arg_index=i,
                  op="", cell=target.id, ref=n.id, via_range="", cross_sheet=False)
             for i, n in enumerate(primitives)]
    edges.append(Edge(source=operation.id, target=target.id, role="result", arg_index=0,
                      op="", cell=target.id, ref="", via_range="", cross_sheet=False))
    graph = Graph("synthetic", {n.id:n for n in [*primitives, operation, target]}, edges)
    for e in edges:
        graph.in_edges.setdefault(e.target, []).append(e)
        graph.out_edges.setdefault(e.source, []).append(e)
    ev = module.Evaluator(graph, project.build(graph), strict_proof=True,
                          calculation=module.CalculationMetadata(available=True, iterate=False))
    return ev.run({n.id for n in primitives})




def test_sum_ignores_original_text_and_never_uses_target_cache():
    result = run(candidate, [("2", "int"), ("\u00a03", "str")], "SUM")
    assert result.values["Sheet!A2"] == "\u00a03"
    assert result.values["Sheet!B1"] == 2
    assert not result.unresolved




@pytest.mark.parametrize("raw,expected", [("True", 10), ("False", 0), ("TRUE", 10), ("FALSE", 0), ("1", 10), ("0", 0)])
def test_boolean_primitive_retains_logical_type(raw, expected):
    assert run(candidate, [(raw, "bool"), ("10", "int")], "*").values["Sheet!B1"] == expected


@pytest.mark.parametrize("text", ["TRUE", "False", "2026-09-08T00:00:00", "42", ""])
def test_typed_text_is_not_reinterpreted_as_boolean_date_or_number(text):
    assert candidate.primitive_value(node("S!A1", "label", text, "str")) == text


def test_invalid_boolean_metadata_fails_closed():
    assert isinstance(candidate.primitive_value(node("S!A1", "input", "maybe", "bool")), candidate.Unresolved)




@pytest.mark.parametrize("formula,span", [
    ("=SUM(A1:OFFSET(C1,0,0))", "A1:C1"),
    ("=SUM($A1:OFFSET($C1,0,0))", "$A1:$C1"),
    ("=SUM(A$1:OFFSET(C$1,0.0,0E0))", "A$1:C$1"),
])
def test_static_zero_offset_endpoint_retains_exact_operation(formula, span):
    root = candidate_parser.parse_formula(formula)
    assert root.kind == "func" and root.name == "SUM"
    child = root.args[0]
    assert child.kind == "infix" and child.name == ":"
    assert child.args[0].name + ":" + child.args[1].args[0].name == span
    assert child.args[1].kind == "func" and child.args[1].name == "OFFSET"


@pytest.mark.parametrize("formula", [
    "=SUM(A1:OFFSET(C1,D1,0))",
    "=SUM(A1:OFFSET(C1,0,0,2,1))", "=SUM(A1:OFFSET(C1,TRUE,0))",
    "=SUM(A1:OFFSET('Other'!C1,0,0))", "=SUM(A1:OFFSET(C1:C2,0,0))",
])
def test_unproved_or_shape_changing_endpoint_is_not_normalized(formula):
    child = candidate_parser.parse_formula(formula).args[0]
    assert child.kind == "func" and child.name.endswith(":OFFSET")


def test_ordinary_dynamic_offset_remains_unchanged():
    child = candidate_parser.parse_formula("=SUM(OFFSET(A1,B1,0))").args[0]
    assert child.kind == "func" and child.name == "OFFSET"

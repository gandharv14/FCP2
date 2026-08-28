from __future__ import annotations

import json
from types import SimpleNamespace

from xl_ast_graph import role_for
from xl_input_mask import stabilized_proof_inputs
from xl_seg import lineage, project
from xl_seg.evaluate import CalculationMetadata, EvalResult, Evaluator, Unresolved
from xl_seg.model import Edge, Graph, Node, split_ref
from xl_seg.proof import load_contract
from xl_segment import stabilize_runtime_proof


def _cell(ref, kind, value, formula=""):
    sheet, row, col = split_ref(ref)
    return Node(
        id=ref,
        kind=kind,
        sheet=sheet,
        coordinate=ref.rpartition("!")[2],
        row=row,
        col=col,
        owner="",
        op="",
        op_kind="",
        arity="",
        expr="",
        label=ref,
        formula=formula,
        value=str(value),
        in_cycle=False,
    )


def _ast(node_id, owner, kind, *, op="", arity="", value="", expr=""):
    sheet = owner.rpartition("!")[0]
    return Node(
        id=node_id,
        kind=kind,
        sheet=sheet,
        coordinate="",
        row=None,
        col=None,
        owner=owner,
        op=op,
        op_kind="func" if kind == "op" else "const",
        arity=str(arity),
        expr=expr,
        label=op or value,
        formula="",
        value=str(value),
        in_cycle=False,
    )


def _edge(source, target, index=0, *, role="arg", op="", via=""):
    return Edge(
        source=source,
        target=target,
        role=role,
        arg_index=index,
        op=op,
        cell=target.split("#", 1)[0],
        ref="",
        via_range=via,
        cross_sheet=False,
    )


def _graph(nodes, edges):
    graph = Graph("book", {node.id: node for node in nodes}, edges)
    for edge in edges:
        graph.in_edges.setdefault(edge.target, []).append(edge)
        graph.out_edges.setdefault(edge.source, []).append(edge)
    return graph, project.build(graph)


CALC = CalculationMetadata(available=True, iterate=False)


def test_formula_literal_is_computed_through_ast_not_seeded():
    formula = _cell("Sheet!B1", "formula", 5, "=5")
    const = _ast("Sheet!B1#0:const", formula.id, "const", value="5")
    graph, cg = _graph(
        [formula, const],
        [_edge(const.id, formula.id, role="constant")],
    )

    result = Evaluator(
        graph, cg, strict_proof=True, calculation=CALC
    ).run({formula.id})

    assert result.values[formula.id] == 5
    assert result.seeded_cells == set()
    assert result.seed_provenance["categories"]["formula_cache_rejected"] == {
        "count": 1,
        "sample": [formula.id],
    }
    assert result.oracle_accesses == {}


def test_multistep_offset_closure_separates_address_origins():
    selector = _cell("Sheet!A1", "input", 1)
    outer_base = _cell("Sheet!B1", "input", 999)
    inner = _cell("Sheet!C1", "formula", 42, "=OFFSET(D1,0,0)")
    value = _cell("Sheet!D1", "input", 42)
    output = _cell("Sheet!E1", "formula", 42, "=OFFSET(B1,0,A1)")
    zero1 = _ast("Sheet!C1#0:const", inner.id, "const", value="0")
    zero2 = _ast("Sheet!C1#1:const", inner.id, "const", value="0")
    inner_op = _ast(
        "Sheet!C1#2:OFFSET", inner.id, "op", op="OFFSET", arity=3,
        expr="OFFSET(D1,0,0)",
    )
    outer_zero = _ast("Sheet!E1#0:const", output.id, "const", value="0")
    outer_op = _ast(
        "Sheet!E1#1:OFFSET", output.id, "op", op="OFFSET", arity=3,
        expr="OFFSET(B1,0,A1)",
    )
    graph, cg = _graph(
        [
            selector, outer_base, inner, value, output,
            zero1, zero2, inner_op, outer_zero, outer_op,
        ],
        [
            _edge(value.id, inner_op.id, 0, role="address", op="OFFSET"),
            _edge(zero1.id, inner_op.id, 1, op="OFFSET"),
            _edge(zero2.id, inner_op.id, 2, op="OFFSET"),
            _edge(inner_op.id, inner.id, role="result", op="OFFSET"),
            _edge(outer_base.id, outer_op.id, 0, role="address", op="OFFSET"),
            _edge(outer_zero.id, outer_op.id, 1, op="OFFSET"),
            _edge(selector.id, outer_op.id, 2, op="OFFSET"),
            _edge(outer_op.id, output.id, role="result", op="OFFSET"),
        ],
    )

    result, proof_inputs, proof_radj, proof = stabilize_runtime_proof(
        graph, cg, {selector.id, outer_base.id}, {output.id}, calculation=CALC
    )

    assert result.values[output.id] == 42
    assert proof_inputs == {selector.id, value.id}
    assert outer_base.id in cg.radj[output.id]  # structural partition is unchanged
    assert proof_radj[output.id] == {selector.id, inner.id}
    assert proof_radj[inner.id] == {value.id}
    assert outer_base.id not in proof_radj[output.id]
    assert proof["runtime_address_radj"][output.id] == [outer_base.id]
    assert role_for("func", "OFFSET", 0, 3) == "address"
    assert proof["closure"]["stabilized"] is True


def test_dynamic_indirect_discovers_computed_target_and_seed():
    address = _cell("Sheet!A1", "input", "C1")
    target = _cell("Sheet!C1", "input", 9)
    output = _cell("Sheet!D1", "formula", 9, "=INDIRECT(A1)")
    op = _ast(
        "Sheet!D1#0:INDIRECT", output.id, "op", op="INDIRECT", arity=1,
        expr="INDIRECT(A1)",
    )
    graph, cg = _graph(
        [address, target, output, op],
        [
            _edge(address.id, op.id, 0, op="INDIRECT"),
            _edge(op.id, output.id, role="result", op="INDIRECT"),
        ],
    )

    result, proof_inputs, proof_radj, proof = stabilize_runtime_proof(
        graph, cg, {address.id}, {output.id}, calculation=CALC
    )

    assert result.values[output.id] == 9
    assert proof_inputs == {address.id, target.id}
    assert proof_radj[output.id] == {address.id, target.id}
    assert proof["resolved_targets"][output.id] == [target.id]


def test_index_match_prunes_unselected_result_cells_and_unrelated_seeds():
    key = _cell("Sheet!A1", "input", "b")
    results = [_cell(f"Sheet!B{i}", "input", i * 10) for i in range(1, 4)]
    selectors = [
        _cell(f"Sheet!C{i}", "input", value)
        for i, value in enumerate(("a", "b", "c"), 1)
    ]
    unrelated = _cell("Sheet!Z9", "input", 999)
    output = _cell("Sheet!D1", "formula", 20, "=INDEX(B1:B3,MATCH(A1,C1:C3,0))")
    result_range = _ast(
        "Sheet!B1:B3", output.id, "range", value="3", expr="B1:B3"
    )
    selector_range = _ast(
        "Sheet!C1:C3", output.id, "range", value="3", expr="C1:C3"
    )
    zero = _ast("Sheet!D1#0:const", output.id, "const", value="0")
    match = _ast("Sheet!D1#1:MATCH", output.id, "op", op="MATCH", arity=3)
    index = _ast("Sheet!D1#2:INDEX", output.id, "op", op="INDEX", arity=2)
    graph, cg = _graph(
        [
            key, *results, *selectors, unrelated, output,
            result_range, selector_range, zero, match, index,
        ],
        [
            _edge(key.id, match.id, 0, op="MATCH"),
            _edge(selector_range.id, match.id, 1, op="MATCH", via="C1:C3"),
            _edge(zero.id, match.id, 2, op="MATCH"),
            _edge(result_range.id, index.id, 0, op="INDEX", via="B1:B3"),
            _edge(match.id, index.id, 1, op="INDEX"),
            _edge(index.id, output.id, role="result", op="INDEX"),
        ],
    )
    declared = {key.id, unrelated.id, *(node.id for node in results + selectors)}

    first = stabilize_runtime_proof(
        graph, cg, declared, {output.id}, calculation=CALC
    )
    second = stabilize_runtime_proof(
        graph, cg, declared, {output.id}, calculation=CALC
    )
    result, proof_inputs, proof_radj, proof = first

    assert result.values[output.id] == 20
    assert results[1].id in proof_radj[output.id]
    assert results[0].id not in proof_radj[output.id]
    assert results[2].id not in proof_radj[output.id]
    assert unrelated.id not in proof_inputs
    assert result.seeded_cells == proof_inputs
    assert proof == second[3]
    assert proof["benchmark"]["discovery_probes_enabled"] is False
    assert proof["benchmark"]["scoped_cells"] < proof["benchmark"]["workbook_cells"]
    assert proof["benchmark"]["within_hard_limits"] is True


def test_sumif_records_all_conditions_and_only_matched_summands():
    sums = [_cell(f"Sheet!B{i}", "input", i * 10) for i in range(1, 4)]
    flags = [
        _cell(f"Sheet!C{i}", "input", value)
        for i, value in enumerate(("yes", "no", "yes"), 1)
    ]
    output = _cell("Sheet!D1", "formula", 40, '=SUMIF(C1:C3,"yes",B1:B3)')
    sum_range = _ast("Sheet!B1:B3", output.id, "range", value="3")
    flag_range = _ast("Sheet!C1:C3", output.id, "range", value="3")
    criterion = _ast("Sheet!D1#0:const", output.id, "const", value="yes")
    op = _ast("Sheet!D1#1:SUMIF", output.id, "op", op="SUMIF", arity=3)
    graph, cg = _graph(
        [*sums, *flags, output, sum_range, flag_range, criterion, op],
        [
            _edge(flag_range.id, op.id, 0, op="SUMIF", via="C1:C3"),
            _edge(criterion.id, op.id, 1, op="SUMIF"),
            _edge(sum_range.id, op.id, 2, op="SUMIF", via="B1:B3"),
            _edge(op.id, output.id, role="result", op="SUMIF"),
        ],
    )

    result, _, radj, _ = stabilize_runtime_proof(
        graph,
        cg,
        {node.id for node in sums + flags},
        {output.id},
        calculation=CALC,
    )

    assert result.values[output.id] == 40
    assert set(node.id for node in flags) <= radj[output.id]
    assert sums[0].id in radj[output.id]
    assert sums[2].id in radj[output.id]
    assert sums[1].id not in radj[output.id]


def test_unresolved_selectors_conservatively_discover_all_dynamic_branches():
    condition = _cell("Sheet!A1", "formula", 1, "=1")
    choices = [_cell(f"Sheet!B{i}", "input", i) for i in range(1, 5)]
    if_output = _cell("Sheet!C1", "formula", 1, "=IF(A1,B1,B2)")
    choose_output = _cell("Sheet!C2", "formula", 1, "=CHOOSE(A1,B1,B2)")
    ifs_output = _cell(
        "Sheet!C3", "formula", 1, "=IFS(A1,B1,B2,B3)"
    )
    if_op = _ast("Sheet!C1#IF", if_output.id, "op", op="IF", arity=3)
    choose_op = _ast(
        "Sheet!C2#CHOOSE", choose_output.id, "op", op="CHOOSE", arity=3
    )
    ifs_op = _ast(
        "Sheet!C3#IFS", ifs_output.id, "op", op="IFS", arity=4
    )
    graph, cg = _graph(
        [condition, *choices, if_output, choose_output, ifs_output,
         if_op, choose_op, ifs_op],
        [
            *[
                _edge(source, if_op.id, index, op="IF")
                for index, source in enumerate(
                    [condition.id, choices[0].id, choices[1].id]
                )
            ],
            _edge(if_op.id, if_output.id, role="result"),
            *[
                _edge(source, choose_op.id, index, op="CHOOSE")
                for index, source in enumerate(
                    [condition.id, choices[0].id, choices[1].id]
                )
            ],
            _edge(choose_op.id, choose_output.id, role="result"),
            *[
                _edge(source, ifs_op.id, index, op="IFS")
                for index, source in enumerate(
                    [condition.id, choices[0].id, choices[1].id, choices[2].id]
                )
            ],
            _edge(ifs_op.id, ifs_output.id, role="result"),
        ],
    )
    evaluator = Evaluator(graph, cg, strict_proof=True, calculation=CALC)
    evaluator.values[condition.id] = Unresolved("uncomputed-precedent")

    _, radj = evaluator._active_graph(cg.info)

    assert radj[if_output.id] == {
        condition.id, choices[0].id, choices[1].id
    }
    assert radj[choose_output.id] == {
        condition.id, choices[0].id, choices[1].id
    }
    assert radj[ifs_output.id] == {
        condition.id, choices[0].id, choices[1].id, choices[2].id
    }


class _AlternatingEvaluator:
    calls = 0

    def __init__(self, graph, cg, **kwargs):
        self.graph = graph

    def run(self, inputs):
        type(self).calls += 1
        source = "Sheet!A1" if type(self).calls % 2 else "Sheet!B1"
        return EvalResult(
            values={},
            unresolved={},
            iterated=[],
            coverage={},
            runtime_radj={"Sheet!C1": {source}},
            seeded_cells=set(inputs),
            strict_proof=True,
        )


def test_nonstabilizing_closure_is_bounded_and_diagnostic():
    a = _cell("Sheet!A1", "input", 1)
    b = _cell("Sheet!B1", "input", 2)
    output = _cell("Sheet!C1", "formula", 1, "=A1")
    graph, cg = _graph([a, b, output], [])
    _AlternatingEvaluator.calls = 0

    _, _, _, proof = stabilize_runtime_proof(
        graph,
        cg,
        {a.id, b.id},
        {output.id},
        calculation=CALC,
        max_passes=3,
        evaluator_factory=_AlternatingEvaluator,
    )

    assert proof["closure"] == {
        "stabilized": False,
        "passes": 3,
        "max_passes": 3,
        "diagnostic": "runtime_dependency_closure_not_stabilized",
        "history": proof["closure"]["history"],
    }


def test_mask_and_task_load_stabilized_proof_with_legacy_fallback(tmp_path):
    seg_dir = tmp_path / "0391"
    seg_dir.mkdir()
    proof = {
        "declared_static_inputs": ["Sheet!A1", "Sheet!B1"],
        "effective_inputs": ["Sheet!A1"],
        "runtime_radj": {"Sheet!C1": ["Sheet!A1"]},
        "closure": {"stabilized": True},
    }
    (seg_dir / "segments.json").write_text(
        json.dumps({"proof": proof}), encoding="utf-8"
    )

    assert stabilized_proof_inputs(seg_dir) == {"Sheet": {(1, 1)}}
    assert load_contract(seg_dir) == proof

    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "segments.json").write_text("{}", encoding="utf-8")
    assert stabilized_proof_inputs(legacy) is None
    assert load_contract(legacy) is None


def test_lineage_cell_trace_consumes_stabilized_graph():
    selected = _cell("Sheet!A1", "input", 1)
    unrelated = _cell("Sheet!B1", "input", 2)
    output = _cell("Sheet!C1", "formula", 1, "=A1")
    graph, cg = _graph(
        [selected, unrelated, output],
        [
            _edge(selected.id, output.id, role="identity"),
            _edge(unrelated.id, output.id, role="arg"),
        ],
    )
    trace = lineage.cell_trace(
        cg,
        {selected.id: 1, output.id: 1},
        output.id,
        {selected.id},
        20,
        adj={selected.id: {output.id}},
        radj={output.id: {selected.id}},
    )

    assert [step["cell"] for step in trace["steps"]] == [
        selected.id,
        output.id,
    ]
    assert trace["steps"][-1]["precedents"] == [selected.id]

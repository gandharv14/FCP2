from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

from xl_ast_graph import role_for
from xl_input_mask import stabilized_proof_inputs
from xl_seg import diagnostics, lineage, project, restriction_cone
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


def _ast(
    node_id,
    owner,
    kind,
    *,
    op="",
    arity="",
    value="",
    expr="",
    op_kind=None,
):
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
        op_kind=op_kind or ("func" if kind == "op" else "const"),
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
    assert proof["resolved_operation_targets"][op.id] == [target.id]


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


def test_optimized_static_source_walk_matches_reference_and_reuses_work():
    left = _cell("Sheet!A1", "input", 2)
    right = _cell("Sheet!B1", "input", 3)
    output = _cell("Sheet!C1", "formula", 5, "=SUM(A1,B1)")
    operation = _ast(
        "Sheet!C1#SUM", output.id, "op", op="SUM", arity=2
    )
    graph, cg = _graph(
        [left, right, output, operation],
        [
            _edge(left.id, operation.id, 0, op="SUM"),
            _edge(right.id, operation.id, 1, op="SUM"),
            _edge(operation.id, output.id, role="result", op="SUM"),
        ],
    )

    reference = Evaluator(
        graph, cg, strict_proof=True, calculation=CALC, optimize=False
    ).run({left.id, right.id})
    optimized = Evaluator(
        graph, cg, strict_proof=True, calculation=CALC, optimize=True
    ).run({left.id, right.id})

    assert optimized.values == reference.values
    assert optimized.runtime_radj == reference.runtime_radj
    assert optimized.resolved_targets == reference.resolved_targets
    assert optimized.resolved_operation_targets == reference.resolved_operation_targets
    assert optimized.runtime_address_radj == reference.runtime_address_radj
    assert optimized.read_attempts == reference.read_attempts
    assert optimized.coverage == reference.coverage
    active = optimized.benchmark["active_graph"]
    assert active["static_source_cache_misses"] == 1
    assert active["static_source_cache_hits"] >= 1
    assert active["reused_initial_graphs"] >= 1
    assert active["calls"] < reference.benchmark["active_graph"]["calls"]


def test_nested_dynamic_selector_is_not_static_cached():
    condition = _cell("Sheet!A1", "input", 1)
    selected = _cell("Sheet!B1", "input", 10)
    unselected = _cell("Sheet!B2", "input", 20)
    output = _cell("Sheet!C1", "formula", 10, "=SUM(IF(A1,B1,B2))")
    selector = _ast("Sheet!C1#IF", output.id, "op", op="IF", arity=3)
    wrapper = _ast("Sheet!C1#SUM", output.id, "op", op="SUM", arity=1)
    graph, cg = _graph(
        [condition, selected, unselected, output, selector, wrapper],
        [
            _edge(condition.id, selector.id, 0, op="IF"),
            _edge(selected.id, selector.id, 1, op="IF"),
            _edge(unselected.id, selector.id, 2, op="IF"),
            _edge(selector.id, wrapper.id, 0, op="SUM"),
            _edge(wrapper.id, output.id, role="result", op="SUM"),
        ],
    )

    evaluator = Evaluator(
        graph, cg, strict_proof=True, calculation=CALC, optimize=True
    )
    result = evaluator.run({condition.id, selected.id, unselected.id})

    assert result.values[output.id] == 10
    assert result.runtime_radj[output.id] == {condition.id, selected.id}
    assert unselected.id not in result.runtime_radj[output.id]
    assert evaluator._source_walk_is_pure(wrapper.id) is False
    assert result.benchmark["active_graph"]["static_source_cache_hits"] == 0


def test_source_walk_allowlist_normalizes_prefixes_and_rejects_unknown_ops():
    source = _cell("Sheet!A1", "input", 1)
    output = _cell("Sheet!B1", "formula", 1, "=_xlfn.SUM(A1)")
    prefixed = _ast(
        "Sheet!B1#SUM", output.id, "op", op="_XLFN.SUM", arity=1
    )
    unknown = _ast(
        "Sheet!B1#FUTURE", output.id, "op", op="FUTURE.OP", arity=1
    )
    graph, cg = _graph(
        [source, output, prefixed, unknown],
        [
            _edge(source.id, prefixed.id, 0, op="_XLFN.SUM"),
            _edge(source.id, unknown.id, 0, op="FUTURE.OP"),
        ],
    )
    evaluator = Evaluator(graph, cg)

    assert evaluator._source_walk_is_pure(prefixed.id) is True
    assert evaluator._source_walk_is_pure(unknown.id) is False


@pytest.mark.parametrize(
    "operation",
    [
        "IF",
        "IFS",
        "_XLFN.IFS",
        "CHOOSE",
        "IFERROR",
        "SUMIF",
        "SUMIFS",
        "VLOOKUP",
        "HLOOKUP",
        "XLOOKUP",
        "_XLFN.XLOOKUP",
        "LOOKUP",
        "MATCH",
        "INDEX",
        "OFFSET",
        "INDIRECT",
    ],
)
def test_dynamic_source_walk_operations_default_to_uncacheable(operation):
    output = _cell("Sheet!B1", "formula", 1, f"={operation}()")
    dynamic = _ast(
        f"Sheet!B1#{operation}", output.id, "op", op=operation, arity=0
    )
    graph, cg = _graph([output, dynamic], [])

    assert Evaluator(graph, cg)._source_walk_is_pure(dynamic.id) is False


def test_static_subtotal_is_cacheable_but_name_nodes_are_not():
    source = _cell("Sheet!A1", "input", 1)
    output = _cell("Sheet!B1", "formula", 1, "=SUBTOTAL(9,A1)")
    subtotal = _ast(
        "Sheet!B1#SUBTOTAL", output.id, "op", op="SUBTOTAL", arity=1
    )
    name = _ast("Sheet!B1#NAME", output.id, "name")
    graph, cg = _graph(
        [source, output, subtotal, name],
        [_edge(source.id, subtotal.id, 0, op="SUBTOTAL")],
    )
    evaluator = Evaluator(graph, cg)

    assert evaluator._source_walk_is_pure(subtotal.id) is True
    assert evaluator._source_walk_is_pure(name.id) is False


def test_component_plan_cache_keys_isolated_cell_universe():
    evaluator = Evaluator(graph=None, cg=None, optimize=True)

    first = evaluator._component_plan(["Sheet!A1"], {})
    second = evaluator._component_plan(["Sheet!B1"], {})
    repeated = evaluator._component_plan(["Sheet!A1"], {})

    assert first[0] == [("Sheet!A1",)]
    assert second[0] == [("Sheet!B1",)]
    assert repeated == first
    assert evaluator._benchmark["component_plan"]["cache_hits"] == 1


def test_clock_is_injected_for_volatile_date_functions():
    fixed = datetime(2026, 8, 31, 6, 30, 15)
    evaluator = Evaluator(graph=None, cg=None, clock=lambda: fixed)

    assert evaluator._fn_today([]) == float(
        int(evaluator._fn_now([]))
    )
    assert evaluator._fn_now([]) > evaluator._fn_today([])


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
        "targets_stable": False,
        "passes": 3,
        "max_passes": 3,
        "diagnostic": "runtime_dependency_closure_not_stabilized",
        "history": proof["closure"]["history"],
    }


def _restricted_certificate_source(tmp_path, monkeypatch, events):
    profile = {
        "schema_version": "source-restriction-profile/v2",
        "allowlist": [
            "confirmed_false_external_detection",
            "worksheet_cell_filename",
            "worksheet_indirect_a1",
            "worksheet_now",
            "worksheet_offset",
            "worksheet_today",
        ],
    }
    health = {
        "route": "restricted_pass",
        "policy_version": "source-recalc-policy/v2",
        "report_sha256": "1" * 64,
        "restriction_profile": profile,
        "restriction_events": events,
        "restriction_events_sha256": restriction_cone.object_hash(events),
    }
    result = {"restriction": {"profile": profile, "restriction_events": events}}
    source_dir = tmp_path / "source-generation"
    source_dir.mkdir(parents=True)
    (source_dir / "health.json").write_text(json.dumps(health), encoding="utf-8")
    (source_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")
    (source_dir / "generation-manifest.json").write_text(
        json.dumps({
            "layout": {"ast_directory": "ast", "workbook_id": "book"}
        }),
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "source-generation/v2",
        "generation_id": "a" * 64,
        "bindings": {
            "source_sha256": "b" * 64,
            "health_sha256": "c" * 64,
            "health_report_sha256": health["report_sha256"],
            "restriction_evidence_sha256": "d" * 64,
            "restriction_events_sha256": health["restriction_events_sha256"],
        },
    }
    monkeypatch.setattr(
        "xl_source_publication.validate_source_generation",
        lambda path: manifest,
    )
    return source_dir


def _passing_certificate_verification(proof, ordered_outputs=None):
    fingerprints = {
        "source": {
            "path": "book.xlsx",
            "available": True,
            "algorithm": "sha256",
            "sha256": "e" * 64,
            "size_bytes": 1,
        }
    }
    return {
        "status": "pass",
        "disposition": "pass",
        "blocking_reasons": [],
        "skipped": False,
        "passed": True,
        "provenance": {
            "proof": {"strict": True},
            "runtime": {"closure": proof["closure"]},
        },
        "counts": {"cache_reads": {"proof": 0}},
        "fingerprints": fingerprints,
        "ordered_output_cells": list(ordered_outputs or ["Sheet!D1"]),
    }


def test_restriction_events_are_covered_once_and_time_is_outside_cone(
    tmp_path, monkeypatch
):
    events = [
        {
            "allowed": True,
            "event": "volatile_function",
            "function": "TODAY",
            "location": "Sheet!B1",
            "reason": "worksheet_today",
            "scope": "worksheet",
            # Source-health offsets are measured in OOXML formula text, which
            # omits the AST model's leading "=".
            "token_offset": 0,
        },
        {
            "allowed": True,
            "event": "false_external_detection",
            "location": "Sheet!C1",
            "reason": "confirmed_false_external_detection",
            "scope": "worksheet",
            "token": "[label]",
            "token_offset": 1,
        },
    ]
    source_dir = _restricted_certificate_source(tmp_path, monkeypatch, events)
    source = _cell("Sheet!A1", "input", 1)
    today_host = _cell("Sheet!B1", "formula", 1, "=TODAY()")
    false_host = _cell("Sheet!C1", "formula", 1, '="[label]"')
    output = _cell("Sheet!D1", "formula", 1, "=A1")
    today = _ast(
        "Sheet!B1#TODAY",
        today_host.id,
        "op",
        op="TODAY",
        arity=0,
        expr="TODAY()",
    )
    graph, _ = _graph(
        [source, today_host, false_host, output, today],
        [_edge(today.id, today_host.id, role="result")],
    )
    proof = {
        "runtime_radj": {output.id: [source.id]},
        "resolved_targets": {},
        "resolved_operation_targets": {},
        "closure": {"stabilized": True, "targets_stable": True},
    }
    verification = _passing_certificate_verification(proof, [output.id])

    certificate = restriction_cone.build_certificate(
        source_generation_dir=source_dir,
        graph=graph,
        proof=proof,
        verification=verification,
        ordered_outputs=[output.id],
        segmentation_fingerprints=verification["fingerprints"],
    )

    assert certificate["event_count"] == 2
    assert len({item["event_id"] for item in certificate["events"]}) == 2
    assert certificate["events"][0]["in_output_cone"] is False
    assert certificate["events"][0]["disposition"] == (
        "allowed_only_outside_output_cone"
    )


def test_shared_offset_follower_requires_its_own_health_event(
    tmp_path, monkeypatch
):
    master_event = {
        "allowed": True,
        "event": "volatile_function",
        "function": "OFFSET",
        "location": "Sheet!B1",
        "reason": "worksheet_offset",
        "scope": "worksheet",
        "token_offset": 0,
    }
    source_dir = _restricted_certificate_source(
        tmp_path, monkeypatch, [master_event]
    )
    master = _cell("Sheet!B1", "formula", 1, "=OFFSET(A1,0,0)")
    follower = _cell("Sheet!B2", "formula", 2, "=OFFSET(A2,0,0)")
    target = _cell("Sheet!A2", "input", 2)
    master_op = _ast(
        "Sheet!B1#0:OFFSET",
        master.id,
        "op",
        op="OFFSET",
        arity=3,
        expr="OFFSET(A1,0,0)",
    )
    follower_op = _ast(
        "Sheet!B2#0:OFFSET",
        follower.id,
        "op",
        op="OFFSET",
        arity=3,
        expr="OFFSET(A2,0,0)",
    )
    graph, _ = _graph(
        [master, follower, target, master_op, follower_op],
        [
            _edge(master_op.id, master.id, role="result", op="OFFSET"),
            _edge(follower_op.id, follower.id, role="result", op="OFFSET"),
        ],
    )
    proof = {
        "runtime_radj": {follower.id: [target.id]},
        "resolved_targets": {follower.id: [target.id]},
        "resolved_operation_targets": {follower_op.id: [target.id]},
        "closure": {"stabilized": True, "targets_stable": True},
    }
    verification = _passing_certificate_verification(proof, [follower.id])

    assert follower.id in restriction_cone._proof_cone(
        [follower.id], proof["runtime_radj"]
    )
    assert master.id not in restriction_cone._proof_cone(
        [follower.id], proof["runtime_radj"]
    )
    with pytest.raises(
        restriction_cone.RestrictionConeError,
        match="restricted volatile AST operations lack one-to-one",
    ):
        restriction_cone.build_certificate(
            source_generation_dir=source_dir,
            graph=graph,
            proof=proof,
            verification=verification,
            ordered_outputs=[follower.id],
            segmentation_fingerprints=verification["fingerprints"],
        )


def _offset_certificate_graph(*, rows_node_kind="const", height=1):
    base = _cell("Sheet!A1", "input", 0)
    rows = (
        _cell("Sheet!B1", "input", 1)
        if rows_node_kind == "input"
        else _ast("Sheet!C1#rows", "Sheet!C1", "const", value="0")
    )
    columns = _ast("Sheet!C1#cols", "Sheet!C1", "const", value="1")
    height_node = _ast(
        "Sheet!C1#height", "Sheet!C1", "const", value=str(height)
    )
    width = _ast("Sheet!C1#width", "Sheet!C1", "const", value="1")
    output = _cell("Sheet!C1", "formula", 9, "=OFFSET(A1,0,1,1,1)")
    target = _cell("Sheet!B1", "input", 9)
    op = _ast(
        "Sheet!C1#OFFSET",
        output.id,
        "op",
        op="OFFSET",
        arity=5,
        expr="OFFSET(A1,0,1,1,1)",
    )
    graph, _ = _graph(
        [base, rows, columns, height_node, width, output, target, op],
        [
            _edge(base.id, op.id, 0, role="address", op="OFFSET"),
            _edge(rows.id, op.id, 1, op="OFFSET"),
            _edge(columns.id, op.id, 2, op="OFFSET"),
            _edge(height_node.id, op.id, 3, op="OFFSET"),
            _edge(width.id, op.id, 4),
            _edge(op.id, output.id, role="result", op="OFFSET"),
        ],
    )
    return graph, output, target


def _two_offset_certificate_graph():
    base = _cell("Sheet!A1", "input", 0)
    first_target = _cell("Sheet!B1", "input", 4)
    second_target = _cell("Sheet!C1", "input", 5)
    output = _cell(
        "Sheet!D1",
        "formula",
        9,
        "=OFFSET(A1,0,1)+OFFSET(A1,0,2)",
    )
    first_row = _ast("Sheet!D1#0:const", output.id, "const", value="0")
    first_col = _ast("Sheet!D1#1:const", output.id, "const", value="1")
    first_op = _ast(
        "Sheet!D1#2:OFFSET",
        output.id,
        "op",
        op="OFFSET",
        arity=3,
        expr="OFFSET(A1,0,1)",
    )
    second_row = _ast("Sheet!D1#3:const", output.id, "const", value="0")
    second_col = _ast("Sheet!D1#4:const", output.id, "const", value="2")
    second_op = _ast(
        "Sheet!D1#5:OFFSET",
        output.id,
        "op",
        op="OFFSET",
        arity=3,
        expr="OFFSET(A1,0,2)",
    )
    root = _ast(
        "Sheet!D1#6:+", output.id, "op", op="+", arity=2,
        expr="(OFFSET(A1,0,1)+OFFSET(A1,0,2))",
        op_kind="infix",
    )
    graph, _ = _graph(
        [
            base,
            first_target,
            second_target,
            output,
            first_row,
            first_col,
            first_op,
            second_row,
            second_col,
            second_op,
            root,
        ],
        [
            _edge(base.id, first_op.id, 0, role="address", op="OFFSET"),
            _edge(first_row.id, first_op.id, 1, op="OFFSET"),
            _edge(first_col.id, first_op.id, 2, op="OFFSET"),
            _edge(base.id, second_op.id, 0, role="address", op="OFFSET"),
            _edge(second_row.id, second_op.id, 1, op="OFFSET"),
            _edge(second_col.id, second_op.id, 2, op="OFFSET"),
            _edge(first_op.id, root.id, 0, op="+"),
            _edge(second_op.id, root.id, 1, op="+"),
            _edge(root.id, output.id, role="result", op="+"),
        ],
    )
    return graph, output, first_target, second_target, first_op, second_op


def _two_offset_events():
    formula = "OFFSET(A1,0,1)+OFFSET(A1,0,2)"
    offsets = [
        index for index in range(len(formula))
        if formula.startswith("OFFSET", index)
    ]
    return [
        {
            "allowed": True,
            "event": "volatile_function",
            "function": "OFFSET",
            "location": "Sheet!D1",
            "reason": "worksheet_offset",
            "scope": "worksheet",
            "token_offset": offset,
        }
        for offset in offsets
    ]


def _two_offset_proof(output, first_target, second_target, first_op, second_op):
    return {
        "runtime_radj": {
            output.id: [first_target.id, second_target.id],
        },
        "resolved_targets": {
            output.id: [first_target.id, second_target.id],
        },
        "resolved_operation_targets": {
            first_op.id: [first_target.id],
            second_op.id: [second_target.id],
        },
        "closure": {"stabilized": True, "targets_stable": True},
    }


def test_evaluator_records_two_dynamic_calls_by_operation():
    graph, output, first, second, first_op, second_op = (
        _two_offset_certificate_graph()
    )
    result, proof_inputs, _, proof = stabilize_runtime_proof(
        graph,
        project.build(graph),
        {first.id, second.id},
        {output.id},
        calculation=CALC,
    )

    assert result.values[output.id] == 9
    assert proof_inputs == {first.id, second.id}
    assert result.resolved_operation_targets == {
        first_op.id: (first.id,),
        second_op.id: (second.id,),
    }
    assert proof["resolved_operation_targets"] == {
        first_op.id: [first.id],
        second_op.id: [second.id],
    }


def test_two_dynamic_calls_require_separate_operation_evidence(
    tmp_path, monkeypatch
):
    events = _two_offset_events()
    source_dir = _restricted_certificate_source(
        tmp_path, monkeypatch, events
    )
    graph, output, first, second, first_op, second_op = (
        _two_offset_certificate_graph()
    )
    proof = _two_offset_proof(output, first, second, first_op, second_op)
    verification = _passing_certificate_verification(proof, [output.id])

    certificate = restriction_cone.build_certificate(
        source_generation_dir=source_dir,
        graph=graph,
        proof=proof,
        verification=verification,
        ordered_outputs=[output.id],
        segmentation_fingerprints=verification["fingerprints"],
    )

    assert [
        record["ast_operation_node"] for record in certificate["events"]
    ] == [first_op.id, second_op.id]
    assert certificate["events"][0]["runtime_targets"] == [first.id]
    assert certificate["events"][1]["runtime_targets"] == [second.id]
    assert all(
        record["stability_equality_check"]["static_runtime_equal"] is True
        for record in certificate["events"]
    )

    mismatched = _two_offset_proof(
        output, first, second, first_op, second_op
    )
    mismatched["resolved_operation_targets"][second_op.id] = [first.id]
    mismatched_verification = _passing_certificate_verification(
        mismatched, [output.id]
    )
    with pytest.raises(
        restriction_cone.RestrictionConeError,
        match="static/runtime targets differ",
    ):
        restriction_cone.build_certificate(
            source_generation_dir=source_dir,
            graph=graph,
            proof=mismatched,
            verification=mismatched_verification,
            ordered_outputs=[output.id],
            segmentation_fingerprints=mismatched_verification["fingerprints"],
        )


def test_duplicate_and_missing_dynamic_operation_mapping_fail(
    tmp_path, monkeypatch
):
    events = _two_offset_events()
    graph, output, first, second, first_op, second_op = (
        _two_offset_certificate_graph()
    )
    proof = _two_offset_proof(output, first, second, first_op, second_op)

    duplicate_events = [events[0], dict(events[1], token_offset=events[0]["token_offset"])]
    duplicate_source = _restricted_certificate_source(
        tmp_path / "duplicate", monkeypatch, duplicate_events
    )
    verification = _passing_certificate_verification(proof, [output.id])
    with pytest.raises(
        restriction_cone.RestrictionConeError, match="mapping is ambiguous"
    ):
        restriction_cone.build_certificate(
            source_generation_dir=duplicate_source,
            graph=graph,
            proof=proof,
            verification=verification,
            ordered_outputs=[output.id],
            segmentation_fingerprints=verification["fingerprints"],
        )

    missing_source = _restricted_certificate_source(
        tmp_path / "missing", monkeypatch, events
    )
    missing = _two_offset_proof(
        output, first, second, first_op, second_op
    )
    del missing["resolved_operation_targets"][second_op.id]
    missing_verification = _passing_certificate_verification(
        missing, [output.id]
    )
    with pytest.raises(
        restriction_cone.RestrictionConeError,
        match="runtime targets were omitted for operation",
    ):
        restriction_cone.build_certificate(
            source_generation_dir=missing_source,
            graph=graph,
            proof=missing,
            verification=missing_verification,
            ordered_outputs=[output.id],
            segmentation_fingerprints=missing_verification["fingerprints"],
        )


def test_tampered_per_operation_certificate_fails_rebuild_validation(
    tmp_path, monkeypatch
):
    events = _two_offset_events()
    source_dir = _restricted_certificate_source(
        tmp_path, monkeypatch, events
    )
    graph, output, first, second, first_op, second_op = (
        _two_offset_certificate_graph()
    )
    proof = _two_offset_proof(output, first, second, first_op, second_op)
    verification = _passing_certificate_verification(proof, [output.id])
    certificate = restriction_cone.build_certificate(
        source_generation_dir=source_dir,
        graph=graph,
        proof=proof,
        verification=verification,
        ordered_outputs=[output.id],
        segmentation_fingerprints=verification["fingerprints"],
    )
    tampered = json.loads(json.dumps(certificate))
    tampered["events"][1]["runtime_targets"] = [first.id]
    tampered["events"][1]["record_sha256"] = restriction_cone.object_hash({
        key: value
        for key, value in tampered["events"][1].items()
        if key != "record_sha256"
    })
    tampered["event_coverage_sha256"] = restriction_cone.object_hash([
        {
            "event_id": record["event_id"],
            "event_sha256": record["event_sha256"],
            "record_sha256": record["record_sha256"],
        }
        for record in tampered["events"]
    ])
    tampered["certificate_sha256"] = restriction_cone._unsigned_hash(
        tampered, "certificate_sha256"
    )
    monkeypatch.setattr(restriction_cone.model, "load", lambda *args: graph)

    with pytest.raises(
        restriction_cone.RestrictionConeError,
        match="does not match exact source and segmentation",
    ):
        restriction_cone.validate_certificate(
            tampered,
            source_generation_dir=source_dir,
            segmentation_artifact={
                "verification": verification,
                "proof": proof,
            },
        )


def test_in_cone_offset_requires_literal_bounded_stable_equal_targets(
    tmp_path, monkeypatch
):
    event = {
        "allowed": True,
        "event": "volatile_function",
        "function": "OFFSET",
        "location": "Sheet!C1",
        "reason": "worksheet_offset",
        "scope": "worksheet",
        "token_offset": 0,
    }
    source_dir = _restricted_certificate_source(
        tmp_path, monkeypatch, [event]
    )
    graph, output, target = _offset_certificate_graph()
    proof = {
        "runtime_radj": {output.id: [target.id]},
        "resolved_targets": {output.id: [target.id]},
        "resolved_operation_targets": {"Sheet!C1#OFFSET": [target.id]},
        "closure": {"stabilized": True, "targets_stable": True},
    }
    verification = _passing_certificate_verification(proof, [output.id])

    certificate = restriction_cone.build_certificate(
        source_generation_dir=source_dir,
        graph=graph,
        proof=proof,
        verification=verification,
        ordered_outputs=[output.id],
        segmentation_fingerprints=verification["fingerprints"],
    )

    record = certificate["events"][0]
    assert record["static_targets"] == [target.id]
    assert record["runtime_targets"] == [target.id]
    assert record["resolution_status"] == "bounded_static_runtime_equal"


def test_dynamic_input_oversize_unstable_and_closed_events_fail(
    tmp_path, monkeypatch
):
    offset_event = {
        "allowed": True,
        "event": "volatile_function",
        "function": "OFFSET",
        "location": "Sheet!C1",
        "reason": "worksheet_offset",
        "scope": "worksheet",
        "token_offset": 0,
    }

    def build(graph, output, target, events, *, stable=True):
        source_dir = _restricted_certificate_source(
            tmp_path / str(len(list(tmp_path.iterdir()))),
            monkeypatch,
            events,
        )
        proof = {
            "runtime_radj": {output.id: [target.id]},
            "resolved_targets": {output.id: [target.id]},
            "resolved_operation_targets": {
                "Sheet!C1#OFFSET": [target.id]
            },
            "closure": {"stabilized": stable, "targets_stable": stable},
        }
        verification = _passing_certificate_verification(proof, [output.id])
        return restriction_cone.build_certificate(
            source_generation_dir=source_dir,
            graph=graph,
            proof=proof,
            verification=verification,
            ordered_outputs=[output.id],
            segmentation_fingerprints=verification["fingerprints"],
        )

    graph, output, target = _offset_certificate_graph(rows_node_kind="input")
    with pytest.raises(restriction_cone.RestrictionConeError, match="task-editable"):
        build(graph, output, target, [offset_event])

    graph, output, target = _offset_certificate_graph(height=10_001)
    with pytest.raises(restriction_cone.RestrictionConeError, match="hard cap"):
        build(graph, output, target, [offset_event])

    graph, output, target = _offset_certificate_graph()
    with pytest.raises(
        restriction_cone.RestrictionConeError,
        match="strict segmentation verification",
    ):
        build(graph, output, target, [offset_event], stable=False)

    closed = {
        "allowed": False,
        "event": "structured_reference",
        "location": "DEFINED_NAME:Unsafe",
        "reason": "structured_reference_unresolved_by_ast",
        "scope": "defined_name",
        "token_offset": 0,
    }
    with pytest.raises(restriction_cone.RestrictionConeError, match="remains closed"):
        build(graph, output, target, [closed])


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


def test_lineage_cell_trace_is_stable_across_hash_seeds():
    script = """\
import json
from types import SimpleNamespace
from xl_seg import lineage

inputs = [f"Sheet!A{i}" for i in range(1, 81)]
output = "Sheet!Z1"
adj = {cell: {output} for cell in inputs}
radj = {output: set(inputs)}
info = {
    cell: SimpleNamespace(
        node=SimpleNamespace(
            label=cell, formula="", kind="input", value=cell
        )
    )
    for cell in inputs
}
info[output] = SimpleNamespace(
    node=SimpleNamespace(
        label=output,
        formula="=SUM(A1:A80)",
        kind="formula",
        value="3240",
    )
)
cg = SimpleNamespace(adj=adj, radj=radj, info=info)
trace = lineage.cell_trace(cg, {}, output, set(inputs), 1000)
print(json.dumps([step["cell"] for step in trace["steps"]]))
"""
    traces = []
    for seed in ("1", "2", "3"):
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=os.path.dirname(os.path.dirname(__file__)),
            env={**os.environ, "PYTHONHASHSEED": seed},
            check=True,
            capture_output=True,
            text=True,
        )
        traces.append(json.loads(result.stdout))

    assert traces[0] == traces[1] == traces[2]
    assert traces[0] == sorted(traces[0][:-1]) + ["Sheet!Z1"]

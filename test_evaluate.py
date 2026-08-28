import unittest
from types import SimpleNamespace

from xl_seg.evaluate import ExcelError, Evaluator, RangeValues, Unresolved, compare
from xl_seg.model import Edge, Graph, Node
from xl_seg.project import CellGraph


def make_node(node_id, kind, *, sheet="Sheet", row=None, col=None, owner="",
              op="", op_kind="", arity="", expr="", value=""):
    return Node(
        id=node_id,
        kind=kind,
        sheet=sheet,
        coordinate=node_id.rpartition("!")[2] if "!" in node_id else "",
        row=row,
        col=col,
        owner=owner,
        op=op,
        op_kind=op_kind,
        arity=str(arity),
        expr=expr,
        label="",
        formula="",
        value=str(value),
        in_cycle=False,
    )


def make_edge(source, target, index):
    return Edge(
        source=source,
        target=target,
        role="arg",
        arg_index=index,
        op="",
        cell="",
        ref="",
        via_range="",
        cross_sheet=False,
    )


def evaluator(nodes, edges=()):
    graph = Graph("book", {node.id: node for node in nodes}, list(edges))
    for edge in edges:
        graph.in_edges.setdefault(edge.target, []).append(edge)
        graph.out_edges.setdefault(edge.source, []).append(edge)
    return Evaluator(graph, CellGraph(graph, {}))


class EvaluatorRegressionTests(unittest.TestCase):
    def test_empty_text_constant_is_not_returned_with_formula_quotes(self):
        const = make_node(
            "Sheet!A1#0:const",
            "const",
            owner="Sheet!A1",
            op="text",
            op_kind="const",
            expr='""',
            value="",
        )
        self.assertEqual(evaluator([const])._eval_node(const.id), "")

    def test_index_uses_both_dimensions(self):
        ev = evaluator([])
        table = RangeValues([1, 2, 3, 4, 5, 6], rows=2, cols=3)
        self.assertEqual(ev._fn_index([table, 2, 3]), 6)

    def test_opaque_graph_range_retains_its_shape(self):
        span = make_node("Sheet!B2:C3", "range", row=2, col=2)
        span.coordinate = "B2:C3"
        ev = evaluator([span])
        ev.values.update({
            "Sheet!B2": 1.0,
            "Sheet!C2": 2.0,
            "Sheet!B3": 3.0,
            "Sheet!C3": 4.0,
        })
        result = ev._expand_range(span)
        self.assertIsInstance(result, RangeValues)
        self.assertEqual((result.rows, result.cols), (2, 2))
        self.assertEqual(ev._fn_index([result, 2, 2]), 4.0)

    def test_hlookup_selects_requested_row(self):
        ev = evaluator([])
        table = RangeValues(["A", "B", 10, 20, 30, 40], rows=3, cols=2)
        self.assertEqual(ev._fn_hlookup(["B", table, 3, False]), 40)

    def test_lookup_ignores_errors_outside_the_matching_row(self):
        op = make_node(
            "Sheet!D5#0:VLOOKUP",
            "op",
            owner="Sheet!D5",
            op="VLOOKUP",
            op_kind="func",
            arity=4,
        )
        ev = evaluator([op])
        table = RangeValues(
            ["A", 10, "B", 20, "C", ExcelError("#N/A")],
            rows=3,
            cols=2,
        )
        ev._args = lambda node: ["B", table, 2, False]
        self.assertEqual(ev._apply(op), 20)

    def test_choose_ignores_errors_in_unselected_branches(self):
        selector = make_node("Sheet!D5#0:const", "const", owner="Sheet!D5",
                             op="number", op_kind="const", value=1, expr="1")
        selected = make_node("Sheet!D5#1:const", "const", owner="Sheet!D5",
                             op="number", op_kind="const", value=42, expr="42")
        unselected = make_node("name:Missing", "name", sheet="")
        op = make_node(
            "Sheet!D5#2:CHOOSE",
            "op",
            owner="Sheet!D5",
            op="CHOOSE",
            op_kind="func",
            arity=3,
        )
        edges = [
            make_edge(selector.id, op.id, 0),
            make_edge(selected.id, op.id, 1),
            make_edge(unselected.id, op.id, 2),
        ]
        ev = evaluator([selector, selected, unselected, op], edges)
        self.assertEqual(ev._apply(op), 42)

    def test_active_graph_prunes_an_unselected_self_reference(self):
        owner = make_node("Sheet!A1", "formula", row=1, col=1, value=42)
        selector = make_node("Sheet!C1", "input", row=1, col=3, value=1)
        selected = make_node("Sheet!B1", "input", row=1, col=2, value=42)
        op = make_node(
            "Sheet!A1#0:IF",
            "op",
            owner=owner.id,
            op="IF",
            op_kind="func",
            arity=3,
        )
        edges = [
            make_edge(selector.id, op.id, 0),
            make_edge(selected.id, op.id, 1),
            make_edge(owner.id, op.id, 2),
            make_edge(op.id, owner.id, 0),
        ]
        ev = evaluator([owner, selector, selected, op], edges)
        ev.cg.info[owner.id] = SimpleNamespace(node=owner, empty_ref=False)
        ev.values.update({owner.id: 0.0, selector.id: 1.0, selected.id: 42.0})
        self.assertEqual(ev._active_node_sources(op.id), {selector.id, selected.id})
        self.assertEqual(ev._eval_cell(owner.id), 42.0)

    def test_ifs_does_not_evaluate_unselected_values(self):
        false = make_node("Sheet!D5#0:const", "const", owner="Sheet!D5",
                          op="logical", op_kind="const", value=False, expr="FALSE")
        bad = make_node("name:Missing", "name", sheet="")
        true = make_node("Sheet!D5#2:const", "const", owner="Sheet!D5",
                         op="logical", op_kind="const", value=True, expr="TRUE")
        selected = make_node("Sheet!D5#3:const", "const", owner="Sheet!D5",
                             op="number", op_kind="const", value=7, expr="7")
        op = make_node("Sheet!D5#4:IFS", "op", owner="Sheet!D5",
                       op="IFS", op_kind="func", arity=4)
        nodes = [false, bad, true, selected, op]
        edges = [make_edge(node.id, op.id, index) for index, node in enumerate(nodes[:-1])]
        self.assertEqual(evaluator(nodes, edges)._apply(op), 7.0)

    def test_iferror_preserves_a_valid_blank_primary_value(self):
        fallback = make_node("Sheet!D5#0:const", "const", owner="Sheet!D5",
                             op="number", op_kind="const", value=7, expr="7")
        op = make_node("Sheet!D5#1:IFERROR", "op", owner="Sheet!D5",
                       op="IFERROR", op_kind="func", arity=2)
        ev = evaluator([fallback, op], [make_edge(fallback.id, op.id, 1)])
        self.assertIsNone(ev._apply(op))

    def test_selected_self_retaining_branch_is_non_unique(self):
        owner = make_node("Sheet!A1", "formula", row=1, col=1, value=99)
        selector = make_node("Sheet!C1", "input", row=1, col=3, value=0)
        selected = make_node("Sheet!B1", "input", row=1, col=2, value=42)
        op = make_node("Sheet!A1#0:IF", "op", owner=owner.id,
                       op="IF", op_kind="func", arity=3)
        edges = [
            make_edge(selector.id, op.id, 0),
            make_edge(selected.id, op.id, 1),
            make_edge(owner.id, op.id, 2),
            make_edge(op.id, owner.id, 0),
        ]
        ev = evaluator([owner, selector, selected, op], edges)
        for node in (owner, selector, selected):
            ev.cg.info[node.id] = SimpleNamespace(
                node=node, empty_ref=False, is_literal=False
            )
        result = ev.run({selector.id, selected.id})
        self.assertIsInstance(result.values[owner.id], Unresolved)
        self.assertEqual(
            result.values[owner.id].reason, "non-unique-circular-reference"
        )
        self.assertEqual(result.coverage["stable_self_cycles"], 0)
        self.assertFalse(result.iterated[0]["unique"])
        self.assertEqual(
            result.iterated[0]["canonical_fixed_point"][owner.id], 0.0
        )
        self.assertEqual(
            result.iterated[0]["alternate_fixed_point"][owner.id], 1.0
        )

    def test_contractive_self_cycle_has_a_unique_zero_fixed_point(self):
        owner = make_node("Sheet!A1", "formula", row=1, col=1, value=0)
        factor = make_node(
            "Sheet!A1#0:const", "const", owner=owner.id,
            op="number", op_kind="const", value=0.5, expr="0.5"
        )
        product = make_node(
            "Sheet!A1#1:*", "op", owner=owner.id,
            op="*", op_kind="infix", arity=2
        )
        edges = [
            make_edge(owner.id, product.id, 0),
            make_edge(factor.id, product.id, 1),
            make_edge(product.id, owner.id, 0),
        ]
        ev = evaluator([owner, factor, product], edges)
        ev.cg.info[owner.id] = SimpleNamespace(
            node=owner, empty_ref=False, is_literal=False
        )
        result = ev.run(set())
        self.assertEqual(result.values[owner.id], 0.0)
        self.assertEqual(result.coverage["stable_self_cycles"], 1)

    def test_iteration_disabled_waits_for_selector_topology_to_settle(self):
        owner = make_node("Sheet!A1", "formula", row=1, col=1, value=42)
        selected = make_node("Sheet!B1", "input", row=1, col=2, value=42)
        selector = make_node("Sheet!C1", "formula", row=1, col=3, value=1)
        selector_source = make_node("Sheet!D1", "input", row=1, col=4, value=1)
        op = make_node(
            "Sheet!A1#0:IF", "op", owner=owner.id,
            op="IF", op_kind="func", arity=3
        )
        edges = [
            make_edge(selector.id, op.id, 0),
            make_edge(selected.id, op.id, 1),
            make_edge(owner.id, op.id, 2),
            make_edge(op.id, owner.id, 0),
            make_edge(selector_source.id, selector.id, 0),
        ]
        ev = evaluator([owner, selected, selector, selector_source, op], edges)
        for node in (owner, selected, selector, selector_source):
            ev.cg.info[node.id] = SimpleNamespace(
                node=node, empty_ref=False, is_literal=False
            )
        ev.oracle = SimpleNamespace(
            iterate=False, iterate_count=None, iterate_delta=None
        )
        result = ev.run({selected.id, selector_source.id})
        self.assertEqual(result.values[owner.id], 42.0)
        self.assertNotIn(owner.id, result.unresolved)

    def test_date_normalizes_overflowing_months_and_days(self):
        ev = evaluator([])
        self.assertEqual(ev._fn_date([2020, 13, 1]), ev._fn_date([2021, 1, 1]))
        self.assertEqual(ev._fn_date([2020, 1, 32]), ev._fn_date([2020, 2, 1]))

    def test_row_without_argument_uses_formula_owner(self):
        owner = make_node("Sheet!D17", "formula", row=17, col=4)
        row_op = make_node(
            "Sheet!D17#0:ROW",
            "op",
            owner=owner.id,
            op="ROW",
            op_kind="func",
            arity=0,
            expr="ROW()",
        )
        self.assertEqual(evaluator([owner, row_op])._row(row_op), 17.0)

    def test_counta_recovers_range_missing_from_old_ast(self):
        op = make_node(
            "Sheet!D5#0:COUNTA",
            "op",
            owner="Sheet!D5",
            op="COUNTA",
            op_kind="func",
            arity=1,
            expr="COUNTA($B$2:$C$3)",
        )
        ev = evaluator([op])
        ev.values.update({
            "Sheet!B2": 1.0,
            "Sheet!C2": "",
            "Sheet!B3": None,
            "Sheet!C3": "x",
        })
        # Excel counts the formula-produced "" in C2; only the genuinely
        # empty B3 is skipped (workbook 0342 regression).
        self.assertEqual(ev._counta(op), 3.0)

    def test_iterate_group_polishes_past_workbook_delta(self):
        # Workbook 0451 regression: Excel's cached circular values sit at the
        # fixed point (every save iterates again), so stopping at the coarse
        # workbook delta (0.001) leaves a drift the verifier rejects. The
        # group must keep polishing once converged.
        ev = evaluator([])
        ev.values["Sheet!A1"] = 0.0
        ev._eval_cell = lambda cid: (ev.values[cid] + 10.0) / 2.0
        diagnostic = ev._iterate_group(["Sheet!A1"], 5000, 0.001)
        self.assertTrue(diagnostic["converged"])
        self.assertAlmostEqual(ev.values["Sheet!A1"], 10.0, places=9)

    def test_offset_returns_requested_range(self):
        base = make_node("Sheet!A1", "input", row=1, col=1, value=1)
        op = make_node(
            "Sheet!D5#5:OFFSET",
            "op",
            owner="Sheet!D5",
            op="OFFSET",
            op_kind="func",
            arity=5,
            expr="OFFSET(A1,1,1,2,2)",
        )
        constants = [
            make_node(f"Sheet!D5#{index}:const", "const", owner="Sheet!D5",
                      op="number", op_kind="const", value=value, expr=str(value))
            for index, value in enumerate((1, 1, 2, 2), 1)
        ]
        edges = [make_edge(base.id, op.id, 0)] + [
            make_edge(node.id, op.id, index)
            for index, node in enumerate(constants, 1)
        ]
        ev = evaluator([base, op, *constants], edges)
        ev.values.update({
            "Sheet!B2": 1.0,
            "Sheet!C2": 2.0,
            "Sheet!B3": 3.0,
            "Sheet!C3": 4.0,
        })
        result = ev._offset(op)
        self.assertIsInstance(result, RangeValues)
        self.assertEqual((result.rows, result.cols, list(result)), (2, 2, [1.0, 2.0, 3.0, 4.0]))
        self.assertEqual(
            ev._active_node_sources(op.id),
            {"Sheet!B2", "Sheet!C2", "Sheet!B3", "Sheet!C3"},
        )

    def test_offset_recovers_a_blank_base_anchor_from_expression(self):
        op = make_node(
            "Sheet!D5#2:OFFSET",
            "op",
            owner="Sheet!D5",
            op="OFFSET",
            op_kind="func",
            arity=3,
            expr="OFFSET($A$1,1,1)",
        )
        constants = [
            make_node(f"Sheet!D5#{index}:const", "const", owner="Sheet!D5",
                      op="number", op_kind="const", value=1, expr="1")
            for index in (0, 1)
        ]
        edges = [
            make_edge(constants[0].id, op.id, 1),
            make_edge(constants[1].id, op.id, 2),
        ]
        ev = evaluator([op, *constants], edges)
        ev.values["Sheet!B2"] = 42.0
        self.assertEqual(ev._offset(op), 42.0)

    def test_unresolved_name_behaves_as_excel_error(self):
        name = make_node("name:Missing", "name", sheet="")
        result = evaluator([name])._eval_node(name.id)
        self.assertIsInstance(result, ExcelError)
        self.assertEqual(result.code, "#NAME?")

    def test_stabilization_refreshes_a_stale_dynamic_consumer(self):
        source = make_node("Sheet!B1", "input", row=1, col=2, value=5)
        consumer = make_node("Sheet!A1", "formula", row=1, col=1, value=5)
        ev = evaluator([consumer, source], [make_edge(source.id, consumer.id, 0)])
        ev.cg.info[consumer.id] = SimpleNamespace(node=consumer, empty_ref=False)
        ev.values.update({source.id: 5.0, consumer.id: 0.0})
        ev._stabilize([consumer.id], {source.id})
        self.assertEqual(ev.values[consumer.id], 5.0)

    def test_if_omitted_then_branch_returns_zero_like_excel(self):
        cond = make_node("Sheet!D5#0:const", "const", owner="Sheet!D5",
                         op="logical", op_kind="const", value=True, expr="TRUE")
        op = make_node("Sheet!D5#1:IF", "op", owner="Sheet!D5",
                       op="IF", op_kind="func", arity=3)
        ev = evaluator([cond, op], [make_edge(cond.id, op.id, 0)])
        self.assertEqual(ev._apply(op), 0.0)

    def test_if_omitted_else_branch_returns_false_like_excel(self):
        cond = make_node("Sheet!D5#0:const", "const", owner="Sheet!D5",
                         op="logical", op_kind="const", value=False, expr="FALSE")
        then = make_node("Sheet!D5#1:const", "const", owner="Sheet!D5",
                         op="number", op_kind="const", value=7, expr="7")
        op = make_node("Sheet!D5#2:IF", "op", owner="Sheet!D5",
                       op="IF", op_kind="func", arity=3)
        edges = [make_edge(cond.id, op.id, 0), make_edge(then.id, op.id, 1)]
        ev = evaluator([cond, then, op], edges)
        self.assertIs(ev._apply(op), False)

    def test_lookup_propagates_an_unresolved_key(self):
        op = make_node("Sheet!D5#0:VLOOKUP", "op", owner="Sheet!D5",
                       op="VLOOKUP", op_kind="func", arity=4)
        ev = evaluator([op])
        table = RangeValues(["A", 10, "B", 20], rows=2, cols=2)
        ev._args = lambda node: [Unresolved("uncomputed-precedent"), table, 2, False]
        result = ev._apply(op)
        self.assertIsInstance(result, Unresolved)

    def test_match_propagates_an_error_key(self):
        op = make_node("Sheet!D5#0:MATCH", "op", owner="Sheet!D5",
                       op="MATCH", op_kind="func", arity=3)
        ev = evaluator([op])
        pool = RangeValues([1, 2, 3], rows=3, cols=1)
        ev._args = lambda node: [ExcelError("#REF!"), pool, 0]
        result = ev._apply(op)
        self.assertIsInstance(result, ExcelError)

    def test_lookup_still_tolerates_bad_values_inside_the_range(self):
        op = make_node("Sheet!D5#0:VLOOKUP", "op", owner="Sheet!D5",
                       op="VLOOKUP", op_kind="func", arity=4)
        ev = evaluator([op])
        table = RangeValues(
            ["A", 10, "B", 20, "C", Unresolved("range-uncomputed")],
            rows=3, cols=2,
        )
        ev._args = lambda node: ["B", table, 2, False]
        self.assertEqual(ev._apply(op), 20)

    def test_compare_reports_blank_cache_as_unverifiable(self):
        self.assertEqual(compare("", 0.0), ("unverifiable", None))
        self.assertEqual(compare("", None), ("unverifiable", None))
        self.assertEqual(compare("", False), ("unverifiable", None))
        self.assertEqual(compare("", 123.0), ("unverifiable", None))

    def test_oracle_fallback_reads_are_recorded_with_their_consumer(self):
        graph = Graph("book", {}, [])
        ev = Evaluator(graph, CellGraph(graph, {}),
                       oracle=lambda sheet, row, col: 42.0)
        ev._current_cell = "Sheet!A1"
        self.assertEqual(ev._read("Sheet!Z9"), 42.0)
        self.assertEqual(ev.oracle_reads, {"Sheet!Z9": {"Sheet!A1"}})

    def test_oracle_reads_of_empty_cells_are_not_recorded(self):
        graph = Graph("book", {}, [])
        ev = Evaluator(graph, CellGraph(graph, {}),
                       oracle=lambda sheet, row, col: None)
        ev._current_cell = "Sheet!A1"
        self.assertIsNone(ev._read("Sheet!Z9"))
        self.assertEqual(ev.oracle_reads, {})

    def test_run_exposes_runtime_edges_for_offset_targets(self):
        owner = make_node("Sheet!D5", "formula", row=5, col=4, value=42)
        base = make_node("Sheet!A1", "input", row=1, col=1, value=1)
        target = make_node("Sheet!B2", "input", row=2, col=2, value=42)
        op = make_node(
            "Sheet!D5#2:OFFSET", "op", owner=owner.id,
            op="OFFSET", op_kind="func", arity=3, expr="OFFSET(A1,1,1)",
        )
        constants = [
            make_node(f"Sheet!D5#{index}:const", "const", owner=owner.id,
                      op="number", op_kind="const", value=1, expr="1")
            for index in (0, 1)
        ]
        edges = [
            make_edge(base.id, op.id, 0),
            make_edge(constants[0].id, op.id, 1),
            make_edge(constants[1].id, op.id, 2),
            make_edge(op.id, owner.id, 0),
        ]
        ev = evaluator([owner, base, target, op, *constants], edges)
        for node in (owner, base, target):
            ev.cg.info[node.id] = SimpleNamespace(
                node=node, empty_ref=False, is_literal=False
            )
        result = ev.run({base.id, target.id})
        self.assertEqual(result.values[owner.id], 42.0)
        # The static graph has no edge from B2 to D5; the runtime graph must.
        self.assertIn(target.id, result.runtime_radj.get(owner.id, set()))


class NewFunctionGoldenValueTests(unittest.TestCase):
    """Golden values from Excel docs / numpy-financial (which mirrors Excel)."""

    def setUp(self):
        self.ev = evaluator([])

    def test_pmt(self):
        self.assertAlmostEqual(
            self.ev._fn_pmt([0.075 / 12, 12 * 15, 200000]), -1854.0247200054619)
        self.assertAlmostEqual(self.ev._fn_pmt([0, 10, 1000]), -100.0)

    def test_ipmt_and_ppmt(self):
        rate = 0.0824 / 12
        self.assertAlmostEqual(self.ev._fn_ipmt([rate, 1, 12, 2500]), -17.166666666666668)
        payment = self.ev._fn_pmt([rate, 12, 2500])
        interest = self.ev._fn_ipmt([rate, 2, 12, 2500])
        self.assertAlmostEqual(self.ev._fn_ppmt([rate, 2, 12, 2500]), payment - interest)
        # Amortization identity: principal repayments sum to the whole loan.
        total_principal = sum(
            self.ev._fn_ppmt([rate, per, 12, 2500]) for per in range(1, 13))
        self.assertAlmostEqual(total_principal, -2500.0)

    def test_pv_fv_nper_roundtrip(self):
        self.assertAlmostEqual(
            self.ev._fn_fv([0.05 / 12, 10 * 12, -100, -100]), 15692.928894335748)
        self.assertAlmostEqual(
            self.ev._fn_pv([0.05 / 12, 10 * 12, -100, 15692.93]), -100.00067131625819)
        self.assertAlmostEqual(
            self.ev._fn_nper([0.07 / 12, -150, 8000]), 64.07334877066185)
        payment = self.ev._fn_pmt([0.006, 240, 100000])
        self.assertAlmostEqual(self.ev._fn_nper([0.006, payment, 100000]), 240.0)
        self.assertAlmostEqual(self.ev._fn_pv([0.006, 240, payment]), 100000.0, places=6)

    def test_rate(self):
        self.assertAlmostEqual(
            self.ev._fn_rate([10, 0, -3500, 10000]), 0.11069085371426901, places=8)
        self.assertAlmostEqual(
            self.ev._fn_rate([4 * 12, -200, 8000]), 0.007701472488201379, places=8)
        # Round-trip: the rate that reproduces a known payment.
        payment = self.ev._fn_pmt([0.05 / 12, 360, 200000])
        self.assertAlmostEqual(
            self.ev._fn_rate([360, payment, 200000]), 0.05 / 12, places=9)

    def test_cumipmt_cumprinc(self):
        args = [0.09 / 12, 30 * 12, 125000, 13, 24, 0]
        self.assertAlmostEqual(self.ev._fn_cumipmt(args), -11135.232130750845, places=4)
        self.assertAlmostEqual(self.ev._fn_cumprinc(args), -934.1071234, places=4)
        self.assertEqual(self.ev._fn_cumipmt([0.01, 12, 1000, 5, 3, 0]).code, "#NUM!")

    def test_mirr(self):
        flows = RangeValues(
            [-4500, -800, 800, 800, 600, 600, 800, 800, 700, 3000], rows=10, cols=1)
        self.assertAlmostEqual(
            self.ev._fn_mirr([flows, 0.08, 0.055]), 0.06659717503155349)

    def test_array_member_takes_positional_element(self):
        # Workbook 0451 regression: members of a multi-cell CSE span like
        # {=TRANSPOSE(LBO!E314:E322)} entered over I24:Q24 must each take
        # their own element, not the anchor's first value.
        anchor = make_node("Sheet!I24", "formula", row=24, col=9)
        anchor.array_span = "I24:Q24"
        member = make_node("Sheet!L24", "formula", row=24, col=12)
        member.array_span = "I24:Q24"
        plain = make_node("Sheet!Z1", "formula", row=1, col=26)
        result = RangeValues([7.0, 8.0, 8.5, 9.0, 9.5, 10.0, 10.5, 11.0, 12.0],
                             rows=1, cols=9)
        ev = evaluator([anchor, member, plain])
        self.assertEqual(ev._array_element(anchor, result), 7.0)
        self.assertEqual(ev._array_element(member, result), 9.0)
        # No recorded span keeps the historical first-element behavior.
        self.assertEqual(ev._array_element(plain, result), 7.0)
        # Single-row results broadcast down a multi-row span.
        tall_member = make_node("Sheet!I26", "formula", row=26, col=9)
        tall_member.array_span = "I24:I30"
        one_row = RangeValues([3.0], rows=1, cols=1)
        self.assertEqual(ev._array_element(tall_member, one_row), 3.0)
        # Members outside the result's extent read #N/A, as in Excel.
        wide = make_node("Sheet!S24", "formula", row=24, col=19)
        wide.array_span = "I24:S24"
        short = RangeValues([1.0, 2.0], rows=1, cols=2)
        self.assertEqual(ev._array_element(wide, short).code, "#N/A")

    def test_index_lone_position_on_single_row_vector(self):
        # Workbook 0613 regression: INDEX(J9:O9, MATCH(...)) walks along the
        # row when the vector has one row, instead of demanding row=1.
        vector = RangeValues([0.1, 0.2, 0.3, 0.4], rows=1, cols=4)
        self.assertEqual(self.ev._fn_index([vector, 2.0]), 0.2)
        self.assertEqual(self.ev._fn_index([vector, 4.0]), 0.4)
        self.assertEqual(self.ev._fn_index([vector, 5.0]).code, "#REF!")
        # Explicit row/col arguments keep their meaning.
        self.assertEqual(self.ev._fn_index([vector, 1.0, 3.0]), 0.3)

    def test_criteria_accepts_percent_literals(self):
        # Workbook 0661 regression: AVERAGEIF(range, ">0%", range) parses the
        # percent criteria numerically, as Excel does.
        from xl_seg.evaluate import _matches
        self.assertTrue(_matches(0.05, ">0%"))
        self.assertFalse(_matches(-0.05, ">0%"))
        self.assertTrue(_matches(0.10, ">=10%"))
        self.assertFalse(_matches(0.09, ">=10%"))

    def test_irr_requires_a_sign_change(self):
        # Workbook 0354 regression: Excel returns #NUM! for an all-zero series,
        # while the grid scan used to report a rate near the default guess.
        zeros = RangeValues([0.0] * 11, rows=1, cols=11)
        result = self.ev._fn_irr([zeros])
        self.assertIsInstance(result, ExcelError)
        self.assertEqual(result.code, "#NUM!")
        one_sided = RangeValues([100.0, 250.0, 300.0], rows=1, cols=3)
        self.assertEqual(self.ev._fn_irr([one_sided]).code, "#NUM!")

    def test_irr_matches_excel_on_0354_row_11(self):
        # Real cash flows from golden workbook 0354 Cap Table!M11:W11; Excel
        # cached X11 = 0.2024400597734355.
        flows = RangeValues([
            -70000000, -4592867.329370629, -20868351.54630504,
            -11783491.649813956, -30128500.031470563, 673897.8796283146,
            12959856.453163365, 12617898.583657674, 13279295.109379407,
            13954486.138147676, 604841505.7387657,
        ], rows=1, cols=11)
        self.assertAlmostEqual(self.ev._fn_irr([flows]), 0.2024400597734355, places=9)

    def test_vdb_matches_excel_documentation(self):
        # The five worked examples from Microsoft's VDB documentation
        # (published rounded to cents).
        self.assertAlmostEqual(self.ev._fn_vdb([2400, 300, 3650, 0, 1]), 1.32, places=2)
        self.assertAlmostEqual(self.ev._fn_vdb([2400, 300, 120, 0, 1]), 40.0)
        self.assertAlmostEqual(self.ev._fn_vdb([2400, 300, 10, 0, 1]), 480.0)
        self.assertAlmostEqual(self.ev._fn_vdb([2400, 300, 120, 6, 18]), 396.31, places=2)
        self.assertAlmostEqual(self.ev._fn_vdb([2400, 300, 120, 6, 18, 1.5]), 311.81, places=2)

    def test_vdb_switches_to_straight_line_and_validates_domain(self):
        # With switching allowed, late periods fall back to straight line, so
        # a zero-salvage asset depreciates to exactly its full cost.
        total = sum(
            self.ev._fn_vdb([2400, 0, 10, start, start + 1])
            for start in range(10)
        )
        self.assertAlmostEqual(total, 2400.0)
        # no_switch keeps pure declining balance, which leaves the geometric
        # residual (2400 * 0.8^10) undepreciated.
        no_switch_total = sum(
            self.ev._fn_vdb([2400, 0, 10, start, start + 1, 2, True])
            for start in range(10)
        )
        self.assertAlmostEqual(no_switch_total, 2400.0 - 2400.0 * 0.8 ** 10)
        self.assertEqual(self.ev._fn_vdb([2400, 300, 10, 5, 4]).code, "#NUM!")
        self.assertEqual(self.ev._fn_vdb([2400, 300, 10, 0, 11]).code, "#NUM!")

    def test_xnpv(self):
        flows = RangeValues([-10000, 2750, 4250, 3250, 2750], rows=5, cols=1)
        dates = RangeValues([39448, 39508, 39751, 39859, 39904], rows=5, cols=1)
        self.assertAlmostEqual(self.ev._fn_xnpv([0.09, flows, dates]), 2086.6476020315354, places=4)

    def test_mod_uses_divisor_sign(self):
        self.assertEqual(self.ev._fn_mod([3, 2]), 1.0)
        self.assertEqual(self.ev._fn_mod([-3, 2]), 1.0)
        self.assertEqual(self.ev._fn_mod([3, -2]), -1.0)
        self.assertEqual(self.ev._fn_mod([3, 0]).code, "#DIV/0!")

    def test_networkdays(self):
        # 2012-10-01 .. 2013-03-01 (Excel doc example)
        self.assertEqual(self.ev._fn_networkdays([41183, 41334]), 110.0)
        holidays = RangeValues([41235], rows=1, cols=1)
        self.assertEqual(self.ev._fn_networkdays([41183, 41334, holidays]), 109.0)
        self.assertEqual(self.ev._fn_networkdays([41334, 41183]), -110.0)

    def test_yearfrac(self):
        # 2012-01-01 .. 2012-07-30 (Excel doc example)
        self.assertAlmostEqual(self.ev._fn_yearfrac([40909, 41120]), 0.58055556, places=7)
        self.assertAlmostEqual(self.ev._fn_yearfrac([40909, 41120, 1]), 0.57650273, places=7)
        self.assertAlmostEqual(self.ev._fn_yearfrac([40909, 41120, 3]), 0.57808219, places=7)

    def test_norm_dist_family(self):
        self.assertAlmostEqual(
            self.ev._fn_norm_dist([42, 40, 1.5, True]), 0.9087887802741321, places=7)
        self.assertAlmostEqual(
            self.ev._fn_norm_dist([42, 40, 1.5, False]), 0.10934004978399577, places=7)
        self.assertAlmostEqual(
            self.ev._fn_norm_s_dist([1.333333, True]), 0.9087887259176292, places=7)
        self.assertAlmostEqual(
            self.ev._fn_norm_inv([0.908789, 40, 1.5]), 42.00000200956616, places=5)
        self.assertAlmostEqual(
            self.ev._fn_norm_s_inv([0.908789]), 1.3333346730441074, places=5)
        self.assertEqual(self.ev._fn_norm_dist([1, 0, 0, True]).code, "#NUM!")

    def test_cheap_wins(self):
        self.assertEqual(self.ev._fn_product([RangeValues([2, 3, 4], 3, 1)]), 24.0)
        self.assertEqual(self.ev._fn_power([2, 10]), 1024.0)
        self.assertAlmostEqual(self.ev._fn_exp([1]), 2.718281828459045)
        self.assertAlmostEqual(self.ev._fn_ln([2.718281828459045]), 1.0)
        self.assertEqual(self.ev._fn_ln([0]).code, "#NUM!")
        self.assertEqual(self.ev._fn_concatenate(["a", 1.0, None, "b"]), "a1b")
        self.assertTrue(self.ev._fn_isnumber([3.0]))
        self.assertFalse(self.ev._fn_isnumber(["3"]))
        self.assertFalse(self.ev._fn_isnumber([ExcelError("#N/A")]))
        self.assertTrue(self.ev._fn_isblank([None]))
        self.assertTrue(self.ev._fn_iserr([ExcelError("#DIV/0!")]))
        self.assertFalse(self.ev._fn_iserr([ExcelError("#N/A")]))
        self.assertTrue(self.ev._fn_isna([ExcelError("#N/A")]))
        self.assertTrue(self.ev._fn_iserror([ExcelError("#N/A")]))


class CensusLongTailTests(unittest.TestCase):
    """The multi-workbook blockers surfaced by the FAIL-set census."""

    def setUp(self):
        self.ev = evaluator([])

    def test_averageif(self):
        pool = RangeValues([1, 2, 3, 4], 4, 1)
        target = RangeValues([10, 20, 30, 40], 4, 1)
        self.assertEqual(self.ev._fn_averageif([pool, ">2", target]), 35.0)
        self.assertEqual(self.ev._fn_averageif([pool, ">2"]), 3.5)
        self.assertEqual(self.ev._fn_averageif([pool, ">9"]).code, "#DIV/0!")

    def test_days360(self):
        # 2011-01-30 (40573) .. 2011-02-01 (40575): Excel doc example gives 1.
        self.assertEqual(self.ev._fn_days360([40573, 40575]), 1.0)
        # 2011-01-01 (40544) .. 2011-12-31 (40908) = 360.
        self.assertEqual(self.ev._fn_days360([40544, 40908]), 360.0)

    def test_lookup_vector_and_array_forms(self):
        keys = RangeValues([1, 2, 3], 3, 1)
        results = RangeValues(["a", "b", "c"], 3, 1)
        self.assertEqual(self.ev._fn_lookup([2.5, keys, results]), "b")
        table = RangeValues([1, 2, 3, "a", "b", "c"], rows=2, cols=3)
        self.assertEqual(self.ev._fn_lookup([3, table]), "c")
        self.assertEqual(self.ev._fn_lookup([0.5, keys, results]).code, "#N/A")

    def test_subtotal_codes_including_hidden_variants(self):
        rng = RangeValues([1, 2, 3, 4], 4, 1)
        self.assertEqual(self.ev._fn_subtotal([9, rng]), 10.0)
        self.assertEqual(self.ev._fn_subtotal([109, rng]), 10.0)
        self.assertEqual(self.ev._fn_subtotal([1, rng]), 2.5)
        self.assertEqual(self.ev._fn_subtotal([4, rng]), 4.0)

    def test_maxifs_minifs_default_to_zero_on_no_match(self):
        target = RangeValues([5, 7, 9], 3, 1)
        crit = RangeValues(["x", "y", "x"], 3, 1)
        self.assertEqual(self.ev._fn_maxifs([target, crit, "x"]), 9.0)
        self.assertEqual(self.ev._fn_minifs([target, crit, "x"]), 5.0)
        self.assertEqual(self.ev._fn_maxifs([target, crit, "z"]), 0.0)

    def test_workday_and_weekday(self):
        # 41183 = Mon 2012-10-01; +5 workdays = Mon 2012-10-08 (41190).
        self.assertEqual(self.ev._fn_workday([41183, 5]), 41190.0)
        self.assertEqual(self.ev._fn_workday([41183, -1]), 41180.0)
        holidays = RangeValues([41184], 1, 1)
        self.assertEqual(self.ev._fn_workday([41183, 1, holidays]), 41185.0)
        self.assertEqual(self.ev._fn_weekday([41183]), 2.0)
        self.assertEqual(self.ev._fn_weekday([41183, 2]), 1.0)

    def test_int_n_and_misc(self):
        self.assertEqual(self.ev._fn_int([-1.5]), -2.0)
        self.assertEqual(self.ev._fn_n([True]), 1.0)
        self.assertEqual(self.ev._fn_n(["text"]), 0.0)
        self.assertEqual(self.ev._fn_n([7.5]), 7.5)
        self.assertEqual(self.ev._fn_countblank([RangeValues([1, None, ""], 3, 1)]), 2.0)
        self.assertEqual(self.ev._fn_rows([RangeValues([1, 2, 3, 4], 2, 2)]), 2.0)
        self.assertEqual(self.ev._fn_trim(["  a   b  "]), "a b")
        self.assertEqual(self.ev._fn_substitute(["a-b-c", "-", "+", 2]), "a-b+c")
        self.assertEqual(self.ev._fn_small([RangeValues([9, 1, 5], 3, 1), 2]), 5)
        self.assertEqual(self.ev._fn_quartile([RangeValues([1, 2, 3, 4], 4, 1), 2]), 2.5)

    def test_array_constant_evaluates_as_horizontal_range(self):
        constants = [
            make_node(f"Sheet!A1#{i}:const", "const", owner="Sheet!A1",
                      op="number", op_kind="const", value=v, expr=str(v))
            for i, v in enumerate((1, 4, 7, 10))
        ]
        op = make_node("Sheet!A1#4:{}", "op", owner="Sheet!A1",
                       op="{}", op_kind="func", arity=4, expr="{1,4,7,10}")
        edges = [make_edge(node.id, op.id, i) for i, node in enumerate(constants)]
        result = evaluator([*constants, op], edges)._apply(op)
        self.assertIsInstance(result, RangeValues)
        self.assertEqual(list(result), [1.0, 4.0, 7.0, 10.0])
        self.assertEqual((result.rows, result.cols), (1, 4))


class XlfnDispatchTests(unittest.TestCase):
    """Prefixed future-function names must reach their bare-name handlers."""

    def _apply_op(self, op_name, stub_args):
        op = make_node(
            f"Sheet!A1#0:{op_name}", "op", owner="Sheet!A1",
            op=op_name, op_kind="func", arity=len(stub_args))
        ev = evaluator([op])
        ev._args = lambda node: stub_args
        return ev._apply(op)

    def test_prefixed_op_dispatches_to_bare_handler(self):
        result = self._apply_op("_XLFN.NORM.DIST", [42, 40, 1.5, True])
        self.assertAlmostEqual(result, 0.9087887802741321, places=7)

    def test_prefix_only_handler_still_reachable(self):
        result = self._apply_op("_XLFN.VSTACK", [1.0, 2.0])
        self.assertIsInstance(result, RangeValues)
        self.assertEqual(list(result), [1.0, 2.0])

    def test_unknown_op_still_counted_under_raw_name(self):
        op = make_node(
            "Sheet!A1#0:_XLFN.NOSUCHFN", "op", owner="Sheet!A1",
            op="_XLFN.NOSUCHFN", op_kind="func", arity=1)
        ev = evaluator([op])
        ev._args = lambda node: [1.0]
        result = ev._apply(op)
        self.assertIsInstance(result, Unresolved)
        self.assertEqual(ev.unknown_ops.get("_XLFN.NOSUCHFN"), 1)


if __name__ == "__main__":
    unittest.main()

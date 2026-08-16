import unittest
from types import SimpleNamespace

from xl_seg.evaluate import ExcelError, Evaluator, RangeValues, Unresolved
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
        self.assertEqual(ev._counta(op), 2.0)

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


if __name__ == "__main__":
    unittest.main()

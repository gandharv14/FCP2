from __future__ import annotations

from types import SimpleNamespace
import zipfile

from xl_seg import partition, project
from xl_seg.evaluate import (
    CalculationMetadata,
    Evaluator,
    Unresolved,
    workbook_calculation_metadata,
)
from xl_seg.model import Edge, Graph, Node, split_ref
from xl_segment import _verify


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


def _ast(node_id, owner, kind, *, op="", arity="", value=""):
    return Node(
        id=node_id,
        kind=kind,
        sheet=owner.rpartition("!")[0],
        coordinate="",
        row=None,
        col=None,
        owner=owner,
        op=op,
        op_kind="infix" if kind == "op" else "const",
        arity=str(arity),
        expr=str(value),
        label=op or str(value),
        formula="",
        value=str(value),
        in_cycle=False,
    )


def _edge(source, target, index=0, role="arg", via_range=""):
    return Edge(
        source=source,
        target=target,
        role=role,
        arg_index=index,
        op="",
        cell=target.split("#", 1)[0],
        ref="",
        via_range=via_range,
        cross_sheet=False,
    )


def _affine(ref, factor, constant, cached=0):
    owner = _cell(ref, "formula", cached, f"={factor}*{ref}+{constant}")
    factor_node = _ast(f"{ref}#factor", ref, "const", value=factor)
    product = _ast(f"{ref}#product", ref, "op", op="*", arity=2)
    constant_node = _ast(f"{ref}#constant", ref, "const", value=constant)
    add = _ast(f"{ref}#add", ref, "op", op="+", arity=2)
    nodes = [owner, factor_node, product, constant_node, add]
    edges = [
        _edge(owner.id, product.id, 0),
        _edge(factor_node.id, product.id, 1),
        _edge(product.id, add.id, 0),
        _edge(constant_node.id, add.id, 1),
        _edge(add.id, owner.id, 0, role="result"),
    ]
    return owner, nodes, edges


def _graph(nodes, edges):
    graph = Graph("book", {node.id: node for node in nodes}, edges)
    for edge in edges:
        graph.in_edges.setdefault(edge.target, []).append(edge)
        graph.out_edges.setdefault(edge.source, []).append(edge)
    return graph, project.build(graph)


def _calculation(*, enabled=True, count=100, delta=0.001):
    return CalculationMetadata(
        available=True,
        iterate=enabled,
        iterate_count=count,
        iterate_delta=delta,
        iterate_origin="explicit",
        iterate_count_origin="explicit",
        iterate_delta_origin="explicit",
    )


def test_raw_calculation_metadata_preserves_explicit_and_unknown_values(tmp_path):
    path = tmp_path / "calculation.xlsx"
    workbook_xml = (
        '<workbook xmlns="http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main"><calcPr iterate="1" iterateCount="17" '
        'iterateDelta="0.025"/></workbook>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml", workbook_xml)
    metadata = workbook_calculation_metadata(path)
    assert metadata.iterate is True
    assert metadata.iterate_origin == "explicit"
    assert metadata.iterate_count_origin == "explicit"
    assert metadata.iterate_delta_origin == "explicit"
    assert metadata.raw_calc_pr["iterateCount"] == "17"

    malformed = tmp_path / "malformed.xlsx"
    with zipfile.ZipFile(malformed, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            workbook_xml.replace('iterate="1"', 'iterate="maybe"'),
        )
    metadata = workbook_calculation_metadata(malformed)
    assert metadata.iterate is None
    assert metadata.iterate_origin == "unknown"
    assert metadata.raw_calc_pr["iterate"] == "maybe"

    unsafe = tmp_path / "unsafe.xlsx"
    with zipfile.ZipFile(unsafe, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            workbook_xml.replace(
                'iterateCount="17" iterateDelta="0.025"',
                'iterateCount="1000000000000" iterateDelta="INF"',
            ),
        )
    metadata = workbook_calculation_metadata(unsafe)
    assert metadata.iterate_count is None
    assert metadata.iterate_delta is None
    assert metadata.iterate_count_origin == "unknown"
    assert metadata.iterate_delta_origin == "unknown"


def test_disabled_and_oscillating_active_cycles_fail_closed(tmp_path):
    disabled_owner, nodes, edges = _affine("Sheet!A1", 0.5, 1)
    graph, cg = _graph(nodes, edges)
    disabled = Evaluator(
        graph,
        cg,
        calculation=_calculation(enabled=False),
        proof_outputs={disabled_owner.id},
    ).run(set())
    diagnostic = disabled.iterated[0]
    assert diagnostic["iteration_enabled"] is False
    assert diagnostic["converged"] is False
    assert diagnostic["output_relevant"] is True
    assert isinstance(disabled.values[disabled_owner.id], Unresolved)
    bg, cd, part = _verification_shape(cg, disabled_owner)
    source_path, ast, curation = _evidence(tmp_path)
    disabled_report = _verify(
        cg,
        disabled,
        set(),
        {disabled_owner.id},
        part,
        cd,
        bg,
        SimpleNamespace(sample=10),
        expected_cache=_ExpectedCache({disabled_owner.id: 2}),
        calculation=_calculation(enabled=False),
        source_path=source_path,
        ast_dir=ast,
        curation_path=curation,
        proof={
            "runtime_radj": {disabled_owner.id: [disabled_owner.id]},
            "closure": {"stabilized": True},
        },
    )
    assert "active_cycle_iteration_disabled" in disabled_report["blocking_reasons"]

    oscillating_owner, nodes, edges = _affine("Sheet!A1", -1, 1)
    graph, cg = _graph(nodes, edges)
    oscillating = Evaluator(
        graph,
        cg,
        calculation=_calculation(count=6, delta=0.0001),
        proof_outputs={oscillating_owner.id},
    ).run(set())
    diagnostic = oscillating.iterated[0]
    assert diagnostic["workbook_iterations"] == 6
    assert diagnostic["budget_exhausted"] is True
    assert diagnostic["converged"] is False
    assert diagnostic["max_change_history_complete_count"] == 6


def test_identity_loop_demonstrates_non_uniqueness_without_claiming_uniqueness():
    owner, nodes, edges = _affine("Sheet!A1", 1, 0)
    graph, cg = _graph(nodes, edges)
    result = Evaluator(
        graph,
        cg,
        calculation=_calculation(),
        proof_outputs={owner.id},
    ).run(set())

    diagnostic = result.iterated[0]
    assert diagnostic["uniqueness"] == "demonstrated_non_unique"
    assert diagnostic["unique"] is False
    assert diagnostic["canonical_fixed_point"][owner.id] == 0.0
    assert diagnostic["alternate_fixed_point"][owner.id] == 1.0
    assert isinstance(result.values[owner.id], Unresolved)


def test_cycles_share_one_global_budget_and_stable_coordinate_order():
    first, first_nodes, first_edges = _affine("Sheet!A1", 0.5, 1)
    second, second_nodes, second_edges = _affine("Sheet!Z1", 0.9, 1)
    graph, cg = _graph(
        first_nodes + second_nodes,
        first_edges + second_edges,
    )

    class RecordingEvaluator(Evaluator):
        advances = []

        def _eval_cell(self, cid):
            if cid in {first.id, second.id}:
                self.advances.append(cid)
            return super()._eval_cell(cid)

    evaluator = RecordingEvaluator(
        graph,
        cg,
        calculation=_calculation(count=3, delta=1e-12),
        proof_outputs={first.id, second.id},
    )
    result = evaluator.run(set())

    assert result.coverage["workbook_iterations"] == 3
    assert [item["workbook_iterations"] for item in result.iterated] == [3, 3]
    assert evaluator.advances[:6] == [first.id, second.id] * 3


def test_convergence_on_final_allowed_iteration_is_not_budget_exhaustion():
    owner, nodes, edges = _affine("Sheet!A1", 0.001, 1)
    graph, cg = _graph(nodes, edges)
    result = Evaluator(
        graph,
        cg,
        calculation=_calculation(count=2, delta=0.01),
        proof_outputs={owner.id},
    ).run(set())

    diagnostic = result.iterated[0]
    assert diagnostic["converged"] is True
    assert diagnostic["workbook_iterations"] == 2
    assert diagnostic["budget_exhausted"] is False
    assert diagnostic["certified"] is True
    assert diagnostic["reason"] is None


def test_contractive_multicell_affine_cycle_gets_bounded_certificate():
    a = _cell("Sheet!A1", "formula", 0, "=0.2*B1+1")
    b = _cell("Sheet!B1", "formula", 0, "=0.3*A1+2")
    nodes = [a, b]
    edges = []
    for owner, source, factor, constant in (
        (a, b, 0.2, 1),
        (b, a, 0.3, 2),
    ):
        factor_node = _ast(f"{owner.id}#factor", owner.id, "const", value=factor)
        product = _ast(f"{owner.id}#product", owner.id, "op", op="*", arity=2)
        constant_node = _ast(
            f"{owner.id}#constant", owner.id, "const", value=constant
        )
        add = _ast(f"{owner.id}#add", owner.id, "op", op="+", arity=2)
        nodes.extend([factor_node, product, constant_node, add])
        edges.extend([
            _edge(source.id, product.id, 0),
            _edge(factor_node.id, product.id, 1),
            _edge(product.id, add.id, 0),
            _edge(constant_node.id, add.id, 1),
            _edge(add.id, owner.id, role="result"),
        ])
    graph, cg = _graph(nodes, edges)

    result = Evaluator(
        graph,
        cg,
        calculation=_calculation(count=100, delta=1e-9),
        proof_outputs={a.id},
    ).run(set())

    diagnostic = result.iterated[0]
    certificate = diagnostic["certification"]
    assert diagnostic["converged"] is True
    assert diagnostic["certified"] is True
    assert certificate["kind"] == "affine_system"
    assert certificate["dimension"] == 2
    assert certificate["contraction_bound"] == 0.3
    assert certificate["endpoint_error_bound"] is not None


def test_sum_range_blank_is_zero_for_affine_cycle_certificate():
    owner = _cell("Sheet!A1", "formula", 0, "=0.5*SUM(A1:B1)+1")
    total = _ast("Sheet!A1#sum", owner.id, "op", op="SUM", arity=1)
    factor = _ast("Sheet!A1#factor", owner.id, "const", value=0.5)
    product = _ast("Sheet!A1#product", owner.id, "op", op="*", arity=2)
    one = _ast("Sheet!A1#one", owner.id, "const", value=1)
    add = _ast("Sheet!A1#add", owner.id, "op", op="+", arity=2)
    graph, cg = _graph(
        [owner, total, factor, product, one, add],
        [
            _edge(owner.id, total.id, via_range="A1:B1"),
            _edge(factor.id, product.id, 0),
            _edge(total.id, product.id, 1),
            _edge(product.id, add.id, 0),
            _edge(one.id, add.id, 1),
            _edge(add.id, owner.id, role="result"),
        ],
    )

    result = Evaluator(
        graph,
        cg,
        strict_proof=True,
        calculation=_calculation(count=100, delta=1e-9),
        proof_outputs={owner.id},
    ).run(set())

    diagnostic = result.iterated[0]
    assert result.missing_reads["Sheet!B1"] == {owner.id}
    assert diagnostic["converged"] is True
    assert diagnostic["certified"] is True
    assert diagnostic["certification"]["kind"] == "scalar_affine"
    assert diagnostic["certification"]["coefficient"] == 0.5
    assert diagnostic["certification"]["fixed_point"] == 2.0


def test_sum_range_formula_returning_blank_is_zero_for_cycle_certificate():
    owner = _cell("Sheet!A1", "formula", 0, "=0.5*SUM(A1:B1)+1")
    blank_formula = _cell("Sheet!B1", "formula", 0, "=C1")
    blank_input = _cell("Sheet!C1", "input", "")
    total = _ast("Sheet!A1#sum", owner.id, "op", op="SUM", arity=1)
    factor = _ast("Sheet!A1#factor", owner.id, "const", value=0.5)
    product = _ast("Sheet!A1#product", owner.id, "op", op="*", arity=2)
    one = _ast("Sheet!A1#one", owner.id, "const", value=1)
    add = _ast("Sheet!A1#add", owner.id, "op", op="+", arity=2)
    graph, cg = _graph(
        [owner, blank_formula, blank_input, total, factor, product, one, add],
        [
            _edge(blank_input.id, blank_formula.id, role="identity"),
            _edge(owner.id, total.id, via_range="A1:B1"),
            _edge(blank_formula.id, total.id, via_range="A1:B1"),
            _edge(factor.id, product.id, 0),
            _edge(total.id, product.id, 1),
            _edge(product.id, add.id, 0),
            _edge(one.id, add.id, 1),
            _edge(add.id, owner.id, role="result"),
        ],
    )

    result = Evaluator(
        graph,
        cg,
        strict_proof=True,
        calculation=_calculation(count=100, delta=1e-9),
        proof_outputs={owner.id},
    ).run({blank_input.id})

    diagnostic = result.iterated[0]
    assert result.values[blank_formula.id] is None
    assert diagnostic["converged"] is True
    assert diagnostic["certified"] is True
    assert diagnostic["certification"]["coefficient"] == 0.5
    assert diagnostic["certification"]["fixed_point"] == 2.0


def test_cycle_topology_is_certified_after_post_iteration_stabilization():
    owner = _cell("Sheet!A1", "formula", 0, "=IF(A1,B1,C1)")
    low = _cell("Sheet!B1", "input", 1)
    high = _cell("Sheet!C1", "input", 1)
    choose = _ast("Sheet!A1#IF", owner.id, "op", op="IF", arity=3)
    graph, cg = _graph(
        [owner, low, high, choose],
        [
            _edge(owner.id, choose.id, 0),
            _edge(low.id, choose.id, 1),
            _edge(high.id, choose.id, 2),
            _edge(choose.id, owner.id, role="result"),
        ],
    )

    result = Evaluator(
        graph,
        cg,
        calculation=_calculation(count=4),
        proof_outputs={owner.id},
    ).run({low.id, high.id})

    diagnostic = result.iterated[0]
    assert result.values[owner.id] == 1
    assert diagnostic["converged"] is True
    assert diagnostic["topology_stable"] is True
    assert diagnostic["targets_stable"] is True


def test_detached_cycle_does_not_consume_budget_or_block_output(tmp_path):
    source = _cell("Sheet!A1", "input", 7)
    output = _cell("Sheet!B1", "formula", 7, "=A1")
    detached, detached_nodes, detached_edges = _affine("Sheet!Z1", -1, 1)
    graph, cg = _graph(
        [source, output, *detached_nodes],
        [_edge(source.id, output.id, role="identity"), *detached_edges],
    )
    result = Evaluator(
        graph,
        cg,
        calculation=_calculation(count=4),
        proof_outputs={output.id},
    ).run({source.id})

    diagnostic = result.iterated[0]
    assert result.values[output.id] == 7
    assert diagnostic["output_relevant"] is False
    assert diagnostic["evaluated"] is False
    assert diagnostic["iterations"] == 0
    assert not isinstance(result.values[detached.id], Unresolved)

    bg, cd, part = _verification_shape(cg, output)
    source_path, ast, curation = _evidence(tmp_path)
    report = _verify(
        cg,
        result,
        {source.id},
        {output.id},
        part,
        cd,
        bg,
        SimpleNamespace(sample=10),
        expected_cache=_ExpectedCache({output.id: 7}),
        calculation=_calculation(count=4),
        source_path=source_path,
        ast_dir=ast,
        curation_path=curation,
        proof={
            "runtime_radj": {output.id: [source.id]},
            "closure": {"stabilized": True},
        },
    )
    assert report["status"] == "pass"
    assert report["counts"]["cycles"]["output_relevant"] == 0


def test_unexecuted_middle_sample_is_reported_not_run(tmp_path):
    output = _cell("Sheet!A1", "formula", 5, "=5")
    middle = _cell("Sheet!B1", "formula", 9, "=A1+4")
    output_const = _ast("Sheet!A1#const", output.id, "const", value=5)
    middle_const = _ast("Sheet!B1#const", middle.id, "const", value=4)
    middle_add = _ast("Sheet!B1#add", middle.id, "op", op="+", arity=2)
    graph, cg = _graph(
        [output, middle, output_const, middle_const, middle_add],
        [
            _edge(output_const.id, output.id, role="constant"),
            _edge(output.id, middle_add.id, 0),
            _edge(middle_const.id, middle_add.id, 1),
            _edge(middle_add.id, middle.id, role="result"),
        ],
    )
    result = Evaluator(
        graph,
        cg,
        strict_proof=True,
        proof_scope={output.id},
        proof_outputs={output.id},
        calculation=_calculation(enabled=False),
    ).run(set())
    bg = SimpleNamespace(bands={
        "output-band": SimpleNamespace(cells=[output.id]),
        "middle-band": SimpleNamespace(cells=[middle.id]),
    })
    cd = SimpleNamespace(comp_members={
        "output-comp": {"output-band"},
        "middle-comp": {"middle-band"},
    })
    part = SimpleNamespace(bucket={
        "output-comp": partition.OUTPUT,
        "middle-comp": partition.MIDDLE,
    })
    source, ast, curation = _evidence(tmp_path)
    expected = _ExpectedCache({output.id: 5, middle.id: 9})

    report = _verify(
        cg,
        result,
        set(),
        {output.id},
        part,
        cd,
        bg,
        SimpleNamespace(sample=10),
        expected_cache=expected,
        calculation=_calculation(enabled=False),
        source_path=source,
        ast_dir=ast,
        curation_path=curation,
        proof={
            "runtime_radj": {output.id: []},
            "closure": {"stabilized": True},
        },
    )

    assert report["status"] == "pass"
    assert report["counts"]["middle"]["not_run"] == 1
    assert report["counts"]["middle"]["unresolved"] == 0
    assert report["samples"]["middle_not_run_cells"] == [middle.id]
    assert middle.id not in expected.reads


def test_cycle_driven_branch_change_marks_topology_unstable(tmp_path):
    owner = _cell("Sheet!A1", "formula", 0, "=IF(A1,B1,C1)")
    low = _cell("Sheet!B1", "input", 0)
    high = _cell("Sheet!C1", "input", 1)
    choose = _ast("Sheet!A1#IF", owner.id, "op", op="IF", arity=3)
    graph, cg = _graph(
        [owner, low, high, choose],
        [
            _edge(owner.id, choose.id, 0),
            _edge(low.id, choose.id, 1),
            _edge(high.id, choose.id, 2),
            _edge(choose.id, owner.id, role="result"),
        ],
    )
    result = Evaluator(
        graph,
        cg,
        calculation=_calculation(count=1),
        proof_outputs={owner.id},
    ).run({low.id, high.id})

    assert result.iterated[0]["topology_stable"] is False
    bg, cd, part = _verification_shape(cg, owner)
    source_path, ast, curation = _evidence(tmp_path)
    report = _verify(
        cg,
        result,
        {low.id, high.id},
        {owner.id},
        part,
        cd,
        bg,
        SimpleNamespace(sample=10),
        expected_cache=_ExpectedCache({owner.id: result.values[owner.id]}),
        calculation=_calculation(count=1),
        source_path=source_path,
        ast_dir=ast,
        curation_path=curation,
        proof={
            "runtime_radj": {
                target: sorted(sources)
                for target, sources in result.runtime_radj.items()
            },
            "closure": {"stabilized": True},
        },
    )
    assert "active_cycle_topology_not_stabilized" in report["blocking_reasons"]


class _ExpectedCache:
    def __init__(self, values):
        self.values = values
        self.reads = {}

    def __call__(self, sheet, row, col):
        ref = f"{sheet}!{chr(64 + col)}{row}"
        self.reads[ref] = self.reads.get(ref, 0) + 1
        return self.values.get(ref)


def _verification_shape(cg, output):
    bg = SimpleNamespace(
        bands={"output-band": SimpleNamespace(cells=[output.id])}
    )
    cd = SimpleNamespace(comp_members={"output-comp": {"output-band"}})
    part = SimpleNamespace(
        bucket={"output-comp": partition.OUTPUT}
    )
    return bg, cd, part


def _evidence(tmp_path):
    source = tmp_path / "book.xlsx"
    source.write_bytes(b"book")
    ast = tmp_path / "ast"
    ast.mkdir()
    (ast / "nodes.csv").write_text("nodes", encoding="utf-8")
    (ast / "edges.csv").write_text("edges", encoding="utf-8")
    curation = tmp_path / "curation.toml"
    curation.write_text("[[output]]\n", encoding="utf-8")
    return source, ast, curation


def test_cycle_endpoint_equivalence_is_delta_bounded_but_acyclic_stays_strict(
    tmp_path,
):
    owner, nodes, edges = _affine("Sheet!A1", 0.5, 1, cached=2)
    graph, cg = _graph(nodes, edges)
    calculation = _calculation(delta=0.01)
    result = Evaluator(
        graph,
        cg,
        strict_proof=True,
        calculation=calculation,
        proof_outputs={owner.id},
    ).run(set())
    bg, cd, part = _verification_shape(cg, owner)
    source, ast, curation = _evidence(tmp_path)
    proof = {
        "runtime_radj": {owner.id: [owner.id]},
        "closure": {"stabilized": True},
    }
    report = _verify(
        cg,
        result,
        set(),
        {owner.id},
        part,
        cd,
        bg,
        SimpleNamespace(sample=10),
        expected_cache=_ExpectedCache({owner.id: 2.0}),
        calculation=calculation,
        source_path=source,
        ast_dir=ast,
        curation_path=curation,
        proof=proof,
    )
    assert abs(result.values[owner.id] - 2.0) > 1e-6
    assert result.iterated[0]["certified"] is True
    assert report["status"] == "pass"
    assert report["outputs"]["match"] == 1

    source_cell = _cell("Sheet!A1", "input", 1)
    acyclic = _cell("Sheet!B1", "formula", 1, "=A1")
    graph, cg = _graph(
        [source_cell, acyclic],
        [_edge(source_cell.id, acyclic.id, role="identity")],
    )
    result = Evaluator(
        graph,
        cg,
        strict_proof=True,
        calculation=_calculation(enabled=False),
        proof_outputs={acyclic.id},
    ).run({source_cell.id})
    bg, cd, part = _verification_shape(cg, acyclic)
    report = _verify(
        cg,
        result,
        {source_cell.id},
        {acyclic.id},
        part,
        cd,
        bg,
        SimpleNamespace(sample=10),
        expected_cache=_ExpectedCache({acyclic.id: 1.01}),
        calculation=_calculation(enabled=False),
        source_path=source,
        ast_dir=ast,
        curation_path=curation,
        proof={
            "runtime_radj": {acyclic.id: [source_cell.id]},
            "closure": {"stabilized": True},
        },
    )
    assert report["status"] == "fail"
    assert report["outputs"]["mismatch"] == 1


def test_uncertified_active_cycle_cannot_pass_an_exact_cache(tmp_path):
    owner = _cell("Sheet!A1", "formula", 0, "=ABS(A1)/2")
    absolute = _ast("Sheet!A1#abs", owner.id, "op", op="ABS", arity=1)
    two = _ast("Sheet!A1#two", owner.id, "const", value=2)
    divide = _ast("Sheet!A1#divide", owner.id, "op", op="/", arity=2)
    graph, cg = _graph(
        [owner, absolute, two, divide],
        [
            _edge(owner.id, absolute.id, 0),
            _edge(absolute.id, divide.id, 0),
            _edge(two.id, divide.id, 1),
            _edge(divide.id, owner.id, role="result"),
        ],
    )
    calculation = _calculation(count=100, delta=0.001)
    result = Evaluator(
        graph,
        cg,
        strict_proof=True,
        calculation=calculation,
        proof_outputs={owner.id},
    ).run(set())
    bg, cd, part = _verification_shape(cg, owner)
    source, ast, curation = _evidence(tmp_path)
    report = _verify(
        cg,
        result,
        set(),
        {owner.id},
        part,
        cd,
        bg,
        SimpleNamespace(sample=10),
        expected_cache=_ExpectedCache({owner.id: 0}),
        calculation=calculation,
        source_path=source,
        ast_dir=ast,
        curation_path=curation,
        proof={
            "runtime_radj": {owner.id: [owner.id]},
            "closure": {"stabilized": True},
        },
    )

    assert result.iterated[0]["converged"] is True
    assert result.iterated[0]["certified"] is False
    assert report["status"] == "fail"
    assert "active_cycle_uncertified" in report["blocking_reasons"]

    mismatch_report = _verify(
        cg,
        result,
        set(),
        {owner.id},
        part,
        cd,
        bg,
        SimpleNamespace(sample=10),
        expected_cache=_ExpectedCache({owner.id: 0.1}),
        calculation=calculation,
        source_path=source,
        ast_dir=ast,
        curation_path=curation,
        proof={
            "runtime_radj": {owner.id: [owner.id]},
            "closure": {"stabilized": True},
        },
    )
    assert mismatch_report["outputs"]["mismatch"] == 1


def test_unknown_cycle_count_and_delta_origins_block(tmp_path):
    owner, nodes, edges = _affine("Sheet!A1", 0.5, 1, cached=2)
    graph, cg = _graph(nodes, edges)
    calculation = CalculationMetadata(
        available=True,
        iterate=True,
        iterate_count=100,
        iterate_delta=0.001,
        iterate_origin="explicit",
        iterate_count_origin="unknown",
        iterate_delta_origin="unknown",
    )
    result = Evaluator(
        graph,
        cg,
        strict_proof=True,
        calculation=calculation,
        proof_outputs={owner.id},
    ).run(set())
    bg, cd, part = _verification_shape(cg, owner)
    source, ast, curation = _evidence(tmp_path)
    report = _verify(
        cg,
        result,
        set(),
        {owner.id},
        part,
        cd,
        bg,
        SimpleNamespace(sample=10),
        expected_cache=_ExpectedCache({owner.id: 2}),
        calculation=calculation,
        source_path=source,
        ast_dir=ast,
        curation_path=curation,
        proof={
            "runtime_radj": {owner.id: [owner.id]},
            "closure": {"stabilized": True},
        },
    )

    assert "active_cycle_iteration_count_unknown" in report["blocking_reasons"]
    assert "active_cycle_iteration_delta_unknown" in report["blocking_reasons"]

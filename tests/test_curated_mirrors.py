"""Curated mirrors remain exact targets without becoming cached inputs."""
import pytest
from xl_seg import condense as proposal, condense, emit, frontier, partition, publication
from xl_seg.bands import Band, BandGraph
from xl_seg.project import NUMERIC
from xl_segment import _curated_output_identity as identity

def graph(mirror_cycle=False):
    # Inputs -> Calculation -> Alias -> Dashboard; Calc also feeds another output.
    flags = {"Input!A1": False, "Calc!A1": False, "Alias!A1": True,
             "Dashboard!A1": True, "Other!A1": False}
    bands = {
        key: Band(key, key.split("!")[0], 1, 1, 1,
                  "input" if key.startswith("Input") else "formula", "",
                  NUMERIC, key.split("!")[0], [key], mirror, False)
        for key, mirror in flags.items()
    }
    edges = [("Input!A1", "Calc!A1"), ("Calc!A1", "Alias!A1"),
             ("Alias!A1", "Dashboard!A1"), ("Calc!A1", "Other!A1")]
    if mirror_cycle:
        edges = [("Alias!A1", "Dashboard!A1"), ("Dashboard!A1", "Alias!A1")]
    adj, radj = {}, {}
    for a, b in edges:
        adj.setdefault(a, set()).add(b)
        radj.setdefault(b, set()).add(a)
    return BandGraph(bands, {key: key for key in bands}, adj, radj)


def emit_fixture(tmp_path, bg, cd, fn, chosen):
    entries = [{"band": x, "include": True} for x in chosen]
    outputs, selected, cells, ordered = fn(entries, set(chosen), cd, bg)
    inputs = frontier.input_frontier(cd, outputs)
    part = partition.build(cd, inputs, outputs)
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "curation.toml").write_text("\n".join(
        f'[[output]]\nband = "{b}"\ninclude = true\n' for b in chosen))
    emit.write_segments(tmp_path, "synthetic", bg, cd, part, inputs, outputs,
                        [], {}, selected_output_bands=selected)
    return outputs, selected, cells, ordered, inputs, part




def test_retains_exact_curated_targets_and_true_input_frontier(tmp_path):
    bg = graph()
    chosen = ["Other!A1", "Dashboard!A1"]
    cd = proposal.build(bg, retain_bands=chosen)
    outputs, selected, cells, ordered, inputs, part = emit_fixture(tmp_path, bg, cd, identity, chosen)
    assert selected == set(chosen) and cells == set(chosen) and ordered == chosen
    assert inputs == {"Input!A1"}
    assert not outputs & inputs
    assert part.bucket["Calc!A1"] == partition.MIDDLE
    assert cd.mirrors == {"Alias!A1"}
    assert publication._curated_cells_from_artifacts(tmp_path) == chosen


def test_only_selected_mirror_also_retains_a_calculation_as_middle(tmp_path):
    bg = graph()
    chosen = ["Dashboard!A1"]
    cd = proposal.build(bg, retain_bands=chosen)
    out = emit_fixture(tmp_path, bg, cd, identity, chosen)
    assert out[4] == {"Input!A1"}
    assert out[5].bucket["Calc!A1"] == partition.MIDDLE
    assert publication._curated_cells_from_artifacts(tmp_path) == chosen


def test_selected_source_and_mirror_remain_distinct_targets_in_curation_order(tmp_path):
    bg = graph()
    chosen = ["Dashboard!A1", "Calc!A1"]
    cd = proposal.build(bg, retain_bands=chosen)
    out = emit_fixture(tmp_path, bg, cd, identity, chosen)
    assert out[3] == chosen and out[4] == {"Input!A1"}
    assert publication._curated_cells_from_artifacts(tmp_path) == chosen




def test_unknown_curation_rejected_before_any_target_is_silently_dropped():
    bg = graph()
    with pytest.raises(ValueError, match="absent from band graph"):
        proposal.build(bg, retain_bands={"Missing!A1"})
    with pytest.raises(ValueError, match="identity is incomplete"):
        identity([{"band": "Missing!A1", "include": True}], {"Missing!A1"}, condense.build(bg), bg)


def test_unrepresented_mirror_is_rejected_by_identity_defense():
    bg = graph()
    with pytest.raises(ValueError, match="unrepresented bands"):
        identity([{"band": "Dashboard!A1", "include": True}], {"Dashboard!A1"}, condense.build(bg), bg)


def test_selected_mirror_cycle_is_not_replaced_by_a_primitive_input():
    bg = graph(mirror_cycle=True)
    cd = proposal.build(bg, retain_bands={"Dashboard!A1"})
    assert cd.adj["Dashboard!A1"] == {"Dashboard!A1"}
    # Topology retention makes the recurrence visible. Full strict proof must
    # still reject this ungrounded cycle; no cached value is supplied here.
    assert bg.bands["Dashboard!A1"].kind == "formula"


def test_existing_scc_identity_keeps_only_the_explicit_band():
    from types import SimpleNamespace
    bg = SimpleNamespace(bands={"chosen": SimpleNamespace(cells=["S!A1"]), "sibling": SimpleNamespace(cells=["S!B1"])})
    cd = SimpleNamespace(comp_of={"chosen": "cycle", "sibling": "cycle"})
    assert identity([{"band": "chosen", "include": True}], {"chosen"}, cd, bg) == ({"cycle"}, {"chosen"}, {"S!A1"}, ["S!A1"])


def proof_graph(cycle=False):
    from xl_seg.model import Node, Edge, Graph
    from xl_seg import project, bands
    nodes = []
    for key, kind, formula, cache in [
        ("Input!A1", "formula" if cycle else "input", "=Dashboard!A1" if cycle else "", "999" if cycle else "7"),
        ("Dashboard!A1", "formula", "=Input!A1", "123456"),
    ]:
        sheet, coordinate = key.split("!")
        nodes.append(Node(id=key, kind=kind, sheet=sheet, coordinate=coordinate,
            row=1, col=1, owner="", op="", op_kind="", arity="", expr="",
            label=sheet, formula=formula, value=cache, in_cycle=cycle))
    edges = [Edge(source="Input!A1", target="Dashboard!A1", role="identity", arg_index=0,
                  op="", cell="Dashboard!A1", ref="Input!A1", via_range="", cross_sheet=True)]
    if cycle:
        edges.append(Edge(source="Dashboard!A1", target="Input!A1", role="identity", arg_index=0,
                          op="", cell="Input!A1", ref="Dashboard!A1", via_range="", cross_sheet=True))
    graph = Graph("synthetic", {n.id:n for n in nodes}, edges)
    for e in edges:
        graph.in_edges.setdefault(e.target, []).append(e)
        graph.out_edges.setdefault(e.source, []).append(e)
    cg = project.build(graph)
    bg = bands.build(cg)
    return graph, cg, bg


def test_strict_proof_reconstructs_selected_mirror_without_its_wrong_cache():
    from xl_segment import stabilize_runtime_proof
    from xl_seg.evaluate import CalculationMetadata
    graph, cg, bg = proof_graph()
    selected = bg.of_cell["Dashboard!A1"]
    cd = proposal.build(bg, retain_bands={selected})
    comps = frontier.input_frontier(cd, {cd.comp_of[selected]})
    seeds = {c for comp in comps for b in cd.comp_members[comp] for c in bg.bands[b].cells}
    result, effective, _, proof = stabilize_runtime_proof(graph, cg, seeds,
        {"Dashboard!A1"}, calculation=CalculationMetadata(available=True, iterate=False))
    assert result.values["Dashboard!A1"] == 7
    assert effective == {"Input!A1"}
    assert "Dashboard!A1" not in proof["effective_inputs"]
    assert not result.unresolved


def test_underdetermined_mirror_cycle_is_not_recovered_from_cached_answers():
    from xl_segment import stabilize_runtime_proof
    from xl_seg.evaluate import CalculationMetadata
    graph, cg, bg = proof_graph(cycle=True)
    selected = bg.of_cell["Dashboard!A1"]
    cd = proposal.build(bg, retain_bands={selected})
    comps = frontier.input_frontier(cd, {cd.comp_of[selected]})
    seeds = {c for comp in comps for b in cd.comp_members[comp] for c in bg.bands[b].cells}
    result, effective, _, proof = stabilize_runtime_proof(graph, cg, seeds,
        {"Dashboard!A1"}, calculation=CalculationMetadata(available=True, iterate=True))
    assert effective == set()
    assert proof["effective_inputs"] == []
    assert "Dashboard!A1" in result.unresolved


def test_default_graph_still_bypasses_unselected_mirrors():
    cd = condense.build(graph())
    assert cd.mirrors == {"Alias!A1", "Dashboard!A1"}
    assert not cd.mirrors.intersection(cd.comp_of)
    assert cd.adj["Calc!A1"] == {"Other!A1"}

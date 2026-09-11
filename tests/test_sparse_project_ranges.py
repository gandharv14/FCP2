"""Sparse projection preserves existing range grammar and dependency identity."""
from dataclasses import fields

import pytest

from xl_seg import model, project


@pytest.mark.parametrize("sheet", ["Sheet", "2026 Base", "O'Brien", "'O''Brien'", "Bang!Sheet"])
@pytest.mark.parametrize("span", ["A1:C4", "C4:A1", "b3:a1", "A0:C2", "A1", "$A$1:C4",
                                  "A1:B2,C3:D4", "Sheet!A1:C4", "A1:", "A1:ZZZ99999999"])
def test_sparse_intersection_matches_existing_closed_reference_grammar(sheet, span):
    ids = [model.a1(sheet, r, c) for r, c in [(4, 3), (1, 1), (0, 2), (2, 2), (5, 7)]]
    ids += [f"{sheet}!a1", f"{sheet}!A01", "Other!A1", "invalid"]
    info = dict.fromkeys(ids)
    expected = [cell for cell in model.range_members(sheet, span) if cell in info]
    actual = project._sparse_range_members(sheet, span, project._cell_coordinate_index(info))
    assert actual == expected


def node(node_id, kind, *, sheet="Sheet", coordinate="", owner="", value=""):
    kwargs = {f.name: "" for f in fields(model.Node)}
    kwargs.update(id=node_id, kind=kind, sheet=sheet, coordinate=coordinate, owner=owner,
                  value=value, row=None, col=None, array_row=None, array_col=None,
                  in_cycle=False, array_formula=False, range_truncated=True)
    return model.Node(**kwargs)


def test_maximum_height_and_width_range_projects_only_populated_cells(monkeypatch):
    # Enumerating this range would allocate >17 billion cell strings. The AST
    # truncation flag still reaches downstream proof gates unchanged.
    cells = ["Sheet!A1", "Sheet!Z1048576", "Sheet!XFD1048576", "Sheet!XFE1", "Other!A1"]
    nodes = {cid: node(cid, "input", value="1") for cid in cells}
    nodes["Sheet!B2"] = node("Sheet!B2", "formula", value="3")
    nodes["range"] = node("range", "range", coordinate="A1:XFD1048576")
    edge = model.Edge("range", "Sheet!B2", "arg", 0, "", "", "", "", False)
    graph = model.Graph("fixture", nodes, [edge], {"Sheet!B2": [edge]}, {"range": [edge]})
    monkeypatch.setattr(model, "range_members", lambda *args: pytest.fail("dense enumeration is forbidden"))
    cg = project.build(graph)
    assert cg.predecessors("Sheet!B2") == {"Sheet!A1", "Sheet!Z1048576", "Sheet!XFD1048576"}
    assert nodes["range"].range_truncated is True


def test_index_uses_exact_id_not_contradictory_coordinate_metadata():
    info = {"A!B3": object(), "A!b3": object(), "A!B03": object(), "B!B3": object()}
    assert project._sparse_range_members("A", "A1:C4", project._cell_coordinate_index(info)) == ["A!B3"]

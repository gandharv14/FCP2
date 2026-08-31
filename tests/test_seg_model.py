from __future__ import annotations

import csv
from pathlib import Path

from xl_seg.model import load


NODE_FIELDS = [
    "id",
    "kind",
    "sheet",
    "coordinate",
    "row",
    "col",
    "owner",
    "op",
    "op_kind",
    "arity",
    "expr",
    "label",
    "formula",
    "value",
    "in_cycle",
    "array_formula",
    "array_anchor",
    "array_ref",
    "array_row",
    "array_col",
    "ast_schema_version",
    "parse_status",
    "parse_error",
    "value_type",
    "range_truncated",
]
EDGE_FIELDS = [
    "source",
    "target",
    "role",
    "arg_index",
    "op",
    "cell",
    "ref",
    "via_range",
    "cross_sheet",
]


def _write_graph(path: Path, node: dict[str, object]) -> None:
    path.mkdir()
    with (path / "nodes.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=NODE_FIELDS)
        writer.writeheader()
        writer.writerow({
            **{field: "" for field in NODE_FIELDS},
            "sheet": "Sheet",
            "ast_schema_version": "xl-ast/v2",
            "parse_status": "not_applicable",
            "value_type": "number",
            **node,
        })
    with (path / "edges.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=EDGE_FIELDS)
        writer.writeheader()


def test_shared_range_node_does_not_require_formula_owner(tmp_path: Path) -> None:
    ast = tmp_path / "ast"
    _write_graph(ast, {
        "id": "Sheet!A1:A100",
        "kind": "range",
        "coordinate": "A1:A100",
        "row": 1,
        "col": 1,
        "owner": "",
    })

    graph = load(ast, "book")

    assert graph.integrity_errors == []


def test_operation_node_still_requires_formula_owner(tmp_path: Path) -> None:
    ast = tmp_path / "ast"
    _write_graph(ast, {
        "id": "Sheet!A1#0:SUM",
        "kind": "op",
        "op": "SUM",
        "owner": "",
    })

    graph = load(ast, "book")

    assert graph.integrity_errors == [{
        "code": "invalid_ast_owner",
        "node": "Sheet!A1#0:SUM",
        "owner": "",
    }]

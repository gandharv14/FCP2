"""CSV-only Harbor production output leaves the graph and downstream results unchanged."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import zipfile
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import openpyxl
import pytest

import xl_ast_graph
import xl_segment
from xl_seg import emit


DEBUG_ARTIFACTS = ("graph.json", "graph.graphml", "graph.html")
REPO = Path(__file__).resolve().parents[1]
DISCLOSE = REPO / ".cursor/skills/task-disclosure/scripts"
WB = "prod01"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _write_small_workbook(path: Path) -> None:
    book = openpyxl.Workbook()
    ws = book.active
    ws.title = "Summary"
    ws["A1"] = "Revenue"
    ws["B1"] = 100
    ws["A2"] = "Growth"
    ws["B2"] = 0.1
    ws["A3"] = "IRR"
    ws["B3"] = "=B1*(1+B2)"
    book.save(path)
    _inject_cached_value(path, "B3", "110")


def _inject_cached_value(path: Path, coord: str, value: str) -> None:
    with zipfile.ZipFile(path) as src:
        parts = {item.filename: src.read(item.filename) for item in src.infolist()}
    sheet = "xl/worksheets/sheet1.xml"
    xml = parts[sheet]
    needle = f'r="{coord}"'.encode()
    attr = xml.find(needle)
    assert attr != -1, f"{coord} missing from {sheet}"
    start = xml.rfind(b"<c", 0, attr)
    close = xml.find(b"</c>", attr)
    assert start != -1 and close != -1
    end = close + len(b"</c>")
    cell = xml[start:end]
    new_v = f"<v>{value}</v>".encode()
    if re.search(rb"<v\b", cell):
        cell = re.sub(rb"<v\b[^/]*/>|<v>[^<]*</v>", new_v, cell, count=1)
    else:
        cell = cell.replace(b"</c>", new_v + b"</c>", 1)
    parts[sheet] = xml[:start] + cell + xml[end:]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as out:
        for name, data in parts.items():
            out.writestr(name, data)


def _blank_formula(src: Path, dest: Path, coord: str) -> None:
    shutil.copy2(src, dest)
    book = openpyxl.load_workbook(dest)
    ws = book.active
    ws[coord] = None
    book.save(dest)


def _build_ast(workbook: Path, out_dir: Path, *flags: str) -> None:
    xl_ast_graph.main([str(workbook), "-o", str(out_dir), "-q", *flags])


def _segment(wb: str, ast_root: Path, source: Path, seg_root: Path, env_file: Path):
    args = SimpleNamespace(
        ast_dir=str(ast_root),
        source=str(source),
        out=str(seg_root),
        threshold=6.0,
        top=40,
        sample=400,
        lineage_max=4000,
        recurate=False,
        llm=False,
        model="unused",
        env_file=str(env_file),
        no_verify=False,
    )
    return xl_segment.segment(wb, args)


def _select(task_dir: Path, golden: Path, ast_dir: Path) -> dict:
    sys.path.insert(0, str(DISCLOSE))
    try:
        import disclose
    finally:
        if str(DISCLOSE) in sys.path:
            sys.path.remove(str(DISCLOSE))
    args = Namespace(
        task_dir=str(task_dir),
        golden=str(golden),
        ast_dir=str(ast_dir),
        out=None,
        runs_root=str(task_dir / "runs"),
    )
    return disclose.select_payload(args)


@pytest.fixture
def small_model(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    workbook = source / f"{WB}.xlsx"
    _write_small_workbook(workbook)
    return workbook


def test_production_csv_matches_default_and_skips_debug_artifacts(small_model, tmp_path):
    source = small_model.parent
    default_root = tmp_path / "ast_default"
    production_root = tmp_path / "ast_production"
    _build_ast(small_model, default_root)
    _build_ast(small_model, production_root, "--production")

    default_dir = default_root / WB
    production_dir = production_root / WB
    assert _sha256(default_dir / "nodes.csv") == _sha256(production_dir / "nodes.csv")
    assert _sha256(default_dir / "edges.csv") == _sha256(production_dir / "edges.csv")
    for name in DEBUG_ARTIFACTS:
        assert not (production_dir / name).exists()
        assert (default_dir / name).is_file()

    default_seg = tmp_path / "seg_default"
    production_seg = tmp_path / "seg_production"
    env_file = tmp_path / "no.env"
    default_payload = _segment(WB, default_root, source, default_seg, env_file)
    production_payload = _segment(WB, production_root, source, production_seg, env_file)
    assert production_payload["verification"] == default_payload["verification"]
    assert production_payload["verification"].get("passed") is True
    assert (
        json.loads((default_seg / WB / "segments.json").read_text(encoding="utf-8"))
        ["verification"]
        == json.loads((production_seg / WB / "segments.json").read_text(encoding="utf-8"))
        ["verification"]
    )
    assert emit.read_curation(default_seg / WB / "curation.toml")
    assert emit.read_curation(production_seg / WB / "curation.toml")

    task_dir = tmp_path / f"{WB}-outputs"
    env = task_dir / "environment"
    tests = task_dir / "tests"
    env.mkdir(parents=True)
    tests.mkdir()
    delivered = env / f"{WB}-inputs.xlsx"
    _blank_formula(small_model, delivered, "B3")
    (tests / "answer_key.json").write_text(
        json.dumps({"targets": {"Summary!B3": 110}, "tolerance": {}}, indent=2),
        encoding="utf-8",
    )
    default_select = _select(task_dir, small_model, default_root)
    production_select = _select(task_dir, small_model, production_root)
    for payload in (default_select, production_select):
        assert payload["selection"]["closure_source"] == "ast"
        assert payload["selection"]["ast_status"] == "ok"
    assert production_select["selection"] == default_select["selection"]
    assert [band["cell_keys"] for band in production_select["bands"]] == [
        band["cell_keys"] for band in default_select["bands"]
    ]

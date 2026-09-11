"""Materialize sensitivity tables only after proving exclusion from targets.

The inspection generation is never a source approval. The candidate must still
pass source publication, independent reconstruction, and every packaging gate.
"""
from __future__ import annotations

from argparse import Namespace
import json
import math
from pathlib import Path
import shutil
from xml.etree import ElementTree as ET

import openpyxl
from openpyxl.utils.cell import range_boundaries, get_column_letter

from scripts.batch.hardened_runner import atomic_write_json
from xl_volatile_formula_remediation import (_package, _sheet_parts, _cell_blocks,
    FORMULA_TAG_RE, _attrs, _write_package, _value_ledger, sha256_file,
    canonical_bytes, sha256_bytes)

POLICY = "presentation-data-tables/v2"


def table_inventory(members):
    tables = []
    for sheet, part in _sheet_parts(members):
        root = ET.fromstring(members[part])
        for cell in root.findall(".//{*}sheetData/{*}row/{*}c"):
            formula = cell.find("{*}f")
            if formula is None or formula.get("t") != "dataTable":
                continue
            bounds = range_boundaries(formula.get("ref", ""))
            if None in bounds:
                raise ValueError("data table has no bounded result rectangle")
            c1, r1, c2, r2 = bounds
            if (c2-c1+1)*(r2-r1+1) > 100000:
                raise ValueError("data table exceeds inspection budget")
            cells = [f"{sheet}!{get_column_letter(c)}{r}"
                     for c in range(c1, c2+1) for r in range(r1, r2+1)]
            if f"{sheet}!{cell.get('r')}" not in cells:
                raise ValueError("data table anchor is outside result rectangle")
            tables.append(dict(sheet=sheet, part=part, anchor=cell.get("r"),
                               rectangle=formula.get("ref"), cells=cells))
    if not tables:
        raise ValueError("source contains no data tables")
    return tables


def check_exclusion(tables, payload):
    from xl_preflight import classify
    from xl_seg.restriction_cone import _proof_cone
    verification = payload.get("verification", {})
    if classify(verification, True)[0] != "healthy":
        raise ValueError("original workbook reconstruction is not healthy")
    outputs = verification.get("ordered_output_cells")
    radj = payload.get("proof", {}).get("runtime_radj")
    if not outputs or not isinstance(radj, dict):
        raise ValueError("complete output dependency proof is required")
    cone = _proof_cone(outputs, radj)
    table_cells = {cell for table in tables for cell in table["cells"]}
    if table_cells & cone:
        raise ValueError("data table intersects a graded dependency cone")
    return outputs, sorted(cone)


def scan_leaks(source, tables, outputs):
    book = openpyxl.load_workbook(source, data_only=True, read_only=True)
    try:
        def value(key):
            sheet, coordinate = key.rsplit("!", 1)
            return book[sheet][coordinate].value
        targets = [value(key) for key in outputs]
        for table in tables:
            for key in table["cells"]:
                observed = value(key)
                for target in targets:
                    if isinstance(target, (float, int)) and not isinstance(target, bool):
                        if isinstance(observed, (float, int)) and math.isclose(observed, target, rel_tol=1e-6, abs_tol=1e-9):
                            raise ValueError("presentation table discloses a graded numeric answer")
                    elif observed is not None and observed == target:
                        raise ValueError("presentation table discloses a graded answer")
    finally:
        book.close()


def transformed_members(members, tables):
    after = dict(members)
    for part in sorted({table["part"] for table in tables}):
        xml = members[part].decode("utf-8")
        blocks = _cell_blocks(xml)
        for table in (t for t in tables if t["part"] == part):
            block = blocks[table["anchor"]]["block"]
            matches = list(FORMULA_TAG_RE.finditer(block))
            if len(matches) != 1 or _attrs(matches[0].group("attrs") or matches[0].group("self_attrs") or "").get("t") != "dataTable":
                raise ValueError("data table XML does not match inventory")
            replacement = FORMULA_TAG_RE.sub("", block)
            if xml.count(block) != 1:
                raise ValueError("data table anchor XML is ambiguous")
            xml = xml.replace(block, replacement, 1)
        after[part] = xml.encode("utf-8")
    if _value_ledger(members) != _value_ledger(after):
        raise ValueError("materialization changed workbook values or caches")
    return after


def materialize(source, workbook_id, curation, output_root, *, allow_initial_curation=False):
    from xl_ast_graph import main as ast_main
    from xl_segment import segment
    from xl_seg.publication import validate_generation_directory
    from xl_source_health import inspect_workbook
    source, curation, root = Path(source), Path(curation), Path(output_root)
    if source.is_symlink() or not source.is_file() or (not curation.is_file() and not allow_initial_curation):
        raise ValueError("materialization requires a regular source and retained curation")
    if root.exists():
        raise ValueError("materialization output already exists")
    root.mkdir(parents=True)
    infos, members = _package(source)
    tables = table_inventory(members)
    private = root / "inspection-source"
    private.mkdir()
    inspected = private / f"{workbook_id}.xlsx"
    shutil.copyfile(source, inspected)
    ast_main([str(inspected), "-o", str(root / "ast"), "--production", "--quiet"])
    from xl_source_publication import create_ast_provenance
    ast_provenance = create_ast_provenance(inspected, root / "ast" / workbook_id,
                                         builder_args=["--production", "--quiet"])
    segmentation = root / "inspection-segments" / workbook_id
    segmentation.mkdir(parents=True)
    retained = curation.is_file()
    if retained:
        shutil.copyfile(curation, segmentation / "curation.toml")
    options = Namespace(ast_dir=str(root / "ast"), source=str(private), out=str(segmentation.parent),
        allow_legacy_ast=True, threshold=6.0, top=40, sample=400, lineage_max=4000,
        recurate=False, llm=False, no_auto_adjudication=True, model="", env_file="", no_verify=False)
    payload = segment(workbook_id, options)
    if not retained:
        curation = segmentation / "curation.toml"
    elif curation.read_bytes() != (segmentation / "curation.toml").read_bytes():
        raise ValueError("retained curation changed during inspection")
    generation = Path(payload["generation"]["directory"])
    validate_generation_directory(generation, require_pass=True)
    outputs, cone = check_exclusion(tables, payload)
    scan_leaks(inspected, tables, outputs)
    candidate = root / f"{workbook_id}.xlsx"
    after = transformed_members(members, tables)
    _write_package(infos, after, candidate)
    health = inspect_workbook(candidate)
    atomic_write_json(root / "health.json", health)
    if health["route"] not in {"pass", "restricted_pass"}:
        raise ValueError("materialized candidate still requires source support")
    if sha256_file(source) != sha256_file(inspected):
        raise ValueError("original source changed during inspection")
    receipt = dict(policy_version=POLICY, original_sha256=sha256_file(source),
        candidate_sha256=sha256_file(candidate), curation=str(curation), curation_sha256=sha256_file(curation),
        curation_origin="retained" if retained else "initial_selection_on_original",
        inspection_generation=str(generation), inspection_manifest_sha256=sha256_file(generation / "generation-manifest.json"),
        inspection_ast_provenance=ast_provenance,
        ordered_outputs=outputs, excluded_cone_sha256=sha256_bytes(canonical_bytes(cone)),
        tables=tables, leak_scan="passed", disposition="presentation_only_materialized")
    receipt["manifest_sha256"] = sha256_bytes(canonical_bytes(receipt))
    atomic_write_json(root / "manifest.json", receipt)
    return candidate, receipt


def verify_materialization(source, candidate, curation, receipt):
    from xl_seg.publication import validate_generation_directory, resolve_generation_by_id
    from xl_source_publication import validate_ast_provenance, _default_builder_paths
    unsigned = {k:v for k,v in receipt.items() if k != "manifest_sha256"}
    if (receipt.get("policy_version") != POLICY
            or receipt.get("manifest_sha256") != sha256_bytes(canonical_bytes(unsigned))
            or receipt.get("original_sha256") != sha256_file(source)
            or receipt.get("candidate_sha256") != sha256_file(candidate)
            or receipt.get("curation_sha256") != sha256_file(curation)):
        raise ValueError("materialization evidence is changed or incomplete")
    generation = Path(receipt["inspection_generation"])
    validate_generation_directory(generation, require_pass=True,
        expected_manifest_sha256=receipt["inspection_manifest_sha256"])
    payload = json.loads((generation / "segments.json").read_text())
    _, before = _package(Path(source))
    tables = table_inventory(before)
    outputs, cone = check_exclusion(tables, payload)
    if (tables != receipt["tables"] or outputs != receipt["ordered_outputs"]
            or sha256_bytes(canonical_bytes(cone)) != receipt["excluded_cone_sha256"]):
        raise ValueError("materialization coverage changed")
    # The inspection source is retained beside its generation; bind it back
    # to the original, not just to the receipt's assertion.
    inspection_root = generation.parents[3]
    inspected = inspection_root / "inspection-source" / Path(candidate).name
    if sha256_file(inspected) != sha256_file(source):
        raise ValueError("inspection source is not the original workbook")
    ast_dir = inspection_root / "ast" / Path(candidate).stem
    provenance = receipt.get("inspection_ast_provenance")
    if not isinstance(provenance, dict):
        raise ValueError("materialization lacks inspection AST provenance")
    validate_ast_provenance(inspected, ast_dir, provenance)
    # Immutable provenance may point to a still-valid historical code snapshot.
    # Require the running parser too; relocating identical code is harmless.
    recorded = provenance["builder"]["code"]
    current = _default_builder_paths()
    if sorted((r["name"], r["sha256"], r["size_bytes"]) for r in recorded) != sorted(
            (p.name, sha256_file(p), p.stat().st_size) for p in current):
        raise ValueError("materialization inspection AST builder changed")
    resolve_generation_by_id(generation.parents[1], generation.name,
        source_path=inspected, ast_dir=ast_dir, require_pass=True,
        validate_live_evidence=True)
    _, after = _package(Path(candidate))
    if transformed_members(before, tables) != after:
        raise ValueError("candidate contains unapproved edits")
    scan_leaks(source, tables, outputs)
    return {"status": "verified", "manifest_sha256": receipt["manifest_sha256"]}

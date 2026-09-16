"""Remove explicitly audited numeric outputs without round-tripping XLSX packages.

The plan, never numeric equality to an answer key, determines masking scope. Only
numeric cell <v> elements are removed. Styles, text, errors, formulas outside the
scope, native package parts, and every other worksheet byte remain unchanged.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import posixpath
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
CELL = re.compile(rb"<(?:[A-Za-z_][\w.-]*:)?c\b[^>]*?(?:/>|>.*?</(?:[A-Za-z_][\w.-]*:)?c>)", re.S)
VALUE = re.compile(rb"<(?:[A-Za-z_][\w.-]*:)?v\b[^>]*>.*?</(?:[A-Za-z_][\w.-]*:)?v>", re.S)
COORD = re.compile(r"([A-Z]+)([1-9][0-9]*)\Z")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def coordinate(value: str) -> tuple[int, int]:
    match = COORD.fullmatch(value)
    if not match:
        raise ValueError(f"Invalid coordinate: {value!r}")
    col = 0
    for char in match[1]:
        col = col * 26 + ord(char) - 64
    return col, int(match[2])


def in_ranges(cell: str, ranges: list[str]) -> bool:
    col, row = coordinate(cell)
    for region in ranges:
        bounds = region.split(":")
        if len(bounds) not in (1, 2):
            raise ValueError(f"Invalid range: {region!r}")
        c1, r1 = coordinate(bounds[0])
        c2, r2 = coordinate(bounds[-1])
        if c1 > c2 or r1 > r2:
            raise ValueError(f"Reversed range: {region!r}")
        if c1 <= col <= c2 and r1 <= row <= r2:
            return True
    return False


def sheets(z: ZipFile) -> dict[str, str]:
    relationships = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    targets = {r.attrib["Id"]: r.attrib["Target"] for r in relationships}
    result = {}
    for sheet in ET.fromstring(z.read("xl/workbook.xml")).findall("s:sheets/s:sheet", NS):
        target = targets[sheet.attrib[f"{{{REL}}}id"]]
        result[sheet.attrib["name"]] = posixpath.normpath(target.lstrip("/") if target.startswith("/") else "xl/" + target)
    return result


def mask_xml(data: bytes, ranges: list[str]) -> tuple[bytes, list[dict]]:
    parsed = ET.fromstring(data)
    cells = {c.attrib["r"]: c for c in parsed.findall(".//s:sheetData/s:row/s:c", NS)}
    targets = {}
    for name, cell in cells.items():
        if not in_ranges(name, ranges):
            continue
        if cell.find("s:f", NS) is not None:
            raise ValueError(f"Target {name} still contains a formula; adjudicate separately")
        value = cell.find("s:v", NS)
        if cell.attrib.get("t", "n") == "n" and value is not None and value.text:
            float(value.text)  # Fail closed on malformed numeric payloads.
            targets[name] = value.text
    changes = []

    def replace(match: re.Match) -> bytes:
        raw = match[0]
        ref = re.search(rb'\br\s*=\s*["\']([^"\']+)["\']', raw)
        name = ref[1].decode() if ref else ""
        if name not in targets:
            return raw
        result, count = VALUE.subn(b"", raw)
        if count != 1:
            raise ValueError(f"Expected one value element in {name}; found {count}")
        changes.append({"cell": name, "removed_numeric_value": targets[name], "original_cell_sha256": hashlib.sha256(raw).hexdigest()})
        return result

    patched = CELL.sub(replace, data)
    if len(changes) != len(targets):
        raise ValueError("XML byte matcher did not cover every audited numeric cell")
    check = ET.fromstring(patched)
    for cell in check.findall(".//s:sheetData/s:row/s:c", NS):
        if in_ranges(cell.attrib["r"], ranges) and cell.attrib.get("t", "n") == "n":
            value = cell.find("s:v", NS)
            if value is not None and value.text:
                raise AssertionError(f"Numeric output remains at {cell.attrib['r']}")
    return patched, changes


def mask_workbook(source: Path, destination: Path, ranges: dict[str, list[str]], expected_sha256: str | None = None) -> dict:
    if source.resolve() == destination.resolve():
        raise ValueError("Source artifacts must never be edited in place")
    before = sha256(source)
    if expected_sha256 and before != expected_sha256:
        raise ValueError("Source hash differs from the audited plan")
    patches, changes, entry_hashes = {}, {}, {}
    with ZipFile(source) as original:
        paths = sheets(original)
        for sheet, regions in ranges.items():
            if sheet not in paths:
                raise ValueError(f"Missing sheet: {sheet!r}")
            raw = original.read(paths[sheet])
            patched, cells = mask_xml(raw, regions)
            if cells:
                patches[paths[sheet]] = patched
            changes[sheet] = cells
        destination.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(destination, "w") as output:
            output.comment = original.comment
            for info in original.infolist():
                output.writestr(copy.copy(info), patches.get(info.filename, original.read(info.filename)))
        with ZipFile(destination) as output:
            assert original.namelist() == output.namelist(), "Package entries changed"
            for name in original.namelist():
                old, new = original.read(name), output.read(name)
                assert new == patches.get(name, old), f"Unexpected entry mutation: {name}"
                entry_hashes[name] = {"before": hashlib.sha256(old).hexdigest(), "after": hashlib.sha256(new).hexdigest()}
            assert output.testzip() is None, "Invalid output ZIP CRC"
    assert sha256(source) == before, "Source artifact mutated"
    return {"source": str(source.resolve()), "output": str(destination.resolve()), "before_sha256": before, "after_sha256": sha256(destination), "ranges": ranges, "changed_cells": changes, "changed_cell_count": sum(map(len, changes.values())), "changed_entries": sorted(patches), "entry_hashes": entry_hashes, "validation": {"zero_remaining_numeric_values_in_scope": True, "all_unrelated_entry_bytes_unchanged": True, "all_unrelated_worksheet_bytes_unchanged": True, "source_unchanged": True, "no_formulas_added": True, "package_crc_valid": True}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path, help="JSON list of {task, source, ranges, ...}")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    results = []
    for task in json.loads(args.plan.read_text()):
        source = Path(task["source"])
        key_path = Path(task["answer_key_path"]) if task.get("answer_key_path") else None
        key_hash = sha256(key_path) if key_path else None
        if key_hash and key_hash != task.get("answer_key_sha256_before", key_hash):
            raise ValueError(f"Answer key changed since plan was built: {task['task']}")
        output = args.output_dir / task["task"] / source.name
        result = mask_workbook(source, output, task["ranges"], task.get("source_sha256"))
        result["task"] = task["task"]
        result["plan_evidence"] = {k: v for k, v in task.items() if k not in ("ranges", "source", "task")}
        if key_path:
            assert sha256(key_path) == key_hash, "Masking must not mutate answer keys"
            result["validation"]["expected_answer_key_unchanged"] = True
            result["answer_key_sha256_before"] = key_hash
            result["answer_key_sha256_after"] = sha256(key_path)
        results.append(result)
        (output.parent / "validation.json").write_text(json.dumps(result, indent=2) + "\n")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "manifest.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps({"tasks": len(results), "numeric_cells_masked": sum(x["changed_cell_count"] for x in results), "manifest": str(args.output_dir / "manifest.json")}))


if __name__ == "__main__":
    main()

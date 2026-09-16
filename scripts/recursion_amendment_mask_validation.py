"""Independently verify an exact, approved workbook mask; never edit workbooks.

The spec's workbookPath/hash identify the immediate predecessor. The caller
must separately bind its spec hash to the complete semantic gate and active
task version. Source membership alone is deliberately not an approval rule.
"""
from __future__ import annotations

import hashlib
import copy
import json
import re
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET

from scripts.recursion_hardening_workbooks import NS, sheets

CELL = re.compile(rb'<(?:[A-Za-z_][\w.-]*:)?c\b[^>]*?(?:/>|>.*?</(?:[A-Za-z_][\w.-]*:)?c>)', re.S)
SI = re.compile(rb'<(?:[A-Za-z_][\w.-]*:)?si\b[^>]*?(?:/>|>.*?</(?:[A-Za-z_][\w.-]*:)?si>)', re.S)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(Path(path).read_text())


def _texts(raw):
    if raw is None:
        return []
    return [''.join(si.itertext()) for si in ET.fromstring(raw)]


def _cells(raw):
    root = ET.fromstring(raw)
    nodes = root.findall('.//s:sheetData/s:row/s:c', NS)
    result = {c.attrib['r']: c for c in nodes}
    require(len(result) == len(nodes), 'Duplicate worksheet cell')
    return result


def _value(cell, strings):
    typ = cell.attrib.get('t', 'n')
    value = cell.find('s:v', NS)
    inline = cell.find('s:is', NS)
    require(not (value is not None and inline is not None), 'Ambiguous cell payload')
    if typ == 'inlineStr':
        return ''.join(inline.itertext()) if inline is not None else None
    if value is None or value.text is None:
        return None
    if typ == 's':
        return strings[int(value.text)]
    if typ == 'b':
        require(value.text in ('0', '1'), 'Invalid boolean payload')
        return value.text == '1'
    if typ == 'n':
        return float(value.text)
    require(typ in ('str', 'e'), 'Unsupported targeted cell type')
    return value.text


def _same_value(left, right):
    # Python bool equality must not turn a primitive True into numeric 1.
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    return left == right


def _skeleton(cell):
    clean = copy.deepcopy(cell)
    clean.attrib.pop('t', None)
    for child in list(clean):
        if child.tag.rsplit('}', 1)[-1] in ('v', 'is'):
            # Text outside the payload is not an approved edit, even if an
            # ordinary cell reader ignores it. Preserve tails when stripping.
            children = list(clean)
            pos = children.index(child)
            if child.tail:
                if pos:
                    children[pos-1].tail = (children[pos-1].tail or '') + child.tail
                else:
                    clean.text = (clean.text or '') + child.tail
            clean.remove(child)
    return ET.tostring(clean)


def _outside_cells(raw, coords):
    seen = set()
    def replace(match):
        cell = ET.fromstring(match.group())
        coord = cell.attrib.get('r')
        if coord not in coords:
            return match.group()
        require(coord not in seen, 'Duplicate targeted raw XML cell')
        seen.add(coord)
        return ('<AUDITED-CELL-' + coord + '/>').encode()
    result = CELL.sub(replace, raw)
    require(seen == set(coords), 'Exact cell-byte matcher coverage failed')
    return result


def _sst_references(zipfile, worksheet_paths):
    refs = set()
    for path in worksheet_paths:
        for cell in _cells(zipfile.read(path)).values():
            if cell.attrib.get('t') == 's':
                value = cell.findtext('s:v', namespaces=NS)
                require(value is not None, 'Missing shared-string index')
                refs.add(int(value))
    return refs


def verify_mask(spec_path, output_path, *, expected_spec_sha256=None, expected_output_sha256=None):
    """Return fresh evidence, failing closed on any unapproved package change."""
    spec_path, output_path = Path(spec_path), Path(output_path)
    spec_hash = sha(spec_path)
    require(expected_spec_sha256 is None or spec_hash == expected_spec_sha256, 'Mask specification hash mismatch')
    spec = read(spec_path)
    require(spec['schemaVersion'] == 1, 'Unsupported mask specification schema')
    require(re.fullmatch(r'[A-Za-z0-9_.-]+', spec['taskId']) and spec['taskId'] not in ('.', '..'), 'Invalid task ID')
    require(bool(spec['activeProblemVersionId']), 'Missing predecessor version')
    source, key = Path(spec['workbookPath']), Path(spec['answerKeyPath'])
    require(source.resolve() != output_path.resolve(), 'Never mutate predecessor workbook')
    require(output_path.is_file() and not output_path.is_symlink(), 'Output must be a regular independent file')
    require(sha(source) == spec['workbookSha256'], 'Predecessor workbook hash mismatch')
    require(sha(key) == spec['answerKeySha256'], 'Answer key hash mismatch')
    output_hash = sha(output_path)
    require(output_hash != spec['workbookSha256'], 'No workbook change')
    require(expected_output_sha256 is None or output_hash == expected_output_sha256, 'Output workbook hash mismatch')
    evidence = spec['sourceEvidenceHashes']
    require(isinstance(evidence, dict) and bool(evidence), 'Missing source evidence bindings')
    for path, digest in evidence.items():
        require(sha(path) == digest, 'Source evidence hash mismatch')
    targets = {}
    for item in spec['cells']:
        host = (item['sheet'], item['cell'])
        require(host not in targets and re.fullmatch(r'[A-Z]+[1-9][0-9]*', host[1]), 'Duplicate or invalid target')
        require(isinstance(item['classification'], str) and bool(item['classification'].strip()), 'Missing semantic classification')
        require(item['expectedValue'] is not None, 'Target was already blank')
        require(item['replacement'] is None or isinstance(item['replacement'], str), 'Replacement must be blank or approved text')
        require(not _same_value(item['expectedValue'], item['replacement']), 'Unchanged target')
        targets[host] = item
    require(bool(targets), 'Empty approved cell set')
    orphans = {}
    for item in spec['orphanSharedStrings']:
        index = item['index']
        require(type(index) is int and index >= 0 and index not in orphans, 'Invalid or duplicate orphan index')
        require(isinstance(item['expectedValue'], str) and bool(item['expectedValue']), 'Invalid orphan expected value')
        orphans[index] = item['expectedValue']
    changed_entries, seen = [], set()
    with ZipFile(source) as before, ZipFile(output_path) as after:
        require(before.namelist() == after.namelist() and len(set(before.namelist())) == len(before.namelist()), 'Package topology changed')
        require(before.comment == after.comment, 'ZIP comment changed')
        require(after.testzip() is None, 'Invalid output ZIP CRC')
        for old, new in zip(before.infolist(), after.infolist()):
            for field in ('filename', 'date_time', 'compress_type', 'comment', 'extra', 'create_system', 'external_attr', 'internal_attr'):
                require(getattr(old, field) == getattr(new, field), 'ZIP entry metadata changed: ' + old.filename)
        paths = sheets(before)
        require(paths == sheets(after), 'Worksheet mapping changed')
        require({sheet for sheet, _ in targets} <= set(paths), 'Target worksheet missing')
        sst_name = 'xl/sharedStrings.xml'
        old_sst = before.read(sst_name) if sst_name in before.namelist() else None
        new_sst = after.read(sst_name) if sst_name in after.namelist() else None
        old_strings, new_strings = _texts(old_sst), _texts(new_sst)
        require(len(old_strings) == len(new_strings), 'Shared-string indices changed')
        reverse = {path: sheet for sheet, path in paths.items()}
        for name in before.namelist():
            old_raw, new_raw = before.read(name), after.read(name)
            if old_raw != new_raw:
                changed_entries.append(name)
            if name == sst_name:
                continue
            sheet = reverse.get(name)
            selected = {cell for sh, cell in targets if sh == sheet}
            if not selected:
                require(old_raw == new_raw, 'Unapproved ZIP entry changed: ' + name)
                continue
            require(_outside_cells(old_raw, selected) == _outside_cells(new_raw, selected), 'Unapproved worksheet bytes changed: ' + name)
            old_cells, new_cells = _cells(old_raw), _cells(new_raw)
            for coord in selected:
                require(coord in old_cells and coord in new_cells, 'Target cell deleted or missing')
                old, new = old_cells[coord], new_cells[coord]
                item = targets[(sheet, coord)]
                require(old.find('s:f', NS) is None and new.find('s:f', NS) is None, 'Target contains a formula')
                require(_same_value(_value(old, old_strings), item['expectedValue']), 'Target source value mismatch: ' + sheet + '!' + coord)
                require(_skeleton(old) == _skeleton(new), 'Target styling or non-payload content changed')
                require(_same_value(_value(new, new_strings), item['replacement']), 'Target replacement mismatch: ' + sheet + '!' + coord)
                if item['replacement'] is None:
                    require(new.find('s:v', NS) is None and new.find('s:is', NS) is None and new.attrib.get('t', 'n') == 'n', 'Blank target retains payload/type')
                else:
                    require(new.attrib.get('t') == 'inlineStr', 'Replacement must use local inline text')
                seen.add((sheet, coord))
        require(seen == set(targets), 'Incomplete approved mask coverage')
        live_after = _sst_references(after, paths.values())
        require(all(0 <= index < len(new_strings) for index in live_after), 'Invalid live shared-string reference')
        for index, expected in orphans.items():
            require(index < len(old_strings) and old_strings[index] == expected, 'Orphan source value mismatch')
            require(index not in live_after, 'Approved orphan still has legitimate live references')
            require(new_strings[index] == '', 'Orphan disclosure not scrubbed')
        removed_texts = {i['expectedValue'] for i in targets.values() if isinstance(i['expectedValue'], str)}
        required_orphans = {i for i, value in enumerate(old_strings) if value in removed_texts and i not in live_after}
        require(required_orphans <= set(orphans), 'Unreferenced removed target remains in shared strings')
        if old_sst is not None:
            old_entries, new_entries = SI.findall(old_sst), SI.findall(new_sst)
            require(len(old_entries) == len(new_entries) == len(old_strings), 'Shared-string byte matcher failed')
            for index, (old, new) in enumerate(zip(old_entries, new_entries)):
                if index not in orphans:
                    require(old == new, 'Unapproved or live shared-string entry changed')
                else:
                    old_node, new_node = ET.fromstring(old), ET.fromstring(new)
                    require(old_node.tag == new_node.tag and old_node.attrib == new_node.attrib, 'Shared-string attributes changed')
                    require(not list(new_node) and not (new_node.text or '').strip(), 'Orphan retained hidden rich-text content')
            require(SI.sub(b'<AUDITED-SI/>', old_sst) == SI.sub(b'<AUDITED-SI/>', new_sst), 'Shared-string surrounding bytes changed')
        else:
            require(not orphans, 'Orphans specified without shared strings')
    require(sha(source) == spec['workbookSha256'] and sha(key) == spec['answerKeySha256'] and sha(spec_path) == spec_hash, 'Input artifact changed during verification')
    return {'schemaVersion': 1, 'taskId': spec['taskId'], 'activeProblemVersionId': spec['activeProblemVersionId'],
            'maskSpecificationPath': str(spec_path.resolve()), 'maskSpecificationSha256': spec_hash,
            'workbookSourcePath': str(source.resolve()), 'workbookSourceSha256': spec['workbookSha256'],
            'workbookPath': str(output_path.resolve()), 'workbookSha256': output_hash,
            'answerKeyPath': str(key.resolve()), 'answerKeySha256': spec['answerKeySha256'],
            'sourceEvidenceHashes': evidence, 'maskedCellCount': len(targets),
            'maskedCells': sorted(sheet+'!'+cell for sheet, cell in targets),
            'blankedSharedStringIndices': sorted(orphans), 'changedEntries': changed_entries,
            'validation': {name: True for name in ('passed', 'exactApprovedMasksApplied', 'unrelatedZipAndXmlBytesPreserved',
                'targetMetadataPreserved', 'noFormulasAdded', 'sharedStringIndicesPreserved',
                'legitimateLiveSharedStringsPreserved', 'removedOrphanPayloadsScrubbed',
                'noUnapprovedCellsRestored', 'sourceAndAnswerKeyUnchanged')},
            'semanticLimit': 'Exact approved scope verified; classification must be approved by the separate source/semantic gate.'}

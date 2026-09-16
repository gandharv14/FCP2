#!/usr/bin/env python3
"""Build claim cards for the additional-assumptions dialogue writer."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tomllib
from pathlib import Path

from aa_lib import (
    JUNIOR_TITLES,
    SENIOR_TITLES,
    clean_label,
    cell_refs_in,
    disclosure_body,
    humanize_token,
    sheet_from_cells,
    strip_cell_refs,
    write_json,
)
from spoken_formula import (calculation_scope, collect_phrases, needs_spoken, speak_record,
                            QUOTED_LABEL, all_labels, quote_label)


LABEL_RE = re.compile(r'"([^"]+)"')
SOURCE_TAB_RE = re.compile(
    r"\bon the ([A-Za-z0-9][A-Za-z0-9 _&.'()/-]*?) tab\b",
    re.I,
)
LOCATOR_ATOMS = (
    "last period",
    "this period",
    "next period",
    "locked input",
)
OPERATOR_ATOMS = (
    "smaller",
    "greater",
    "at least",
    "at most",
    "floor",
    "flip",
    "minus",
    "plus",
    "average",
    "held flat",
    "lower bound",
    "upper bound",
    "result locked input",
    "first locked input",
    "second locked input",
    "corresponding",
    "otherwise",
    "errors",
)
NON_LABEL_LITERALS = frozenset({"", "N/A", "#N/A", "NA"})


SKILL_ROOT = Path(__file__).resolve().parents[1]
DISCLOSE_SCRIPTS = SKILL_ROOT.parent / "task-disclosure" / "scripts"
if str(DISCLOSE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(DISCLOSE_SCRIPTS))

import disclose  # noqa: E402


def load_disclosure(task_dir: Path) -> dict:
    path = task_dir / "tests" / "disclosure.json"
    if not path.is_file():
        raise SystemExit(f"missing {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def writer_disclosure_body(records: list[dict], raw_body: str) -> str:
    lines = [
        "These are modelling assumptions the seniors already know.",
        "They describe how the new model should work, not the figures you are asked to report.",
        "",
    ]
    if records:
        for rec in records:
            line = rec.get("spoken") or rec.get("rendered") or ""
            lines.append(f"- On `{rec['sheet']}`, the row labelled {quote_label(rec['row_label'])}: {line}")
        return "\n".join(lines).rstrip() + "\n"
    return strip_cell_refs(raw_body).rstrip() + "\n"


def must_say_atoms(spoken: str, sheet: str = "", row_label: str = "") -> list[str]:
    """Fact atoms only. Never the spoken paragraph or 'copied across the forecast'."""
    blob = spoken or ""
    low = blob.lower()
    out: list[str] = []
    seen: set[str] = set()

    def add(atom: str) -> None:
        text = (atom or "").strip()
        if not text:
            return
        key = text.lower()
        if key in seen or "copied across the forecast" in key:
            return
        seen.add(key)
        out.append(text)

    if row_label:
        add(f'the row labelled {quote_label(row_label)}')
    # Quotation marks do not establish worksheet-row identity. Preserve the
    # source type attached to each occurrence, including same-spelling names
    # and rows; string operands and quoted sheet names are not row labels.
    for match in re.finditer(r'\bthe named range\s+"([^"\n]+)"', blob, re.I):
        add(f'the named range "{match.group(1)}"')
    rows = re.compile(
        rf'\brow(?P<plural>s)? label(?:led|ed)\s+(?P<labels>{QUOTED_LABEL}'
        rf'(?(plural)(?:\s*(?:,\s*(?:and\s+)?|and\s+){QUOTED_LABEL})*))', re.I)
    for match in rows.finditer(blob):
        for label in all_labels(match.group('labels')):
            if label.strip() not in NON_LABEL_LITERALS:
                add(f'the row labelled {quote_label(label)}')
    for scope in ("only for this cell", "only within the specified columns",
                  "only within the specified rows", "only for the specified cells"):
        if scope in low:
            add(scope)
    for tab in SOURCE_TAB_RE.findall(blob):
        if tab.strip().casefold() != sheet.strip().casefold():
            add(f"the {tab.strip()} tab")
    for atom in LOCATOR_ATOMS:
        if atom in low:
            add(atom)
    for atom in OPERATOR_ATOMS:
        if atom in low:
            add(atom)
    for cardinality in re.findall(r"\b\d+-row\b", low):
        add(cardinality)
    for ordinal in re.findall(
        r"\b(first|second|third|fourth|fifth) locked \d+-row input block\b",
        low,
    ):
        add(f"{ordinal} locked input block")
    return out


def formula_text_atoms(record: dict) -> list[str]:
    """Only actual AST string operands license typed literal requirements.

    Labels, sheet names and defined names remain reference metadata, even if
    their spelling happens to match a formula's text constant.
    """
    formula = record.get("evidence") or (record.get("method_profile") or {}).get("formula") or ""
    if not formula:
        if (record.get("method_profile") or {}).get("text_literals"):
            raise ValueError("text literals lack original formula evidence")
        return []
    if not str(formula).lstrip().startswith("="):
        if record.get("entry") in disclose.METHOD_ENTRY_IDS:
            raise ValueError("method text operands lack a literal formula")
        return []  # Non-method diagnostic prose is not formula evidence.
    ast = disclose._parse_formula_ast(formula)
    if ast is None:
        raise ValueError("formula text operands cannot be independently parsed")
    atoms = []
    for node in disclose.walk_ast(ast):
        if node.kind == "const" and node.name == "text":
            atom = "the text literal " + json.dumps(str(node.shape), ensure_ascii=False)
            # Current delivered-dialogue policy forbids senior A1 references.
            # Do not emit an impossible requirement or grant a blanket token
            # exemption. A richer occurrence proof is a separate capability.
            from validate_dialogue import whole_axis_refs
            if (cell_refs_in(atom) or whole_axis_refs(atom)) and not re.fullmatch(r"[A-Z]{1,3}[1-9][0-9]{0,6}|[A-Z]{1,3}0[0-9]{1,6}", str(node.shape)):
                raise ValueError("formula text operand resembles a prohibited cell reference")
            if atom not in atoms:
                atoms.append(atom)
    return atoms



# This is deliberately a small label-comparison capability. No reference
# interpretation, dynamic names, UDFs, or transformed originals are licensed.
_LITERAL_SAFE_FUNCTIONS = frozenset(
    "IF IFERROR IFNA AND OR NOT TRUE FALSE SUM SUMIF SUMIFS SUMPRODUCT MIN MAX "
    "ABS ROUND ROUNDUP ROUNDDOWN INT MOD SIGN COUNT COUNTA COUNTIF COUNTIFS "
    "AVERAGE AVERAGEIF AVERAGEIFS ISERROR ISNUMBER ISTEXT ISBLANK N NA "
    "YEAR MONTH DAY DATE EDATE EOMONTH DAYS DAYS360 YEARFRAC TODAY "
    "IRR XIRR NPV XNPV PMT PV FV RATE NPER EXP LN LOG LOG10 SQRT POWER "
    "VLOOKUP HLOOKUP MATCH INDEX CHOOSE LEFT RIGHT MID LEN TRIM UPPER LOWER "
    "CONCATENATE TEXT VALUE SUBSTITUTE REPLACE FIND SEARCH EXACT ROW COLUMN "
    "ROWS COLUMNS SUMSQ PRODUCT MEDIAN STDEV STDEV.P STDEV.S SUBTOTAL".split())


def _literal_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _literal_json_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                   ensure_ascii=False).encode()).hexdigest()


def validate_reference_label_workbook(book):
    """Conservative whole-source no-pointer proof, without reading caches."""
    # Workbook-wide rejection also closes indirect use through another cell
    # or defined name, without assuming a partial frontier is complete.
    formula_count = 0
    visited_cells = 0
    for sheet in book:
        for row in sheet:
            for cell in row:
                visited_cells += 1
                if visited_cells > 1000000:
                    raise ValueError('reference literal workbook proof exceeds supported cell count')
                if cell.data_type != 'f':
                    continue
                formula_count += 1
                if formula_count > 50000 or not isinstance(cell.value, str):
                    raise ValueError('reference literal workbook proof exceeds supported scope')
                ast = disclose._parse_formula_ast(cell.value)
                if ast is None or any(
                        n.kind not in {'const', 'func', 'infix', 'prefix', 'postfix', 'ref', 'range', 'array'}
                        or (n.kind == 'func' and n.name.upper() not in _LITERAL_SAFE_FUNCTIONS)
                        for n in disclose.walk_ast(ast)):
                    raise ValueError('reference-interpreting or unknown workbook formula')
    for name in [n for owner in (book, *book.worksheets) for n in owner.defined_names.values()]:
        ast = disclose._parse_formula_ast('='+str(name.attr_text).lstrip('='))
        if ast is None or any(n.kind == 'func' for n in disclose.walk_ast(ast)):
            raise ValueError('formula defined name has unproved reference interpretation')

def reference_literal_proofs(task_dir: Path, claims: list[dict]) -> dict:
    """Reconstruct reviewer-only licenses from the owning immutable pair.

    The caller cannot nominate a workbook/formula/curation. No supplied proof
    flag is trusted, and no original cached calculated value is read.
    """
    requested = {}
    for claim in claims:
        values = []
        for atom in claim.get('must_say') or []:
            if atom.startswith('the text literal '):
                value = json.loads(atom[len('the text literal '):])
                if isinstance(value, str) and cell_refs_in(atom):
                    if not re.fullmatch(r'[A-Z]{1,3}[0-9]{1,7}', value):
                        raise ValueError('reference literal is not a bare comparison label')
                    values.append(value)
        if values:
            requested[claim['record_id']] = values
    if not requested:
        return {}
    task_dir = Path(task_dir)
    path = task_dir/'tests/disclosure.json'
    d = load_disclosure(task_dir)
    binding = d.get('pipeline_bindings') or {}
    generation = d.get('segmentation_generation') or {}
    golden = Path(d.get('golden', ''))
    if (d.get('segmentation_mode') != 'strict' or generation.get('mode') != 'strict'
            or d.get('selection', {}).get('closure_source') != 'ast'
            or not golden.is_file() or _literal_digest(golden) != d.get('golden_sha256')):
        raise ValueError('reference literal requires current owning source provenance')
    from xl_seg.publication import resolve_generation_by_id
    metadata = tomllib.loads((task_dir/'task.toml').read_text())['metadata']
    package_binding_path = task_dir/'tests/pipeline_bindings.json'
    if (golden.stem != task_dir.name.split('-')[0] or d.get('task') != task_dir.name
            or metadata.get('workbook') != golden.stem
            or metadata.get('source_file') != golden.name
            or metadata.get('source_generation_id') != binding.get('source_generation_id')
            or metadata.get('segmentation_generation_id') != binding.get('segmentation_generation_id')
            or metadata.get('pipeline_bindings') != 'tests/pipeline_bindings.json'
            or json.loads(package_binding_path.read_text()) != binding):
        raise ValueError('reference literal does not belong to the packaged task identity')
    source_dir = golden.parent.parent
    seg_dir, manifest = resolve_generation_by_id(
        Path(d['seg_root'])/golden.stem, generation.get('generation_id'),
        source_path=golden, ast_dir=Path(d['ast_dir'])/golden.stem,
        source_generation_dir=source_dir, require_pass=True, validate_live_evidence=True)
    source_manifest = json.loads((source_dir/'generation-manifest.json').read_text())
    if (source_manifest['identity']['engine']['mode'] != 'identity'
            or source_manifest['identity']['source_sha256'] != d['golden_sha256']
            or binding.get('source_generation_id') != source_dir.name
            or binding.get('source_manifest_sha256') != _literal_digest(source_dir/'generation-manifest.json')
            or binding.get('segmentation_generation_id') != seg_dir.name
            or binding.get('segmentation_manifest_sha256') != _literal_digest(seg_dir/'generation-manifest.json')
            or Path(generation.get('directory', '')).resolve() != seg_dir.resolve()):
        raise ValueError('reference literal original source/pair identity changed')
    packaged = task_dir/'tests/segmentation_generation_manifest.json'
    inputs = task_dir/'tests/inputs_generation.json'
    if (_literal_digest(packaged) != binding['segmentation_manifest_sha256']
            or json.loads(inputs.read_text()).get('pipeline_bindings') != binding):
        raise ValueError('reference literal packaged pair differs from owning disclosure')
    key_path = task_dir/'tests/answer_key.json'
    target_key = json.loads(key_path.read_text())
    actual_targets = [disclose.parse_ref(k, '') for k in target_key.get('targets', {})]
    if actual_targets != manifest['curated_output_cells']['ordered']:
        raise ValueError('reference literal target selection changed')
    from openpyxl import load_workbook
    book = load_workbook(golden, data_only=False, read_only=True, keep_links=True)
    try:
        validate_reference_label_workbook(book)
        originals = {build_claim(rec, i)['record_id']:(i, rec)
                     for i, rec in enumerate(d.get('agent_records') or [], 1)}
        if [claim['record_id'] for claim in claims] != list(originals):
            raise ValueError('reference literal claims differ from the complete owning disclosure order')
        result = {}
        for claim in claims:
            rid = claim['record_id']
            i, rec = originals[rid]
            expected = build_claim(rec, i)
            actual = {**claim, 'reviewer_only':dict(claim.get('reviewer_only') or {})}
            actual['reviewer_only'].pop('reference_literal_proof', None)
            if actual != expected:
                raise ValueError('reference literal claim differs from owning disclosure')
            if rid not in requested:
                continue
            representative = (rec.get('fields') or {}).get('representative')
            if representative not in rec.get('cells', []):
                raise ValueError('reference literal lacks exact representative source cell')
            ref = disclose.parse_ref(representative, '')
            sheet, coord = ref.rsplit('!', 1)
            source_formula = book[sheet][coord].value
            if source_formula != expected['reviewer_only']['evidence']:
                raise ValueError('reference literal formula differs from original source cell')
            ast = disclose._parse_formula_ast(source_formula)
            original_texts = sorted(str(n.shape) for n in disclose.walk_ast(ast)
                                    if n.kind == 'const' and n.name == 'text')
            for cell_ref in rec['cells']:
                cell_sheet, cell_coord = disclose.parse_ref(cell_ref, '').rsplit('!', 1)
                cell_formula = book[cell_sheet][cell_coord].value
                cell_ast = disclose._parse_formula_ast(cell_formula) if isinstance(cell_formula, str) and cell_formula.startswith('=') else None
                if cell_ast is None or sorted(str(n.shape) for n in disclose.walk_ast(cell_ast)
                                             if n.kind == 'const' and n.name == 'text') != original_texts:
                    raise ValueError('reference literal scope contains differing original text operands')
            uses = {}
            def visit(node, parent=None):
                if node.kind == 'const' and node.name == 'text' and str(node.shape) in requested[rid]:
                    if parent is None or parent.kind != 'infix' or parent.name not in ('=', '<>'):
                        raise ValueError('reference literal is not solely an equality comparison operand')
                    uses[str(node.shape)] = uses.get(str(node.shape), 0) + 1
                for child in node.args:
                    visit(child, node)
            visit(ast)
            if set(uses) != set(requested[rid]):
                raise ValueError('reference literal missing from original comparison')
            result[rid] = dict(schema_version='dialogue-reference-literal/v1',
                disclosure_sha256=_literal_digest(path), task_identity_sha256=_literal_json_digest({
                    k:metadata[k] for k in ('workbook','source_file','source_generation_id','segmentation_generation_id','pipeline_bindings')}),
                pipeline_bindings_sha256=_literal_digest(package_binding_path), record_sha256=_literal_json_digest(rec),
                source_sha256=d['golden_sha256'], source_generation_id=source_dir.name,
                source_manifest_sha256=binding['source_manifest_sha256'],
                segmentation_generation_id=seg_dir.name,
                segmentation_manifest_sha256=binding['segmentation_manifest_sha256'],
                curation_sha256=manifest['evidence']['curation']['sha256'],
                answer_key_sha256=_literal_digest(key_path), representative=ref,
                formula_sha256=hashlib.sha256(source_formula.encode()).hexdigest(),
                values=sorted(uses), occurrence_limit=1)
        return result
    finally:
        book.close()

def validate_method_card(record: dict, card: dict) -> None:
    """Reject lost scope or changed operand types before emitting a writer pack.

    The disclosure's actual cell set and typed source steps own this check;
    neither a producer-supplied calculation_kind nor a cached result does.
    Independent dialogue review is still required for the authored prose.
    """
    if record.get("entry") not in disclose.METHOD_ENTRY_IDS:
        return
    steps = str((record.get("fields") or {}).get("steps") or "")
    if not steps:
        return
    spoken = card["spoken"]
    atoms = card["must_say"]
    if any(atom not in atoms for atom in formula_text_atoms(record)):
        raise ValueError("method card lost an original formula text operand")
    scope = calculation_scope(record)
    if scope and (scope not in spoken or scope not in atoms):
        raise ValueError("method card lost its disclosed calculation scope")
    if re.search(r"\b(?:across (?:the )?forecast|every forecast period|each forecast period)\b", spoken, re.I):
        raise ValueError("method card invents forecast-wide calculation scope")
    for name in re.findall(r'\bthe named range\s+"([^"\n]+)"', steps, re.I):
        atom = f'the named range "{name}"'
        if atom not in spoken or atom not in atoms:
            raise ValueError("method card lost named-range operand type")
        row_atom = f'the row labelled "{name}"'
        # A real same-spelling worksheet row and named range may coexist, but
        # a named operand cannot manufacture an additional row obligation.
        row_labels = must_say_atoms(steps, row_label=card["row_label"])
        if row_atom in atoms and row_atom not in row_labels:
            raise ValueError("method card relabelled a named range as a row")


def build_claim(record: dict, index: int) -> dict:
    fields = record.get("fields") or {}
    row_label = clean_label(fields.get("label") or record.get("label") or "")
    sheet = sheet_from_cells(record.get("cells") or [])
    rendered = disclose.render_sentence(record)
    if not rendered:
        raise SystemExit(f"record {index} has no renderable sentence")
    raw_steps = str(fields.get("steps") or "")
    if raw_steps or needs_spoken(record) or re.search(r"\b(?:copied-column|cell |range )", raw_steps + " " + rendered, re.I):
        spoken = speak_record(record)
        if not spoken:
            spoken = strip_cell_refs(rendered)
            spoken = re.sub(r"\bFor\s+on the row\b", "On the row", spoken)
        must_say = must_say_atoms(
            spoken + "\n" + raw_steps,
            sheet,
            row_label,
        )
    else:
        spoken = strip_cell_refs(rendered)
        spoken = re.sub(r"\bFor\s+on the row\b", "On the row", spoken)
        must_say = must_say_atoms(spoken, sheet, row_label)
    if record.get("entry") == "source_selection":
        for reference in collect_phrases(str(fields.get("ingredient") or "")):
            atom = f"the {reference['sheet']} tab"
            if reference["sheet"] != sheet and atom not in must_say:
                must_say.append(atom)
    for atom in formula_text_atoms(record):
        if atom not in must_say:
            must_say.append(atom)
    entry = disclose.registry().get(record.get("entry") or "", {})
    alternatives = [
        humanize_token(token)
        for token in (record.get("alternatives") or entry.get("alternatives") or [])
        if humanize_token(token)
    ]
    record_id = "%s::%s::%s::%03d" % (
        record.get("entry") or f"rec{index:03d}",
        sheet or "sheet",
        row_label or f"row{index:03d}",
        index,
    )
    card = {
        "record_id": record_id,
        "sheet": sheet,
        "row_label": row_label,
        "junior_scope": list(record.get("cells") or []),
        "question": (entry.get("question") or "").strip(),
        "must_say": must_say,
        "alternatives": alternatives,
        "chosen_alternative": humanize_token(record.get("value") or ""),
        "spoken": spoken,
        "reviewer_only": {
            "band": record.get("band") or "",
            "cells": list(record.get("cells") or []),
            "entry": record.get("entry") or "",
            "value": record.get("value") or "",
            "source": record.get("source") or "",
            "steps": raw_steps,
            "evidence": record.get("evidence") or (record.get("method_profile") or {}).get("formula") or "",
            "rendered": rendered,
        },
    }

    if card["junior_scope"]:
        # An unused-row policy has a row identity, not calculation geometry.
        # Preserve that exact identity in the junior question without widening
        # the cell-only contract for methods or source-selection claims.
        cells = record.get("cells")
        row_policy = (
            record.get("entry") == "row_populated"
            and record.get("value") == "unused"
            and not raw_steps
            and isinstance(cells, list) and len(cells) == 1
            and isinstance(cells[0], str)
            and re.fullmatch(r".+!row [1-9][0-9]*", cells[0])
            and int(cells[0].rpartition("!row ")[2]) <= 1048576
        )
        if not row_policy:
            calculation_scope(record)  # Exact finite geometry, never representative-only.
    validate_method_card(record, card)
    return card


DRAFT_FORMAT_RULES = (
    "Every speaker turn starts at column 0 as **Title:** text — never number, "
    "bullet, or indent turns.",
    "Claim comments are literal, lowercase <!-- claim:RECORD_ID --> lines with "
    "RECORD_ID copied character-for-character from the claim card (never "
    "retitle, summarize, or capitalize them).",
    "Place each claim comment immediately before that claim's first "
    "conversational turn, one exchange per claim, in claim order.",
    "Keep each claim junior_scope as its exact scoped-cell prefix in a junior turn; never put those cell references in senior turns.",
    "Allowed titles only — juniors: Analyst, Associate; seniors: VP, Director, "
    "Managing Director.",
)


def draft_skeleton(claims: list[dict], juniors: list[str], seniors: list[str], shared=None) -> str:
    """Literal Markdown template the writer must follow, with real record ids."""
    lines: list[str] = []
    by_id = {claim['record_id']: claim for claim in claims}
    groups = shared['groups'] if shared else [[claim['record_id']] for claim in claims]
    for index, group in enumerate(groups):
        claim = by_id[group[0]]
        junior = juniors[index % len(juniors)]
        senior = seniors[index % len(seniors)]
        lines.extend("<!-- claim:%s -->" % identity for identity in group)
        from aa_lib import junior_scope_text
        scope = '; '.join(junior_scope_text(by_id[identity]) for identity in group
                          if by_id[identity].get('junior_scope'))
        lines.append(
            '**%s:** %s<junior question about the row labelled %s>'
            % (junior, (scope + ': ') if scope else '', quote_label(claim.get("row_label") or ""))
        )
        lines.append(
            "**%s:** <senior answer covering every must_say atom for this claim>"
            % senior
        )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def writer_claim(claim: dict) -> dict:
    keep = (
        "record_id",
        "sheet",
        "row_label",
        "junior_scope",
        "question",
        "must_say",
        "alternatives",
        "chosen_alternative",
        "spoken",
    )
    return {key: claim[key] for key in keep if key in claim}


def row_reference_warnings(claims_payload: dict) -> list[dict]:
    """Report accepted row-policy notation without changing frozen claim cards."""
    return [{'code': 'non_calculation_row_reference', 'record_id': claim['record_id'],
             'scope': claim['junior_scope'][0]}
            for claim in claims_payload.get('claims', [])
            if (claim.get('reviewer_only') or {}).get('entry') == 'row_populated'
            and (claim.get('reviewer_only') or {}).get('value') == 'unused'
            and len(claim.get('junior_scope') or []) == 1
            and re.fullmatch(r'.+!row [1-9][0-9]*', claim['junior_scope'][0])]


def extract(task_dir: Path, *, source_workbook=None, scope_evidence=None) -> dict:
    payload = load_disclosure(task_dir)
    shipped = list(payload.get("agent_records") or [])
    instruction = (task_dir / "instruction.md").read_text(encoding="utf-8")
    claims = [build_claim(record, index) for index, record in enumerate(shipped, 1)]
    literal_proofs = reference_literal_proofs(task_dir, claims)
    for claim in claims:
        proof = literal_proofs.get(claim['record_id'])
        if proof is not None:
            claim['reviewer_only']['reference_literal_proof'] = proof
    shared = None
    if bool(source_workbook) != bool(scope_evidence):
        raise ValueError('source workbook and scope evidence must be paired')
    if scope_evidence:
        from claim_scope import prove_groups
        shared = prove_groups(claims, task_dir / 'tests/disclosure.json', source_workbook, scope_evidence)
    raw_body = disclosure_body(instruction)
    body = writer_disclosure_body(claims, raw_body)
    juniors = list(JUNIOR_TITLES)
    seniors = list(SENIOR_TITLES)
    return {
        "schema_version": "1.1",
        "task": task_dir.name,
        "workbook": task_dir.name.split("-")[0],
        "junior_titles": juniors,
        "senior_titles": seniors,
        "empty": not claims,
        "claims": claims,
        "shared_source_links": shared,
        "disclosure_body": body,
        "writer_pack": {
            "junior_titles": juniors,
            "senior_titles": seniors,
            "disclosure_body": body,
            "claims": [writer_claim(claim) for claim in claims],
            "shared_source_links": shared,
            "draft_format_rules": [
                rule.replace('one exchange per claim, in claim order',
                             'one exchange per certified group, with all member claim comments')
                if shared else rule for rule in DRAFT_FORMAT_RULES],
            "draft_skeleton": draft_skeleton(claims, juniors, seniors, shared),
        },
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path, help="run directory for claims.json")
    parser.add_argument("--source-workbook", type=Path)
    parser.add_argument("--scope-evidence", type=Path)
    args = parser.parse_args(argv)
    task_dir = args.task_dir.resolve()
    if not (task_dir / "instruction.md").is_file():
        raise SystemExit(f"not a task bundle: {task_dir}")
    payload = extract(task_dir, source_workbook=args.source_workbook, scope_evidence=args.scope_evidence)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "claims.json", payload)
    write_json(out / "writer_pack.json", payload["writer_pack"])
    print("%s: %d claim(s) -> %s" % (payload["task"], len(payload["claims"]), out / "claims.json"))
    if payload["empty"]:
        print("empty agent_records: no-op")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

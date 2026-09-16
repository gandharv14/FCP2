"""Private, response-informed instruction evidence and an independent release gate.

This is additive to the existing numerical, disclosure, and container gates.
Nothing in this module establishes those gates or grants publication authority.
Call bindings must be selected by the owning runner from its actual journals.
The source response and original workbook evidence must never be copied into
the task or its Docker context.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import posixpath
import re
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from scripts.batch.harbor_model_review_provenance import Call, Policy, validate_call, require_independent

SCHEMA = "harbor-private-response-guidance/v1"
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
CHECKS = ("source_match", "response_conflicts_resolved", "task_scope_faithful", "no_answer_leakage")


def require(ok, message):
    if not ok:
        raise ValueError(message)


def binding(path):
    path = Path(path).absolute()
    require(path.is_file() and not any(p.is_symlink() for p in (path, *path.parents)),
            "regular non-symlink private evidence required")
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def checked(row):
    require(set(row) == {"path", "sha256"} and binding(row["path"]) == row,
            "private evidence changed")
    return Path(row["path"])


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def workbook_evidence(path, *, max_uncompressed=128 * 1024**2, max_cells=200_000):
    """Read original OOXML without executing macros, formulas, or external links.

    Evidence is complete within the declared bound; oversized inputs fail closed
    instead of silently presenting a partial workbook to the reviewer.
    """
    with ZipFile(path) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)), "duplicate ZIP members")
        require(sum(x.file_size for x in archive.infolist()) <= max_uncompressed,
                "source exceeds private review evidence size bound")
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relations = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {r.attrib["Id"]: r.attrib["Target"] for r in relations
                   if r.attrib.get("TargetMode") != "External"}
        strings = []
        if "xl/sharedStrings.xml" in names:
            strings = ["".join(x.itertext()) for x in ET.fromstring(archive.read("xl/sharedStrings.xml"))]
        sheets = []
        count = 0
        for sheet in workbook.findall("s:sheets/s:sheet", NS):
            target = targets[sheet.attrib[f"{{{REL}}}id"]]
            member = posixpath.normpath(target.lstrip("/") if target.startswith("/") else "xl/" + target)
            require(member.startswith("xl/") and member in names, "invalid workbook sheet relationship")
            cells = {}
            for cell in ET.fromstring(archive.read(member)).findall(".//s:sheetData/s:row/s:c", NS):
                formula, value = cell.find("s:f", NS), cell.find("s:v", NS)
                kind = cell.attrib.get("t", "n")
                raw = value.text if value is not None else None
                if kind == "s" and raw is not None:
                    raw = strings[int(raw)]
                elif kind == "inlineStr":
                    raw = "".join(t.text or "" for t in cell.findall("s:is//s:t", NS))
                if raw is None and formula is None:
                    continue
                address = cell.attrib["r"]
                require(address not in cells, "duplicate source cell")
                cells[address] = {"type": kind, "value": raw}
                if formula is not None:
                    cells[address]["formula"] = formula.text or ""
                    cells[address]["formula_attributes"] = dict(formula.attrib)
                count += 1
                require(count <= max_cells, "source exceeds private review cell bound")
            sheets.append({"name": sheet.attrib["name"], "state": sheet.attrib.get("state", "visible"), "cells": cells})
        require(sheets and count, "empty source workbook evidence")
        return {"sheets": sheets, "cell_count": count, "complete": True,
                "defined_names": [{"attributes": dict(n.attrib), "formula": n.text or ""}
                                  for n in workbook.findall("s:definedNames/s:definedName", NS)]}


def prepare(workbook_id, source, response, *, original_filename=None, corpus="RawWorkbooks4"):
    require(re.fullmatch(r"4\d{3}", workbook_id) is not None, "reserved RawWorkbooks4 identity required")
    source, response = binding(source), binding(response)
    name = original_filename or Path(source["path"]).name
    require(Path(name).name == name, "original filename must be a basename")
    text = Path(response["path"]).read_text(encoding="utf-8")
    declared = re.findall(r"^Workbook:\s*(.+?)\s*$", text, re.MULTILINE)
    response_stem = Path(response["path"]).stem
    require(declared == [name] and (response_stem == Path(name).stem or
            (original_filename is not None and response_stem == workbook_id)),
            "response must correspond exactly to original workbook filename")
    require("## What instructions were you given to build this model?" in text,
            "response instructions section missing")
    return {"schema_version": SCHEMA, "corpus": corpus, "workbook_id": workbook_id,
            "original_filename": name, "source": source, "response": response,
            "response_text": text, "workbook": workbook_evidence(source["path"]),
            "private_only": True}


def validate_context(row):
    path = checked(row)
    context = json.loads(path.read_text())
    require(context.get("schema_version") == SCHEMA and context.get("private_only") is True,
            "private response guidance schema required")
    checked(context["source"]); checked(context["response"])
    expected = prepare(context["workbook_id"], context["source"]["path"], context["response"]["path"],
                       original_filename=context["original_filename"], corpus=context["corpus"])
    require(context == expected, "private response guidance does not match source bytes")
    return context


AUTHOR_RULES = """Use this private source evidence and corresponding author response to infer the task's financial scenario and intended work. Treat response claims as fallible evidence: verify them against workbook sheets, labels, formulas, and dependencies; explicitly discard conflicting claims. The source workbook determines the task. Do not reproduce solved values, intermediate results, answer keys, unsupported financial scenarios, or response metadata in public instructions. Retain all existing target, output-contract, disclosure, and frozen-span requirements. This context is private and must not be packaged in agent-visible artifacts."""

REVIEW_RULES = """Independently review the exact final instruction against the complete private source workbook, its corresponding author response, and private answer key. The author response is fallible; workbook evidence controls. Check scenario/entity/period/unit consistency, requested outputs and task scope, unsupported or conflicting response claims, and leakage of target answers or reconstructable intermediate results. Legitimate supplied input assumptions and target cell references are not by themselves leakage. Do not treat a lexical number match as sufficient evidence of a leak or its absence. Return JSON with exactly verdict (PASS or HOLD), checks, response_assessment, findings. checks must contain source_match, response_conflicts_resolved, task_scope_faithful, no_answer_leakage; each has passed (boolean), reasoning (substantive explanation), source_refs (nonempty list of {sheet,cell,value}, quoting exact source values). response_assessment must contain one or more {claim,disposition,reasoning,source_refs}; quote each substantive scenario/scope claim and classify supported, conflicting, or unverifiable, documenting how instructions use or exclude it. findings is a list of unresolved issues; PASS requires none. Read all provided evidence. Never author or repair the instruction in this review."""


def author_context(context_binding):
    return {"rules": AUTHOR_RULES, "context_binding": context_binding,
            "evidence": validate_context(context_binding)}


def selected_author_context(workbook_id, source_sha256, *, index_binding=None):
    """Mandatory prospective 4xxx-corpus context, selected by the root runner.

    Index schema: {schema_version: harbor-response-guidance-index/v1,
    contexts: {4001: {path: ..., sha256: ...}, ...}}. Paths are native to the
    owning VM. Create/freeze these before the first author call, never midrun.
    """
    if index_binding is None:
        path = os.environ.get("FCP_RESPONSE_GUIDANCE_INDEX")
        digest = os.environ.get("FCP_RESPONSE_GUIDANCE_INDEX_SHA256")
        if not path and not digest:
            return None
        require(path and digest, "RawWorkbooks4 requires hash-bound private response guidance index")
        index_binding = {"path": path, "sha256": digest}
    require(re.fullmatch(r"4\d{3}", workbook_id) is not None,
            "response guidance only supports explicitly selected reserved corpus")
    index = json.loads(checked(index_binding).read_text())
    require(set(index) == {"schema_version", "contexts"}
            and index["schema_version"] == "harbor-response-guidance-index/v1"
            and workbook_id in index["contexts"], "workbook absent from private guidance index")
    context_binding = index["contexts"][workbook_id]
    result = author_context(context_binding)
    require(result["evidence"]["workbook_id"] == workbook_id
            and result["evidence"]["source"]["sha256"] == source_sha256,
            "private response guidance differs from owning original source")
    return {"index_binding": index_binding, **result}


def review_request(context_binding, instruction, answer_key, *, policy):
    context = validate_context(context_binding)
    instruction, answer_key = binding(instruction), binding(answer_key)
    payload = {"context_binding": context_binding, "source_evidence": context,
               "instruction_binding": instruction, "instruction": checked(instruction).read_text(),
               "answer_key_binding": answer_key, "answer_key": json.loads(checked(answer_key).read_text())}
    return {"model": policy.model, "reasoning_effort": policy.reasoning_effort,
            "messages": [{"role": "system", "content": REVIEW_RULES},
                         {"role": "user", "content": canonical(payload)}]}


def qualify(context_binding, instruction, answer_key, *, review_call, author_calls, policy):
    """Revalidate a root-selected actual independent call before publication.

    author_calls contains (Call, exact_expected_request, Policy) tuples. An
    empty list cannot qualify: author/reviewer independence must be evidenced.
    Return private evidence for inclusion in the owning publication journal.
    """
    require(isinstance(policy, Policy) and isinstance(review_call, Call), "typed actual review call required")
    require(author_calls, "actual instruction author invocation required")
    expected = review_request(context_binding, instruction, answer_key, policy=policy)
    reviewed = validate_call(review_call, expected, policy=policy)
    authors = [validate_call(call, request, policy=author_policy,
                             json_response=json.loads(checked(call.submitted).read_text()).get("response_mode") != "text")
               for call, request, author_policy in author_calls]
    require_independent(reviewed, authors)
    verdict = reviewed["review"]
    require(set(verdict) == {"verdict", "checks", "response_assessment", "findings"}, "closed semantic review required")
    require(verdict["verdict"] == "PASS" and verdict["findings"] == [], "response-guided instruction review holds publication")
    context = validate_context(context_binding)
    source = {sheet["name"]: sheet["cells"] for sheet in context["workbook"]["sheets"]}

    def substance(item):
        require(isinstance(item.get("reasoning"), str) and len(item["reasoning"].strip()) >= 40,
                "substantive review explanation required")
        refs = item.get("source_refs")
        require(isinstance(refs, list) and refs, "source-grounded review citations required")
        for ref in refs:
            require(set(ref) == {"sheet", "cell", "value"} and ref["sheet"] in source
                    and ref["cell"] in source[ref["sheet"]]
                    and ref["value"] == source[ref["sheet"]][ref["cell"]]["value"],
                    "review source citation differs from workbook")

    require(set(verdict["checks"]) == set(CHECKS), "all four semantic checks required")
    for item in verdict["checks"].values():
        require(set(item) == {"passed", "reasoning", "source_refs"} and item["passed"] is True,
                "failed or incomplete semantic check")
        substance(item)
    assessments = verdict["response_assessment"]
    require(isinstance(assessments, list) and assessments, "response claims must be assessed")
    for item in assessments:
        require(set(item) == {"claim", "disposition", "reasoning", "source_refs"}
                and isinstance(item["claim"], str) and len(item["claim"].strip()) >= 15
                and item["claim"] in context["response_text"]
                and item["disposition"] in {"supported", "conflicting", "unverifiable"},
                "response assessment must quote actual claim")
        substance(item)
    return {"schema_version": "harbor-response-guided-instruction-qualification/v1",
            "workbook_id": context["workbook_id"], "context": context_binding,
            "instruction": binding(instruction), "answer_key": binding(answer_key),
            "review_call": asdict(review_call), "review_policy": asdict(policy),
            "author_calls": [{"call": asdict(c), "expected_request": r, "policy": asdict(p)}
                             for c, r, p in author_calls],
            "implementation": binding(__file__), "passed": True, "private_only": True}


def revalidate(receipt_binding, *, instruction, answer_key, task_root):
    receipt = json.loads(checked(receipt_binding).read_text())
    require(receipt["implementation"] == binding(__file__), "response-guidance verifier changed")
    require(receipt["instruction"] == binding(instruction) and receipt["answer_key"] == binding(answer_key),
            "final instruction or answer key differs from reviewed bytes")
    expected = qualify(receipt["context"], instruction, answer_key,
                       review_call=Call(**receipt["review_call"]),
                       author_calls=[(Call(**a["call"]), a["expected_request"], Policy(**a["policy"]))
                                     for a in receipt["author_calls"]],
                       policy=Policy(**receipt["review_policy"]))
    require(receipt == expected, "response instruction qualification receipt differs")
    root = Path(task_root).resolve()
    context = validate_context(receipt["context"])
    private = [receipt_binding, receipt["context"], context["source"], context["response"]]
    forbidden_hashes = {r["sha256"] for r in private}
    for row in private:
        require(not Path(row["path"]).resolve().is_relative_to(root), "private guidance inside task")
    for path in root.rglob("*"):
        if path.is_file():
            require(hashlib.sha256(path.read_bytes()).hexdigest() not in forbidden_hashes,
                    "private source/response evidence copied into task")
    return receipt

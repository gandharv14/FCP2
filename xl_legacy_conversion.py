"""Isolated legacy candidates and a deliberately separate financial proof gate.

Conversion is never an approval. The installed Excel cache-graft runner cannot
produce the original-binary observations required by ``verify_equivalence``.
Nothing here changes the source inventory's fail-closed adoption policy.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile
from xml.etree import ElementTree as ET

from xl_source_recalc import verify_signed_runner_receipt

POLICY = "legacy-conversion-policy/v2"
SNAPSHOT_SCHEMA = "legacy-scalar-semantics/v2"
OBSERVATION_SCHEMA = "legacy-binary-observation/v2"
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


class LegacyConversionError(ValueError):
    pass


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, allow_nan=False) + "\n").encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _path(value: str | Path) -> Path:
    text = str(value)
    path = Path(text)
    if (not path.is_absolute() or str(path) != text or ".." in path.parts
            or any(ord(c) < 32 for c in text) or "\\" in text):
        raise LegacyConversionError("a canonical absolute path is required")
    for item in (path, *path.parents):
        if item.is_symlink():
            raise LegacyConversionError("symlink paths are not permitted")
    return path


def file_hash(value: str | Path) -> str:
    path = _path(value)
    with path.open("rb") as handle:
        metadata = os.fstat(handle.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise LegacyConversionError("expected a regular file")
        result = hashlib.file_digest(handle, "sha256").hexdigest()
    return result


def _sha(value: object) -> str:
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise LegacyConversionError("invalid SHA256 identity")
    return value


def detect_format(path: Path) -> str:
    """OLE takes precedence over incidental ZIP streams embedded in XLS files."""
    file_hash(path)
    with path.open("rb") as handle:
        if handle.read(8) == bytes.fromhex("d0cf11e0a1b11ae1"):
            return ".xls"
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if "xl/workbook.bin" in names and "xl/workbook.xml" not in names:
                return ".xlsb"
            if "xl/workbook.xml" in names and "xl/workbook.bin" not in names:
                return ".xlsx"
    except zipfile.BadZipFile:
        pass
    raise LegacyConversionError("unrecognized or ambiguous workbook container")


@dataclass(frozen=True)
class ConverterIdentity:
    executable: str
    sha256: str
    application_root: str
    sandbox_sha256: str

    def validate(self) -> None:
        executable, application = _path(self.executable), _path(self.application_root)
        _sha(self.sha256)
        _sha(self.sandbox_sha256)
        if not executable.is_relative_to(application) or not application.is_dir():
            raise LegacyConversionError("converter must belong to the selected application")
        if file_hash(executable) != self.sha256 or not os.access(executable, os.X_OK):
            raise LegacyConversionError("converter executable identity changed")
        sandbox = Path("/usr/bin/sandbox-exec")
        if file_hash(sandbox) != self.sandbox_sha256:
            raise LegacyConversionError("sandbox executable identity changed")


def sandbox_profile(identity: ConverterIdentity, workspace: Path) -> str:
    """No host profile, arbitrary child executable, network, or writable host IPC."""
    application, work = _path(identity.application_root), _path(workspace)
    quote = lambda value: json.dumps(str(value))
    reads = [application, work, Path("/System/Library"), Path("/usr/lib"),
             Path("/Library/Fonts"), Path("/private/etc"), Path("/dev")]
    return "\n".join([
        "(version 1)", "(deny default)",
        "(deny process-fork)",
        f"(allow process-exec (literal {quote(identity.executable)}))",
        "(allow sysctl-read)",
        "(allow file-read* " + " ".join(f"(subpath {quote(p)})" for p in reads) + ")",
        f"(allow file-write* (subpath {quote(work)}) (literal \"/dev/null\"))",
        "(deny network*)", "(deny mach-lookup)", "",
    ])


def _calculation_attributes(candidate: Path) -> dict:
    with zipfile.ZipFile(candidate) as archive:
        root = ET.fromstring(archive.read("xl/workbook.xml"))
    elements = root.findall(f"{NS}calcPr")
    if len(elements) > 1:
        raise LegacyConversionError("ambiguous calculation properties")
    return dict(elements[0].attrib) if elements else {}


def _financial_calculation(attributes: dict) -> dict:
    """Effective CT_CalcPr financial settings, observable through native Excel.

    Apply the SpreadsheetML schema defaults to omitted XML attributes. Never
    infer original-binary settings from these candidate-side defaults; the
    original observer must read their effective values before recalculation.
    Engine version, cache completion and forced-rebuild hints are provenance.
    """
    def boolean(name, default):
        value = attributes.get(name)
        if value is None:
            return default
        if value not in {"0", "1", "true", "false"}:
            raise LegacyConversionError("invalid financial calculation boolean")
        return value in {"1", "true"}
    mode, reference = attributes.get("calcMode", "auto"), attributes.get("refMode", "A1")
    if mode not in {"auto", "manual", "autoNoTable"} or reference not in {"A1", "R1C1"}:
        raise LegacyConversionError("unsupported calculation or reference mode")
    try:
        count = int(attributes.get("iterateCount", "100"))
        delta = float(attributes.get("iterateDelta", "0.001"))
    except (TypeError, ValueError) as exc:
        raise LegacyConversionError("invalid financial iteration settings") from exc
    if count < 1 or count > 2**32 - 1 or not math.isfinite(delta) or delta <= 0:
        raise LegacyConversionError("invalid financial iteration settings")
    return {"calculation_mode": mode, "reference_mode": reference,
            "iteration_enabled": boolean("iterate", False),
            "iteration_count": count, "iteration_delta": delta,
            "full_precision": boolean("fullPrecision", True)}


def candidate_provenance(candidate: Path) -> dict:
    """Preserve all exact XML calcPr attributes separately from neutral semantics."""
    return {"schema_version": "legacy-candidate-provenance/v1",
            "ooxml_calculation_attributes": _calculation_attributes(candidate)}


def scalar_snapshot(candidate: Path) -> dict:
    """Independent OOXML observation; no formula caches are read or supplied.

    This initial contract excludes rich/dynamic features instead of silently
    dropping their semantics. Original observations must use the same closed
    representation, captured from Excel *before* conversion.
    """
    if detect_format(candidate) != ".xlsx":
        raise LegacyConversionError("candidate is not OOXML")
    try:
        with zipfile.ZipFile(candidate) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or any(
                    n.startswith("/") or ".." in Path(n).parts for n in names):
                raise LegacyConversionError("unsafe or duplicate package members")
            if sum(i.file_size for i in archive.infolist()) > 512 * 1024 * 1024:
                raise LegacyConversionError("candidate exceeds structural inspection limit")
            unsupported = ("vba", "externalLink", "pivot", "connections", "queryTable",
                           "slicer", "drawing", "activeX", "ctrlProp", "embedding", "table")
            if any(any(part.lower() in n.lower() for part in unsupported)
                   for n in names if n.startswith("xl/")):
                raise LegacyConversionError("unsupported candidate feature requires richer observations")
            for name in names:
                if name.endswith(".rels"):
                    root = ET.fromstring(archive.read(name))
                    if any(r.get("TargetMode") == "External" for r in root):
                        raise LegacyConversionError("external candidate relationship")
                if name.startswith("xl/worksheets/") and name.endswith(".xml"):
                    root = ET.fromstring(archive.read(name))
                    allowed = {"sheetPr", "dimension", "sheetViews", "sheetFormatPr", "cols", "sheetData",
                               "mergeCells", "printOptions", "pageMargins", "pageSetup", "headerFooter"}
                    if any(child.tag.removeprefix(NS) not in allowed for child in root):
                        raise LegacyConversionError("worksheet feature is unsupported")
                    if any(f.attrib for f in root.iter(f"{NS}f")):
                        raise LegacyConversionError("non-scalar formula encoding is unsupported")
                    for formula in root.iter(f"{NS}f"):
                        if re.search(r"\b(?:NOW|TODAY|RAND|RANDBETWEEN|RANDARRAY|INDIRECT|OFFSET|CELL|INFO|RTD|WEBSERVICE)\s*\(",
                                     formula.text or "", re.IGNORECASE) or "[" in (formula.text or ""):
                            raise LegacyConversionError("dynamic or external formula requires richer proof")
        from openpyxl import load_workbook
        workbook = load_workbook(candidate, data_only=False, keep_links=True)
        try:
            sheets = []
            for sheet in workbook:
                cells = []
                for _, cell in sorted(sheet._cells.items()):
                    value = cell.value
                    if value is None and not cell.has_style:
                        continue
                    if not (value is None or type(value) in (str, int, float, bool)):
                        raise LegacyConversionError("typed date or non-scalar cell requires richer observations")
                    cells.append({"address": cell.coordinate, "type": cell.data_type,
                                  "value": value, "number_format": cell.number_format})
                sheets.append({"name": sheet.title, "state": sheet.sheet_state,
                               "cells": cells, "merged_ranges": sorted(str(x) for x in sheet.merged_cells)})
            if not sheets or not any(s["cells"] for s in sheets):
                raise LegacyConversionError("empty workbook cannot establish financial semantics")
            names = [dict(n) for n in workbook.defined_names.values()]
            if names or any(sheet.defined_names for sheet in workbook):
                raise LegacyConversionError("defined names require richer observations")
            result = {"schema_version": SNAPSHOT_SCHEMA, "date_epoch": workbook.epoch.isoformat(),
                      "calculation": _financial_calculation(_calculation_attributes(candidate)),
                      "sheets": sheets, "defined_names": [], "unsupported_features": []}
            canonical(result)
            return result
        finally:
            workbook.close()
    except (zipfile.BadZipFile, ET.ParseError, KeyError, TypeError) as exc:
        raise LegacyConversionError("malformed candidate structure") from exc


def produce_candidate(source: dict, destination: Path, identity: ConverterIdentity,
                      *, timeout_seconds: int = 120) -> dict:
    """Run the actual sandbox command and retain immutable unverified evidence.

    Requires an explicit caller-selected converter. Host qualification is not
    implied by hermetic command tests. Failure leaves diagnostics, never a
    completed manifest. Existing destinations are never overwritten or reused.
    """
    if type(identity) is not ConverterIdentity:
        raise LegacyConversionError("explicit typed converter identity is required")
    if platform.system() != "Darwin":
        raise LegacyConversionError("isolated converter requires macOS sandbox-exec")
    identity.validate()
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 600:
        raise LegacyConversionError("conversion timeout must be bounded")
    original = _path(source["path"])
    expected = _sha(source["sha256"])
    workbook_id = source.get("workbook_id")
    if type(workbook_id) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", workbook_id):
        raise LegacyConversionError("invalid workbook identity")
    source_format = detect_format(original)
    if source_format not in {".xls", ".xlsb"} or source_format != source.get("format"):
        raise LegacyConversionError("legacy container and frozen format disagree")
    if original.stem != workbook_id or file_hash(original) != expected:
        raise LegacyConversionError("original workbook identity changed")
    destination = _path(destination)
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix=".conversion-", dir=destination) as temp:
        work = Path(temp)
        staged = work / (workbook_id + source_format)
        shutil.copyfile(original, staged)
        if file_hash(staged) != expected:
            raise LegacyConversionError("staged original hash mismatch")
        profile, output = work / "profile", work / "output"
        profile.mkdir()
        output.mkdir()
        # LibreOffice's highest macro security; the OS sandbox also denies
        # networking, arbitrary executable launches and host writes/IPC.
        (profile / "user").mkdir()
        macro_profile = (
            '<oor:items xmlns:oor="http://openoffice.org/2001/registry">'
            '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
            '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>'
            '</item></oor:items>')
        (profile / "user" / "registrymodifications.xcu").write_text(macro_profile)
        (destination / "macro-profile.xcu").write_text(macro_profile)
        policy = sandbox_profile(identity, work)
        policy_path = destination / "sandbox.sb"
        policy_path.write_text(policy)
        argv = ["/usr/bin/sandbox-exec", "-f", str(policy_path), identity.executable,
                f"-env:UserInstallation={profile.as_uri()}", "--headless", "--norestore",
                "--nodefault", "--nolockcheck", "--convert-to", "xlsx:Calc MS Excel 2007 XML",
                "--outdir", str(output), str(staged)]
        (destination / "command.json").write_bytes(canonical(argv))
        with (destination / "stdout.txt").open("wb") as stdout, (destination / "stderr.txt").open("wb") as stderr:
            try:
                completed = subprocess.run(argv, cwd=work, env={"HOME": str(work), "TMPDIR": str(work),
                    "LANG": "en_US.UTF-8", "PATH": "/usr/bin:/bin"}, stdin=subprocess.DEVNULL,
                    stdout=stdout, stderr=stderr, timeout=timeout_seconds, check=False)
            except subprocess.TimeoutExpired as exc:
                raise LegacyConversionError("isolated conversion timed out") from exc
        if completed.returncode != 0:
            raise LegacyConversionError("isolated conversion failed; diagnostics retained")
        candidate = output / (workbook_id + ".xlsx")
        if sorted(p.name for p in output.iterdir()) != [candidate.name]:
            raise LegacyConversionError("converter output is missing or ambiguous")
        snapshot = scalar_snapshot(candidate)
        identity.validate()
        if file_hash(original) != expected or file_hash(staged) != expected:
            raise LegacyConversionError("original changed during conversion")
        candidate_hash = file_hash(candidate)
        final = destination / candidate.name
        shutil.copyfile(candidate, final)
        if file_hash(final) != candidate_hash:
            raise LegacyConversionError("candidate copy changed")
        record = {"schema_version": "legacy-conversion-candidate/v1", "policy_version": POLICY,
                  "classification": "conversion_unverified", "workbook_id": workbook_id,
                  "original": {"path": str(original), "sha256": expected, "format": source_format},
                  "candidate": {"path": str(final), "sha256": candidate_hash},
                  "converter": dict(identity.__dict__), "sandbox_sha256": hashlib.sha256(policy.encode()).hexdigest(),
                  "snapshot_sha256": digest(snapshot), "required_proof": "trusted original-binary-before-SaveAs observation"}
        (destination / "candidate-snapshot.json").write_bytes(canonical(snapshot))
        (destination / "candidate-provenance.json").write_bytes(canonical(candidate_provenance(final)))
        for artifact in ("command.json", "stdout.txt", "stderr.txt", "sandbox.sb", "macro-profile.xcu", "candidate-snapshot.json", "candidate-provenance.json"):
            record.setdefault("evidence", []).append({"path": str(destination / artifact), "sha256": file_hash(destination / artifact)})
        record["record_sha256"] = digest(record)
        manifest = destination / "candidate.json"
        pending = destination / ".candidate.pending"
        with pending.open("xb") as handle:
            handle.write(canonical(record))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(pending, manifest)
        for item in destination.iterdir():
            if item.is_file():
                item.chmod(0o400)
        return record


def validate_candidate(manifest: Path) -> dict:
    """Resume only an intact candidate; this does not qualify it for adoption."""
    _path(manifest)
    try:
        record = json.loads(manifest.read_bytes())
        expected = record.pop("record_sha256")
        if _sha(expected) != digest(record):
            raise LegacyConversionError("candidate manifest identity changed")
        record["record_sha256"] = expected
        if (record["schema_version"] != "legacy-conversion-candidate/v1"
                or record["policy_version"] != POLICY
                or record["classification"] != "conversion_unverified"):
            raise LegacyConversionError("unsupported candidate manifest")
        for entry in [record["original"], record["candidate"], *record["evidence"]]:
            if file_hash(entry["path"]) != _sha(entry["sha256"]):
                raise LegacyConversionError("candidate evidence changed")
        if detect_format(Path(record["original"]["path"])) != record["original"]["format"]:
            raise LegacyConversionError("original container identity changed")
        if digest(scalar_snapshot(Path(record["candidate"]["path"]))) != record["snapshot_sha256"]:
            raise LegacyConversionError("candidate semantic observation changed")
        if json.loads((manifest.parent / "candidate-provenance.json").read_bytes()) != candidate_provenance(Path(record["candidate"]["path"])):
            raise LegacyConversionError("candidate calculation provenance changed")
        return record
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise LegacyConversionError("incomplete candidate evidence") from exc


def verify_equivalence(*, original: Path, candidate: Path, original_observation: dict,
                       receipt: dict, trusted_public_key: Path, request_sha256: str,
                       observer_implementation_sha256: str) -> dict:
    """Validate independently signed original semantics; never infer them from XLSX.

    This is a prospective contract, not an installed observation producer or an
    inventory adoption API. A plain cache-graft receipt cannot pass this gate.
    """
    original_hash, candidate_hash = file_hash(original), file_hash(candidate)
    if detect_format(original) not in {".xls", ".xlsb"}:
        raise LegacyConversionError("observation requires the original binary workbook")
    if type(original_observation) is not dict or set(original_observation) != {
            "schema_version", "source_sha256", "capture_method", "snapshot"}:
        raise LegacyConversionError("invalid original observation schema")
    if (original_observation["schema_version"] != OBSERVATION_SCHEMA
            or original_observation["source_sha256"] != original_hash
            or original_observation["capture_method"] != "excel-original-before-save-as"):
        raise LegacyConversionError("observation is not bound to the original binary")
    snapshot = scalar_snapshot(candidate)
    if canonical(original_observation["snapshot"]) != canonical(snapshot):
        raise LegacyConversionError("original financial semantics differ from candidate")
    payload = verify_signed_runner_receipt(receipt, trusted_public_key,
        request_sha256=_sha(request_sha256), source_sha256=original_hash, output_sha256=candidate_hash)
    claims = {"conversion_policy": POLICY, "observation_schema": OBSERVATION_SCHEMA,
              "original_observation_sha256": digest(original_observation),
              "candidate_observation_sha256": digest(snapshot),
              "observer_implementation_sha256": _sha(observer_implementation_sha256),
              "observed_original_before_save_as": True, "original_recalculation_complete": True,
              "candidate_recalculation_complete": True}
    if any(type(payload.get(key)) is not type(value) or payload.get(key) != value
           for key, value in claims.items()):
        raise LegacyConversionError("trusted receipt lacks independent conversion observations")
    if file_hash(original) != original_hash or file_hash(candidate) != candidate_hash:
        raise LegacyConversionError("conversion evidence changed during verification")
    return {"schema_version": "legacy-conversion-proof/v2", "policy_version": POLICY,
            "original_sha256": original_hash, "candidate_sha256": candidate_hash,
            "original_observation_sha256": digest(original_observation),
            "candidate_observation_sha256": digest(snapshot), "receipt_sha256": digest(receipt),
            "financial_semantics_equivalent": True, "inventory_adoption_supported": False}

#!/usr/bin/python3
"""Root-installed, fail-closed Microsoft Excel recalculation runner.

The maintained source has no imports from the surrounding workspace. Production
invocation is through the root-owned wrapper emitted by install_runner.py so the
private receipt key remains readable only by root.
"""

from __future__ import annotations

import argparse
import base64
import copy
import contextlib
import hashlib
import json
import os
import platform
import pwd
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Callable, Mapping
from xml.etree import ElementTree


REQUEST_SCHEMA = "source-recalc-request/v2"
POLICY_VERSION = "source-recalc-policy/v3"
RECEIPT_SCHEMA = "excel-runner-receipt/v1"
ATTESTATION_SCHEMA = "excel-isolation-attestation/v1"
CONFIG_PATH = Path("/etc/harbor/excel-runner.json")
REQUIRED_CONTROLS = (
    "dedicated_session",
    "network_disabled",
    "macros_disabled",
    "add_ins_disabled",
    "link_updates_disabled",
    "prompts_suppressed",
)
NETWORK_SANDBOX = Path("/usr/bin/sandbox-exec")
NETWORK_SANDBOX_PROFILE = (
    "(version 1)\n"
    "(allow default)\n"
    "(deny network*)\n"
)
NETWORK_SANDBOX_PROFILE_SHA256 = hashlib.sha256(
    NETWORK_SANDBOX_PROFILE.encode("utf-8")
).hexdigest()
PFCTL = Path("/sbin/pfctl")
PF_CONFIG = Path("/etc/pf.conf")
PF_ANCHOR = "com.apple/fcp2-excel-runner"
PF_RULES = (
    "pass quick on lo0 all\n"
    "block drop in quick all\n"
    "block drop out quick all\n"
)
PF_RULES_SHA256 = hashlib.sha256(PF_RULES.encode("utf-8")).hexdigest()


def _pf_config_activates_anchor(contents: str) -> bool:
    return any(
        line.split("#", 1)[0].strip() == 'anchor "com.apple/*"'
        for line in contents.splitlines()
    )
MAX_REQUEST_BYTES = 1024 * 1024
MAX_CLOCK_SKEW_NS = 60 * 1_000_000_000


class RunnerError(RuntimeError):
    """The runner could not prove a safe, complete recalculation."""


def canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_nofollow(path: Path) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise RunnerError(f"required file is unavailable: {path}") from exc
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise RunnerError(f"required path is not a regular non-symlink file: {path}")
    return metadata


def validate_private_key(path: Path, *, expected_uid: int = 0) -> None:
    metadata = _regular_nofollow(path)
    if metadata.st_uid != expected_uid or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise RunnerError("receipt private key must be owner-only (0600)")


def validate_root_file(path: Path, *, executable: bool = False) -> None:
    metadata = _regular_nofollow(path)
    if metadata.st_uid != 0 or metadata.st_mode & 0o022:
        raise RunnerError(f"trusted file is not root-protected: {path}")
    if executable and not metadata.st_mode & 0o111:
        raise RunnerError(f"trusted executable is not executable: {path}")
    resolved = path.resolve(strict=True)
    for parent in resolved.parents:
        parent_metadata = parent.stat()
        if (
            parent_metadata.st_uid != 0
            or parent_metadata.st_mode & 0o022
            or not stat.S_ISDIR(parent_metadata.st_mode)
        ):
            raise RunnerError(f"trusted file has an unprotected parent: {parent}")


def validate_root_directory(path: Path) -> None:
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise RunnerError(f"trusted directory is unavailable: {path}") from exc
    if (
        path.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_mode & 0o022
    ):
        raise RunnerError(f"trusted directory is not root-protected: {path}")
    for parent in resolved.parents:
        parent_metadata = parent.stat()
        if parent_metadata.st_uid != 0 or parent_metadata.st_mode & 0o022:
            raise RunnerError(f"trusted directory has an unprotected parent: {parent}")


def validate_invoker_file(path: Path, invoking_uid: int, *, request: bool) -> None:
    metadata = _regular_nofollow(path)
    if (
        metadata.st_uid != invoking_uid
        or metadata.st_nlink != 1
        or metadata.st_mode & 0o077
    ):
        kind = "request" if request else "candidate"
        raise RunnerError(f"{kind} must be owner-only and owned by the invoking user")


def load_config(path: Path = CONFIG_PATH) -> dict:
    validate_root_file(path)
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RunnerError("runner configuration is unreadable") from exc
    if config.get("schema_version") != "excel-runner-config/v1":
        raise RunnerError("runner configuration schema is invalid")
    controls = config.get("isolation_controls")
    if not isinstance(controls, dict) or any(
        controls.get(name) is not True for name in REQUIRED_CONTROLS
    ):
        raise RunnerError("runner isolation controls are incomplete")
    automation_user = config.get("automation_user")
    if not isinstance(automation_user, str) or not automation_user:
        raise RunnerError("dedicated automation user is not configured")
    try:
        automation_account = pwd.getpwnam(automation_user)
    except KeyError as exc:
        raise RunnerError("dedicated automation user is unknown") from exc
    if automation_account.pw_uid == 0:
        raise RunnerError("dedicated automation user cannot be root")
    implementation = Path(str(config.get("runner_implementation", "")))
    validate_root_file(implementation, executable=True)
    if sha256_file(implementation) != config.get("runner_implementation_sha256"):
        raise RunnerError("installed runner implementation hash does not match")
    public_key = Path(str(config.get("receipt_public_key", "")))
    validate_root_file(public_key)
    if sha256_file(public_key) != config.get("receipt_public_key_sha256"):
        raise RunnerError("receipt public key hash does not match")
    private_key = Path(str(config.get("receipt_private_key", "")))
    validate_private_key(private_key)
    openssl = Path(str(config.get("openssl_binary", "")))
    validate_root_file(openssl, executable=True)
    sandbox = Path(str(config.get("network_sandbox", "")))
    validate_root_file(sandbox, executable=True)
    if (
        sandbox != NETWORK_SANDBOX
        or config.get("network_sandbox_profile_sha256")
        != NETWORK_SANDBOX_PROFILE_SHA256
    ):
        raise RunnerError("network sandbox configuration is invalid")
    validate_root_file(PFCTL, executable=True)
    if (
        config.get("pf_anchor") != PF_ANCHOR
        or config.get("pf_rules_sha256") != PF_RULES_SHA256
    ):
        raise RunnerError("PF network isolation configuration is invalid")
    return config


def _read_request(path: Path) -> dict:
    metadata = _regular_nofollow(path)
    if metadata.st_size > MAX_REQUEST_BYTES:
        raise RunnerError("request exceeds the size limit")
    try:
        request = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RunnerError("request is unreadable") from exc
    if not isinstance(request, dict):
        raise RunnerError("request must be a JSON object")
    return request


def validate_request(
    request: Mapping[str, object],
    workbook: Path,
    *,
    public_key_sha256: str,
    now_ns: int | None = None,
) -> None:
    """Validate the request and candidate without workspace imports."""
    now = time.time_ns() if now_ns is None else now_ns
    failures: list[str] = []
    if request.get("schema_version") != REQUEST_SCHEMA:
        failures.append("schema_version")
    if request.get("policy_version") != POLICY_VERSION:
        failures.append("policy_version")
    request_id = request.get("request_id")
    if (
        not isinstance(request_id, str)
        or not request_id
        or len(request_id) > 128
        or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in request_id)
    ):
        failures.append("request_id")
    unsigned = dict(request)
    claimed_request_hash = unsigned.pop("request_sha256", None)
    if claimed_request_hash != sha256_bytes(canonical_json(unsigned)):
        failures.append("request_sha256")
    created = request.get("created_at_ns")
    expires = request.get("expires_at_ns")
    if (
        not isinstance(created, int)
        or not isinstance(expires, int)
        or expires <= created
        or created > now + MAX_CLOCK_SKEW_NS
        or expires < now
    ):
        failures.append("request_expiry")
    metadata = _regular_nofollow(workbook)
    if workbook.suffix.lower() != ".xlsx" or metadata.st_size <= 0:
        failures.append("workbook_type")
    candidate_hash = sha256_file(workbook)
    source = request.get("source")
    if not isinstance(source, dict):
        source = {}
        failures.append("source")
    if (
        source.get("sha256") != candidate_hash
        or request.get("source_sha256") != candidate_hash
        or source.get("size_bytes") != metadata.st_size
        or request.get("source_size_bytes") != metadata.st_size
    ):
        failures.append("source_binding")
    maximum = request.get("max_source_size_bytes")
    if not isinstance(maximum, int) or metadata.st_size > maximum:
        failures.append("size_constraint")
    source_path_text = source.get("path")
    if isinstance(source_path_text, str) and source_path_text:
        try:
            if Path(source_path_text).resolve() == workbook.resolve():
                failures.append("candidate_is_original")
        except OSError:
            failures.append("source_path")
    policy = request.get("policy")
    if (
        not isinstance(policy, dict)
        or policy.get("require_semantic_equivalence") is not True
        or policy.get("allow_cache_only_changes") is not True
    ):
        failures.append("policy")
    constraints = request.get("engine_constraints")
    if not isinstance(constraints, dict):
        failures.append("engine_constraints")
    else:
        allowed = constraints.get("allowed_engines")
        if not isinstance(allowed, list) or "excel-macos" not in allowed:
            failures.append("allowed_engine")
        if constraints.get("required_engine") != "excel-macos":
            failures.append("required_engine")
        versions = constraints.get("permitted_versions")
        if not isinstance(versions, list) or not versions:
            failures.append("permitted_versions")
        if constraints.get("trusted_runner_public_key_sha256") != public_key_sha256:
            failures.append("trusted_runner_public_key")
    if failures:
        raise RunnerError(
            "request validation failed: " + ", ".join(sorted(set(failures)))
        )


def validate_xlsx(path: Path) -> None:
    _regular_nofollow(path)
    if not zipfile.is_zipfile(path):
        raise RunnerError("Excel did not leave a valid OOXML workbook")
    try:
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:
                raise RunnerError("Excel left a corrupt OOXML workbook")
            names = set(archive.namelist())
    except (OSError, zipfile.BadZipFile) as exc:
        raise RunnerError("Excel left an unreadable OOXML workbook") from exc
    if not {"[Content_Types].xml", "xl/workbook.xml"}.issubset(names):
        raise RunnerError("Excel output is missing required OOXML parts")


_AUTOMATION_SCRIPT = r'''
on run argv
    set workbookPath to item 1 of argv
    set excelAppPath to item 2 of argv
    using terms from application "Microsoft Excel"
        tell application excelAppPath
            if (count of workbooks) is not 0 then error "Excel session is not dedicated"
            set display alerts to false
            set ask to update links to false
            set enable events to false
            set automation security to msoAutomationSecurityForceDisable
            -- Excel returns `missing value` for the inline filtered-count
            -- expression even when no add-ins are enabled. Materializing the
            -- filtered list first makes `count` return the actual integer.
            set enabledAddIns to every add in whose installed is true
            if (count of enabledAddIns) is not 0 then error "Excel add-ins are enabled"
            set calculate before save to true
            set previousCalculation to calculation
            set calculation to calculation manual
            set targetBook to open workbook workbook file name workbookPath update links do not update links read only false editable true notify false
            set calculation to calculation automatic
            calculate full rebuild
            save workbook as targetBook filename workbookPath
            close targetBook saving no
            set calculation to previousCalculation
            quit
        end tell
    end using terms from
end run
'''


def _excel_running() -> bool:
    probe = subprocess.run(
        ["/usr/bin/pgrep", "-x", "Microsoft Excel"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return probe.returncode == 0


def excel_version(excel_app: Path) -> str:
    version = subprocess.run(
        [
            "/usr/bin/mdls",
            "-raw",
            "-name",
            "kMDItemVersion",
            str(excel_app),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    value = version.stdout.strip()
    if version.returncode != 0 or not value or value == "(null)":
        fallback = subprocess.run(
            [
                "/usr/bin/defaults",
                "read",
                str(excel_app / "Contents" / "Info"),
                "CFBundleShortVersionString",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        value = fallback.stdout.strip()
        if fallback.returncode != 0 or not value:
            raise RunnerError("Microsoft Excel version is unavailable")
    return value


def _excel_container_staging_root(account: pwd.struct_passwd) -> Path:
    """Return a protected user-owned staging root accessible to sandboxed Excel."""
    documents = (
        Path(account.pw_dir)
        / "Library"
        / "Containers"
        / "com.microsoft.Excel"
        / "Data"
        / "Documents"
    )
    if (
        documents.is_symlink()
        or not documents.is_dir()
        or documents.stat().st_uid != account.pw_uid
        or documents.stat().st_mode & 0o022
    ):
        raise RunnerError("Excel container Documents directory is unavailable or unsafe")
    root = documents / "FCP2-Recalc"
    try:
        root.mkdir(mode=0o700)
        if os.geteuid() == 0:
            os.chown(root, account.pw_uid, account.pw_gid)
    except FileExistsError:
        pass
    if (
        root.is_symlink()
        or not root.is_dir()
        or root.stat().st_uid != account.pw_uid
        or root.stat().st_mode & 0o077
    ):
        raise RunnerError("Excel container staging root is unavailable or unsafe")
    return root


def _formula_cells(root: ElementTree.Element) -> dict[str, ElementTree.Element]:
    cells = {}
    for cell in root.findall(".//{*}c"):
        if cell.find("{*}f") is None:
            continue
        coordinate = cell.attrib.get("r")
        if not coordinate or coordinate in cells:
            raise RunnerError("worksheet has ambiguous formula coordinates")
        cells[coordinate] = cell
    return cells


def _replace_candidate_from_staging(staged: Path, candidate: Path) -> None:
    """Atomically graft Excel's formula caches into the original package.

    A normal Excel save reserializes drawings, relationships, comments/person
    metadata, styles, themes, and calculation settings. Replacing the complete
    package would therefore lose or rewrite content unrelated to recalculation.
    Preserve every original member and transplant only formula result types and
    ``<v>`` cache nodes from Excel's private calculated copy.
    """
    validate_xlsx(staged)
    metadata = _regular_nofollow(candidate)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{candidate.name}.",
        suffix=".excel-output.xlsx",
        dir=str(candidate.parent),
    )
    temporary = Path(temporary_name)
    try:
        os.close(descriptor)
        replacements: dict[str, bytes] = {}
        try:
            with (
                zipfile.ZipFile(candidate) as original,
                zipfile.ZipFile(staged) as calculated,
            ):
                original_sheets = {
                    name for name in original.namelist()
                    if name.startswith("xl/worksheets/")
                    and name.endswith(".xml")
                    and "/_rels/" not in name
                }
                calculated_sheets = {
                    name for name in calculated.namelist()
                    if name.startswith("xl/worksheets/")
                    and name.endswith(".xml")
                    and "/_rels/" not in name
                }
                if original_sheets != calculated_sheets:
                    raise RunnerError("Excel changed the worksheet package layout")
                for name in sorted(original_sheets):
                    original_root = ElementTree.fromstring(original.read(name))
                    calculated_root = ElementTree.fromstring(calculated.read(name))
                    original_cells = _formula_cells(original_root)
                    calculated_cells = _formula_cells(calculated_root)
                    if set(original_cells) != set(calculated_cells):
                        raise RunnerError("Excel changed the formula cell set")
                    for coordinate, original_cell in original_cells.items():
                        calculated_cell = calculated_cells[coordinate]
                        original_formula = original_cell.find("{*}f")
                        calculated_formula = calculated_cell.find("{*}f")
                        if (
                            original_formula is None
                            or calculated_formula is None
                            or (original_formula.text or "")
                            != (calculated_formula.text or "")
                            or original_formula.attrib != calculated_formula.attrib
                        ):
                            raise RunnerError(
                                f"Excel changed formula encoding at {coordinate}"
                            )
                        if "t" in calculated_cell.attrib:
                            original_cell.attrib["t"] = calculated_cell.attrib["t"]
                        else:
                            original_cell.attrib.pop("t", None)
                        for child in list(original_cell):
                            if child.tag.rsplit("}", 1)[-1] == "v":
                                original_cell.remove(child)
                        calculated_value = calculated_cell.find("{*}v")
                        if calculated_value is not None:
                            formula_index = list(original_cell).index(original_formula)
                            original_cell.insert(
                                formula_index + 1,
                                copy.deepcopy(calculated_value),
                            )
                    replacements[name] = ElementTree.tostring(
                        original_root,
                        encoding="utf-8",
                        xml_declaration=True,
                    )
                with zipfile.ZipFile(temporary, "w") as output:
                    for info in original.infolist():
                        output.writestr(
                            info,
                            replacements.get(info.filename, original.read(info)),
                        )
        except (KeyError, OSError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
            raise RunnerError(f"cannot graft Excel formula caches: {exc}") from exc
        validate_xlsx(temporary)
        os.chmod(temporary, stat.S_IMODE(metadata.st_mode))
        if os.geteuid() == 0:
            os.chown(temporary, metadata.st_uid, metadata.st_gid)
        os.replace(temporary, candidate)
        directory = os.open(candidate.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def run_excel_automation(
    excel_app: Path,
    workbook: Path,
    timeout_seconds: int,
) -> str:
    if _excel_running():
        raise RunnerError("Microsoft Excel is already running; session is not dedicated")
    command_prefix: list[str] = []
    if os.geteuid() == 0:
        invoking_user = os.environ.get("SUDO_USER")
        if not invoking_user or invoking_user == "root":
            raise RunnerError("root runner has no dedicated automation user")
        try:
            account = pwd.getpwnam(invoking_user)
        except KeyError as exc:
            raise RunnerError("dedicated automation user is unknown") from exc
        if account.pw_uid == 0:
            raise RunnerError("Excel automation cannot run as root")
        command_prefix = ["/usr/bin/sudo", "-n", "-u", invoking_user, "--"]
    else:
        account = pwd.getpwuid(os.geteuid())
    # Do not wrap osascript in sandbox-exec. A sandboxed process cannot send
    # Apple Events without a code-signing entitlement, and /usr/bin/osascript
    # consequently fails with errAEPrivilegeError (-10004) even under an
    # allow-default profile. Network isolation is already fail-closed here:
    # process_recalculation first requires loopback to be the only active
    # interface and invokes this function inside enforce_pf_network_isolation.
    staging_root = _excel_container_staging_root(account)
    staging_dir = Path(tempfile.mkdtemp(prefix="run-", dir=staging_root))
    staged_workbook = staging_dir / workbook.name
    try:
        shutil.copyfile(workbook, staged_workbook)
        os.chmod(staging_dir, 0o700)
        os.chmod(staged_workbook, 0o600)
        if os.geteuid() == 0:
            os.chown(staging_dir, account.pw_uid, account.pw_gid)
            os.chown(staged_workbook, account.pw_uid, account.pw_gid)
    except BaseException:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".applescript", encoding="utf-8", delete=False
    ) as handle:
        handle.write(_AUTOMATION_SCRIPT)
        script_path = Path(handle.name)
    os.chmod(script_path, 0o644)
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            command_prefix
            + [
                "/usr/bin/osascript",
                str(script_path),
                str(staged_workbook),
                str(excel_app),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            _, stderr = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
            raise RunnerError("Excel automation timed out") from exc
        if process.returncode != 0:
            raise RunnerError(
                "Excel automation failed: " + (stderr.strip() or "osascript failed")
            )
        _replace_candidate_from_staging(staged_workbook, workbook)
    finally:
        script_path.unlink(missing_ok=True)
        if _excel_running():
            subprocess.run(
                command_prefix
                + [
                    "/usr/bin/osascript",
                    "-e",
                    'tell application "Microsoft Excel" to quit',
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
            )
        shutil.rmtree(staging_dir, ignore_errors=True)
    if _excel_running():
        raise RunnerError("Microsoft Excel did not exit its isolated session")
    return excel_version(excel_app)


def sign_payload(payload: Mapping[str, object], config: Mapping[str, object]) -> str:
    private_key = Path(str(config["receipt_private_key"]))
    public_key = Path(str(config["receipt_public_key"]))
    openssl = Path(str(config["openssl_binary"]))
    validate_private_key(private_key)
    with tempfile.TemporaryDirectory(prefix="excel-receipt-sign-") as temporary:
        message = Path(temporary) / "payload.json"
        signature = Path(temporary) / "signature.bin"
        message.write_bytes(canonical_json(payload))
        signed = subprocess.run(
            [
                str(openssl),
                "pkeyutl",
                "-sign",
                "-rawin",
                "-inkey",
                str(private_key),
                "-in",
                str(message),
                "-out",
                str(signature),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if signed.returncode != 0:
            raise RunnerError("Ed25519 receipt signing failed")
        verified = subprocess.run(
            [
                str(openssl),
                "pkeyutl",
                "-verify",
                "-rawin",
                "-pubin",
                "-inkey",
                str(public_key),
                "-in",
                str(message),
                "-sigfile",
                str(signature),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if verified.returncode != 0:
            raise RunnerError("Ed25519 receipt self-verification failed")
        return base64.b64encode(signature.read_bytes()).decode("ascii")


Automation = Callable[[Path, Path, int], str]
Signer = Callable[[Mapping[str, object], Mapping[str, object]], str]


def no_default_network_route() -> bool:
    if platform.system() != "Darwin":
        return False
    try:
        interfaces = subprocess.run(
            ["/sbin/ifconfig", "-l", "-u"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return False
    if interfaces.returncode != 0:
        return False
    # A compliant dedicated host has no active network interface except loopback.
    return set(interfaces.stdout.split()).issubset({"lo0"})


@contextlib.contextmanager
def enforce_pf_network_isolation():
    """Globally block network traffic on the dedicated host while active."""
    if platform.system() != "Darwin" or os.geteuid() != 0:
        raise RunnerError("PF network isolation requires macOS root")
    validate_root_file(PFCTL, executable=True)
    validate_root_file(PF_CONFIG)
    try:
        pf_config = PF_CONFIG.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RunnerError("PF configuration is unreadable") from exc
    if not _pf_config_activates_anchor(pf_config):
        raise RunnerError("PF configuration does not activate the runner anchor")
    enabled = subprocess.run(
        [str(PFCTL), "-E"],
        check=False,
        capture_output=True,
        text=True,
    )
    if enabled.returncode != 0:
        raise RunnerError("could not enable PF network isolation")
    loaded = subprocess.run(
        [str(PFCTL), "-a", PF_ANCHOR, "-f", "-"],
        input=PF_RULES,
        check=False,
        capture_output=True,
        text=True,
    )
    if loaded.returncode != 0:
        raise RunnerError("could not load PF network isolation rules")
    states = subprocess.run(
        [str(PFCTL), "-F", "states"],
        check=False,
        capture_output=True,
        text=True,
    )
    if states.returncode != 0:
        subprocess.run(
            [str(PFCTL), "-a", PF_ANCHOR, "-F", "all"],
            check=False,
            capture_output=True,
            text=True,
        )
        raise RunnerError("could not clear pre-existing PF connection states")
    try:
        yield
    finally:
        flushed = subprocess.run(
            [str(PFCTL), "-a", PF_ANCHOR, "-F", "all"],
            check=False,
            capture_output=True,
            text=True,
        )
        if flushed.returncode != 0:
            raise RunnerError("could not clear PF network isolation rules")


def process_recalculation(
    *,
    excel_app: Path,
    workbook: Path,
    request_path: Path,
    config: Mapping[str, object],
    automation: Automation = run_excel_automation,
    signer: Signer = sign_payload,
    network_check: Callable[[], bool] = no_default_network_route,
    network_enforcer: Callable[[], object] = enforce_pf_network_isolation,
) -> dict:
    if platform.system() != "Darwin":
        raise RunnerError("the Excel runner only runs on macOS")
    if (
        excel_app.is_symlink()
        or not excel_app.is_dir()
        or excel_app.name != "Microsoft Excel.app"
    ):
        raise RunnerError("Microsoft Excel application is unavailable")
    request = _read_request(request_path)
    public_key_hash = str(config.get("receipt_public_key_sha256", ""))
    validate_request(request, workbook, public_key_sha256=public_key_hash)
    validate_xlsx(workbook)
    initial_candidate_hash = sha256_file(workbook)
    source_path: Path | None = None
    source_hash: str | None = None
    source_record = request.get("source")
    if isinstance(source_record, dict) and isinstance(source_record.get("path"), str):
        possible_source = Path(source_record["path"])
        if possible_source.exists() and possible_source.resolve() != workbook.resolve():
            _regular_nofollow(possible_source)
            source_path = possible_source
            source_hash = sha256_file(possible_source)
            if source_hash != request["source_sha256"]:
                raise RunnerError("original source no longer matches the request")
    timeout = config.get("timeout_seconds", 300)
    if not isinstance(timeout, int) or timeout < 1 or timeout > 3600:
        raise RunnerError("runner timeout is invalid")
    controls = {
        name: bool(config.get("isolation_controls", {}).get(name))
        for name in REQUIRED_CONTROLS
    }
    if any(value is not True for value in controls.values()):
        raise RunnerError("runner isolation controls are incomplete")
    if not network_check():
        raise RunnerError("network isolation is not enforced at execution time")
    with network_enforcer():
        engine_version = automation(excel_app, workbook, timeout)
        validate_xlsx(workbook)
        if source_path is not None and sha256_file(source_path) != source_hash:
            raise RunnerError("Excel automation modified the original workbook")
        output_hash = sha256_file(workbook)
        if initial_candidate_hash != request["source_sha256"]:
            raise RunnerError("private candidate changed before automation")
        permitted = request["engine_constraints"]["permitted_versions"]
        if "*" not in permitted and engine_version not in permitted:
            raise RunnerError("Excel version is not permitted by the request")
        payload = {
            "request_sha256": request["request_sha256"],
            "source_sha256": request["source_sha256"],
            "output_sha256": output_hash,
            "engine": "excel-macos",
            "engine_version": engine_version,
            "calculation_complete": True,
            "isolation_enforced": True,
            "network_isolation_mechanism": "macos-pf-anchor",
            "network_isolation_rules_sha256": PF_RULES_SHA256,
            "isolation_controls": controls,
            "completed_at_ns": time.time_ns(),
        }
        signature = signer(payload, config)
    return {
        "schema_version": RECEIPT_SCHEMA,
        "signature_algorithm": "ed25519",
        "signed_payload": payload,
        "signature_base64": signature,
    }


def preflight(config_path: Path = CONFIG_PATH) -> dict:
    checks: dict[str, object] = {
        "platform_macos": platform.system() == "Darwin",
        "effective_uid_root": os.geteuid() == 0,
    }
    try:
        config = load_config(config_path)
        checks["config_valid"] = True
        checks["excel_available"] = Path(
            str(config.get("excel_app", "/Applications/Microsoft Excel.app"))
        ).is_dir()
    except RunnerError as exc:
        checks["config_valid"] = False
        checks["error"] = str(exc)
    checks["ready"] = all(value is True for key, value in checks.items() if key != "error")
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--excel-app")
    parser.add_argument("--workbook")
    parser.add_argument("--request")
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args(argv)
    if args.preflight:
        report = preflight()
        print(json.dumps(report, sort_keys=True))
        return 0 if report["ready"] else 2
    if not all((args.excel_app, args.workbook, args.request)):
        parser.error("--excel-app, --workbook, and --request are required")
    if os.geteuid() != 0:
        raise RunnerError("production runner must execute as root")
    config = load_config()
    automation_user = str(config["automation_user"])
    automation_account = pwd.getpwnam(automation_user)
    try:
        invoking_uid = int(os.environ["SUDO_UID"])
    except (KeyError, ValueError) as exc:
        raise RunnerError("production runner requires a sudo invoking user") from exc
    if invoking_uid <= 0:
        raise RunnerError("production runner requires a non-root invoking user")
    if (
        invoking_uid != automation_account.pw_uid
        or os.environ.get("SUDO_USER") != automation_user
    ):
        raise RunnerError("sudo invoker is not the dedicated automation user")
    validate_invoker_file(Path(args.workbook), invoking_uid, request=False)
    validate_invoker_file(Path(args.request), invoking_uid, request=True)
    configured_excel = Path(str(config.get("excel_app", "")))
    if Path(args.excel_app).resolve() != configured_excel.resolve():
        raise RunnerError("--excel-app does not match the installed configuration")
    validate_root_directory(configured_excel)
    receipt = process_recalculation(
        excel_app=Path(args.excel_app),
        workbook=Path(args.workbook),
        request_path=Path(args.request),
        config=config,
    )
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RunnerError as error:
        print(f"excel runner: {error}", file=os.sys.stderr)
        raise SystemExit(1)

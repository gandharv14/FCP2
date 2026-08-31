#!/usr/bin/env python3
"""Plan, preflight, install, and check the macOS Excel runner deployment."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import pwd
import shlex
import stat
import subprocess
import tempfile
from pathlib import Path


INSTALL_ROOT = Path("/Library/Application Support/FCP2/recalc")
CONFIG_ROOT = Path("/etc/harbor")
SUDOERS_PATH = Path("/etc/sudoers.d/fcp2-excel-runner")
IMPLEMENTATION_NAME = "excel_runner.py"
WRAPPER_NAME = "harbor-excel-sandbox"
PUBLIC_KEY_NAME = "excel-runner-public.pem"
PRIVATE_KEY_NAME = "excel-runner-private.pem"
CONFIG_NAME = "excel-runner.json"
ATTESTATION_NAME = "excel-isolation-attestation.json"
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
CONTROLS = (
    "dedicated_session",
    "network_disabled",
    "macros_disabled",
    "add_ins_disabled",
    "link_updates_disabled",
    "prompts_suppressed",
)


class InstallError(RuntimeError):
    """The requested deployment is unsafe or incomplete."""


def canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _root_protected(path: Path, *, directory: bool = False) -> bool:
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError:
        return False
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if (
        path.is_symlink()
        or not expected_type(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_mode & 0o022
    ):
        return False
    return all(
        parent.stat().st_uid == 0 and not parent.stat().st_mode & 0o022
        for parent in resolved.parents
    )


def _openssl_ed25519_available(openssl: Path) -> tuple[bool, str]:
    if not openssl.is_file() or not os.access(openssl, os.X_OK):
        return False, "OpenSSL executable is unavailable"
    with tempfile.TemporaryDirectory(prefix="excel-runner-preflight-") as temporary:
        key = Path(temporary) / "key.pem"
        generated = subprocess.run(
            [str(openssl), "genpkey", "-algorithm", "Ed25519", "-out", str(key)],
            check=False,
            capture_output=True,
            text=True,
        )
    if generated.returncode != 0:
        return False, "OpenSSL does not support Ed25519"
    return True, "available"


def _no_default_network_route() -> bool:
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
    return (
        interfaces.returncode == 0
        and set(interfaces.stdout.split()).issubset({"lo0"})
    )


def _pf_anchor_supported() -> bool:
    if not _root_protected(PFCTL) or not _root_protected(PF_CONFIG):
        return False
    try:
        contents = PF_CONFIG.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    return any(
        line.split("#", 1)[0].strip() == 'anchor "com.apple/*"'
        for line in contents.splitlines()
    )


def _console_user_is(automation_user: str) -> bool:
    try:
        return pwd.getpwuid(Path("/dev/console").stat().st_uid).pw_name == automation_user
    except (OSError, KeyError):
        return False


def _excel_not_running() -> bool:
    process = subprocess.run(
        ["/usr/bin/pgrep", "-x", "Microsoft Excel"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return process.returncode != 0


def preflight(excel_app: Path, openssl: Path, automation_user: str) -> dict:
    try:
        account = pwd.getpwnam(automation_user)
        user_valid = account.pw_uid >= 500 and account.pw_uid != 0
    except KeyError:
        user_valid = False
    openssl_ok, openssl_reason = _openssl_ed25519_available(openssl)
    checks = {
        "platform_macos": platform.system() == "Darwin",
        "excel_app_present": (
            excel_app.is_dir()
            and not excel_app.is_symlink()
            and excel_app.name == "Microsoft Excel.app"
        ),
        "excel_app_root_protected": _root_protected(excel_app, directory=True),
        "osascript_present": Path("/usr/bin/osascript").is_file(),
        "system_python_present": Path("/usr/bin/python3").is_file(),
        "network_sandbox_root_protected": _root_protected(
            NETWORK_SANDBOX
        ),
        "pf_anchor_supported": _pf_anchor_supported(),
        "automation_user_valid": user_valid,
        "automation_user_is_console_user": _console_user_is(automation_user),
        "excel_not_running": _excel_not_running(),
        "no_default_network_route": _no_default_network_route(),
        "openssl_ed25519": openssl_ok,
        "openssl_root_protected": _root_protected(openssl),
        "openssl_detail": openssl_reason,
    }
    checks["ready"] = all(
        value is True
        for key, value in checks.items()
        if key not in {"openssl_detail"}
    )
    return checks


def planned_commands(
    source_runner: Path,
    excel_app: Path,
    openssl: Path,
    automation_user: str,
) -> list[str]:
    return [
        (
            "python3 scripts/recalc/install_runner.py preflight "
            f"--automation-user {shlex.quote(automation_user)} "
            f"--excel-app {shlex.quote(str(excel_app))} "
            f"--openssl {shlex.quote(str(openssl))}"
        ),
        (
            "sudo python3 scripts/recalc/install_runner.py install --apply "
            "--confirm-dedicated-session --confirm-network-disabled "
            "--confirm-macros-disabled --confirm-add-ins-disabled "
            "--confirm-link-updates-disabled --confirm-prompts-suppressed "
            f"--automation-user {shlex.quote(automation_user)} "
            f"--excel-app {shlex.quote(str(excel_app))} "
            f"--openssl {shlex.quote(str(openssl))} "
            f"--source-runner {shlex.quote(str(source_runner))}"
        ),
        (
            "sudo python3 scripts/recalc/install_runner.py check "
            f"--automation-user {shlex.quote(automation_user)} "
            f"--excel-app {shlex.quote(str(excel_app))} "
            f"--openssl {shlex.quote(str(openssl))}"
        ),
    ]


def _atomic_write(path: Path, contents: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.chown(temporary, 0, 0)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _protect_directory(path: Path, mode: int = 0o755) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.chown(path, 0, 0)
    os.chmod(path, mode)


def _sudoers_policy(implementation: Path, automation_user: str) -> bytes:
    escaped = str(implementation).replace("\\", "\\\\").replace(" ", "\\ ")
    return (
        "Defaults!FCP2_EXCEL_RUNNER env_reset\n"
        f"Cmnd_Alias FCP2_EXCEL_RUNNER = {escaped} "
        "--excel-app * --workbook * --request *\n"
        f"{automation_user} ALL=(root) NOPASSWD: FCP2_EXCEL_RUNNER\n"
    ).encode("utf-8")


def _generate_keys(openssl: Path, private_key: Path, public_key: Path) -> None:
    with tempfile.TemporaryDirectory(
        prefix="excel-runner-keys-", dir=str(CONFIG_ROOT)
    ) as temporary:
        staged_private = Path(temporary) / PRIVATE_KEY_NAME
        staged_public = Path(temporary) / PUBLIC_KEY_NAME
        generated = subprocess.run(
            [
                str(openssl),
                "genpkey",
                "-algorithm",
                "Ed25519",
                "-out",
                str(staged_private),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if generated.returncode != 0:
            raise InstallError("failed to generate the Ed25519 private key")
        exported = subprocess.run(
            [
                str(openssl),
                "pkey",
                "-in",
                str(staged_private),
                "-pubout",
                "-out",
                str(staged_public),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if exported.returncode != 0:
            raise InstallError("failed to derive the Ed25519 public key")
        _atomic_write(private_key, staged_private.read_bytes(), 0o600)
        _atomic_write(public_key, staged_public.read_bytes(), 0o644)


def install(
    *,
    source_runner: Path,
    excel_app: Path,
    openssl: Path,
    automation_user: str,
    controls: dict[str, bool],
) -> dict:
    if os.geteuid() != 0:
        raise InstallError("installation requires root")
    report = preflight(excel_app, openssl, automation_user)
    if report["ready"] is not True:
        raise InstallError("preflight did not pass")
    if any(controls.get(name) is not True for name in CONTROLS):
        raise InstallError("all isolation controls require explicit confirmation")
    if source_runner.is_symlink() or not source_runner.is_file():
        raise InstallError("maintained runner source is unavailable")
    _protect_directory(INSTALL_ROOT)
    _protect_directory(CONFIG_ROOT)
    implementation = INSTALL_ROOT / IMPLEMENTATION_NAME
    wrapper = INSTALL_ROOT / WRAPPER_NAME
    public_key = CONFIG_ROOT / PUBLIC_KEY_NAME
    private_key = CONFIG_ROOT / PRIVATE_KEY_NAME
    config_path = CONFIG_ROOT / CONFIG_NAME
    attestation_path = INSTALL_ROOT / ATTESTATION_NAME
    _atomic_write(implementation, source_runner.read_bytes(), 0o755)
    wrapper_text = (
        "#!/bin/sh\n"
        f"exec /usr/bin/sudo -n {str(implementation)!r} \"$@\"\n"
    ).encode("utf-8")
    _atomic_write(wrapper, wrapper_text, 0o755)
    if not private_key.exists() and not public_key.exists():
        _generate_keys(openssl, private_key, public_key)
    elif not private_key.exists() or not public_key.exists():
        raise InstallError("refusing a partial receipt-key installation")
    config = {
        "schema_version": "excel-runner-config/v1",
        "automation_user": automation_user,
        "excel_app": str(excel_app),
        "openssl_binary": str(openssl),
        "receipt_private_key": str(private_key),
        "receipt_public_key": str(public_key),
        "receipt_public_key_sha256": sha256_file(public_key),
        "runner_implementation": str(implementation),
        "runner_implementation_sha256": sha256_file(implementation),
        "network_sandbox": str(NETWORK_SANDBOX),
        "network_sandbox_profile_sha256":
            NETWORK_SANDBOX_PROFILE_SHA256,
        "pf_anchor": PF_ANCHOR,
        "pf_rules_sha256": PF_RULES_SHA256,
        "isolation_controls": controls,
        "timeout_seconds": 300,
    }
    _atomic_write(config_path, canonical_json(config), 0o644)
    sudoers = _sudoers_policy(implementation, automation_user)
    _atomic_write(SUDOERS_PATH, sudoers, 0o440)
    validation = subprocess.run(
        ["/usr/sbin/visudo", "-cf", str(SUDOERS_PATH)],
        check=False,
        capture_output=True,
        text=True,
    )
    if validation.returncode != 0:
        SUDOERS_PATH.unlink(missing_ok=True)
        raise InstallError("generated sudoers policy did not validate")
    attestation = {
        "schema_version": "excel-isolation-attestation/v1",
        **controls,
        "automation_user": automation_user,
        "sandbox_runner": str(wrapper),
        "sandbox_runner_sha256": sha256_file(wrapper),
        "runner_implementation": str(implementation),
        "runner_implementation_sha256": sha256_file(implementation),
        "receipt_public_key": str(public_key),
        "receipt_public_key_sha256": sha256_file(public_key),
        "network_sandbox": str(NETWORK_SANDBOX),
        "network_sandbox_profile_sha256":
            NETWORK_SANDBOX_PROFILE_SHA256,
        "pf_anchor": PF_ANCHOR,
        "pf_rules_sha256": PF_RULES_SHA256,
    }
    _atomic_write(attestation_path, canonical_json(attestation), 0o644)
    return {
        "status": "installed",
        "sandbox_runner": str(wrapper),
        "isolation_attestation": str(attestation_path),
        "receipt_public_key": str(public_key),
        "receipt_private_key": str(private_key),
    }


def check(
    excel_app: Path,
    openssl: Path,
    automation_user: str,
) -> dict:
    implementation = INSTALL_ROOT / IMPLEMENTATION_NAME
    wrapper = INSTALL_ROOT / WRAPPER_NAME
    public_key = CONFIG_ROOT / PUBLIC_KEY_NAME
    private_key = CONFIG_ROOT / PRIVATE_KEY_NAME
    config_path = CONFIG_ROOT / CONFIG_NAME
    attestation_path = INSTALL_ROOT / ATTESTATION_NAME
    paths = (
        implementation,
        wrapper,
        public_key,
        private_key,
        config_path,
        attestation_path,
        SUDOERS_PATH,
    )
    errors: list[str] = []
    if os.geteuid() != 0:
        errors.append("effective_uid_not_root")
    for path in paths:
        try:
            metadata = path.lstat()
        except OSError:
            errors.append(f"missing:{path}")
            continue
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            errors.append(f"unsafe_type:{path}")
        if metadata.st_uid != 0 or metadata.st_mode & 0o022:
            errors.append(f"unsafe_owner_or_mode:{path}")
    if private_key.exists() and stat.S_IMODE(private_key.stat().st_mode) != 0o600:
        errors.append("private_key_mode")
    if SUDOERS_PATH.exists() and stat.S_IMODE(
        SUDOERS_PATH.stat().st_mode
    ) != 0o440:
        errors.append("sudoers_mode")
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        attestation = json.loads(attestation_path.read_text(encoding="utf-8"))
        bindings = {
            implementation: (
                config.get("runner_implementation_sha256"),
                attestation.get("runner_implementation_sha256"),
            ),
            wrapper: (attestation.get("sandbox_runner_sha256"),),
            public_key: (
                config.get("receipt_public_key_sha256"),
                attestation.get("receipt_public_key_sha256"),
            ),
        }
        for path, claims in bindings.items():
            actual = sha256_file(path)
            if any(claim != actual for claim in claims):
                errors.append(f"hash_binding:{path}")
        if any(attestation.get(name) is not True for name in CONTROLS):
            errors.append("isolation_controls")
        if (
            config.get("network_sandbox") != str(NETWORK_SANDBOX)
            or attestation.get("network_sandbox")
            != str(NETWORK_SANDBOX)
            or config.get("network_sandbox_profile_sha256")
            != NETWORK_SANDBOX_PROFILE_SHA256
            or attestation.get("network_sandbox_profile_sha256")
            != NETWORK_SANDBOX_PROFILE_SHA256
        ):
            errors.append("network_sandbox_binding")
        if (
            config.get("pf_anchor") != PF_ANCHOR
            or attestation.get("pf_anchor") != PF_ANCHOR
            or config.get("pf_rules_sha256") != PF_RULES_SHA256
            or attestation.get("pf_rules_sha256") != PF_RULES_SHA256
        ):
            errors.append("pf_isolation_binding")
        if (
            config.get("excel_app") != str(excel_app)
            or config.get("openssl_binary") != str(openssl)
            or config.get("automation_user") != automation_user
            or attestation.get("automation_user") != automation_user
        ):
            errors.append("configured_paths")
        if (
            SUDOERS_PATH.is_file()
            and SUDOERS_PATH.read_bytes()
            != _sudoers_policy(implementation, automation_user)
        ):
            errors.append("sudoers_binding")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors.append(f"metadata:{exc}")
    live = preflight(excel_app, openssl, automation_user)
    for name, value in live.items():
        if name not in {"ready", "openssl_detail"} and value is not True:
            errors.append(f"live_preflight:{name}")
    if SUDOERS_PATH.is_file():
        validation = subprocess.run(
            ["/usr/sbin/visudo", "-cf", str(SUDOERS_PATH)],
            check=False,
            capture_output=True,
            text=True,
        )
        if validation.returncode != 0:
            errors.append("sudoers_validation")
    return {"ready": not errors, "errors": errors}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "preflight", "install", "check"):
        command = commands.add_parser(name)
        command.add_argument(
            "--source-runner",
            type=Path,
            default=Path(__file__).with_name("excel_runner.py"),
        )
        command.add_argument(
            "--excel-app",
            type=Path,
            default=Path("/Applications/Microsoft Excel.app"),
        )
        command.add_argument(
            "--openssl",
            type=Path,
            default=Path("/opt/local/bin/openssl"),
        )
        command.add_argument("--automation-user", required=True)
        if name == "install":
            command.add_argument("--apply", action="store_true")
            for control in CONTROLS:
                command.add_argument(
                    "--confirm-" + control.replace("_", "-"),
                    action="store_true",
                )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "plan":
        print(
            json.dumps(
                {
                    "commands": planned_commands(
                        args.source_runner,
                        args.excel_app,
                        args.openssl,
                        args.automation_user,
                    ),
                    "writes_system_paths": False,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "preflight":
        report = preflight(args.excel_app, args.openssl, args.automation_user)
        print(json.dumps(report, sort_keys=True))
        return 0 if report["ready"] else 2
    if args.command == "check":
        report = check(
            args.excel_app,
            args.openssl,
            args.automation_user,
        )
        print(json.dumps(report, sort_keys=True))
        return 0 if report["ready"] else 2
    if not args.apply:
        raise InstallError("install is dry-run unless --apply is supplied")
    controls = {
        name: bool(getattr(args, "confirm_" + name))
        for name in CONTROLS
    }
    print(
        json.dumps(
            install(
                source_runner=args.source_runner,
                excel_app=args.excel_app,
                openssl=args.openssl,
                automation_user=args.automation_user,
                controls=controls,
            ),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except InstallError as error:
        print(f"excel runner installer: {error}", file=os.sys.stderr)
        raise SystemExit(1)

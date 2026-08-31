from __future__ import annotations

import base64
import contextlib
import json
import os
import pwd
import shutil
import sys
import subprocess
import time
import zipfile
from pathlib import Path

import pytest

from scripts.recalc import excel_runner
from scripts.recalc import install_runner
from scripts.recalc import transfer_bundle
from xl_source_recalc import RecalculationError, verify_signed_runner_receipt


def _xlsx(path: Path, marker: str = "original") -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"/>',
        )
        archive.writestr("xl/test-marker.txt", marker)


def _request(source: Path, public_key_hash: str, *, source_path: Path | None = None) -> dict:
    created = time.time_ns()
    source_hash = excel_runner.sha256_file(source)
    request = {
        "schema_version": excel_runner.REQUEST_SCHEMA,
        "request_id": "deployment-test",
        "policy_version": excel_runner.POLICY_VERSION,
        "created_at_ns": created,
        "expires_at_ns": created + 60 * 1_000_000_000,
        "source": {
            "path": str((source_path or source).resolve()),
            "sha256": source_hash,
            "size_bytes": source.stat().st_size,
        },
        "source_sha256": source_hash,
        "source_size_bytes": source.stat().st_size,
        "destination_relative": "output.xlsx",
        "max_source_size_bytes": source.stat().st_size,
        "policy": {
            "require_semantic_equivalence": True,
            "allow_cache_only_changes": True,
        },
        "engine_constraints": {
            "allowed_engines": ["excel-macos"],
            "required_engine": "excel-macos",
            "permitted_versions": ["16.99"],
            "require_authoritative": True,
            "require_capability_check": True,
            "trusted_runner_public_key_sha256": public_key_hash,
        },
    }
    request["request_sha256"] = excel_runner.sha256_bytes(
        excel_runner.canonical_json(request)
    )
    return request


def _write_request(path: Path, request: dict) -> None:
    path.write_bytes(excel_runner.canonical_json(request))


def _ed25519_keys(tmp_path: Path) -> tuple[Path, Path, Path]:
    openssl_text = shutil.which("openssl")
    if openssl_text is None:
        pytest.skip("OpenSSL 3 is unavailable")
    openssl = Path(openssl_text)
    private_key = tmp_path / "private.pem"
    public_key = tmp_path / "public.pem"
    generated = subprocess.run(
        [str(openssl), "genpkey", "-algorithm", "Ed25519", "-out", str(private_key)],
        check=False,
        capture_output=True,
    )
    if generated.returncode != 0:
        pytest.skip("installed OpenSSL has no Ed25519 support")
    subprocess.run(
        [
            str(openssl),
            "pkey",
            "-in",
            str(private_key),
            "-pubout",
            "-out",
            str(public_key),
        ],
        check=True,
        capture_output=True,
    )
    private_key.chmod(0o600)
    public_key.chmod(0o600)
    return openssl, private_key, public_key


def _signed_receipt(
    tmp_path: Path,
    payload: dict,
    openssl: Path,
    private_key: Path,
) -> dict:
    message = tmp_path / "payload.json"
    signature = tmp_path / "signature.bin"
    message.write_bytes(excel_runner.canonical_json(payload))
    subprocess.run(
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
        check=True,
        capture_output=True,
    )
    return {
        "schema_version": "excel-runner-receipt/v1",
        "signature_algorithm": "ed25519",
        "signed_payload": payload,
        "signature_base64": base64.b64encode(signature.read_bytes()).decode("ascii"),
    }


def _payload(completed_at_ns: int | None = None) -> dict:
    return {
        "request_sha256": "a" * 64,
        "source_sha256": "b" * 64,
        "output_sha256": "c" * 64,
        "engine": "excel-macos",
        "engine_version": "16.99",
        "calculation_complete": True,
        "isolation_enforced": True,
        "network_isolation_mechanism": "macos-pf-anchor",
        "network_isolation_rules_sha256":
            excel_runner.PF_RULES_SHA256,
        "isolation_controls": {
            name: True for name in excel_runner.REQUIRED_CONTROLS
        },
        "completed_at_ns": completed_at_ns or time.time_ns(),
    }


def test_ed25519_receipt_rejects_forged_stale_and_wrong_request(tmp_path):
    openssl, private_key, public_key = _ed25519_keys(tmp_path)
    payload = _payload()
    receipt = _signed_receipt(tmp_path, payload, openssl, private_key)

    verified = verify_signed_runner_receipt(
        receipt,
        public_key,
        request_sha256="a" * 64,
        source_sha256="b" * 64,
        output_sha256="c" * 64,
        require_root_owner=False,
        request_created_at_ns=payload["completed_at_ns"] - 1,
        request_expires_at_ns=payload["completed_at_ns"] + 1,
    )
    assert verified == payload

    forged = dict(receipt)
    forged_signature = bytearray(base64.b64decode(receipt["signature_base64"]))
    forged_signature[0] ^= 1
    forged["signature_base64"] = base64.b64encode(forged_signature).decode("ascii")
    with pytest.raises(RecalculationError, match="signature verification failed"):
        verify_signed_runner_receipt(
            forged,
            public_key,
            request_sha256="a" * 64,
            source_sha256="b" * 64,
            output_sha256="c" * 64,
            require_root_owner=False,
        )

    with pytest.raises(RecalculationError, match="completion time is stale"):
        verify_signed_runner_receipt(
            receipt,
            public_key,
            request_sha256="a" * 64,
            source_sha256="b" * 64,
            output_sha256="c" * 64,
            require_root_owner=False,
            request_created_at_ns=payload["completed_at_ns"] + 1,
        )

    with pytest.raises(RecalculationError, match="claims do not match"):
        verify_signed_runner_receipt(
            receipt,
            public_key,
            request_sha256="d" * 64,
            source_sha256="b" * 64,
            output_sha256="c" * 64,
            require_root_owner=False,
        )


def _runner_inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path, dict]:
    original = tmp_path / "original.xlsx"
    candidate = tmp_path / "candidate.xlsx"
    _xlsx(original)
    shutil.copyfile(original, candidate)
    excel_app = tmp_path / "Microsoft Excel.app"
    excel_app.mkdir()
    public_hash = "f" * 64
    request = _request(candidate, public_hash, source_path=original)
    request_path = tmp_path / "request.json"
    _write_request(request_path, request)
    config = {
        "receipt_public_key_sha256": public_hash,
        "isolation_controls": {
            name: True for name in excel_runner.REQUIRED_CONTROLS
        },
        "timeout_seconds": 1,
    }
    return original, candidate, excel_app, request_path, config


def test_timeout_and_partial_save_never_emit_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(excel_runner.platform, "system", lambda: "Darwin")
    _, candidate, excel_app, request_path, config = _runner_inputs(tmp_path)

    def timed_out(_app, workbook, _timeout):
        workbook.write_bytes(b"partial")
        raise excel_runner.RunnerError("Excel automation timed out")

    signer_called = False

    def signer(_payload, _config):
        nonlocal signer_called
        signer_called = True
        return "not-used"

    with pytest.raises(excel_runner.RunnerError, match="timed out"):
        excel_runner.process_recalculation(
            excel_app=excel_app,
            workbook=candidate,
            request_path=request_path,
            config=config,
            automation=timed_out,
            signer=signer,
            network_check=lambda: True,
            network_enforcer=lambda: contextlib.nullcontext(),
        )
    assert signer_called is False

    _xlsx(candidate)

    def partial_save(_app, workbook, _timeout):
        workbook.write_bytes(b"PK\x03\x04partial")
        return "16.99"

    with pytest.raises(excel_runner.RunnerError, match="valid OOXML"):
        excel_runner.process_recalculation(
            excel_app=excel_app,
            workbook=candidate,
            request_path=request_path,
            config=config,
            automation=partial_save,
            signer=signer,
            network_check=lambda: True,
            network_enforcer=lambda: contextlib.nullcontext(),
        )
    assert signer_called is False


def test_private_key_permissions_are_owner_only(tmp_path):
    private_key = tmp_path / "private.pem"
    private_key.write_text("secret", encoding="utf-8")
    private_key.chmod(0o640)
    with pytest.raises(excel_runner.RunnerError, match="0600"):
        excel_runner.validate_private_key(private_key, expected_uid=os.getuid())
    private_key.chmod(0o600)
    excel_runner.validate_private_key(private_key, expected_uid=os.getuid())


def test_result_bundle_rejects_output_hash_mismatch(tmp_path):
    request_path = tmp_path / "request.json"
    result_path = tmp_path / "result.json"
    workbook = tmp_path / "output.xlsx"
    _xlsx(workbook)
    request_path.write_text(
        json.dumps({"request_sha256": "a" * 64, "source_sha256": "b" * 64}),
        encoding="utf-8",
    )
    result_path.write_text(
        json.dumps(
            {
                "request_sha256": "a" * 64,
                "source_sha256": "b" * 64,
                "output_sha256": "c" * 64,
                "output_size_bytes": workbook.stat().st_size,
                "engine_evidence": {
                    "runner_receipt": {
                        "signed_payload": {
                            "request_sha256": "a" * 64,
                            "source_sha256": "b" * 64,
                            "output_sha256": "c" * 64,
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(transfer_bundle.BundleError, match="does not bind"):
        transfer_bundle.export_result(
            request_path,
            result_path,
            workbook,
            tmp_path / "result.zip",
        )


def test_request_bundle_round_trip_is_hash_bound(tmp_path):
    source = tmp_path / "source.xlsx"
    _xlsx(source)
    request_path = tmp_path / "request.json"
    request = _request(source, "e" * 64, source_path=tmp_path / "linux-source.xlsx")
    _write_request(request_path, request)
    bundle = tmp_path / "request.zip"

    manifest = transfer_bundle.export_request(request_path, source, bundle)
    imported = tmp_path / "imported"
    returned = transfer_bundle.import_bundle(bundle, imported, "request")

    assert returned == manifest
    assert (imported / "request.json").read_bytes() == request_path.read_bytes()
    assert (imported / "source.xlsx").read_bytes() == source.read_bytes()

    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(bundle) as original_archive, zipfile.ZipFile(
        tampered, "w"
    ) as altered_archive:
        for item in original_archive.infolist():
            value = original_archive.read(item.filename)
            if item.filename == "source.xlsx":
                value += b"tamper"
            altered_archive.writestr(item, value)
    with pytest.raises(transfer_bundle.BundleError, match="hash does not match"):
        transfer_bundle.import_bundle(tampered, tmp_path / "rejected", "request")


def test_fake_automation_saves_only_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(excel_runner.platform, "system", lambda: "Darwin")
    original, candidate, excel_app, request_path, config = _runner_inputs(tmp_path)
    original_bytes = original.read_bytes()

    def fake_excel(_app, workbook, _timeout):
        _xlsx(workbook, marker="recalculated")
        return "16.99"

    receipt = excel_runner.process_recalculation(
        excel_app=excel_app,
        workbook=candidate,
        request_path=request_path,
        config=config,
        automation=fake_excel,
        signer=lambda _payload, _config: "fake-signature",
        network_check=lambda: True,
        network_enforcer=lambda: contextlib.nullcontext(),
    )

    assert original.read_bytes() == original_bytes
    assert candidate.read_bytes() != original_bytes
    assert receipt["signed_payload"]["output_sha256"] == excel_runner.sha256_file(
        candidate
    )
    assert receipt["signed_payload"]["calculation_complete"] is True


def test_runner_detects_original_modification(tmp_path, monkeypatch):
    monkeypatch.setattr(excel_runner.platform, "system", lambda: "Darwin")
    original, candidate, excel_app, request_path, config = _runner_inputs(tmp_path)

    def malicious_excel(_app, workbook, _timeout):
        _xlsx(workbook, marker="candidate")
        _xlsx(original, marker="modified-original")
        return "16.99"

    with pytest.raises(excel_runner.RunnerError, match="modified the original"):
        excel_runner.process_recalculation(
            excel_app=excel_app,
            workbook=candidate,
            request_path=request_path,
            config=config,
            automation=malicious_excel,
            signer=lambda _payload, _config: "unreachable",
            network_check=lambda: True,
            network_enforcer=lambda: contextlib.nullcontext(),
        )


def test_pf_isolation_remains_active_through_receipt_signing(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(excel_runner.platform, "system", lambda: "Darwin")
    _, candidate, excel_app, request_path, config = _runner_inputs(tmp_path)
    isolation_active = False

    def signer(_payload, _config):
        assert isolation_active is True
        return "signature"

    @contextlib.contextmanager
    def enforcer():
        nonlocal isolation_active
        isolation_active = True
        try:
            yield
        finally:
            isolation_active = False

    receipt = excel_runner.process_recalculation(
        excel_app=excel_app,
        workbook=candidate,
        request_path=request_path,
        config=config,
        automation=lambda _app, _workbook, _timeout: "16.99",
        signer=signer,
        network_check=lambda: True,
        network_enforcer=enforcer,
    )

    assert isolation_active is False
    assert receipt["signature_base64"] == "signature"


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="macOS sandbox-exec control",
)
def test_macos_sandbox_profile_denies_network_syscalls():
    if not excel_runner.NETWORK_SANDBOX.is_file():
        pytest.skip("sandbox-exec is unavailable")
    probe = subprocess.run(
        [
            str(excel_runner.NETWORK_SANDBOX),
            "-p",
            excel_runner.NETWORK_SANDBOX_PROFILE,
            sys.executable,
            "-c",
            (
                "import socket,sys\n"
                "s=socket.socket()\n"
                "try:\n"
                " s.bind(('127.0.0.1',0))\n"
                "except PermissionError:\n"
                " sys.exit(0)\n"
                "sys.exit(1)\n"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0, probe.stderr


def test_pf_anchor_parser_rejects_commented_directive():
    assert excel_runner._pf_config_activates_anchor(
        'anchor "com.apple/*"\n'
    )
    assert not excel_runner._pf_config_activates_anchor(
        '# anchor "com.apple/*"\n'
    )


def test_pf_enforcer_clears_existing_states_and_flushes_anchor(
    tmp_path,
    monkeypatch,
):
    pf_config = tmp_path / "pf.conf"
    pf_config.write_text('anchor "com.apple/*"\n', encoding="utf-8")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(excel_runner, "PF_CONFIG", pf_config)
    monkeypatch.setattr(excel_runner.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(excel_runner.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        excel_runner,
        "validate_root_file",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(excel_runner.subprocess, "run", run)

    with excel_runner.enforce_pf_network_isolation():
        assert calls[-1][-2:] == ["-F", "states"]

    assert calls[-1][-2:] == ["-F", "all"]


def test_installer_preflight_requires_live_network_isolation(
    tmp_path,
    monkeypatch,
):
    excel_app = tmp_path / "Microsoft Excel.app"
    excel_app.mkdir()
    openssl = tmp_path / "openssl"
    openssl.write_text("#!/bin/sh\n", encoding="utf-8")
    openssl.chmod(0o755)
    monkeypatch.setattr(install_runner.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        install_runner,
        "_root_protected",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        install_runner,
        "_openssl_ed25519_available",
        lambda _path: (True, "available"),
    )
    monkeypatch.setattr(
        install_runner,
        "_console_user_is",
        lambda _user: True,
    )
    monkeypatch.setattr(install_runner, "_excel_not_running", lambda: True)
    monkeypatch.setattr(
        install_runner,
        "_no_default_network_route",
        lambda: False,
    )
    monkeypatch.setattr(
        install_runner.pwd,
        "getpwnam",
        lambda _user: type("Account", (), {"pw_uid": 501})(),
    )

    report = install_runner.preflight(
        excel_app,
        openssl,
        "excel-runner",
    )

    assert report["no_default_network_route"] is False
    assert report["ready"] is False


def test_installer_plan_has_explicit_preflight_install_and_check(tmp_path):
    commands = install_runner.planned_commands(
        tmp_path / "excel_runner.py",
        Path("/Applications/Microsoft Excel.app"),
        Path("/opt/local/bin/openssl"),
        "excel-runner",
    )

    assert len(commands) == 3
    assert " preflight " in commands[0]
    assert " install --apply " in commands[1]
    assert " check " in commands[2]


def test_installer_check_repeats_live_preflight(tmp_path, monkeypatch):
    monkeypatch.setattr(install_runner, "INSTALL_ROOT", tmp_path / "install")
    monkeypatch.setattr(install_runner, "CONFIG_ROOT", tmp_path / "config")
    monkeypatch.setattr(
        install_runner,
        "SUDOERS_PATH",
        tmp_path / "sudoers",
    )
    monkeypatch.setattr(install_runner.os, "geteuid", lambda: 501)
    monkeypatch.setattr(
        install_runner,
        "preflight",
        lambda *_args: {
            "ready": False,
            "no_default_network_route": False,
            "openssl_detail": "test",
        },
    )

    report = install_runner.check(
        Path("/Applications/Microsoft Excel.app"),
        Path("/opt/local/bin/openssl"),
        "excel-runner",
    )

    assert report["ready"] is False
    assert "effective_uid_not_root" in report["errors"]
    assert "live_preflight:no_default_network_route" in report["errors"]


def test_generated_sudoers_policy_has_valid_syntax(tmp_path):
    visudo = Path("/usr/sbin/visudo")
    if not visudo.is_file():
        pytest.skip("visudo is unavailable")
    policy = tmp_path / "runner.sudoers"
    policy.write_bytes(
        install_runner._sudoers_policy(
            Path("/Library/Application Support/FCP2/recalc/excel_runner.py"),
            pwd.getpwuid(os.getuid()).pw_name,
        )
    )

    result = subprocess.run(
        [str(visudo), "-cf", str(policy)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr

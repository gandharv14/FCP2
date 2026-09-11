"""Production failures must not be hidden by pass flags or compatibility paths."""
import importlib.util
import io
import json
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

from xl_preflight import classify, preflight
from xl_artifact_paths import resolve_workbook_artifact
import xl_variable_source_audit as audit


def proof(**changes):
    result = dict(passed=True, outputs=dict(match=3, mismatch=0, unresolved=0,
                  unverifiable=0), seeded_inside_output_cone_count=0,
                  oracle_fallback_inside_output_cone_count=0,
                  ordered_output_cells=["Model!A1", "Model!A2", "Model!A3"])
    result.update(changes)
    return result


@pytest.mark.parametrize("changes,expected", [
    ({"seeded_inside_output_cone_count": 1}, "frontier_unsafe"),
    ({"oracle_fallback_inside_output_cone_count": 1}, "frontier_unsafe"),
    ({"outputs": {"match": 2, "mismatch": 1}}, "cached_mismatch"),
    ({"outputs": {}}, "unverified"),
    ({"outputs": {"match": True}}, "unverified"),
    ({"counts": {"outputs": {"eligible": 4, "checked": 3}}}, "cached_mismatch"),
    ({"blocking_reasons": ["proof_missing"]}, "cached_mismatch"),
    ({"disposition": "quarantine"}, "cached_mismatch"),
    ({"failures": [{"cell": "Model!A1", "recomputed": "error"}]}, "cached_mismatch"),
    ({"outputs": {"match": 3}}, "cached_mismatch"),
    ({"ordered_output_cells": []}, "cached_mismatch"),
    ({"ordered_output_cells": ["Model!A1"] * 3}, "cached_mismatch"),
    ({"ordered_output_cells": ["Model!A1", None, {}]}, "cached_mismatch"),
    ({"iterative_blocks": [{"converged": True, "unique": False}]}, "unsafe_circular"),
])
def test_contradictory_pass_is_rejected(changes, expected):
    assert classify(proof(**changes), True)[0] == expected


def test_irrelevant_cycle_does_not_block_valid_outputs():
    assert classify(proof(iterative_blocks=[{"output_relevant": False,
                    "converged": False}]), True)[0] == "healthy"


@pytest.mark.parametrize("changes", [{"iterative_blocks": [None]}, {"counts": []},
                                     {"failures": ["bad"]}, {"counts": {"outputs": "bad"}}])
def test_malformed_proof_fails_closed(changes):
    assert classify(proof(**changes), True)[0] == "unverified"


def test_failed_pinned_cli_replaces_stale_healthy_verdict(tmp_path):
    from xl_preflight import main
    (tmp_path / "0001.json").write_text('{"healthy": true}')
    with patch("xl_release_publication.resolve_build_context", side_effect=ValueError("stale")):
        assert main(["0001", "--out", str(tmp_path), "--source-generation-id", "old",
                     "--segmentation-generation-id", "old"]) == 1
    assert json.loads((tmp_path / "0001.json").read_text())["healthy"] is False


def test_preflight_pins_inactive_generation_and_custom_roots(tmp_path):
    source = tmp_path / "candidate.xlsx"
    source.write_bytes(b"source")
    inactive = tmp_path / "inactive"
    inactive.mkdir()
    (inactive / "segments.json").write_text(json.dumps({"verification": proof()}))
    context = dict(source_path=str(source), segmentation_dir=str(inactive),
                   bindings={"source_generation_id": "source-id"})
    with patch("xl_release_publication.resolve_build_context", return_value=context) as resolve:
        verdict = preflight("0001", tmp_path / "missing", tmp_path / "segments",
                            tmp_path / "reports", source_generation_root=tmp_path / "sources",
                            source_generation_id="source-id", segmentation_generation_id="seg-id")
    assert verdict["healthy"]
    assert verdict["segments"] == str(inactive / "segments.json")
    assert resolve.call_args.kwargs["source_root"] == tmp_path / "sources" / "0001"
    assert not (tmp_path / "segments" / "0001" / "current.json").exists()


def test_preflight_does_not_fallback_when_pinned_generation_fails(tmp_path):
    with patch("xl_release_publication.resolve_build_context", side_effect=ValueError("stale")):
        with pytest.raises(ValueError, match="stale"):
            preflight("0001", tmp_path, tmp_path, tmp_path,
                      source_generation_id="old", segmentation_generation_id="old")


def test_uppercase_extension_and_ambiguous_case(tmp_path):
    source = tmp_path / "0001.XLSX"
    source.touch()
    assert resolve_workbook_artifact(tmp_path, "0001") == source
    # Most developer Macs are case insensitive; use distinct case variants
    # only where the filesystem actually supports them.
    lower = tmp_path / "0001.xlsx"
    if not lower.exists():
        lower.touch()
        with pytest.raises(ValueError, match="ambiguous"):
            resolve_workbook_artifact(tmp_path, "0001")


@pytest.fixture
def disclosure():
    path = Path(__file__).resolve().parents[1] / ".cursor/skills/task-disclosure/scripts/disclose.py"
    spec = importlib.util.spec_from_file_location("yield_disclosure", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("sheet", ["5yrDetail", "A B", "Director's plan", "A!B", "Model"])
def test_reference_roundtrip(disclosure, sheet):
    reference = sheet + "!$B$27:$C$27"
    rendered = disclosure.pretty(reference)
    assert disclosure.refs_in("=" + rendered, "wrong") == [sheet + "!B27", sheet + "!C27"]


def test_digit_leading_unquoted_reference(disclosure):
    assert disclosure.refs_in("=5yrDetail!B27", "wrong") == ["5yrDetail!B27"]


@pytest.mark.parametrize("reference", ["5yrDetail!B27", "'Director''s plan'!$B$27:$C$27", "'A!B'!B27"])
def test_independent_reference_reader(disclosure, reference):
    assert disclosure.independent_references("=" + reference, "wrong") == set(disclosure.refs_in("=" + reference, "wrong"))


@pytest.mark.parametrize("answer", [0, 1, -1, 6, 0.5])
def test_low_digit_answer_is_not_exempt(disclosure, tmp_path, answer):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/answer_key.json").write_text(json.dumps({"targets": {"S!B1": answer}}))
    assert disclosure.audit_text(f"The target result is {answer}.", tmp_path)


def test_transport_retries_are_bounded_and_separate_from_content():
    error = lambda: urllib.error.HTTPError("https://example.test", 429, "busy",
                                           {"Retry-After": "2"}, io.BytesIO())
    with patch.object(audit.urllib.request, "urlopen", side_effect=[error() for _ in range(4)]) as call:
        with patch.object(audit.time, "sleep") as sleep:
            with pytest.raises(urllib.error.HTTPError):
                audit._call_model("https://example.test", "key", "model", "project", [])
    assert call.call_count == 4
    assert [args.args[0] for args in sleep.call_args_list] == [2, 2, 2]


def test_bad_request_is_not_retried():
    error = urllib.error.HTTPError("https://example.test", 400, "bad request", {}, io.BytesIO())
    with patch.object(audit.urllib.request, "urlopen", side_effect=error) as call:
        with pytest.raises(urllib.error.HTTPError):
            audit._call_model("https://example.test", "key", "model", "project", [])
    assert call.call_count == 1


def test_macro_container_is_observed_without_becoming_eligible(tmp_path):
    from openpyxl import Workbook
    from xl_source_health import inspect_workbook, validate_report
    import zipfile
    source = tmp_path / "0001.xlsm"
    workbook = Workbook()
    workbook.save(source)
    workbook.close()
    with zipfile.ZipFile(source, "a") as archive:
        archive.writestr("xl/vbaProject.bin", b"opaque macro project")
    report = inspect_workbook(source)
    validate_report(report, source)
    assert report["source_sha256"]
    assert "macros_present" in report["reason_codes"]
    assert report["route"] == "unsupported"


def test_restriction_cone_reads_original_policy_for_signed_recalculation(tmp_path):
    import json
    from xl_seg.restriction_cone import _restriction_policy_record, RestrictionConeError
    events = [{"function": "OFFSET", "location": "Model!A1"}]
    profile = {"schema_version": "source-restriction-profile/v3"}
    (tmp_path / "request.json").write_text(json.dumps({"source_sha256": "original"}))
    inventory = {"workbooks": [{"sha256": "original", "route": "restricted_recalc_pass",
        "health_report": {"restriction_events": events, "restriction_profile": profile}}]}
    (tmp_path / "inventory.json").write_text(json.dumps(inventory))
    result = {"schema_version": "source-recalc-result/v2", "source_sha256": "original",
              "output_sha256": "candidate"}
    assert _restriction_policy_record(tmp_path, result, {"source_sha256": "candidate"}) == {
        "restriction_events": events, "profile": profile}
    with pytest.raises(RestrictionConeError, match="original and candidate"):
        _restriction_policy_record(tmp_path, result, {"source_sha256": "altered"})
    inventory["workbooks"][0]["sha256"] = "candidate"
    (tmp_path / "inventory.json").write_text(json.dumps(inventory))
    with pytest.raises(RestrictionConeError, match="original and candidate"):
        _restriction_policy_record(tmp_path, result, {"source_sha256": "candidate"})


def test_disclosure_direct_entrypoint_bootstraps_pipeline_imports(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    script = Path(__file__).resolve().parents[1] / ".cursor/skills/task-disclosure/scripts/disclose.py"
    result = subprocess.run([sys.executable, "-I", "-c",
        "import runpy,sys; runpy.run_path(sys.argv[1]); import xl_release_publication",
        str(script)], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_maskability_uses_canonical_allowlist_and_binds_evidence(tmp_path, capsys):
    import hashlib
    import openpyxl
    import maskability
    source = tmp_path / 'source'; source.mkdir()
    run = tmp_path / 'runs' / '0001-variable-sources'; run.mkdir(parents=True)
    book = openpyxl.Workbook(); book.active.title = 'Model'; book.active['A1'] = 47
    golden = source / '0001.xlsx'; book.save(golden); book.close()
    spec = {'variables': [{'id': 'v', 'value': 47, 'unit': 'value',
                          'workbook': {'cells': ['Model!A1']}}]}
    (run / 'normalized.json').write_text(json.dumps(spec))
    args = ['0001', '--source', str(source), '--runs-root', str(run.parent)]
    assert maskability.main(args) == 0
    report = json.loads((run / 'maskability_report.json').read_text())
    assert report['allowlist_path'] == str(run / 'oracle-allowlist.json')
    assert report['allowlist_sha256'] is None
    assert report['source_sha256'] == hashlib.sha256(golden.read_bytes()).hexdigest()
    allowlist = run / 'oracle-allowlist.json'; allowlist.write_text('{"workbook_value_leaks": []}')
    assert maskability.main(args) == 0
    assert json.loads((run / 'maskability_report.json').read_text())['allowlist_sha256'] == hashlib.sha256(allowlist.read_bytes()).hexdigest()
    (run / 'leak_allowlist.json').write_text('{"entries": []}')
    with pytest.raises(SystemExit, match='explicit review'):
        maskability.main(args)
    assert json.loads((run / 'maskability_report.json').read_text())['valid'] is False

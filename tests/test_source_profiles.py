from __future__ import annotations

import json
import socket
import urllib.request
from copy import deepcopy
from pathlib import Path

import pytest

from mcp_env.build import (
    _canonical_hash,
    build,
    perturb,
    stable_id,
    validate_spec,
)
from mcp_env.server_assets import SERVER_PY, SIDECAR_DOCKERFILE
from mcp_env.validate import read_jsonl, validate


def legacy_spec():
    return {
        "environment_id": "legacy-fixture",
        "seed": 17,
        "distractors_per_variable": 8,
        "provenance_releases_per_variable": 2,
        "variables": [{
            "id": "legacy-rate",
            "name": "Reference rate",
            "aliases": ["Base rate"],
            "value": 4.25,
            "unit": "percent",
            "entity": "Example asset",
            "period": "2024",
            "scenario": "Management case",
            "basis": "nominal prices",
            "status": "final",
            "question": "What reference rate does the model use?",
            "sources": [{
                "name": "Example Statistics",
                "url": "https://statistics.example/release",
                "role": "contextual",
                "kind": "official-statistics",
            }],
            "workbook": {"cells": ["Inputs!B2"], "value": 0.0425},
        }],
    }


def profiled_spec():
    spec = legacy_spec()
    spec["environment_id"] = "profile-fixture"
    source = spec["variables"][0]["sources"][0]
    source.update({
        "id": "example-rates",
        "profile_id": "example-statistics",
        "dataset_key": "reference-rates",
    })
    spec["source_profiles"] = [{
        "id": "example-statistics",
        "canonical_url": "https://statistics.example/release",
        "access": {"status": "public"},
        "capture": {
            "http_status": 200,
            "content_type": "text/html",
            "content_sha256": "a" * 64,
        },
        "review_status": "approved",
        "source_description": "Official release collection for benchmark rates.",
        "datasets": [{
            "key": "reference-rates",
            "name": "Benchmark Rates Bulletin",
            "description": "Versioned benchmark observations.",
            "aliases": ["Rates bulletin"],
            "field_labels": {
                "metric": "Series",
                "period": "Reference period",
                "value": "Observation",
            },
            "document_kinds": ["statistical-bulletin", "release-archive"],
            "release_cadence": "monthly",
            "release_label_style": "Bulletin {date}",
        }],
        "excerpts": [{
            "text": "The bulletin uses a consistent reference-period convention.",
            "attribution": "Example Statistics",
            "locator": "Release overview",
            "source_url": "https://statistics.example/release",
        }],
    }]
    return spec


def reviewed_catalog_spec():
    spec = legacy_spec()
    spec["environment_id"] = "reviewed-profile-fixture"
    source = spec["variables"][0]["sources"][0]
    source.update({
        "id": "example-statistics",
        "profile_id": "example-statistics",
        "dataset_key": "monthly-indicators",
    })
    evidence = {
        "attributions": [{
            "attribution_id": "landing",
            "title": "Monthly indicators",
            "publisher": "Example Statistics",
            "url": "https://statistics.example/release",
            "accessed_at": "2026-08-15T22:00:00Z",
        }],
        "excerpts": [{
            "text": "The bulletin follows a scheduled monthly release.",
            "attribution_id": "landing",
        }],
    }
    profiles = [{
        "source_id": "example-statistics",
        "source_name": "Example Statistics",
        "canonical_url": "https://statistics.example/release",
        "status": "profiled",
        "skip_reason": None,
        "capture": {
            "attempted_at": "2026-08-15T22:00:00Z",
            "read_count": 1,
            "http_status": 200,
            "final_url": "https://statistics.example/release",
            "content_type": "text/html",
            "evidence_sha256": _canonical_hash(evidence),
        },
        "terminology": ["reference period", "benchmark series"],
        "dataset_names": ["Monthly indicators"],
        "field_conventions": ["period labels use YYYY-MM"],
        "document_types": ["statistical bulletin"],
        "release_cadence": "monthly calendar release",
        "evidence": evidence,
        "review": {
            "status": "accepted",
            "reviewer": "reviewer",
            "reviewed_at": "2026-08-15T22:10:00Z",
            "notes": None,
        },
    }]
    spec["source_profiles"] = {
        "schema_version": "1.0",
        "capture": {
            "created_at": "2026-08-15T22:00:00Z",
            "tool": "profile-mcp-sources",
            "agent_model": "gpt-5.6-sol-high",
            "public_read_limit_per_source": 3,
            "canonicalization": "v1",
            "inventory_sha256": None,
            "spec_sha256": None,
            "profiles_sha256": _canonical_hash(profiles),
        },
        "profiles": profiles,
    }
    return spec


def complete_bundle(spec, output):
    build(spec, output)
    (output / "server.py").write_text(SERVER_PY, encoding="utf-8")
    (output / "Dockerfile").write_text(SIDECAR_DOCKERFILE, encoding="utf-8")


def bundle_bytes(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_legacy_spec_keeps_original_ids_and_shape(tmp_path):
    spec = legacy_spec()
    output = tmp_path / "legacy"
    complete_bundle(spec, output)

    sources = json.loads((output / "runtime/sources.json").read_text())
    datasets = json.loads((output / "runtime/datasets.json").read_text())
    records = read_jsonl(output / "runtime/records.jsonl")

    assert sources == [{
        "description": (
            "Synthetic research collection shaped from Example Statistics metadata."
        ),
        "id": "statistics-example",
        "kind": "official-statistics",
        "name": "Example Statistics",
        "origin_url": "https://statistics.example/release",
        "role": "contextual",
    }]
    assert datasets == [{
        "description": (
            "Records vary by entity, metric, period, scenario, basis, unit, and "
            "status, and exist in multiple releases. Filter all relevant "
            "dimensions, then use only the release whose superseded_by is null."
        ),
        "dimensions": [
            "entity", "metric", "period", "scenario", "basis", "unit", "status"
        ],
        "id": stable_id("dataset", "legacy-fixture", "statistics-example"),
        "name": "Example Statistics versioned indicators",
        "source_id": "statistics-example",
    }]
    assert all("release_label" not in row for row in records)
    assert validate(output)["valid"] is True


def test_profile_shapes_only_approved_metadata(tmp_path):
    spec = profiled_spec()
    output = tmp_path / "profiled"
    complete_bundle(spec, output)
    report = validate(output)

    sources = json.loads((output / "runtime/sources.json").read_text())
    datasets = json.loads((output / "runtime/datasets.json").read_text())
    documents = read_jsonl(output / "runtime/documents.jsonl")
    records = read_jsonl(output / "runtime/records.jsonl")
    supported = next(row for row in records if row["value"] == 4.25)

    assert report["valid"] is True
    assert sources[0]["id"] == "example-rates"
    assert sources[0]["profile_id"] == "example-statistics"
    assert sources[0]["profile_excerpts"] == [{
        "text": "The bulletin uses a consistent reference-period convention.",
        "attribution": "Example Statistics",
        "locator": "Release overview",
        "source_id": "example-rates",
    }]
    assert sources[0]["origin_url"] == "https://statistics.example/release"
    assert datasets[0]["name"] == "Benchmark Rates Bulletin"
    assert datasets[0]["aliases"] == ["Rates bulletin"]
    assert datasets[0]["field_labels"]["metric"] == "Series"
    assert supported["release_label"] == "Bulletin 2019-12-18"
    assert all(document["url"].startswith("mock://") for document in documents)
    assert any(document["kind"] == "statistical-bulletin"
               for document in documents)
    assert any("Short attributed excerpt — Example Statistics"
               in document["content"] for document in documents)
    assert any("Series: Reference rate" in document["content"]
               for document in documents)


def test_reviewed_skill_catalog_shapes_profiled_output(tmp_path):
    spec = reviewed_catalog_spec()
    output = tmp_path / "reviewed"
    complete_bundle(spec, output)
    assert validate(output)["valid"] is True

    sources = json.loads((output / "runtime/sources.json").read_text())
    datasets = json.loads((output / "runtime/datasets.json").read_text())
    documents = read_jsonl(output / "runtime/documents.jsonl")

    assert sources[0]["profile_id"] == "example-statistics"
    assert "Terminology: reference period, benchmark series" in \
        sources[0]["description"]
    assert datasets[0]["name"] == "Monthly indicators"
    assert datasets[0]["field_conventions"] == ["period labels use YYYY-MM"]
    assert datasets[0]["release_cadence"] == "monthly"
    assert any(row["kind"] == "statistical-bulletin" for row in documents)
    assert any("Short attributed excerpt — Example Statistics"
               in row["content"] for row in documents)


def test_profiled_build_is_byte_deterministic(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    complete_bundle(reviewed_catalog_spec(), first)
    complete_bundle(reviewed_catalog_spec(), second)
    assert bundle_bytes(first) == bundle_bytes(second)


def test_float_perturbation_is_distinct_for_zero_and_small_values():
    for value in (0.0, 1e-12, -1e-12):
        variable = {"value": value}
        assert all(perturb(variable, index) != value for index in range(6))


@pytest.mark.parametrize(
    "mutate, message",
    [
        (
            lambda spec: spec["source_profiles"][0].update(
                {"review_status": "pending"}
            ),
            "approved",
        ),
        (
            lambda spec: spec["variables"][0]["sources"][0].update(
                {"profile_id": "missing"}
            ),
            "unknown profile",
        ),
        (
            lambda spec: spec["source_profiles"][0]["excerpts"][0].update(
                {"text": "x" * 241}
            ),
            "exceeds",
        ),
        (
            lambda spec: spec["source_profiles"][0]["excerpts"][0].pop(
                "attribution"
            ),
            "attribution",
        ),
        (
            lambda spec: spec["source_profiles"][0]["excerpts"][0].update(
                {"text": "The workbook observation is 4.25 percent."}
            ),
            "workbook/target value",
        ),
    ],
)
def test_invalid_profiles_are_rejected(mutate, message):
    spec = profiled_spec()
    mutate(spec)
    with pytest.raises(ValueError, match=message):
        validate_spec(spec)


def test_skipped_profile_cannot_supply_auth_derived_content():
    spec = legacy_spec()
    spec["source_profiles"] = [{
        "id": "blocked",
        "canonical_url": "https://statistics.example/login",
        "access": {"status": "skipped_auth"},
        "review_status": "skipped",
        "datasets": [{"key": "private", "name": "Private observations"}],
    }]
    with pytest.raises(ValueError, match="auth/access-derived content"):
        validate_spec(spec)


def test_reviewed_catalog_rejects_pending_and_broken_hash():
    pending = reviewed_catalog_spec()
    pending["source_profiles"]["profiles"][0]["review"]["status"] = "pending"
    pending["source_profiles"]["capture"]["profiles_sha256"] = _canonical_hash(
        pending["source_profiles"]["profiles"])
    with pytest.raises(ValueError, match="accepted"):
        validate_spec(pending)

    broken = reviewed_catalog_spec()
    broken["source_profiles"]["capture"]["profiles_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="profiles_sha256"):
        validate_spec(broken)


def test_conflicting_explicit_source_ids_are_rejected():
    spec = profiled_spec()
    other = deepcopy(spec["variables"][0])
    other.update({"id": "other-rate", "period": "2025"})
    other["sources"][0]["url"] = "https://statistics.example/other"
    spec["variables"].append(other)
    with pytest.raises(ValueError, match="Duplicate explicit source id"):
        validate_spec(spec)


def test_build_never_uses_network(monkeypatch, tmp_path):
    def deny(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(urllib.request, "urlopen", deny)
    complete_bundle(reviewed_catalog_spec(), tmp_path / "offline")

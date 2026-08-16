#!/usr/bin/env python3
"""Deterministically stamp and validate source_profiles.json (stdlib only)."""

from __future__ import annotations

import argparse
import copy
import hashlib
import ipaddress
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


HASH_RE = re.compile(r"^[0-9a-f]{64}$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
CELL_RE = re.compile(
    r"(?:'[^'\r\n]+'|[A-Za-z][A-Za-z0-9 _-]{0,80})!"
    r"\$?[A-Z]{1,3}\$?\d+"
)
AUTH_PATH_RE = re.compile(
    r"/(?:login|log-in|signin|sign-in|sign-on|sso|auth|password)(?:/|$)",
    re.IGNORECASE,
)
SKIP_REASONS = {
    "auth_login_sso_password",
    "http_401",
    "http_403",
    "paywall",
    "bot_challenge",
    "unreachable",
    "unsupported_content",
}
REVIEW_STATUSES = {"pending", "accepted", "needs_review", "rejected"}
LIST_FIELDS = (
    "terminology",
    "dataset_names",
    "field_conventions",
    "document_types",
)
VALUE_KEYS = {
    "value",
    "values",
    "raw_value",
    "raw_values",
    "workbook_value",
    "workbook_values",
    "cached_value",
    "cached_values",
    "display_value",
    "display_values",
    "forbidden_value",
    "forbidden_values",
}
FORBIDDEN_PROFILE_KEYS = VALUE_KEYS | {
    "cell",
    "cells",
    "cell_ref",
    "cell_refs",
    "workbook",
    "workbook_cells",
}
ROOT_KEYS = {"schema_version", "capture", "profiles"}
ROOT_CAPTURE_KEYS = {
    "created_at",
    "tool",
    "agent_model",
    "public_read_limit_per_source",
    "canonicalization",
    "inventory_sha256",
    "spec_sha256",
    "profiles_sha256",
}
PROFILE_KEYS = {
    "source_id",
    "source_name",
    "canonical_url",
    "status",
    "skip_reason",
    "capture",
    *LIST_FIELDS,
    "release_cadence",
    "evidence",
    "review",
}
PROFILE_CAPTURE_KEYS = {
    "attempted_at",
    "read_count",
    "http_status",
    "final_url",
    "content_type",
    "evidence_sha256",
}
EVIDENCE_KEYS = {"attributions", "excerpts"}
ATTRIBUTION_KEYS = {
    "attribution_id",
    "title",
    "publisher",
    "url",
    "accessed_at",
}
EXCERPT_KEYS = {"text", "attribution_id"}
REVIEW_KEYS = {"status", "reviewer", "reviewed_at", "notes"}


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def normalized_key(key: Any) -> str:
    return re.sub(r"[\s-]+", "_", str(key).strip().lower())


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = parts.port
    if port and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        host = f"{host}:{port}"
    path = parts.path or "/"
    query = urlencode(parse_qsl(parts.query, keep_blank_values=True), doseq=True)
    return urlunsplit((scheme, host, path, query, ""))


def public_url_error(url: Any, *, require_canonical: bool) -> str | None:
    if not isinstance(url, str) or not url:
        return "must be a non-empty URL string"
    try:
        parts = urlsplit(url)
        _ = parts.port
    except ValueError as exc:
        return f"cannot be parsed: {exc}"
    if parts.scheme not in {"http", "https"}:
        return "must use http or https"
    if parts.username is not None or parts.password is not None:
        return "must not contain userinfo or credentials"
    if parts.fragment:
        return "must not contain a fragment"
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        return "must contain a host"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host:
            return "single-label hosts are not public source hosts"
        if host.endswith(
            (".local", ".internal", ".localhost", ".test", ".invalid", ".onion")
        ):
            return "private or non-public DNS suffix"
        labels = host.split(".")
        if any(
            not label
            or len(label) > 63
            or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
            for label in labels
        ):
            return "invalid DNS host"
    else:
        if not address.is_global:
            return "IP address is not globally routable"
    if require_canonical:
        try:
            if url != canonicalize_url(url):
                return "is not in canonical form"
        except ValueError as exc:
            return f"cannot be canonicalized: {exc}"
    return None


def origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    port = parts.port
    if port is None:
        port = 80 if parts.scheme == "http" else 443
    return parts.scheme.lower(), (parts.hostname or "").lower(), port


def is_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return "T" in value


def check_object(
    value: Any,
    keys: set[str],
    path: str,
    errors: list[str],
) -> bool:
    if not isinstance(value, dict):
        errors.append(f"{path}: must be an object")
        return False
    actual = set(value)
    for key in sorted(keys - actual):
        errors.append(f"{path}: missing required key {key!r}")
    for key in sorted(actual - keys):
        errors.append(f"{path}: unexpected key {key!r}")
    return not (keys - actual)


def check_nullable_text(
    value: Any,
    path: str,
    errors: list[str],
    limit: int,
) -> None:
    if value is not None and (
        not isinstance(value, str) or not value.strip() or len(value) > limit
    ):
        errors.append(f"{path}: must be null or 1-{limit} characters")


def walk_keys(value: Any, path: str = "$") -> Iterable[tuple[str, Any, str]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            yield normalized_key(key), child, child_path
            yield from walk_keys(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk_keys(child, f"{path}[{index}]")


def flatten_scalars(value: Any) -> Iterable[str]:
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, str):
        text = value.strip()
        if text:
            yield text
    elif isinstance(value, (int, float)):
        yield format(value, ".15g")
        if isinstance(value, float) and value.is_integer():
            yield str(int(value))
    elif isinstance(value, dict):
        for child in value.values():
            yield from flatten_scalars(child)
    elif isinstance(value, list):
        for child in value:
            yield from flatten_scalars(child)


def load_json_or_jsonl(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        rows = []
        for line_number, line in enumerate(text.splitlines(), 1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"{path}:{line_number}: invalid JSON/JSONL: {exc}"
                    ) from exc
        return rows


def forbidden_values(paths: Iterable[Path]) -> set[str]:
    values: set[str] = set()
    for path in paths:
        document = load_json_or_jsonl(path)
        for key, child, _ in walk_keys(document):
            if key in VALUE_KEYS:
                values.update(flatten_scalars(child))
    return values


def semantic_text(profile: dict[str, Any]) -> Iterable[tuple[str, str]]:
    yield "source_name", profile.get("source_name", "")
    for field in LIST_FIELDS:
        values = profile.get(field, [])
        if not isinstance(values, list):
            values = []
        for index, text in enumerate(values):
            yield f"{field}[{index}]", text
    cadence = profile.get("release_cadence")
    if cadence:
        yield "release_cadence", cadence
    evidence = profile.get("evidence", {})
    if not isinstance(evidence, dict):
        evidence = {}
    for index, item in enumerate(evidence.get("attributions", [])):
        if isinstance(item, dict):
            yield f"evidence.attributions[{index}].title", item.get("title", "")
            yield (
                f"evidence.attributions[{index}].publisher",
                item.get("publisher", ""),
            )
    for index, item in enumerate(evidence.get("excerpts", [])):
        if isinstance(item, dict):
            yield f"evidence.excerpts[{index}].text", item.get("text", "")
    review = profile.get("review", {})
    if not isinstance(review, dict):
        review = {}
    if review.get("notes"):
        yield "review.notes", review["notes"]


def leaked_value(text: str, forbidden: set[str]) -> str | None:
    folded = text.casefold()
    for candidate in sorted(forbidden, key=lambda item: (-len(item), item)):
        if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", candidate):
            if re.search(
                rf"(?<![\w.]){re.escape(candidate)}(?![\w.])",
                text,
                re.IGNORECASE,
            ):
                return candidate
        elif candidate.casefold() in folded:
            return candidate
    return None


def validate_profile(
    profile: Any,
    index: int,
    errors: list[str],
    forbidden: set[str],
) -> None:
    path = f"$.profiles[{index}]"
    if not check_object(profile, PROFILE_KEYS, path, errors):
        return
    source_id = profile["source_id"]
    if not isinstance(source_id, str) or len(source_id) > 80 or not SLUG_RE.fullmatch(
        source_id
    ):
        errors.append(f"{path}.source_id: must be a lowercase slug (max 80)")
    source_name = profile["source_name"]
    if (
        not isinstance(source_name, str)
        or not source_name.strip()
        or len(source_name) > 160
    ):
        errors.append(f"{path}.source_name: must be 1-160 characters")
    canonical_url = profile["canonical_url"]
    url_error = public_url_error(canonical_url, require_canonical=True)
    if url_error:
        errors.append(f"{path}.canonical_url: {url_error}")

    status = profile["status"]
    if not isinstance(status, str) or status not in {"profiled", "skipped"}:
        errors.append(f"{path}.status: must be 'profiled' or 'skipped'")
    reason = profile["skip_reason"]
    if reason is not None and (
        not isinstance(reason, str) or reason not in SKIP_REASONS
    ):
        errors.append(f"{path}.skip_reason: invalid reason")
    if status == "profiled" and reason is not None:
        errors.append(f"{path}.skip_reason: profiled sources require null")
    if status == "skipped" and reason not in SKIP_REASONS:
        errors.append(f"{path}.skip_reason: skipped sources require a reason")
    if isinstance(canonical_url, str) and AUTH_PATH_RE.search(
        urlsplit(canonical_url).path
    ):
        if status != "skipped" or reason != "auth_login_sso_password":
            errors.append(f"{path}: obvious auth URL must be auth-skipped")

    capture = profile["capture"]
    capture_object = capture if isinstance(capture, dict) else {}
    if check_object(capture, PROFILE_CAPTURE_KEYS, f"{path}.capture", errors):
        if not is_timestamp(capture["attempted_at"]):
            errors.append(f"{path}.capture.attempted_at: invalid UTC timestamp")
        reads = capture["read_count"]
        if type(reads) is not int or not 0 <= reads <= 3:
            errors.append(f"{path}.capture.read_count: must be an integer 0-3")
        http_status = capture["http_status"]
        if http_status is not None and (
            type(http_status) is not int or not 100 <= http_status <= 599
        ):
            errors.append(f"{path}.capture.http_status: invalid status")
        final_url = capture["final_url"]
        if final_url is not None:
            final_error = public_url_error(final_url, require_canonical=False)
            if final_error:
                errors.append(f"{path}.capture.final_url: {final_error}")
            elif not url_error and origin(final_url) != origin(canonical_url):
                errors.append(f"{path}.capture.final_url: must be same-origin")
        check_nullable_text(
            capture["content_type"], f"{path}.capture.content_type", errors, 120
        )
        evidence_hash = capture["evidence_sha256"]
        if status == "profiled":
            if reads == 0:
                errors.append(f"{path}.capture.read_count: profiled source read none")
            if final_url is None:
                errors.append(f"{path}.capture.final_url: profiled source needs URL")
            if http_status is not None and http_status >= 400:
                errors.append(f"{path}: HTTP error cannot be profiled")
            if not isinstance(evidence_hash, str) or not HASH_RE.fullmatch(
                evidence_hash
            ):
                errors.append(f"{path}.capture.evidence_sha256: invalid hash")
        elif evidence_hash is not None:
            errors.append(f"{path}.capture.evidence_sha256: skipped must be null")
        if reason == "http_401" and http_status != 401:
            errors.append(f"{path}: http_401 reason requires status 401")
        if reason == "http_403" and http_status != 403:
            errors.append(f"{path}: http_403 reason requires status 403")
        if http_status == 401 and reason != "http_401":
            errors.append(f"{path}: status 401 requires http_401 skip")
        if http_status == 403 and reason != "http_403":
            errors.append(f"{path}: status 403 requires http_403 skip")

    nonempty_description = False
    for field in LIST_FIELDS:
        values = profile[field]
        if not isinstance(values, list):
            errors.append(f"{path}.{field}: must be an array")
            continue
        if len(values) > 24:
            errors.append(f"{path}.{field}: at most 24 items")
        seen: set[str] = set()
        for item_index, item in enumerate(values):
            if not isinstance(item, str) or not item.strip() or len(item) > 160:
                errors.append(
                    f"{path}.{field}[{item_index}]: must be 1-160 characters"
                )
            elif item in seen:
                errors.append(f"{path}.{field}: duplicate item {item!r}")
            else:
                seen.add(item)
        nonempty_description |= bool(values)
    cadence = profile["release_cadence"]
    check_nullable_text(cadence, f"{path}.release_cadence", errors, 240)
    nonempty_description |= bool(cadence)

    evidence = profile["evidence"]
    if check_object(evidence, EVIDENCE_KEYS, f"{path}.evidence", errors):
        attributions = evidence["attributions"]
        excerpts = evidence["excerpts"]
        if not isinstance(attributions, list):
            errors.append(f"{path}.evidence.attributions: must be an array")
            attributions = []
        if not isinstance(excerpts, list):
            errors.append(f"{path}.evidence.excerpts: must be an array")
            excerpts = []
        if len(attributions) > 4:
            errors.append(f"{path}.evidence.attributions: at most 4")
        if len(excerpts) > 2:
            errors.append(f"{path}.evidence.excerpts: at most 2")
        attribution_ids: set[str] = set()
        for attr_index, attr in enumerate(attributions):
            attr_path = f"{path}.evidence.attributions[{attr_index}]"
            if not check_object(attr, ATTRIBUTION_KEYS, attr_path, errors):
                continue
            attr_id = attr["attribution_id"]
            if (
                not isinstance(attr_id, str)
                or len(attr_id) > 64
                or not SLUG_RE.fullmatch(attr_id)
            ):
                errors.append(f"{attr_path}.attribution_id: invalid slug")
            elif attr_id in attribution_ids:
                errors.append(f"{attr_path}.attribution_id: duplicate")
            else:
                attribution_ids.add(attr_id)
            for field in ("title", "publisher"):
                text = attr[field]
                if (
                    not isinstance(text, str)
                    or not text.strip()
                    or len(text) > 160
                ):
                    errors.append(f"{attr_path}.{field}: must be 1-160 characters")
            attr_url_error = public_url_error(attr["url"], require_canonical=False)
            if attr_url_error:
                errors.append(f"{attr_path}.url: {attr_url_error}")
            elif not url_error and origin(attr["url"]) != origin(canonical_url):
                errors.append(f"{attr_path}.url: must be same-origin")
            if not is_timestamp(attr["accessed_at"]):
                errors.append(f"{attr_path}.accessed_at: invalid UTC timestamp")
        excerpt_total = 0
        for excerpt_index, excerpt in enumerate(excerpts):
            excerpt_path = f"{path}.evidence.excerpts[{excerpt_index}]"
            if not check_object(excerpt, EXCERPT_KEYS, excerpt_path, errors):
                continue
            text = excerpt["text"]
            if not isinstance(text, str) or not text.strip() or len(text) > 240:
                errors.append(f"{excerpt_path}.text: must be 1-240 code points")
            else:
                excerpt_total += len(text)
            excerpt_attr_id = excerpt["attribution_id"]
            if (
                not isinstance(excerpt_attr_id, str)
                or excerpt_attr_id not in attribution_ids
            ):
                errors.append(
                    f"{excerpt_path}.attribution_id: missing attribution"
                )
        if excerpt_total > 400:
            errors.append(f"{path}.evidence.excerpts: at most 400 total code points")
        if status == "profiled":
            if not attributions:
                errors.append(f"{path}: profiled source needs attribution")
            expected_hash = sha256_bytes(canonical_json(evidence))
            if capture_object.get("evidence_sha256") != expected_hash:
                errors.append(f"{path}.capture.evidence_sha256: hash mismatch")
        elif attributions or excerpts:
            errors.append(f"{path}: skipped source must contain no evidence")

    if status == "profiled" and not nonempty_description:
        errors.append(f"{path}: profiled source needs descriptive content")
    if status == "skipped":
        if nonempty_description:
            errors.append(f"{path}: skipped source must contain no profile content")
        if any(profile[field] for field in LIST_FIELDS) or cadence is not None:
            errors.append(f"{path}: auth/access skip exclusion failed")

    review = profile["review"]
    if check_object(review, REVIEW_KEYS, f"{path}.review", errors):
        if (
            not isinstance(review["status"], str)
            or review["status"] not in REVIEW_STATUSES
        ):
            errors.append(f"{path}.review.status: invalid status")
        check_nullable_text(review["reviewer"], f"{path}.review.reviewer", errors, 120)
        if review["reviewed_at"] is not None and not is_timestamp(
            review["reviewed_at"]
        ):
            errors.append(f"{path}.review.reviewed_at: invalid UTC timestamp")
        check_nullable_text(review["notes"], f"{path}.review.notes", errors, 500)

    for key, _, key_path in walk_keys(profile, path):
        if key in FORBIDDEN_PROFILE_KEYS:
            errors.append(f"{key_path}: forbidden value/workbook key")
    for text_path, text in semantic_text(profile):
        if not isinstance(text, str):
            continue
        if CELL_RE.search(text):
            errors.append(f"{path}.{text_path}: workbook cell reference forbidden")
        leaked = leaked_value(text, forbidden)
        if leaked is not None:
            errors.append(
                f"{path}.{text_path}: forbidden inventory/spec value leaked"
            )


def validate_document(
    document: Any,
    *,
    forbidden: set[str] | None = None,
    inventory_path: Path | None = None,
    spec_path: Path | None = None,
) -> list[str]:
    errors: list[str] = []
    forbidden = forbidden or set()
    if not check_object(document, ROOT_KEYS, "$", errors):
        return errors
    if document["schema_version"] != "1.0":
        errors.append("$.schema_version: must equal '1.0'")
    capture = document["capture"]
    if check_object(capture, ROOT_CAPTURE_KEYS, "$.capture", errors):
        if not is_timestamp(capture["created_at"]):
            errors.append("$.capture.created_at: invalid UTC timestamp")
        if capture["tool"] != "profile-mcp-sources":
            errors.append("$.capture.tool: invalid tool")
        if capture["agent_model"] != "gpt-5.6-sol-high":
            errors.append("$.capture.agent_model: invalid model")
        if capture["public_read_limit_per_source"] != 3:
            errors.append("$.capture.public_read_limit_per_source: must equal 3")
        if capture["canonicalization"] != "v1":
            errors.append("$.capture.canonicalization: must equal 'v1'")
        for field, supplied_path in (
            ("inventory_sha256", inventory_path),
            ("spec_sha256", spec_path),
        ):
            value = capture[field]
            if value is not None and (
                not isinstance(value, str) or not HASH_RE.fullmatch(value)
            ):
                errors.append(f"$.capture.{field}: invalid nullable hash")
            if supplied_path is not None and value != file_sha256(supplied_path):
                errors.append(f"$.capture.{field}: input file hash mismatch")
            if supplied_path is None and value is not None:
                errors.append(f"$.capture.{field}: hash present but input not supplied")
        profiles_hash = capture["profiles_sha256"]
        if not isinstance(profiles_hash, str) or not HASH_RE.fullmatch(profiles_hash):
            errors.append("$.capture.profiles_sha256: invalid hash")
    profiles = document["profiles"]
    if not isinstance(profiles, list):
        errors.append("$.profiles: must be an array")
        return errors
    seen_ids: set[str] = set()
    seen_urls: set[str] = set()
    for index, profile in enumerate(profiles):
        validate_profile(profile, index, errors, forbidden)
        if isinstance(profile, dict):
            source_id = profile.get("source_id")
            canonical_url = profile.get("canonical_url")
            if isinstance(source_id, str):
                if source_id in seen_ids:
                    errors.append(
                        f"$.profiles[{index}].source_id: duplicate source ID"
                    )
                seen_ids.add(source_id)
            if isinstance(canonical_url, str):
                if canonical_url in seen_urls:
                    errors.append(
                        f"$.profiles[{index}].canonical_url: duplicate canonical URL"
                    )
                seen_urls.add(canonical_url)
    expected_profiles_hash = sha256_bytes(canonical_json(profiles))
    if isinstance(capture, dict) and capture.get(
        "profiles_sha256"
    ) != expected_profiles_hash:
        errors.append("$.capture.profiles_sha256: hash mismatch")
    return errors


def stamp_hashes(
    document: dict[str, Any],
    inventory_path: Path | None,
    spec_path: Path | None,
) -> None:
    if not isinstance(document.get("capture"), dict) or not isinstance(
        document.get("profiles"), list
    ):
        raise ValueError("document needs capture object and profiles array")
    document["capture"]["inventory_sha256"] = (
        file_sha256(inventory_path) if inventory_path else None
    )
    document["capture"]["spec_sha256"] = (
        file_sha256(spec_path) if spec_path else None
    )
    for profile in document["profiles"]:
        if not isinstance(profile, dict) or not isinstance(
            profile.get("capture"), dict
        ):
            continue
        if profile.get("status") == "profiled" and isinstance(
            profile.get("evidence"), dict
        ):
            profile["capture"]["evidence_sha256"] = sha256_bytes(
                canonical_json(profile["evidence"])
            )
        elif profile.get("status") == "skipped":
            profile["capture"]["evidence_sha256"] = None
    document["capture"]["profiles_sha256"] = sha256_bytes(
        canonical_json(document["profiles"])
    )


def write_json_atomic(path: Path, document: Any) -> None:
    rendered = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(rendered)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def valid_fixture() -> dict[str, Any]:
    timestamp = "2026-08-15T22:00:00Z"
    profile = {
        "source_id": "example-statistics",
        "source_name": "Example Statistics",
        "canonical_url": "https://www.example.com/",
        "status": "profiled",
        "skip_reason": None,
        "capture": {
            "attempted_at": timestamp,
            "read_count": 1,
            "http_status": 200,
            "final_url": "https://www.example.com/",
            "content_type": "text/html",
            "evidence_sha256": "",
        },
        "terminology": ["seasonally adjusted"],
        "dataset_names": ["Monthly indicators"],
        "field_conventions": ["period labels use YYYY-MM"],
        "document_types": ["dataset landing page"],
        "release_cadence": "monthly scheduled release",
        "evidence": {
            "attributions": [
                {
                    "attribution_id": "landing",
                    "title": "Monthly indicators",
                    "publisher": "Example Statistics",
                    "url": "https://www.example.com/",
                    "accessed_at": timestamp,
                }
            ],
            "excerpts": [
                {
                    "text": "Updated each month after the scheduled release.",
                    "attribution_id": "landing",
                }
            ],
        },
        "review": {
            "status": "pending",
            "reviewer": None,
            "reviewed_at": None,
            "notes": None,
        },
    }
    document = {
        "schema_version": "1.0",
        "capture": {
            "created_at": timestamp,
            "tool": "profile-mcp-sources",
            "agent_model": "gpt-5.6-sol-high",
            "public_read_limit_per_source": 3,
            "canonicalization": "v1",
            "inventory_sha256": None,
            "spec_sha256": None,
            "profiles_sha256": "",
        },
        "profiles": [profile],
    }
    stamp_hashes(document, None, None)
    return document


def run_self_test() -> None:
    cases = 0

    def expect_failure(
        name: str,
        mutate: Any,
        *,
        forbidden: set[str] | None = None,
    ) -> None:
        nonlocal cases
        document = valid_fixture()
        mutate(document)
        stamp_hashes(document, None, None)
        errors = validate_document(document, forbidden=forbidden)
        if not errors:
            raise AssertionError(f"{name}: expected validation failure")
        cases += 1

    if validate_document(valid_fixture()):
        raise AssertionError("valid fixture failed")
    cases += 1

    expect_failure("schema", lambda doc: doc["profiles"][0].pop("source_name"))

    def auth_leak(doc: dict[str, Any]) -> None:
        profile = doc["profiles"][0]
        profile["status"] = "skipped"
        profile["skip_reason"] = "auth_login_sso_password"
        profile["capture"]["http_status"] = 200

    expect_failure("auth-skip exclusion", auth_leak)

    def bad_attribution(doc: dict[str, Any]) -> None:
        doc["profiles"][0]["evidence"]["excerpts"][0][
            "attribution_id"
        ] = "missing"

    expect_failure("attribution", bad_attribution)
    expect_failure(
        "excerpt limit",
        lambda doc: doc["profiles"][0]["evidence"]["excerpts"][0].update(
            {"text": "x" * 241}
        ),
    )

    def duplicate_id(doc: dict[str, Any]) -> None:
        duplicate = copy.deepcopy(doc["profiles"][0])
        duplicate["canonical_url"] = "https://www.example.org/"
        duplicate["capture"]["final_url"] = "https://www.example.org/"
        duplicate["evidence"]["attributions"][0][
            "url"
        ] = "https://www.example.org/"
        doc["profiles"].append(duplicate)

    expect_failure("duplicate IDs", duplicate_id)
    expect_failure(
        "unsafe URL",
        lambda doc: doc["profiles"][0].update(
            {"canonical_url": "http://127.0.0.1/"}
        ),
    )
    with tempfile.TemporaryDirectory() as directory:
        inventory = Path(directory) / "inventory.json"
        spec = Path(directory) / "spec.json"
        inventory.write_text(
            json.dumps({"rows": [{"raw_value": "INVENTORY-SECRET"}]}),
            encoding="utf-8",
        )
        spec.write_text(
            json.dumps({"variables": [{"value": "SPEC-SECRET"}]}),
            encoding="utf-8",
        )
        supplied_values = forbidden_values([inventory, spec])
        if supplied_values != {"INVENTORY-SECRET", "SPEC-SECRET"}:
            raise AssertionError("inventory/spec forbidden values not extracted")
        expect_failure(
            "inventory/spec leakage",
            lambda doc: doc["profiles"][0]["terminology"].extend(
                ["INVENTORY-SECRET", "SPEC-SECRET"]
            ),
            forbidden=supplied_values,
        )

    if cases != 8:
        raise AssertionError(f"expected 8 self-test cases, ran {cases}")
    print(f"SELF-TEST OK ({cases} cases)")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_profiles", nargs="?", type=Path)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--spec", type=Path)
    parser.add_argument(
        "--rehash",
        action="store_true",
        help="update input, evidence, and profiles hashes before validation",
    )
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.self_test:
        run_self_test()
        if args.source_profiles is None:
            return 0
    if args.source_profiles is None:
        print("error: source_profiles is required unless only --self-test is used")
        return 2
    for label, path in (
        ("source_profiles", args.source_profiles),
        ("inventory", args.inventory),
        ("spec", args.spec),
    ):
        if path is not None and not path.is_file():
            print(f"error: {label} file not found: {path}")
            return 2
    try:
        document = json.loads(args.source_profiles.read_text(encoding="utf-8"))
        if args.rehash:
            stamp_hashes(document, args.inventory, args.spec)
            write_json_atomic(args.source_profiles, document)
        source_paths = [
            path for path in (args.inventory, args.spec) if path is not None
        ]
        forbidden = forbidden_values(source_paths)
        errors = validate_document(
            document,
            forbidden=forbidden,
            inventory_path=args.inventory,
            spec_path=args.spec,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}")
        return 2
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        print(f"FAILED ({len(errors)} errors)")
        return 1
    print(
        f"OK ({len(document['profiles'])} profiles; "
        f"{len(forbidden)} forbidden values checked)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

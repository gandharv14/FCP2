"""Build a dense, deterministic MCP research environment from a normalized spec.

For every atomic variable the builder emits one supported record plus a ring of
distractors that each differ on at least one meaningful dimension (scenario,
basis, period, unit, status). No distractor shares every required dimension
with the supported record, so a fully-disambiguated query resolves to exactly
one row while broad queries return conflicting candidates.

Output layout::

    <output>/runtime/   served through MCP (sources, documents, datasets, records)
    <output>/eval/      tasks.jsonl - prompts, typed answers, evidence ids; NEVER shipped
    <output>/environment.json

The spec contract is documented in ``references/scenario-schema.md`` of the
originating skill; this vendored copy additionally accepts per-variable
``workbook`` metadata (cell refs and the raw cell value) and ``alternatives``
(categorical distractor values).
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from datetime import date, timedelta
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

SCENARIOS = ["Management case", "Lender case", "Downside case", "Observed", "Preliminary"]
BASES = ["real prices", "nominal prices", "pre-tax", "post-tax", "constant-price basis"]
STATUSES = ["draft", "superseded", "indicative", "revised", "provisional"]
PUBLIC_ACCESS_STATUSES = {"public"}
SKIPPED_ACCESS_STATUSES = {
    "skipped_auth", "skipped_paywall", "skipped_bot_challenge",
    "skipped_unreachable", "skipped_unsupported", "skipped_robots",
}
MAX_EXCERPT_CHARS = 240
CONTENT_PROFILE_KEYS = {
    "description", "source_description", "terminology",
    "information_architecture", "datasets", "dataset_names",
    "field_conventions", "document_types", "release_cadence",
    "excerpts", "evidence",
}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:72] or "item"


def stable_id(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:12]
    return "%s_%s" % (prefix, digest)


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                    encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def profile_catalog(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return profiles by id, accepting a list or an id-keyed catalog."""
    raw = spec.get("source_profiles")
    if raw is None:
        return {}
    if isinstance(raw, list):
        profiles = raw
    elif isinstance(raw, dict) and isinstance(raw.get("profiles"), list):
        profiles = raw["profiles"]
    elif isinstance(raw, dict):
        profiles = []
        for key, value in raw.items():
            if not isinstance(value, dict):
                raise ValueError("source_profiles[%s] must be an object" % key)
            item = dict(value)
            if item.get("id") not in (None, key):
                raise ValueError("source profile key %s disagrees with id %s"
                                 % (key, item.get("id")))
            item.setdefault("id", key)
            profiles.append(item)
    else:
        raise ValueError("source_profiles must be a list or object catalog")
    result: dict[str, dict[str, Any]] = {}
    for index, profile in enumerate(profiles):
        if not isinstance(profile, dict) or not (
                profile.get("id") or profile.get("source_id")):
            raise ValueError("source_profiles[%d] needs an id" % index)
        pid = str(profile.get("id") or profile["source_id"])
        if pid in result:
            raise ValueError("Duplicate source profile id: %s" % pid)
        result[pid] = profile
    return result


def access_status(profile: dict[str, Any]) -> str:
    if profile.get("status") == "profiled":
        return "public"
    if profile.get("status") == "skipped":
        return "skipped_%s" % (profile.get("skip_reason") or "unsupported")
    access = profile.get("access")
    if isinstance(access, dict):
        return str(access.get("status") or "")
    return str(access or profile.get("access_status") or "")


def review_status(profile: dict[str, Any]) -> str:
    review = profile.get("review")
    if isinstance(review, dict):
        if review.get("status") == "accepted":
            return "approved"
        return str(review.get("status") or "")
    if profile.get("status") == "skipped":
        return "skipped"
    return str(profile.get("review_status") or "")


def profile_dataset(profile: dict[str, Any], dataset_key: str) -> dict[str, Any]:
    datasets = profile.get("datasets") or []
    for dataset in datasets:
        if isinstance(dataset, dict) and str(dataset.get("key")) == dataset_key:
            return dataset
    names = profile.get("dataset_names") or []
    if profile.get("source_id"):
        if not names:
            names = ["%s versioned indicators" % profile["source_name"]]
        matches = [name for name in names if slug(str(name)) == dataset_key]
        if len(matches) == 1:
            name = matches[0]
        elif len(names) == 1:
            name = names[0]
        else:
            raise ValueError("Profile %s has no unambiguous dataset %s"
                             % (profile.get("source_id"), dataset_key))
        cadence_text = profile.get("release_cadence")
        cadence = cadence_kind(cadence_text)
        return {
            "key": dataset_key,
            "name": name,
            "aliases": [item for item in names if item != name],
            "field_conventions": profile.get("field_conventions") or [],
            "document_kinds": [
                slug(item) for item in (profile.get("document_types") or [])
            ],
            "release_cadence": cadence,
            "release_label_style": (
                "%s — {date}" % str(cadence_text).replace(
                    "{", "{{").replace("}", "}}")
                if cadence_text else None),
        }
    raise ValueError("Profile %s has no dataset %s"
                     % (profile.get("id") or profile.get("source_id"), dataset_key))


def profile_excerpts(profile: dict[str, Any]) -> list[dict[str, str]]:
    excerpts = profile.get("excerpts") or []
    if excerpts:
        return [item for item in excerpts if isinstance(item, dict)]
    evidence = profile.get("evidence") or {}
    attributions = {
        item.get("attribution_id"): item
        for item in (evidence.get("attributions") or [])
        if isinstance(item, dict)
    }
    result = []
    for item in evidence.get("excerpts") or []:
        if not isinstance(item, dict):
            continue
        attribution = attributions.get(item.get("attribution_id")) or {}
        result.append({
            "text": item.get("text", ""),
            "attribution": attribution.get("publisher")
                           or attribution.get("title", ""),
            "locator": attribution.get("title") or item.get("attribution_id", ""),
            "source_url": attribution.get("url", ""),
        })
    return result


def cadence_kind(value: Any) -> str:
    text = str(value or "").casefold()
    if "daily" in text or "day" in text:
        return "daily"
    if "weekly" in text or "week" in text:
        return "weekly"
    if "monthly" in text or "month" in text:
        return "monthly"
    if "quarter" in text:
        return "quarterly"
    if "annual" in text or "year" in text:
        return "annual"
    if "continuous" in text or "real-time" in text:
        return "continuous"
    return "irregular"


def _leaf_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _leaf_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _leaf_strings(child)


def _sensitive_values(variable: dict[str, Any]) -> list[str]:
    values: list[Any] = [variable.get("value")]
    workbook = variable.get("workbook") or {}
    if "value" in workbook:
        values.append(workbook["value"])
    result = []
    for value in values:
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (int, float)):
            text = "%.12g" % value
            if text not in {"0", "1", "-1"}:
                result.append(text)
        elif isinstance(value, str) and len(value.strip()) >= 4:
            result.append(value.strip())
    return result


def _profile_content(profile: dict[str, Any]) -> dict[str, Any]:
    content = {
        key: profile[key] for key in CONTENT_PROFILE_KEYS
        if key in profile and key != "evidence"
    }
    evidence = profile.get("evidence")
    if isinstance(evidence, dict):
        content["evidence"] = {
            "attributions": [
                {
                    key: item.get(key)
                    for key in ("title", "publisher")
                    if item.get(key)
                }
                for item in evidence.get("attributions") or []
                if isinstance(item, dict)
            ],
            "excerpts": [
                item.get("text")
                for item in evidence.get("excerpts") or []
                if isinstance(item, dict)
            ],
        }
    return content


def _contains_value(text: str, value: str) -> bool:
    if re.fullmatch(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)", value):
        return re.search(r"(?<![A-Za-z0-9.])%s(?![A-Za-z0-9.])"
                         % re.escape(value), text) is not None
    return value.casefold() in text.casefold()


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _public_url(value: Any) -> bool:
    parsed = urlparse(str(value or ""))
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _auth_url(value: Any) -> bool:
    parsed = urlparse(str(value or ""))
    haystack = "%s%s" % (parsed.netloc.casefold(), parsed.path.casefold())
    return re.search(
        r"(?:^|[/._-])(?:login|signin|sign-in|oauth|sso|auth)(?:[/._-]|$)",
        haystack,
    ) is not None


def _validate_captured_profile(profile: dict[str, Any]) -> None:
    pid = str(profile["source_id"])
    status = profile.get("status")
    capture = profile.get("capture")
    evidence = profile.get("evidence")
    review = profile.get("review")
    if status not in {"profiled", "skipped"} or not isinstance(capture, dict) or \
            not isinstance(evidence, dict) or not isinstance(review, dict):
        raise ValueError("Profile %s has an invalid reviewed capture shape" % pid)
    if not pid or not isinstance(profile.get("source_name"), str) or \
            not profile["source_name"].strip():
        raise ValueError("Captured profile needs source_id and source_name")
    if not _public_url(profile.get("canonical_url")):
        raise ValueError("Profile %s needs a canonical public URL" % pid)
    descriptive = [
        profile.get("terminology") or [],
        profile.get("dataset_names") or [],
        profile.get("field_conventions") or [],
        profile.get("document_types") or [],
    ]
    if not all(isinstance(items, list) and all(
            isinstance(item, str) and item.strip() for item in items)
            for items in descriptive):
        raise ValueError("Profile %s descriptive fields must be string lists" % pid)
    attributions = evidence.get("attributions")
    excerpts = evidence.get("excerpts")
    if not isinstance(attributions, list) or not isinstance(excerpts, list):
        raise ValueError("Profile %s evidence must contain attribution lists" % pid)

    if status == "skipped":
        if not profile.get("skip_reason"):
            raise ValueError("Skipped profile %s needs a skip_reason" % pid)
        if any(descriptive) or profile.get("release_cadence") or \
                attributions or excerpts or capture.get("evidence_sha256"):
            raise ValueError(
                "Skipped profile %s contains auth/access-derived content" % pid)
        return

    if review.get("status") != "accepted":
        raise ValueError("Profile %s must be accepted for public use" % pid)
    if profile.get("skip_reason") is not None:
        raise ValueError("Public profile %s cannot have a skip_reason" % pid)
    http_status = capture.get("http_status")
    if not isinstance(http_status, int) or not 200 <= http_status < 300:
        raise ValueError("Public profile %s must come from a successful capture" % pid)
    if not _public_url(capture.get("final_url")) or \
            _auth_url(capture.get("final_url")):
        raise ValueError("Public profile %s has an auth/unsafe final URL" % pid)
    if not any(descriptive) and not profile.get("release_cadence"):
        raise ValueError("Public profile %s has no descriptive content" % pid)
    evidence_hash = str(capture.get("evidence_sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", evidence_hash) or \
            evidence_hash != _canonical_hash(evidence):
        raise ValueError("Profile %s evidence_sha256 does not match evidence" % pid)
    attribution_by_id: dict[str, dict[str, Any]] = {}
    for item in attributions:
        if not isinstance(item, dict) or not item.get("attribution_id") or \
                not item.get("title") or not item.get("publisher") or \
                not _public_url(item.get("url")):
            raise ValueError("Profile %s has an invalid attribution" % pid)
        aid = str(item["attribution_id"])
        if aid in attribution_by_id:
            raise ValueError("Profile %s has duplicate attribution %s" % (pid, aid))
        attribution_by_id[aid] = item
    if not attribution_by_id:
        raise ValueError("Public profile %s needs attribution" % pid)
    excerpt_chars = 0
    for excerpt in excerpts:
        if not isinstance(excerpt, dict) or not isinstance(
                excerpt.get("text"), str) or not excerpt["text"].strip():
            raise ValueError("Profile %s has an invalid excerpt" % pid)
        excerpt_chars += len(excerpt["text"])
        if len(excerpt["text"]) > MAX_EXCERPT_CHARS:
            raise ValueError("Profile %s excerpt exceeds %d characters"
                             % (pid, MAX_EXCERPT_CHARS))
        if excerpt.get("attribution_id") not in attribution_by_id:
            raise ValueError("Profile %s excerpt has broken attribution" % pid)
    if excerpt_chars > 400:
        raise ValueError("Profile %s excerpts exceed 400 characters total" % pid)


def _validate_profile(profile: dict[str, Any]) -> None:
    if profile.get("source_id"):
        _validate_captured_profile(profile)
        return
    pid = str(profile["id"])
    status = access_status(profile)
    reviewed = review_status(profile)
    if status in PUBLIC_ACCESS_STATUSES:
        if reviewed != "approved":
            raise ValueError("Profile %s must be approved for public use" % pid)
    elif status in SKIPPED_ACCESS_STATUSES:
        if reviewed != "skipped":
            raise ValueError("Skipped profile %s must have review_status skipped" % pid)
        forbidden = sorted(CONTENT_PROFILE_KEYS.intersection(profile))
        if forbidden:
            raise ValueError(
                "Skipped profile %s contains auth/access-derived content: %s"
                % (pid, ", ".join(forbidden)))
        return
    else:
        raise ValueError(
            "Profile %s access status must be public or an approved skipped status"
            % pid)

    canonical = profile.get("canonical_url")
    parsed = urlparse(str(canonical or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Public profile %s needs a canonical HTTP(S) URL" % pid)
    capture = profile.get("capture")
    if not isinstance(capture, dict):
        raise ValueError("Public profile %s needs capture metadata" % pid)
    http_status = capture.get("http_status")
    if not isinstance(http_status, int) or not 200 <= http_status < 300:
        raise ValueError("Public profile %s must come from a successful capture" % pid)
    content_hash = str(capture.get("content_sha256") or "")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", content_hash):
        raise ValueError("Public profile %s needs a capture content_sha256" % pid)
    auth_flags = {
        "auth_detected", "login_detected", "password_form", "requires_auth",
        "paywall_detected", "bot_challenge",
    }
    if any(capture.get(flag) is True for flag in auth_flags):
        raise ValueError("Public profile %s contains auth-derived content" % pid)
    final_url = str(capture.get("final_url") or canonical)
    final = urlparse(final_url)
    if final.scheme not in {"http", "https"} or not final.netloc or \
            _auth_url(final_url):
        raise ValueError("Public profile %s capture ended at an auth/unsafe URL" % pid)
    datasets = profile.get("datasets")
    if not isinstance(datasets, list) or not datasets:
        raise ValueError("Public profile %s needs at least one dataset" % pid)
    dataset_keys: set[str] = set()
    for dataset in datasets:
        if not isinstance(dataset, dict) or not dataset.get("key") or \
                not dataset.get("name"):
            raise ValueError(
                "Every dataset in profile %s needs key and name" % pid)
        key = str(dataset["key"])
        if key in dataset_keys:
            raise ValueError("Duplicate dataset key %s in profile %s" % (key, pid))
        dataset_keys.add(key)
        aliases = dataset.get("aliases", [])
        if not isinstance(aliases, list) or not all(
                isinstance(alias, str) and alias.strip() for alias in aliases):
            raise ValueError("Dataset %s aliases must be non-empty strings" % key)
        labels = dataset.get("field_labels", {})
        if not isinstance(labels, dict) or not all(
                field in {"entity", "metric", "period", "scenario", "basis",
                          "unit", "status", "value"}
                and isinstance(label, str) and label.strip()
                for field, label in labels.items()):
            raise ValueError(
                "Dataset %s field_labels must map canonical fields to labels" % key)
        conventions = dataset.get("field_conventions", {})
        if not isinstance(conventions, dict):
            raise ValueError("Dataset %s field_conventions must be an object" % key)
        kinds = dataset.get("document_kinds", [])
        if not isinstance(kinds, list) or not all(
                isinstance(kind, str) and kind.strip() for kind in kinds):
            raise ValueError("Dataset %s document_kinds must be strings" % key)
        cadence = dataset.get("release_cadence")
        if cadence is not None and cadence not in {
                "daily", "weekly", "monthly", "quarterly", "annual",
                "irregular", "continuous"}:
            raise ValueError("Dataset %s has unsupported release cadence" % key)
        style = dataset.get("release_label_style")
        if style is not None:
            if not isinstance(style, str) or len(style) > 120:
                raise ValueError("Dataset %s has an invalid release label style" % key)
            fields = set(re.findall(r"\{([^{}]+)\}", style))
            if not fields.issubset({"date", "period", "status", "index"}):
                raise ValueError("Dataset %s release label has unknown fields" % key)

    excerpts = profile.get("excerpts", [])
    if not isinstance(excerpts, list):
        raise ValueError("Profile %s excerpts must be a list" % pid)
    for excerpt in excerpts:
        if not isinstance(excerpt, dict):
            raise ValueError("Profile %s excerpt must be an object" % pid)
        text = excerpt.get("text")
        attribution = excerpt.get("attribution")
        locator = excerpt.get("locator")
        attributed_url = excerpt.get("source_url") or excerpt.get("url")
        if not isinstance(text, str) or not text.strip() or \
                len(text) > MAX_EXCERPT_CHARS:
            raise ValueError("Profile %s excerpt exceeds %d characters"
                             % (pid, MAX_EXCERPT_CHARS))
        if not isinstance(attribution, str) or not attribution.strip() or \
                not isinstance(locator, str) or not locator.strip():
            raise ValueError(
                "Profile %s excerpt needs attribution and locator" % pid)
        if attributed_url and str(attributed_url) != str(canonical):
            raise ValueError(
                "Profile %s excerpt attribution URL is not canonical" % pid)


def validate_spec(spec: dict[str, Any]) -> None:
    if not spec.get("environment_id") or not isinstance(spec.get("seed"), int):
        raise ValueError("environment_id and integer seed are required")
    variables = spec.get("variables")
    if not isinstance(variables, list) or not variables:
        raise ValueError("variables must be a non-empty list")
    required = ["id", "name", "value", "unit", "entity", "period", "scenario",
                "basis", "status", "question", "sources"]
    raw_profiles = spec.get("source_profiles")
    if isinstance(raw_profiles, dict) and isinstance(
            raw_profiles.get("profiles"), list):
        if raw_profiles.get("schema_version") != "1.0":
            raise ValueError("source_profiles catalog must use schema_version 1.0")
        capture = raw_profiles.get("capture")
        if not isinstance(capture, dict):
            raise ValueError("source_profiles catalog needs capture metadata")
        expected = str(capture.get("profiles_sha256") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", expected) or \
                expected != _canonical_hash(raw_profiles["profiles"]):
            raise ValueError("source_profiles profiles_sha256 does not match profiles")
    profiles = profile_catalog(spec)
    for profile in profiles.values():
        _validate_profile(profile)
    ids: set[str] = set()
    dimensions: set[tuple[str, ...]] = set()
    explicit_sources: dict[str, tuple[str, ...]] = {}
    resolved_sources: dict[str, tuple[str, ...]] = {}
    referenced_profiles: dict[str, list[dict[str, Any]]] = {}
    for index, variable in enumerate(variables):
        missing = [key for key in required if key not in variable]
        if missing:
            raise ValueError("variables[%d] is missing: %s" % (index, ", ".join(missing)))
        if variable["id"] in ids:
            raise ValueError("Duplicate variable id: %s" % variable["id"])
        ids.add(variable["id"])
        if isinstance(variable["value"], str) and (
                ";" in variable["value"] or "\u2248" in variable["value"]):
            raise ValueError(
                "%s has a compound value; split it into atomic variables" % variable["id"])
        if not variable["sources"]:
            raise ValueError("%s requires at least one source" % variable["id"])
        for source in variable["sources"]:
            if not source.get("name") or not source.get("url") or not source.get("role"):
                raise ValueError(
                    "Every source for %s needs name, url, and role" % variable["id"])
            explicit_id = source.get("id")
            resolved_id = source_id(source)
            resolved_identity = tuple(str(source.get(field) or "") for field in
                                      ("name", "url", "role", "kind",
                                       "profile_id", "dataset_key"))
            prior_resolved = resolved_sources.setdefault(
                resolved_id, resolved_identity)
            if prior_resolved != resolved_identity and (
                    explicit_id or source.get("profile_id")):
                label = ("Duplicate explicit source id" if explicit_id
                         else "Source id")
                raise ValueError(
                    "%s %s collides with a different source definition"
                    % (label, resolved_id))
            if explicit_id:
                identity = tuple(str(source.get(field) or "") for field in
                                 ("name", "url", "role", "kind",
                                  "profile_id", "dataset_key"))
                normalized_id = source_id(source)
                prior = explicit_sources.setdefault(normalized_id, identity)
                if prior != identity:
                    raise ValueError(
                        "Duplicate explicit source id %s has conflicting definitions"
                        % explicit_id)
            pid = source.get("profile_id")
            dataset_key = source.get("dataset_key")
            if bool(pid) != bool(dataset_key):
                raise ValueError(
                    "Source %s must set profile_id and dataset_key together"
                    % (explicit_id or source["name"]))
            if pid:
                profile = profiles.get(str(pid))
                if profile is None:
                    raise ValueError("Source %s references unknown profile %s"
                                     % (source["name"], pid))
                if access_status(profile) != "public" or \
                        review_status(profile) != "approved":
                    raise ValueError(
                        "Source %s references a profile that is not approved/public"
                        % source["name"])
                if profile.get("source_id") and (
                        not source.get("id")
                        or source_id(source) != str(profile["source_id"])):
                    raise ValueError(
                        "Source %s explicit id must match profile source_id %s"
                        % (source["name"], profile["source_id"]))
                if str(source["url"]).rstrip("/") != \
                        str(profile.get("canonical_url") or "").rstrip("/"):
                    raise ValueError(
                        "Source %s URL does not match profile %s canonical_url"
                        % (source["name"], pid))
                profile_dataset(profile, str(dataset_key))
                referenced_profiles.setdefault(str(pid), []).append(variable)
        key = tuple(str(variable[field]).casefold()
                    for field in ["entity", "name", "period", "scenario",
                                  "basis", "unit", "status"])
        if key in dimensions:
            raise ValueError(
                "Two variables claim the same complete dimensions: %s" % variable["id"])
        dimensions.add(key)
    for pid, linked_variables in referenced_profiles.items():
        profile = profiles[pid]
        profile_text = "\n".join(_leaf_strings(_profile_content(profile)))
        for variable in linked_variables:
            for value in _sensitive_values(variable):
                if _contains_value(profile_text, value):
                    raise ValueError(
                        "Profile %s contains workbook/target value %s for %s"
                        % (pid, value, variable["id"]))


def perturb(variable: dict[str, Any], index: int) -> Any:
    """A plausible-but-wrong value for a distractor record."""
    alternatives = variable.get("alternatives") or []
    if alternatives:
        return alternatives[index % len(alternatives)]
    value = variable["value"]
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + [1, -1, 2, -2, 5, -5][index % 6]
    if isinstance(value, float):
        factor = [0.9, 1.1, 0.96, 1.04, 0.75, 1.25][index % 6]
        candidate = round(value * factor, 12)
        if candidate == value:
            # Multiplication cannot perturb zero, and coarse rounding can leave
            # very small values unchanged. Always emit a distinct stale value.
            direction = [1, -1, 2, -2, 5, -5][index % 6]
            step = max(abs(value) * 0.01, 1e-6)
            candidate = round(value + direction * step, 12)
        return candidate
    date = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", str(value))
    if date:
        year = int(date.group(1)) + [1, -1, 2, -2][index % 4]
        return "%04d-%s-%s" % (year, date.group(2), date.group(3))
    return "%s (%s)" % (value, ["prior", "provisional", "alternative"][index % 3])


def alternate_period(period: str, index: int) -> str:
    match = re.search(r"\b(?:19|20)\d{2}\b", period)
    if not match:
        return period
    year = int(match.group()) + [1, -1, 2, -2][index % 4]
    return period[:match.start()] + str(year) + period[match.end():]


def alternate_unit(unit: str, index: int) -> str:
    """A plausible wrong unit that never contains the true unit as a token
    substring; the server matches dimensions by token containment, so a
    colliding alternate would make a fully-filtered query ambiguous."""
    lower = unit.casefold()
    if lower in {"percent", "%"}:
        return "basis points" if index % 2 else "decimal fraction"
    if "eur" in lower or "\u20ac" in lower:
        return "GBP million"
    if "gbp" in lower or "\u00a3" in lower:
        return "EUR million"
    if "year" in lower:
        return "months"
    if "date" in lower:
        return "period index"
    if "mw" in lower:
        return "GW"
    if "ebitda" in lower:
        return "x EBIT"
    if "method" in lower:
        return "profile code"
    return "nonstandard measure"


def source_id(source: dict[str, Any]) -> str:
    if source.get("id"):
        return slug(str(source["id"]))
    parsed = urlparse(source["url"])
    return slug(parsed.netloc or parsed.path or source["name"])


def display_value(value: Any, unit: str) -> str:
    if isinstance(value, float):
        text = "%.12g" % value
    else:
        text = str(value)
    if str(unit).casefold() == "percent":
        return "%s%%" % text
    return "%s %s" % (text, unit)


def make_record(dataset_id: str, source: str, document: str,
                variable: dict[str, Any], value: Any, row_key: str,
                release: str, published_at: str,
                superseded_by: str | None,
                release_label_value: str | None = None) -> dict[str, Any]:
    row = {
        "id": stable_id("row", dataset_id, row_key),
        "dataset_id": dataset_id,
        "source_id": source,
        "document_id": document,
        "entity": variable["entity"],
        "metric": variable["name"],
        "metric_aliases": variable.get("aliases", []),
        "period": str(variable["period"]),
        "scenario": variable["scenario"],
        "basis": variable["basis"],
        "unit": variable["unit"],
        "status": variable["status"],
        "release": release,
        "published_at": published_at,
        "superseded_by": superseded_by,
        "value": value,
    }
    if release_label_value is not None:
        row["release_label"] = release_label_value
    return row


def make_document(doc_id: str, source: dict[str, Any], row: dict[str, Any],
                  kind: str, published: str, supersedes: str | None = None,
                  superseded_by: str | None = None,
                  field_labels: dict[str, str] | None = None,
                  excerpt: dict[str, str] | None = None) -> dict[str, Any]:
    if superseded_by:
        lineage = ("SUPERSEDED: this release has been replaced by release %s. "
                   "Do not use it for current work." % superseded_by)
    elif supersedes:
        lineage = ("This release supersedes release %s and is the "
                   "authoritative figure." % supersedes)
    else:
        lineage = ("Use this observation only when every dimension matches "
                   "the research question.")
    labels = {
        "entity": "Entity", "metric": "Metric", "period": "Period",
        "scenario": "Scenario", "basis": "Basis", "unit": "Unit",
        "status": "Status", "value": "Reported value",
    }
    labels.update(field_labels or {})
    excerpt_text = ""
    if excerpt:
        excerpt_text = (
            "\n\nShort attributed excerpt — %s (%s): “%s”"
            % (excerpt["attribution"], excerpt["locator"], excerpt["text"].strip()))
    return {
        "id": doc_id,
        "source_id": source_id(source),
        "title": "%s \u2014 %s \u2014 %s (%s)" % (
            row["entity"], row["metric"], row["period"], row["scenario"]),
        "kind": kind,
        "published_at": published,
        "url": "mock://%s/documents/%s" % (source_id(source), doc_id),
        "content": (
            "%s | %s release %s\n\n"
            "%s: %s\n%s: %s\n%s: %s\n"
            "%s: %s\n%s: %s\n%s: %s\n"
            "%s: %s\n%s: %s\n\n"
            "%s Related releases may contain nearby values that are not "
            "interchangeable.%s"
            % (source["name"], row["status"], row["release"],
               labels["entity"], row["entity"],
               labels["metric"], row["metric"], labels["period"], row["period"],
               labels["scenario"], row["scenario"], labels["basis"], row["basis"],
               labels["unit"], row["unit"], labels["status"], row["status"],
               labels["value"], display_value(row["value"], row["unit"]),
               lineage, excerpt_text)
        ),
        "related_dataset_id": row["dataset_id"],
    }


def profile_for(source: dict[str, Any],
                profiles: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    pid = source.get("profile_id")
    return profiles.get(str(pid)) if pid else None


def resolved_profile_id(profile: dict[str, Any]) -> str:
    return str(profile.get("id") or profile["source_id"])


def profiled_dataset(source: dict[str, Any],
                     profiles: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    profile = profile_for(source, profiles)
    if profile is None:
        return None
    return profile_dataset(profile, str(source["dataset_key"]))


def source_description(source: dict[str, Any],
                       profile: dict[str, Any] | None) -> str:
    if profile:
        if profile.get("source_id"):
            parts = []
            if profile.get("terminology"):
                parts.append("Terminology: %s" % ", ".join(
                    profile["terminology"][:6]))
            if profile.get("document_types"):
                parts.append("Publishes %s" % ", ".join(
                    profile["document_types"][:4]))
            if profile.get("release_cadence"):
                parts.append("Release cadence: %s" % profile["release_cadence"])
            if parts:
                return ". ".join(parts) + "."
        architecture = profile.get("information_architecture") or {}
        if isinstance(architecture, dict):
            description = architecture.get("description")
            if isinstance(description, str) and description.strip():
                return description.strip()
        for key in ("source_description", "description"):
            description = profile.get(key)
            if isinstance(description, str) and description.strip():
                return description.strip()
    return source.get(
        "description",
        "Synthetic research collection shaped from %s metadata." % source["name"])


def document_profile(dataset: dict[str, Any] | None,
                     profile: dict[str, Any] | None
                     ) -> tuple[list[str], dict[str, str], list[dict[str, str]]]:
    if not dataset or not profile:
        return [], {}, []
    kinds = dataset.get("document_kinds") or []
    architecture = profile.get("information_architecture") or {}
    if not kinds and isinstance(architecture, dict):
        kinds = architecture.get("document_kinds") or []
    labels = dataset.get("field_labels") or {}
    conventions = dataset.get("field_conventions") or {}
    if not labels and isinstance(conventions, dict):
        if isinstance(conventions.get("labels"), dict):
            labels = conventions["labels"]
        else:
            labels = {
                key: value for key, value in conventions.items()
                if key in {"entity", "metric", "period", "scenario", "basis",
                           "unit", "status", "value"}
                and isinstance(value, str)
            }
    if not labels and isinstance(architecture, dict):
        labels = architecture.get("field_labels") or {}
    return list(kinds), dict(labels), profile_excerpts(profile)


def _shift_months(value: date, months: int) -> date:
    total = value.year * 12 + value.month - 1 + months
    year, month_zero = divmod(total, 12)
    month = month_zero + 1
    month_lengths = [31, 29 if year % 4 == 0 and
                     (year % 100 != 0 or year % 400 == 0) else 28,
                     31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return value.replace(year=year, month=month,
                         day=min(value.day, month_lengths[month - 1]))


def release_dates(variable: dict[str, Any], n_stale: int,
                  dataset: dict[str, Any] | None) -> tuple[list[str], str]:
    supported = str(variable.get("published_at", "2019-12-18"))
    if not dataset or not dataset.get("release_cadence"):
        return ["%d-06-30" % (2017 + k) for k in range(n_stale)], supported
    try:
        current = date.fromisoformat(supported)
    except ValueError as exc:
        raise ValueError("%s has invalid published_at %s"
                         % (variable["id"], supported)) from exc
    cadence = dataset["release_cadence"]
    dates = []
    for distance in range(n_stale, 0, -1):
        if cadence == "daily":
            stale = current - timedelta(days=distance)
        elif cadence == "weekly":
            stale = current - timedelta(days=7 * distance)
        elif cadence == "monthly":
            stale = _shift_months(current, -distance)
        elif cadence == "quarterly":
            stale = _shift_months(current, -3 * distance)
        else:
            stale = _shift_months(current, -12 * distance)
        dates.append(stale.isoformat())
    return dates, supported


def release_label(dataset: dict[str, Any] | None, published: str,
                  period: str, status: str, index: int) -> str | None:
    if not dataset:
        return None
    style = dataset.get("release_label_style")
    if style:
        return style.format(date=published, period=period, status=status, index=index)
    cadence = dataset.get("release_cadence")
    if cadence:
        return "%s — %s" % (dataset["name"], published)
    return None


def build(spec: dict[str, Any], output: Path) -> dict[str, Any]:
    validate_spec(spec)
    runtime = output / "runtime"
    evaluation = output / "eval"
    runtime.mkdir(parents=True, exist_ok=True)
    evaluation.mkdir(parents=True, exist_ok=True)

    rng = random.Random(spec["seed"])
    sources_by_id: dict[str, dict[str, Any]] = {}
    datasets_by_id: dict[str, dict[str, Any]] = {}
    documents: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []
    profiles = profile_catalog(spec)
    distractor_count = max(8, min(int(spec.get("distractors_per_variable", 18)), 100))

    def dimension_key(variable: dict[str, Any]) -> tuple[str, ...]:
        return tuple(str(variable[field]).casefold()
                     for field in ["name", "entity", "period", "scenario",
                                   "basis", "unit", "status"])

    # No distractor may share every required dimension with ANY supported
    # record: a scenario-flipped distractor of one variable must not land on
    # another variable's exact dimensions.
    supported_keys = {dimension_key(variable) for variable in spec["variables"]}

    for variable in spec["variables"]:
        for source in variable["sources"]:
            sid = source_id(source)
            profile = profile_for(source, profiles)
            source_row = {
                "id": sid,
                "name": source["name"],
                "kind": source.get("kind", "publisher"),
                "role": source["role"],
                "origin_url": source["url"],
                "description": source_description(source, profile),
            }
            if profile:
                source_row["profile_id"] = resolved_profile_id(profile)
                excerpts = profile_excerpts(profile)
                if excerpts:
                    source_row["profile_excerpts"] = [
                        {
                            **{key: value for key, value in excerpt.items()
                               if key != "source_url"},
                            "source_id": sid,
                        }
                        for excerpt in excerpts
                    ]
            sources_by_id.setdefault(sid, source_row)
        primary = variable["sources"][0]
        sid = source_id(primary)
        primary_profile = profile_for(primary, profiles)
        dataset_profile = profiled_dataset(primary, profiles)
        if dataset_profile:
            dataset_id = stable_id(
                "dataset", spec["environment_id"], sid, primary["dataset_key"])
        else:
            dataset_id = stable_id("dataset", spec["environment_id"], sid)
        dataset_row = {
            "id": dataset_id,
            "source_id": sid,
            "name": (dataset_profile["name"] if dataset_profile else
                     "%s versioned indicators" % primary["name"]),
            "description": ("Records vary by entity, metric, period, scenario, "
                            "basis, unit, and status, and exist in multiple "
                            "releases. Filter all relevant dimensions, then use "
                            "only the release whose superseded_by is null."),
            "dimensions": ["entity", "metric", "period", "scenario", "basis",
                           "unit", "status"],
        }
        if dataset_profile:
            _, profiled_labels, _ = document_profile(
                dataset_profile, primary_profile)
            dataset_row.update({
                "aliases": dataset_profile.get("aliases", []),
                "field_labels": profiled_labels,
                "field_conventions": dataset_profile.get(
                    "field_conventions", {}),
                "document_kinds": dataset_profile.get("document_kinds", []),
                "release_cadence": dataset_profile.get(
                    "release_cadence", "irregular"),
                "profile_id": resolved_profile_id(primary_profile),
                "dataset_key": primary["dataset_key"],
            })
            if dataset_profile.get("description"):
                dataset_row["description"] = dataset_profile["description"]
        datasets_by_id.setdefault(dataset_id, dataset_row)
        doc_kinds, field_labels, excerpts = document_profile(
            dataset_profile, primary_profile)

        # Provenance chain: stale releases share every dimension with the
        # supported record and are distinguishable only by their supersession
        # links. Exactly one release per dimension tuple is unsuperseded.
        n_stale = max(0, min(int(spec.get("provenance_releases_per_variable", 2)), 6))
        release_ids = [stable_id("rel", spec["environment_id"], variable["id"], k)
                       for k in range(n_stale + 1)]  # last one is the supported release
        stale_dates, supported_date = release_dates(
            variable, n_stale, dataset_profile)

        doc_id = stable_id("doc", spec["environment_id"], variable["id"], "supported")
        supported = make_record(
            dataset_id, sid, doc_id, variable, variable["value"],
            "%s-supported" % variable["id"],
            release=release_ids[-1],
            published_at=supported_date,
            superseded_by=None,
            release_label_value=release_label(
                dataset_profile, supported_date, str(variable["period"]),
                str(variable["status"]), n_stale))
        records.append(supported)
        documents.append(make_document(
            doc_id, primary, supported,
            doc_kinds[0] if doc_kinds else "data-release",
            supported_date,
            supersedes=release_ids[-2] if n_stale else None,
            field_labels=field_labels,
            excerpt=excerpts[0] if excerpts else None))

        for k in range(n_stale):
            successor = release_ids[k + 1]
            stale_doc = stable_id("doc", spec["environment_id"], variable["id"],
                                  "release", k)
            stale = make_record(
                dataset_id, sid, stale_doc, variable,
                perturb(variable, [1, 3, 0, 5, 2, 4][k % 6]),
                "%s-release-%d" % (variable["id"], k),
                release=release_ids[k], published_at=stale_dates[k],
                superseded_by=successor,
                release_label_value=release_label(
                    dataset_profile, stale_dates[k], str(variable["period"]),
                    str(variable["status"]), k))
            records.append(stale)
            documents.append(make_document(
                stale_doc, primary, stale,
                doc_kinds[(k + 1) % len(doc_kinds)]
                if doc_kinds else "assumption-book",
                stale_dates[k], superseded_by=successor,
                field_labels=field_labels,
                excerpt=excerpts[(k + 1) % len(excerpts)] if excerpts else None))

        tasks.append({
            "task_id": variable["id"],
            "prompt": variable["question"],
            "answer": {"value": variable["value"], "unit": variable["unit"],
                       "tolerance": variable.get("tolerance", 0)},
            "required_dimensions": {
                "metric": variable["name"],
                **{field: variable[field]
                   for field in ["entity", "period", "scenario",
                                 "basis", "unit", "status"]},
            },
            "resolution_rule": "match all required dimensions, then use the "
                               "release whose superseded_by is null",
            "evidence": {"source_id": sid, "dataset_id": dataset_id,
                         "record_id": supported["id"], "document_id": doc_id,
                         "release": release_ids[-1]},
            "workbook": variable.get("workbook", {}),
        })

        for index in range(distractor_count):
            altered = deepcopy(variable)
            mode = index % 5
            if mode == 0:
                altered["scenario"] = SCENARIOS[(index + 1) % len(SCENARIOS)]
            elif mode == 1:
                altered["basis"] = BASES[(index + 1) % len(BASES)]
            elif mode == 2:
                changed = alternate_period(str(variable["period"]), index)
                altered["period"] = changed
                if changed == str(variable["period"]):
                    altered["status"] = STATUSES[index % len(STATUSES)]
            elif mode == 3:
                altered["unit"] = alternate_unit(str(variable["unit"]), index)
            else:
                altered["status"] = STATUSES[index % len(STATUSES)]
            if dimension_key(altered) in supported_keys:
                altered["status"] = next(
                    status for status in STATUSES
                    if dimension_key({**altered, "status": status})
                    not in supported_keys)
            alt_source = variable["sources"][index % len(variable["sources"])]
            alt_sid = source_id(alt_source)
            alt_profile = profile_for(alt_source, profiles)
            alt_dataset_profile = profiled_dataset(alt_source, profiles)
            alt_kinds, alt_labels, alt_excerpts = document_profile(
                alt_dataset_profile, alt_profile)
            alt_doc = stable_id("doc", spec["environment_id"], variable["id"],
                                "alternative", index)
            alt_published = "%d-06-30" % (2017 + index % 8)
            alt_record = make_record(
                dataset_id, alt_sid, alt_doc, altered, perturb(variable, index),
                "%s-alternative-%d" % (variable["id"], index),
                release=stable_id("rel", spec["environment_id"], variable["id"],
                                  "alt", index),
                published_at=alt_published, superseded_by=None,
                release_label_value=release_label(
                    dataset_profile, alt_published, str(altered["period"]),
                    str(altered["status"]), index))
            records.append(alt_record)
            documents.append(make_document(
                alt_doc, alt_source, alt_record,
                alt_kinds[index % len(alt_kinds)] if alt_kinds else
                ["archive", "market-note", "technical-annex",
                 "data-release"][index % 4],
                alt_published, field_labels=alt_labels,
                excerpt=alt_excerpts[index % len(alt_excerpts)]
                if alt_excerpts else None))

    rng.shuffle(documents)
    rng.shuffle(records)
    server_config = {
        "name": "%s-research-service" % spec["environment_id"],
        "version": "2.0.0",
        "instructions": ("Discover sources and datasets, search and fetch evidence, "
                         "then filter records by every dimension relevant to the "
                         "question. Broad queries can return conflicting values. "
                         "Records exist in multiple releases: a row whose "
                         "superseded_by field is set has been replaced by the "
                         "named later release and must not be used; only the "
                         "unsuperseded release is authoritative. query_records "
                         "requires at least two filter dimensions and returns at "
                         "most 5 rows per page (follow next_cursor)."),
    }
    write_json(runtime / "server.json", server_config)
    write_json(runtime / "sources.json",
               sorted(sources_by_id.values(), key=lambda row: row["id"]))
    write_json(runtime / "datasets.json",
               sorted(datasets_by_id.values(), key=lambda row: row["id"]))
    write_jsonl(runtime / "documents.jsonl", documents)
    write_jsonl(runtime / "records.jsonl", records)
    write_jsonl(evaluation / "tasks.jsonl", tasks)
    write_json(output / "environment.json", {
        "environment_id": spec["environment_id"], "seed": spec["seed"],
        "tasks": len(tasks), "documents": len(documents), "records": len(records)})
    return {"tasks": len(tasks), "documents": len(documents),
            "records": len(records), "output": str(output)}

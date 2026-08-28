#!/usr/bin/env python3
"""Deterministic assembler for source_profiles.json (stdlib only).

Subcommands:

  init    Write a valid empty envelope (capture object + empty profiles array)
          with correct spec/inventory hashes, atomically.
  merge   Insert/replace per-source profile fragments into an existing
          document, validating each fragment standalone against the exact
          per-profile schema enforced by validate_source_profiles.py.
  rehash  Re-stamp capture-level input hashes after the normalized spec or
          inventory legitimately changed, without touching profiles.

Fragments are files containing exactly ONE JSON object matching the
per-profile schema (see SOURCE_PROFILES.md). Prose, markdown fences, arrays,
or trailing text are rejected with an error naming the fragment file.

Merging is idempotent: merging the same fragment twice yields byte-identical
output. All writes are atomic (temp file in the target directory + fsync +
os.replace).
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ASSEMBLER_VERSION = "1.0"

# Values required verbatim by validate_source_profiles.py's document checks.
TOOL_NAME = "profile-mcp-sources"
AGENT_MODEL = "gpt-5.6-sol-high"
PUBLIC_READ_LIMIT = 3
CANONICALIZATION = "v1"
SCHEMA_VERSION = "1.0"


def _load_validator():
    """Import the sibling validator so schema checks can never drift."""
    path = Path(__file__).resolve().parent / "validate_source_profiles.py"
    spec = importlib.util.spec_from_file_location("validate_source_profiles", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import validator at {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V = _load_validator()


class CommandError(Exception):
    """Usage or I/O failure (exit code 2)."""


class RejectionError(Exception):
    """Validation rejection; carries per-field errors (exit code 1)."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def utc_now_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def render_document(document: Any) -> str:
    return json.dumps(document, ensure_ascii=False, indent=2) + "\n"


def write_json_atomic(path: Path, document: Any) -> None:
    """Write via temp file in the same directory, fsync, then os.replace."""
    rendered = render_document(document)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    )
    try:
        with handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


def optional_file_sha256(path: Path | None, label: str) -> str | None:
    if path is None:
        return None
    if not path.is_file():
        raise CommandError(f"{label} file not found: {path}")
    return V.file_sha256(path)


def load_document(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise CommandError(f"document not found: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CommandError(f"{path}: document is malformed JSON: {exc}")
    if (
        not isinstance(document, dict)
        or not isinstance(document.get("capture"), dict)
        or not isinstance(document.get("profiles"), list)
    ):
        raise CommandError(f"{path}: document needs capture object and profiles array")
    return document


def stamp_profile_hashes(profiles: list[Any]) -> None:
    """Recompute per-profile evidence hashes, matching stamp_hashes()."""
    for profile in profiles:
        if not isinstance(profile, dict) or not isinstance(
            profile.get("capture"), dict
        ):
            continue
        if profile.get("status") == "profiled" and isinstance(
            profile.get("evidence"), dict
        ):
            profile["capture"]["evidence_sha256"] = V.sha256_bytes(
                V.canonical_json(profile["evidence"])
            )
        elif profile.get("status") == "skipped":
            profile["capture"]["evidence_sha256"] = None


def stamp_profiles_sha256(document: dict[str, Any]) -> None:
    document["capture"]["profiles_sha256"] = V.sha256_bytes(
        V.canonical_json(document["profiles"])
    )


def load_fragment(path: Path) -> dict[str, Any]:
    """Read exactly one JSON object; reject prose, arrays, or trailing text."""
    if not path.is_file():
        raise RejectionError([f"{path}: fragment file not found"])
    text = path.read_text(encoding="utf-8")
    try:
        fragment = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RejectionError(
            [
                f"{path}: fragment must be exactly one JSON object with no "
                f"surrounding prose: {exc}"
            ]
        )
    if not isinstance(fragment, dict):
        raise RejectionError(
            [
                f"{path}: fragment must be a single per-profile JSON object, "
                f"got {type(fragment).__name__}"
            ]
        )
    return fragment


def normalize_fragment(fragment: dict[str, Any]) -> dict[str, Any]:
    """Stamp the evidence hash the merge would apply, so subagents may send
    "evidence_sha256": null and validation still checks everything else."""
    normalized = copy.deepcopy(fragment)
    if isinstance(normalized.get("capture"), dict):
        if normalized.get("status") == "profiled" and isinstance(
            normalized.get("evidence"), dict
        ):
            normalized["capture"]["evidence_sha256"] = V.sha256_bytes(
                V.canonical_json(normalized["evidence"])
            )
        elif normalized.get("status") == "skipped":
            normalized["capture"]["evidence_sha256"] = None
    return normalized


def validate_fragment(path: Path, fragment: dict[str, Any]) -> dict[str, Any]:
    """Validate one fragment standalone with the real per-profile validator.

    Returns the normalized (hash-stamped) profile ready for insertion.
    Raises RejectionError with field-level errors naming the fragment file.
    """
    normalized = normalize_fragment(fragment)
    raw_errors: list[str] = []
    V.validate_profile(normalized, 0, raw_errors, forbidden=set())
    if raw_errors:
        prefix = "$.profiles[0]"
        errors = [
            f"{path}: " + (
                "$" + error[len(prefix):] if error.startswith(prefix) else error
            )
            for error in raw_errors
        ]
        raise RejectionError(errors)
    return normalized


def cmd_init(args: argparse.Namespace) -> int:
    out: Path = args.out
    if out.exists() and not args.force:
        raise CommandError(f"refusing to overwrite existing document: {out} "
                           f"(use --force to replace)")
    created_at = args.created_at or utc_now_timestamp()
    if not V.is_timestamp(created_at):
        raise CommandError(
            f"--created-at must be a UTC timestamp like 2026-08-27T00:00:00Z, "
            f"got {created_at!r}"
        )
    document = {
        "schema_version": SCHEMA_VERSION,
        "capture": {
            "created_at": created_at,
            "tool": TOOL_NAME,
            "agent_model": AGENT_MODEL,
            "public_read_limit_per_source": PUBLIC_READ_LIMIT,
            "canonicalization": CANONICALIZATION,
            "inventory_sha256": optional_file_sha256(args.inventory, "inventory"),
            "spec_sha256": optional_file_sha256(args.spec, "spec"),
            "profiles_sha256": "",
        },
        "profiles": [],
    }
    stamp_profiles_sha256(document)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(out, document)
    print(f"OK init: wrote empty envelope to {out}")
    return 0


def cmd_merge(args: argparse.Namespace) -> int:
    document = load_document(args.doc)

    # Validate every fragment before touching the document; reject all-or-nothing.
    accepted: list[dict[str, Any]] = []
    errors: list[str] = []
    for fragment_path in args.fragment:
        try:
            fragment = load_fragment(fragment_path)
            accepted.append(validate_fragment(fragment_path, fragment))
        except RejectionError as exc:
            errors.extend(exc.errors)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        print(f"REJECTED ({len(errors)} errors); document not modified")
        return 1

    # Guard the document-level uniqueness rule the validator enforces:
    # a canonical URL may not appear under two different source IDs.
    url_owner: dict[str, str] = {}
    for profile in document["profiles"]:
        if isinstance(profile, dict):
            url = profile.get("canonical_url")
            source_id = profile.get("source_id")
            if isinstance(url, str) and isinstance(source_id, str):
                url_owner[url] = source_id
    for fragment_path, profile in zip(args.fragment, accepted):
        url = profile["canonical_url"]
        owner = url_owner.get(url)
        if owner is not None and owner != profile["source_id"]:
            errors.append(
                f"{fragment_path}: $.canonical_url: {url!r} already belongs to "
                f"source_id {owner!r}"
            )
        else:
            url_owner[url] = profile["source_id"]
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        print(f"REJECTED ({len(errors)} errors); document not modified")
        return 1

    # Insert or replace by source_id, then keep a deterministic stable order.
    by_id: dict[str, Any] = {}
    for profile in document["profiles"]:
        if isinstance(profile, dict) and isinstance(profile.get("source_id"), str):
            by_id[profile["source_id"]] = profile
    replaced = 0
    for profile in accepted:
        if profile["source_id"] in by_id:
            replaced += 1
        by_id[profile["source_id"]] = profile
    document["profiles"] = [by_id[key] for key in sorted(by_id)]

    stamp_profile_hashes(document["profiles"])
    stamp_profiles_sha256(document)
    write_json_atomic(args.doc, document)
    print(
        f"OK merge: {len(accepted)} fragment(s) applied "
        f"({replaced} replaced); {len(document['profiles'])} profiles in {args.doc}"
    )
    return 0


def cmd_rehash(args: argparse.Namespace) -> int:
    document = load_document(args.doc)
    document["capture"]["inventory_sha256"] = optional_file_sha256(
        args.inventory, "inventory"
    )
    document["capture"]["spec_sha256"] = optional_file_sha256(args.spec, "spec")
    stamp_profiles_sha256(document)
    write_json_atomic(args.doc, document)
    print(f"OK rehash: re-stamped capture hashes in {args.doc}")
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {ASSEMBLER_VERSION}"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser(
        "init", help="write a valid empty envelope atomically"
    )
    init_parser.add_argument("--out", type=Path, required=True)
    init_parser.add_argument("--spec", type=Path, help="normalized spec JSON")
    init_parser.add_argument("--inventory", type=Path, help="inventory JSON")
    init_parser.add_argument(
        "--created-at",
        help="UTC timestamp override for reproducible output "
        "(default: current UTC time)",
    )
    init_parser.add_argument(
        "--force", action="store_true", help="overwrite an existing document"
    )
    init_parser.set_defaults(func=cmd_init)

    merge_parser = subparsers.add_parser(
        "merge", help="validate and insert/replace per-source fragments"
    )
    merge_parser.add_argument("--doc", type=Path, required=True)
    merge_parser.add_argument(
        "--fragment",
        type=Path,
        action="append",
        required=True,
        help="path to a file containing exactly one per-profile JSON object "
        "(repeatable)",
    )
    merge_parser.set_defaults(func=cmd_merge)

    rehash_parser = subparsers.add_parser(
        "rehash",
        help="re-stamp capture input hashes after a legitimate spec/inventory "
        "change, without touching profiles",
    )
    rehash_parser.add_argument("--doc", type=Path, required=True)
    rehash_parser.add_argument("--spec", type=Path, help="normalized spec JSON")
    rehash_parser.add_argument("--inventory", type=Path, help="inventory JSON")
    rehash_parser.set_defaults(func=cmd_rehash)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        return args.func(args)
    except CommandError as exc:
        print(f"error: {exc}")
        return 2
    except OSError as exc:
        print(f"error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Write or verify BASELINE_MANIFEST.json for the FCP_recursive_v1 tree.

The hash rule is the one stated inside the manifest itself
(``tree_hash_definition``): SHA-256 of compact, key-sorted JSON mapping each
relative regular-file path to its SHA-256. Excluded: the manifest itself,
anything under ``__pycache__/`` or ``.pytest_cache/``, any ``*.pyc``, and the
``.venv``, ``.env``, and ``.git`` trees. Symlinks are skipped.

Usage:
  baseline_manifest.py verify [--repo DIR]
  baseline_manifest.py write  [--repo DIR] [--note TEXT]

``verify`` exits 0 and prints ``TREEOK <hash> <count>`` when the tree matches,
exits 1 with ``TREE_FAIL`` otherwise. ``write`` refreshes ``tree_sha256`` and
``file_count`` and appends a changelog entry; run it only after an intended
local edit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

MANIFEST_NAME = "BASELINE_MANIFEST.json"
EXCLUDED_DIRS = {"__pycache__", ".pytest_cache", ".venv", ".git"}
EXCLUDED_TOP = {".env"}


def tree_files(repo: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for path in sorted(repo.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(repo).as_posix()
        parts = rel.split("/")
        if rel == MANIFEST_NAME or rel.endswith(".pyc"):
            continue
        if parts[0] in EXCLUDED_TOP or EXCLUDED_DIRS.intersection(parts):
            continue
        files[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def tree_hash(files: dict[str, str]) -> str:
    canonical = json.dumps(files, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("action", choices=("verify", "write"))
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parents[4],
        help="FCP_recursive_v1 root (default: derived from this script's path)",
    )
    parser.add_argument("--note", default="", help="changelog note for write")
    args = parser.parse_args()

    repo = args.repo.resolve()
    manifest_path = repo / MANIFEST_NAME
    if not manifest_path.is_file():
        print(f"TREE_FAIL no {MANIFEST_NAME} in {repo}", file=sys.stderr)
        return 1
    manifest = json.loads(manifest_path.read_text())
    files = tree_files(repo)
    actual = tree_hash(files)

    if args.action == "verify":
        if actual == manifest.get("tree_sha256") and len(files) == manifest.get("file_count"):
            print(f"TREEOK {actual} {len(files)}")
            return 0
        print(
            f"TREE_FAIL count={len(files)} expected={manifest.get('file_count')} "
            f"hash={actual} expected={manifest.get('tree_sha256')}",
            file=sys.stderr,
        )
        return 1

    previous = manifest.get("tree_sha256")
    manifest["tree_sha256"] = actual
    manifest["file_count"] = len(files)
    changelog = manifest.get("changelog")
    if not isinstance(changelog, dict):
        changelog = {}
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    changelog[stamp] = args.note or f"baseline_manifest.py write (was {previous})"
    manifest["changelog"] = changelog
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=False) + "\n")
    print(f"TREEWRITTEN {actual} {len(files)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

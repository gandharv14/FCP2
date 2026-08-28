#!/usr/bin/env python3
"""Self-contained tests for assemble_profiles.py (stdlib only, temp dirs).

Run: python3 test_assemble_profiles.py
"""

from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent


def _import(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


assembler = _import("assemble_profiles")
validator = _import("validate_source_profiles")

CREATED_AT = "2026-08-27T00:00:00Z"


def run_cli(module, argv: list[str]) -> tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = module.main(argv)
    return code, buffer.getvalue()


def profiled_fragment() -> dict:
    """A valid per-profile object; evidence_sha256 left null for merge to stamp."""
    timestamp = "2026-08-27T00:00:00Z"
    return {
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
            "evidence_sha256": None,
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


def skipped_fragment() -> dict:
    return {
        "source_id": "gated-portal",
        "source_name": "Gated Portal",
        "canonical_url": "https://portal.example.org/",
        "status": "skipped",
        "skip_reason": "unreachable",
        "capture": {
            "attempted_at": "2026-08-27T00:00:00Z",
            "read_count": 0,
            "http_status": None,
            "final_url": None,
            "content_type": None,
            "evidence_sha256": None,
        },
        "terminology": [],
        "dataset_names": [],
        "field_conventions": [],
        "document_types": [],
        "release_cadence": None,
        "evidence": {"attributions": [], "excerpts": []},
        "review": {
            "status": "pending",
            "reviewer": None,
            "reviewed_at": None,
            "notes": None,
        },
    }


class AssembleProfilesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.spec = self.dir / "normalized.json"
        self.inventory = self.dir / "inventory.json"
        self.spec.write_text(
            json.dumps({"variables": [{"name": "discount-rate", "value": 123.45}]}),
            encoding="utf-8",
        )
        self.inventory.write_text(
            json.dumps({"rows": [{"raw_value": 999}]}), encoding="utf-8"
        )
        self.doc = self.dir / "source_profiles.json"

    def write_fragment(self, name: str, payload) -> Path:
        path = self.dir / name
        text = payload if isinstance(payload, str) else json.dumps(payload, indent=2)
        path.write_text(text, encoding="utf-8")
        return path

    def init_doc(self) -> None:
        code, output = run_cli(
            assembler,
            [
                "init",
                "--out",
                str(self.doc),
                "--spec",
                str(self.spec),
                "--inventory",
                str(self.inventory),
                "--created-at",
                CREATED_AT,
            ],
        )
        self.assertEqual(code, 0, output)

    def run_validator(self) -> tuple[int, str]:
        return run_cli(
            validator,
            [
                str(self.doc),
                "--spec",
                str(self.spec),
                "--inventory",
                str(self.inventory),
            ],
        )

    # -- init ---------------------------------------------------------------

    def test_init_envelope_passes_real_validator(self) -> None:
        self.init_doc()
        document = json.loads(self.doc.read_text(encoding="utf-8"))
        self.assertEqual(document["profiles"], [])
        self.assertEqual(document["capture"]["tool"], "profile-mcp-sources")
        code, output = self.run_validator()
        self.assertEqual(code, 0, output)
        self.assertIn("OK (0 profiles", output)

    def test_init_is_deterministic_and_refuses_overwrite(self) -> None:
        self.init_doc()
        first = self.doc.read_bytes()
        code, output = run_cli(
            assembler, ["init", "--out", str(self.doc), "--created-at", CREATED_AT]
        )
        self.assertEqual(code, 2)
        self.assertIn("refusing to overwrite", output)
        self.assertEqual(self.doc.read_bytes(), first)
        other = self.dir / "again.json"
        code, _ = run_cli(
            assembler,
            [
                "init",
                "--out",
                str(other),
                "--spec",
                str(self.spec),
                "--inventory",
                str(self.inventory),
                "--created-at",
                CREATED_AT,
            ],
        )
        self.assertEqual(code, 0)
        self.assertEqual(other.read_bytes(), first)

    # -- merge: acceptance and idempotency -----------------------------------

    def test_merge_accepts_valid_fragment_and_is_idempotent(self) -> None:
        self.init_doc()
        fragment = self.write_fragment("frag.json", profiled_fragment())
        code, output = run_cli(
            assembler, ["merge", "--doc", str(self.doc), "--fragment", str(fragment)]
        )
        self.assertEqual(code, 0, output)
        first = self.doc.read_bytes()
        code, _ = self.run_validator()
        self.assertEqual(code, 0)
        code, output = run_cli(
            assembler, ["merge", "--doc", str(self.doc), "--fragment", str(fragment)]
        )
        self.assertEqual(code, 0, output)
        self.assertEqual(self.doc.read_bytes(), first, "merge is not idempotent")

    # -- merge: rejections ----------------------------------------------------

    def assert_rejected(self, fragment_path: Path, expected_substring: str) -> None:
        before = self.doc.read_bytes()
        code, output = run_cli(
            assembler,
            ["merge", "--doc", str(self.doc), "--fragment", str(fragment_path)],
        )
        self.assertEqual(code, 1, output)
        self.assertIn(fragment_path.name, output)
        self.assertIn(expected_substring, output)
        self.assertEqual(self.doc.read_bytes(), before, "rejected merge modified doc")

    def test_merge_rejects_extra_key(self) -> None:
        self.init_doc()
        bad = profiled_fragment()
        bad["confidence"] = "high"
        path = self.write_fragment("extra-key.json", bad)
        self.assert_rejected(path, "unexpected key 'confidence'")

    def test_merge_rejects_bad_skip_reason(self) -> None:
        self.init_doc()
        bad = skipped_fragment()
        bad["skip_reason"] = "server_down"
        path = self.write_fragment("bad-skip.json", bad)
        self.assert_rejected(path, "$.skip_reason: invalid reason")

    def test_merge_rejects_oversized_excerpt(self) -> None:
        self.init_doc()
        bad = profiled_fragment()
        bad["evidence"]["excerpts"][0]["text"] = "x" * 241
        path = self.write_fragment("long-excerpt.json", bad)
        self.assert_rejected(path, "$.evidence.excerpts[0].text")

    def test_merge_rejects_prose_wrapped_json(self) -> None:
        self.init_doc()
        prose = "Here is the profile you asked for:\n" + json.dumps(
            profiled_fragment()
        )
        path = self.write_fragment("prose.json", prose)
        self.assert_rejected(path, "exactly one JSON object")

    def test_merge_rejects_trailing_prose(self) -> None:
        self.init_doc()
        trailing = json.dumps(profiled_fragment()) + "\nAll done!\n"
        path = self.write_fragment("trailing.json", trailing)
        self.assert_rejected(path, "Extra data")

    def test_merge_rejects_json_array(self) -> None:
        self.init_doc()
        path = self.write_fragment("array.json", [profiled_fragment()])
        self.assert_rejected(path, "single per-profile JSON object")

    def test_merge_rejects_canonical_url_collision(self) -> None:
        self.init_doc()
        first = self.write_fragment("first.json", profiled_fragment())
        code, _ = run_cli(
            assembler, ["merge", "--doc", str(self.doc), "--fragment", str(first)]
        )
        self.assertEqual(code, 0)
        clash = profiled_fragment()
        clash["source_id"] = "different-slug"
        path = self.write_fragment("clash.json", clash)
        self.assert_rejected(path, "already belongs to source_id")

    # -- rehash ----------------------------------------------------------------

    def test_rehash_fixes_stale_spec_hash(self) -> None:
        self.init_doc()
        fragment = self.write_fragment("frag.json", profiled_fragment())
        code, _ = run_cli(
            assembler, ["merge", "--doc", str(self.doc), "--fragment", str(fragment)]
        )
        self.assertEqual(code, 0)
        # Legitimate re-normalization changes the spec bytes (the 0453 case).
        self.spec.write_text(
            json.dumps({"variables": [{"name": "discount-rate", "value": 123.46}]}),
            encoding="utf-8",
        )
        code, output = self.run_validator()
        self.assertEqual(code, 1)
        self.assertIn("$.capture.spec_sha256: input file hash mismatch", output)
        profiles_before = json.loads(self.doc.read_text(encoding="utf-8"))["profiles"]
        code, output = run_cli(
            assembler,
            [
                "rehash",
                "--doc",
                str(self.doc),
                "--spec",
                str(self.spec),
                "--inventory",
                str(self.inventory),
            ],
        )
        self.assertEqual(code, 0, output)
        document = json.loads(self.doc.read_text(encoding="utf-8"))
        self.assertEqual(document["profiles"], profiles_before, "rehash touched profiles")
        code, output = self.run_validator()
        self.assertEqual(code, 0, output)

    # -- full roundtrip ----------------------------------------------------------

    def test_full_init_merge_validate_roundtrip(self) -> None:
        self.init_doc()
        frag_a = self.write_fragment("profiled.json", profiled_fragment())
        frag_b = self.write_fragment("skipped.json", skipped_fragment())
        code, output = run_cli(
            assembler,
            [
                "merge",
                "--doc",
                str(self.doc),
                "--fragment",
                str(frag_a),
                "--fragment",
                str(frag_b),
            ],
        )
        self.assertEqual(code, 0, output)
        document = json.loads(self.doc.read_text(encoding="utf-8"))
        self.assertEqual(
            [profile["source_id"] for profile in document["profiles"]],
            ["example-statistics", "gated-portal"],
        )
        code, output = self.run_validator()
        self.assertEqual(code, 0, output)
        self.assertIn("OK (2 profiles", output)

    def test_fragment_evidence_hash_matches_validator_semantics(self) -> None:
        self.init_doc()
        fragment = self.write_fragment("frag.json", profiled_fragment())
        code, _ = run_cli(
            assembler, ["merge", "--doc", str(self.doc), "--fragment", str(fragment)]
        )
        self.assertEqual(code, 0)
        document = json.loads(self.doc.read_text(encoding="utf-8"))
        profile = document["profiles"][0]
        expected = validator.sha256_bytes(
            validator.canonical_json(profile["evidence"])
        )
        self.assertEqual(profile["capture"]["evidence_sha256"], expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)

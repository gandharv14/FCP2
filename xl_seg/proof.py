"""Shared readers for the stabilized strict-proof contract."""

from __future__ import annotations

import json
from pathlib import Path


def load_contract(seg_dir):
    """Return a stabilized proof graph, or ``None`` for legacy/failed runs."""
    segments = Path(seg_dir) / "segments.json"
    if not segments.is_file():
        return None
    proof = json.loads(segments.read_text(encoding="utf-8")).get("proof")
    if not isinstance(proof, dict):
        return None
    if not (proof.get("closure") or {}).get("stabilized"):
        return None
    return proof

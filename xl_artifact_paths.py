#!/usr/bin/env python3
"""Locate workbook artifacts that may be saved as ``.xlsx`` or ``.xlsm``.

``xl_input_mask.py`` names its ``<id>-inputs`` artifact after the golden
source's suffix, so ``.xlsm`` workbooks produce ``.xlsm`` inputs. Every stage
that used to hardcode ``.xlsx`` resolves through here instead. ``.xlsx`` wins
when both suffixes exist, keeping established workbooks byte-for-byte
identical in behavior.
"""

from __future__ import annotations

from pathlib import Path

WORKBOOK_SUFFIXES = (".xlsx", ".xlsm")


def resolve_workbook_artifact(directory, workbook_id, pattern="%s"):
    """Existing ``pattern % workbook_id`` artifact under ``directory``.

    ``pattern`` is a %-style pattern for the file stem, e.g. ``"%s-inputs"``
    for masked inputs or the default ``"%s"`` for golden sources. Tries
    ``.xlsx`` before ``.xlsm``; when neither file exists the ``.xlsx``
    candidate is returned unchanged so callers fail with the same error they
    raised before ``.xlsm`` support existed.
    """
    base = Path(directory)
    stem = pattern % workbook_id
    for suffix in WORKBOOK_SUFFIXES:
        candidate = base / (stem + suffix)
        if candidate.exists():
            return candidate
    return base / (stem + WORKBOOK_SUFFIXES[0])

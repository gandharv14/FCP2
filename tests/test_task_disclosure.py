from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
DISCLOSE_PATH = (
    ROOT / ".cursor" / "skills" / "task-disclosure" / "scripts" / "disclose.py"
)


def _load_disclose():
    spec = importlib.util.spec_from_file_location("task_disclose", DISCLOSE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_expand_method_band_passes_the_expanded_run_to_make_band(
    monkeypatch,
) -> None:
    disclose = _load_disclose()
    gold = SimpleNamespace(formula={
        "Sheet!A1": "=A2",
        "Sheet!B1": "=B2",
        "Sheet!C1": "=C2",
    })
    delivered = SimpleNamespace(has=lambda _key: False)
    captured = {}

    monkeypatch.setattr(disclose, "r1c1ish", lambda *_args: "same-pattern")

    def capture(_gold, sheet, row, run, full=None):
        captured.update(sheet=sheet, row=row, run=run, full=full)
        return {"expanded": True}

    monkeypatch.setattr(disclose, "make_band", capture)

    result = disclose.expand_method_band(
        gold,
        delivered,
        {
            "sheet": "Sheet",
            "row": 1,
            "cell_keys": ["Sheet!B1"],
            "pattern": "same-pattern",
        },
    )

    assert result == {"expanded": True}
    assert captured == {
        "sheet": "Sheet",
        "row": 1,
        "run": [
            (1, "Sheet!A1"),
            (2, "Sheet!B1"),
            (3, "Sheet!C1"),
        ],
        "full": None,
    }


def test_disclosure_quotes_preserve_visible_label_text() -> None:
    disclose = _load_disclose()
    labels = [
        "Revenue",
        "Shareholders' Equity",
        'Ad Cost Per Thousand Impressions ("CPM")',
        'Customer "A"\'s Revenue',
    ]

    for label in labels:
        assert disclose._unquote_label(disclose.q(label)) == label


def test_vertical_singletons_with_different_row_labels_stay_separate(
    monkeypatch,
) -> None:
    disclose = _load_disclose()
    labels = {
        "Analysis!D16": "Term loan",
        "Analysis!D17": "Senior unsecured note",
    }
    gold = SimpleNamespace(row_label=labels.__getitem__)
    bands = [
        {
            "band": "Analysis!D16",
            "cell_keys": ["Analysis!D16"],
            "stated_cell_keys": ["Analysis!D16"],
        },
        {
            "band": "Analysis!D17",
            "cell_keys": ["Analysis!D17"],
            "stated_cell_keys": ["Analysis!D17"],
        },
    ]
    monkeypatch.setattr(disclose, "copy_equivalent", lambda *_args: True)
    monkeypatch.setattr(
        disclose,
        "full_copied_scope_vertical",
        lambda _gold, keys, _targets: keys,
    )

    merged = disclose.merge_vertical_singletons(gold, bands)

    assert merged == bands


def test_method_band_splits_cells_with_different_row_labels() -> None:
    disclose = _load_disclose()
    labels = {
        "Analysis!D16": "Term loan",
        "Analysis!D17": "Senior unsecured note",
    }
    gold = SimpleNamespace(
        formula={
            "Analysis!D16": "=E16*$D$24*(H16/100)",
            "Analysis!D17": "=E17*$D$24*(H17/100)",
        },
        value={"Analysis!D16": 1, "Analysis!D17": 2},
        row_label=labels.__getitem__,
    )
    band = {
        "band": "Analysis!D16:D17",
        "sheet": "Analysis",
        "row": 16,
        "cell_keys": ["Analysis!D16"],
        "stated_cell_keys": ["Analysis!D16", "Analysis!D17"],
        "label": "Term loan",
    }

    split = disclose.method_subbands(gold, band)

    assert [item["band"] for item in split] == [
        "Analysis!D16",
        "Analysis!D17",
    ]
    assert [item["label"] for item in split] == [
        "Term loan",
        "Senior unsecured note",
    ]


def test_faithcheck_accepts_complete_heterogeneous_scope_partition() -> None:
    disclose = _load_disclose()
    labels = {
        "Analysis!D16": "Term loan",
        "Analysis!D17": "Senior unsecured note",
    }
    gold = SimpleNamespace(row_label=labels.__getitem__)
    records = [
        {
            "entry": "method_debt_movement",
            "cells": ["Analysis!D16"],
            "cell_keys": ["Analysis!D16"],
            "fields": {"label": '"Term loan"'},
        },
        {
            "entry": "method_debt_movement",
            "cells": ["Analysis!D17"],
            "cell_keys": ["Analysis!D17"],
            "fields": {"label": '"Senior unsecured note"'},
        },
    ]

    assert disclose._heterogeneous_scope_partitioned(
        gold,
        ["Analysis!D16", "Analysis!D17"],
        records,
        "method_debt_movement",
    )


def test_compensation_type_markers_are_not_row_names() -> None:
    disclose = _load_disclose()
    book = object.__new__(disclose.Book)

    assert not book.is_row_name("Cost Assumptions Matrix", 5, "Salary Only")
    assert not book.is_row_name("Cost Assumptions Matrix", 5, "Hourly Only")
    assert book.is_row_name("Cost Assumptions Matrix", 2, "District Managers")


def test_ungraded_row_label_year_is_licensed_from_target_collision(
    tmp_path: Path,
) -> None:
    disclose = _load_disclose()
    task = tmp_path / "0167-outputs"
    (task / "tests").mkdir(parents=True)
    (task / "tests" / "answer_key.json").write_text(
        json.dumps({"targets": {"Overview!L12": 2013}}),
        encoding="utf-8",
    )
    record = {
        "entry": "source_selection",
        "family": "source_selection",
        "value": "source",
        "disposition": "disclosed",
        "source": "convention_detector",
        "cell_keys": ["Overview!F55"],
        "fields": {
            "label": '"Adjusted EBITDA (FY 2013)"',
            "ingredient": 'cell LBO!X20 on the row labelled "Adjusted EBITDA"',
        },
        "leak_flag": False,
    }

    allowed = disclose.trusted_row_label_literal_counts([record], task)

    assert allowed[2013.0] == 1
    assert disclose.audit_text(
        disclose.render_sentence(record),
        task,
        records=[record],
    ) == []

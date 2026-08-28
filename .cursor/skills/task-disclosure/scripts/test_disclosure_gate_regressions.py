"""Regression tests for disclosure faithfulness and visibility gates."""

from __future__ import annotations

from pathlib import Path

import openpyxl

import disclose


def build_stake_workbook(path: Path) -> None:
    workbook = openpyxl.Workbook()
    assumptions = workbook.active
    assumptions.title = "LBO Assumptions"
    assumptions["B6"], assumptions["C6"] = "Enterprise Value", 100
    assumptions["B7"], assumptions["C7"] = "Equity Stake Acquired", 0.85
    assumptions["B8"], assumptions["C8"] = "Seller Rollover Equity", "=1-C7"
    assumptions["B9"], assumptions["C9"] = "Purchase Price", "=C6*C7"
    assumptions["B10"], assumptions["C10"] = "Transaction Fees", 2
    assumptions["B11"], assumptions["C11"] = "Financing Fees", 1
    assumptions["B12"], assumptions["C12"] = "Total Uses", "=C9+C10+C11"

    returns = workbook.create_sheet("Returns Analysis")
    returns["B20"], returns["C20"] = "GP Co-invest %", 0.05
    returns["B33"] = "LP Investment"
    returns["D33"] = "='LBO Assumptions'!C7*(1-$C$20)"
    returns["B34"] = "GP Co-invest"
    returns["D34"] = "='LBO Assumptions'!C7*$C$20"
    workbook.save(path)


def test_stake_scaling_requires_complete_direct_multiplication(tmp_path: Path) -> None:
    path = tmp_path / "stake.xlsx"
    build_stake_workbook(path)
    gold = disclose.Book(path)
    scope = {
        "LBO Assumptions!C8",
        "LBO Assumptions!C9",
        "LBO Assumptions!C12",
        "Returns Analysis!D33",
        "Returns Analysis!D34",
    }

    records = disclose.detect_stake_scaling(gold, scope)

    assert {record["cell_keys"][0] for record in records} == {
        "LBO Assumptions!C9",
        "Returns Analysis!D34",
    }
    rendered = {record["cell_keys"][0]: disclose.render_sentence(record)
                for record in records}
    assert "Enterprise Value" in rendered["LBO Assumptions!C9"]
    assert "Equity Stake Acquired" in rendered["LBO Assumptions!C9"]
    assert "GP Co-invest %" in rendered["Returns Analysis!D34"]
    assert "Equity Stake Acquired" in rendered["Returns Analysis!D34"]


def test_visible_populated_but_unread_record_is_reviewer_only() -> None:
    record = {
        "entry": "row_populated",
        "value": "populated_but_unread",
        "disposition": "pending",
        "cell_keys": ["Assumptions!C26"],
        "fields": {"label": '"Days per Quarter"'},
    }

    result = disclose.apply_ship_when(
        [record], {"gold": None, "delivered": None, "targets": set()}
    )[0]

    assert result["disposition"] == "suppressed"
    assert "remain visible" in result["declined_reason"]


def test_agent_records_keep_cells_for_blankness_verification() -> None:
    record = {
        "entry": "row_populated",
        "value": "unused",
        "disposition": "disclosed",
        "cell_keys": ["Model!B2"],
        "fields": {"label": '"Unused row"'},
        "leak_flag": False,
    }

    shipped = disclose.agent_records([record])

    assert shipped[0]["cell_keys"] == ["Model!B2"]

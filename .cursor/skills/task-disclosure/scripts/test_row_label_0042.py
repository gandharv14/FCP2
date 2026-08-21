#!/usr/bin/env python3
"""0042 Summary side-by-side blocks: nearest-text label, not left-hand table."""

from __future__ import annotations

from pathlib import Path

import disclose


GOLDEN = Path("/Users/henryhu/Documents/GDM_FCP/FCP Workbooks/batch-flat/0042.xlsx")


def main() -> int:
    gold = disclose.Book(GOLDEN)
    cases = {
        "Summary!K9": "Asset Management Fee",
        "Summary!I9": "Asset Management Fee",
        "Summary!H10": "Garage spot x20",
        "Summary!C7": "# of Keys",
        "Summary!H9": "Courtyard $/sf",
        "Summary!F9": "Parking Spaces on-site (#)",
        "Operations!C18": "Total Revenue",
        "Operations!C146": "Asset Management Fee",
    }
    failed = []
    for cell, want in cases.items():
        got = gold.row_label(cell)
        ok = got == want
        print(("ok" if ok else "FAIL"), cell, "->", repr(got), "want", repr(want))
        if not ok:
            failed.append(cell)

    phrase = disclose.ingredient_phrase(
        gold,
        gold.formula["Operations!C146"],
        ["Operations!B146", "Operations!C146"],
        own_label="Asset Management Fee",
    )
    print("ingredient AMF", phrase)
    if "Parking Spaces" in phrase:
        failed.append("ingredient-still-parking")
    if "Asset Management Fee" not in phrase:
        failed.append("ingredient-missing-amf")
    if "Total Revenue" not in phrase:
        failed.append("ingredient-missing-revenue")

    parking = disclose.ingredient_phrase(
        gold,
        gold.formula["Operations!C53"],
        ["Operations!B53", "Operations!C53"],
        own_label="Parking",
    )
    print("ingredient Parking", parking)
    if "Parking (SqFt)" in parking:
        failed.append("parking-still-sqft")
    if "Garage spot x20" not in parking or "# of Keys" not in parking:
        failed.append("parking-wrong-drivers")

    if failed:
        raise SystemExit("failed: " + ", ".join(failed))
    print("0042 row_label regressions passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

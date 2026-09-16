"""A full denominator may retain aliases; an admitted source must be unique."""
from dataclasses import replace
from pathlib import Path

import pytest

from test_harbor_admission import setup
from scripts.corpus import inventory
from scripts.batch import harbor_admission as A


def with_unrelated_aliases(setup):
    _, policy, history, source, root = setup
    # ZIP readers accept a comment/trailing bytes. No source is modified.
    (source.parent / "0002.xlsx").write_bytes(source.read_bytes() + b"unselected source variant")
    (source.parent / "0003.xlsx").write_bytes((source.parent / "0002.xlsx").read_bytes())
    target = root / "full-inventory-with-aliases.json"
    inventory([source.parent], target, 3)
    return replace(policy, original_inventory=A.record(target), expected_original_count=3)


def test_dispatcher_allows_unique_selected_source_with_held_unrelated_aliases(setup):
    policy = with_unrelated_aliases(setup)
    admission = A.DispatcherAdmission(policy)
    assert len(admission.inventory["sources"]) == 3
    assert admission.policy.expected_original_count == 3
    assert admission.create("0001")["tracker"]["workbook_id"] == "0001"


@pytest.mark.parametrize("selected", [("0002",), ("0002", "0003"), ("0001", "0002")])
def test_dispatcher_rejects_any_selected_source_with_an_alias(setup, selected):
    policy = replace(with_unrelated_aliases(setup), requested_ids=selected)
    with pytest.raises(ValueError, match="duplicate.*original bytes"):
        A.DispatcherAdmission(policy)
    assert not list(policy.tracker_root.glob("*.json"))


def test_dispatcher_cannot_shrink_alias_denominator(setup):
    policy = replace(with_unrelated_aliases(setup), expected_original_count=1)
    with pytest.raises(ValueError, match="denominator changed"):
        A.DispatcherAdmission(policy)

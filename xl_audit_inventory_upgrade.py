"""Exact reviewed audit lookup implementation transition; no old-code execution."""
import hashlib
from pathlib import Path
from scripts.batch.harbor_admission import require,record

BEFORE_SHA256 = '56ab9a052fbfc064e8af67bcd01c4019fe3b673cc6fdda789b3457db47559092'
AFTER_SHA256 = 'a7184754e05168fb810564859a3925651be473a08dea2ec0d2ea234063168f24'
OLD_REFERENCE_BLOCK = b'def _cell_value(book, ref):\n    sheet, _, coord = ref.rpartition("!")\n    sheet = sheet.strip("\'")\n    if not sheet or sheet not in book.sheetnames:\n        return None\n    return book[sheet][coord].value\n'
OLD_SELECTION_BLOCK = b'        for entry in candidates:\n            band = str(entry.get("band") or "")\n            if not band or band in seen or entry.get("kind") == "label":\n                continue\n            seen.add(band)\n            cells = entry.get("cells") or []\n            values = [_cell_value(book, ref) for ref in cells]\n'
NEW_REFERENCE_SHA256 = 'd28993ad682560610405fe584222ee74be5c9f7e77e902b754c9937978dfbb9e'
NEW_SELECTION_SHA256 = 'b06536e338908076931f545b801ade3debeb75ef76b19994fbc3098fcf333bdc'


def restore_inventory_lookup(raw):
    """Return historical bytes only after exact complete current-code checks."""
    require(hashlib.sha256(raw).hexdigest()==AFTER_SHA256,'current auditor differs beyond exact reviewed inventory lookup')
    start=raw.index(b'def _inventory_reference(');end=raw.index(b'\n\ndef build_inventory',start)
    block=raw[start:end]
    require(hashlib.sha256(block).hexdigest()==NEW_REFERENCE_SHA256,'reviewed reference lookup block changed')
    restored=raw[:start]+OLD_REFERENCE_BLOCK+raw[end:]
    start=restored.index(b'        selected_entries = []');end=restored.index(b'            summary = _value_summary(values)',start)
    block=restored[start:end]
    require(hashlib.sha256(block).hexdigest()==NEW_SELECTION_SHA256,'reviewed selected-cell block changed')
    restored=restored[:start]+OLD_SELECTION_BLOCK+restored[end:]
    require(hashlib.sha256(restored).hexdigest()==BEFORE_SHA256,'auditor changed beyond reviewed lookup blocks')
    return restored


def evidence():return record(Path(__file__).resolve())

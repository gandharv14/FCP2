"""Exact typed formula observations for independent workbook review contexts."""
from openpyxl.worksheet.formula import ArrayFormula, DataTableFormula
from scripts.batch.harbor_admission import require


def typed_cell_value(cell):
    value = cell.value
    if cell.data_type != 'f' or isinstance(value, str):
        return value
    if isinstance(value, ArrayFormula):
        require(isinstance(value.text, str) and isinstance(value.ref, str),
                'array formula evidence missing text or range')
        return {'formula_type': 'array', 'text': value.text, 'attributes': dict(value)}
    if isinstance(value, DataTableFormula):
        return {'formula_type': 'data_table', 'attributes': dict(value)}
    raise ValueError('unsupported formula object in workbook evidence')

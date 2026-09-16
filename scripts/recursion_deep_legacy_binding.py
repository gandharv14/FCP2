"""Bind original XLS/XLSB cached values independently of converted inspection copies."""
import hashlib
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, '/tmp/recursion-audit-legacy-readers')
import xlrd
from pyxlsb import open_workbook
from openpyxl.utils.cell import coordinate_to_tuple
from recursion_reward_extremes_audit import AUDIT


def main():
    root = AUDIT / 'deep-review'
    tasks = {t['taskId']: t for n in (1, 2, 3) for t in json.loads((root / f'batch-{n}.json').read_text())}
    results = []
    for rec in json.loads((root / 'legacy-source-list.json').read_text()):
        path, row = Path(rec['path']), tasks[rec['taskId']]
        a = row['frozenActiveAssets']
        key = json.loads(Path(a['answerKeyPath']).read_text())
        targets, wanted, got = key['targets'], {}, {}
        for target in targets:
            sheet, cell = target.rsplit('!', 1)
            sheet = sheet.strip("'").replace("''", "'")
            r, c = coordinate_to_tuple(cell.replace('$', ''))
            wanted.setdefault(sheet, {})[(r - 1, c - 1)] = target
        record = {'taskId': rec['taskId'], 'originalPath': str(path),
                  'originalSha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                  'answerKeySha256': hashlib.sha256(Path(a['answerKeyPath']).read_bytes()).hexdigest()}
        try:
            if path.suffix == '.xls':
                book = xlrd.open_workbook(path, on_demand=True)
                for sheet, cells in wanted.items():
                    ws = book.sheet_by_name(sheet)
                    for (r, c), target in cells.items():
                        got[target] = ws.cell_value(r, c) if r < ws.nrows and c < ws.ncols else None
                book.release_resources()
            else:
                with open_workbook(str(path)) as book:
                    for sheet, cells in wanted.items():
                        with book.get_sheet(sheet) as ws:
                            for items in ws.rows(sparse=True):
                                for cell in items:
                                    if (cell.r, cell.c) in cells:
                                        got[cells[(cell.r, cell.c)]] = cell.v
            matched = [k for k, v in targets.items() if isinstance(got.get(k), (int, float)) and
                       math.isclose(got[k], v, rel_tol=1e-6, abs_tol=1e-8)]
            record.update(status='original_cache_read', targets=len(targets), matchedTargets=len(matched),
                          missingTargets=[k for k in targets if k not in got],
                          mismatchingTargets=[k for k in targets if k not in matched],
                          allTargetCachesMatch=len(matched) == len(targets))
        except Exception as exc:
            record.update(status='original_cache_read_failed', errorType=type(exc).__name__)
        results.append(record)
    (root / 'legacy-original-cache-bindings.json').write_text(json.dumps(results, indent=2) + '\n')
    print(json.dumps([{'taskId': r['taskId'], 'status': r['status'], 'allTargetCachesMatch': r.get('allTargetCachesMatch')} for r in results]))


if __name__ == '__main__':
    main()

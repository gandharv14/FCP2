"""Bounded OOXML structural audit leads; no task or evaluation mutations."""
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import posixpath
import xml.etree.ElementTree as ET
import zipfile

from recursion_reward_extremes_audit import AUDIT

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'


def inspect(row):
    a = row['assets']
    out = {'taskId': row['taskId'], 'problemVersionId': row['problemVersionId'],
           'means': row['meanBackendReward'], 'assetHashChecks': {},
           'targetObservations': [], 'limitations': [
               'Structural leads require source and trajectory review; a retained value may be a legitimate input.',
               'Blank target cells and absent formulas are expected in reconstruction tasks and do not establish missing information.']}
    for stem in ('prompt', 'workbook', 'answerKey', 'additionalAssumptions'):
        if a.get(stem + 'Path'):
            p = Path(a[stem + 'Path'])
            actual = hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
            out['assetHashChecks'][stem] = {'path': str(p), 'exists': p.exists(),
                                          'matchesFrozenHash': actual == a.get(stem + 'Sha256')}
    key = json.loads(Path(a['answerKeyPath']).read_text())
    prompt = Path(a['promptPath']).read_text()
    targets = key['targets']
    out['targetCount'] = len(targets)
    out['zeroTargetCount'] = sum(v == 0 for v in targets.values())
    out['nonfiniteTargetCount'] = sum(not math.isfinite(v) for v in targets.values() if isinstance(v, (int, float)))
    out['researchRootPresent'] = bool(a.get('researchRoot'))
    out['additionalAssumptionsPresent'] = bool(a.get('additionalAssumptionsPath'))
    with zipfile.ZipFile(a['workbookPath']) as z:
        book = ET.fromstring(z.read('xl/workbook.xml'))
        links = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        rels = {x.attrib['Id']: posixpath.normpath(posixpath.join('xl', x.attrib['Target']))
                if not x.attrib['Target'].startswith('/') else x.attrib['Target'].lstrip('/') for x in links}
        sheets = {s.attrib['name']: rels[s.attrib[REL]] for s in book.findall('s:sheets/s:sheet', NS)}
        out['sheetCount'] = len(sheets)
        out['externalLinkParts'] = sum(n.startswith('xl/externalLinks/externalLink') and n.endswith('.xml') for n in z.namelist())
        wanted = {}
        for name in targets:
            sheet, cell = name.rsplit('!', 1)
            sheet = sheet.strip("'").replace("''", "'")
            wanted.setdefault(sheet, {})[cell.replace('$', '')] = name
        for sheet, cells in wanted.items():
            observed = {}
            if sheet in sheets:
                with z.open(sheets[sheet]) as source:
                    for _, el in ET.iterparse(source, events=('end',)):
                        if el.tag.endswith('}c'):
                            if el.get('r') in cells:
                                v = el.find('s:v', NS)
                                raw = v.text if v is not None else None
                                observation = {'cellPresent': True, 'type': el.get('t', 'n'),
                                               'hasFormula': el.find('s:f', NS) is not None,
                                               'hasStoredValue': raw is not None,
                                               'hasInlineText': el.find('s:is', NS) is not None}
                                if raw is not None and el.get('t', 'n') == 'n':
                                    try:
                                        value = float(raw)
                                        expected = targets[cells[el.get('r')]]
                                        observation['numericValueExactlyEqualsTarget'] = value == expected
                                    except (ValueError, TypeError):
                                        observation['numericParseFailed'] = True
                                observed[el.get('r')] = observation
                            el.clear()
            for cell, name in cells.items():
                out['targetObservations'].append({'target': name, 'sheetPresent': sheet in sheets,
                    'explicitTargetStringInPrompt': name in prompt,
                    **observed.get(cell, {'cellPresent': False})})
    return out


def main():
    rows = [r for n in (1, 2, 3) for r in json.loads((AUDIT / f'shard-{n}.json').read_text())]
    results = []
    for index, row in enumerate(rows):
        try:
            results.append(inspect(row))
        except Exception as error:
            results.append({'taskId': row['taskId'], 'structuralReadError': type(error).__name__ + ': ' + str(error)[:200]})
        if (index + 1) % 100 == 0:
            print(f'Structural screen: {index + 1}/{len(rows)}', flush=True)
    (AUDIT / 'structural-screen.json').write_text(json.dumps(results, indent=2) + '\n')
    print(f'Structural screen complete: {len(results)}', flush=True)


if __name__ == '__main__':
    main()

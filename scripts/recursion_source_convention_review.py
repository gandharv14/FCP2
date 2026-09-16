"""Bounded read-only evidence for two source-convention repairs."""
import gzip
import json
import re
from pathlib import Path
from recursion_small_literal_census import Book, sha, save, WORK


def review():
    rows={r['taskId']:r for r in json.loads((WORK/'final-assets.json').read_text())}
    for tid in ('0701','0708'):
        row=rows[tid]
        census=WORK/'small-literal-census/tasks'/f'{tid}.json'
        source=json.loads(census.read_text())
        native=Book(source['nativeSource']);frozen=Book(row['workbookPath'])
        terms=('non.recurring|other income|equity method|other current assets' if tid=='0701'
               else 'lease modification|sub.lease|exchange diff')
        pattern=re.compile(terms,re.I)
        paths=[census,Path(source['nativeSource']),Path(row['workbookPath']),
               Path(row['promptPath']),Path(row['answerKeyPath']),Path(row['additionalAssumptionsPath'])]
        public=[]
        for name in ('records.jsonl.gz','documents.jsonl.gz'):
            path=Path(row['researchRoot'])/'runtime'/name;paths.append(path)
            items=[json.loads(line) for line in gzip.open(path,'rt') if line.strip()]
            matches=[item for item in items if pattern.search(json.dumps(item))]
            assert not matches
            public.append({'file':str(path),'rowsRead':len(items),'semanticPattern':terms,'matchingRows':0})
        hosts=[]
        key=json.loads(Path(row['answerKeyPath']).read_text())
        for lead in source['candidates']:
            host=lead['host']
            assert native.formulas[host]==lead['nativeFormula']
            assert frozen.cells.get(host) is None
            assert host not in key['targets'],'Never expose a graded host as a clarification'
            hosts.append({'host':host,'nativeFormula':native.formulas[host],
                'labels':lead['rowLabels'],'frozenHostBlank':True,
                'isGradedTarget':False,'impactEvidence':lead.get('downstreamCounterfactual'),
                'impactLimitation':lead.get('counterfactualUncheckable')})
        if tid=='0701':
            proposal=('Historical source components, in the units of their original rows: '
                'IS!E28 = -4 + 45; IS!F28 = 45 - 1; IS!E29 = 40 - 34 - 13; '
                'IS!F29 = 58 - 54 - 17; BS!F28 = 308 + 12 + 4 + 1; '
                'BS!E35 = 20 + 37 + 15; BS!F35 = 25 + 34 + 25. '
                'Use these components to reconstruct the historical input rows before forecasting.')
            interpretation='Confirmed omitted historical components and combination conventions; full additional assumptions discuss subsequent forecast behavior but do not supply these historical components.'
        else:
            assert frozen.cells['Cash Flows Statement!L17']==-7
            assert frozen.cells['Cash Flows Statement!L18']==9
            proposal=('For Ratio Analysis!AE60, combine the lease-modification gain and sublease loss: '
                'negate the sum of Cash Flows Statement!L17 and L18. '
                'The source exchange-difference components for Ratio Analysis!AC215 are -5 and +4; '
                'for Ratio Analysis!AC243 they are -2 and +19. Add each pair in its row\'s workbook units.')
            interpretation=('AE60 primitives are already supplied on the Cash Flows Statement; the missing source convention includes both lease rows despite the abbreviated ratio label. '
                'AC215 and AC243 omit source exchange-difference components. Their downstream paths contain unsupported INDEX expressions in the bounded counterfactual engine; do not claim independent full-impact validation for those two hosts.')
        aa=Path(row['additionalAssumptionsPath']).read_text()
        evidence={'taskId':tid,'status':'confirmed_source_convention_omissions',
            'interpretation':interpretation,'hosts':hosts,'publicSourceScan':public,
            'additionalAssumptionsReadInFull':True,'assumptionsCharacters':len(aa),
            'relevantAssumptionsLines':[line for line in aa.splitlines() if pattern.search(line)],
            'proposedClarification':proposal,'scope':'Original economic inputs and arithmetic only; no target, tolerance, or source-economics change.',
            'evidenceHashes':{str(p.resolve()):sha(p) for p in paths}}
        save(WORK/'small-literal-census/adjudications'/f'{tid}.json',evidence)
        print(tid,len(hosts),'source conventions documented')


if __name__=='__main__':review()

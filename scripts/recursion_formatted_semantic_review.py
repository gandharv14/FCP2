"""Source-backed dispositions for selected formatted-string audit leads."""
import json
import re
from recursion_small_literal_census import Book, WORK, sha, save


def review():
    out=[]
    for tid in ('0074','0389','0137','0211','0690'):
        path=WORK/'formatted-result-census/adjudicated'/f'{tid}.json'
        receipt=json.loads(path.read_text());native=Book(receipt['nativeSource']);frozen=Book(receipt['workbookPath'])
        leads=[item for category in ('leads','confirmed','excluded') for item in receipt[category]
               if item.get('reason')=='single_formatted_value_or_axis_label_needs_semantic_review'];proof=[]
        assert leads, (tid,'Missing semantic-review candidates')
        assert len({item['cell'] for item in leads})==len(leads)
        for item in leads:
            cell=item['cell'];assert native.formulas[cell]==item['nativeFormula']
            assert frozen.cells[cell]==item['frozenValue']
            if tid=='0074':
                match=re.fullmatch(r"='Capital Structure Details'!(E\d+)",item['nativeFormula']);assert match
                target='Capital Structure Details!'+match[1]
                assert target not in native.formulas and frozen.cells[target]==item['frozenValue']
                proof.append({'cell':cell,'primitiveSource':target,'retainedPrimitiveMatches':True})
            elif tid=='0389':
                match=re.fullmatch(r'=TEXT\(([A-Z]+\d+),"0\.0[x%]"\)',item['nativeFormula']);assert match
                target='LBO!'+match[1]
                proof.append({'cell':cell,'axisCoordinate':target,'axisNativeValue':native.cells[target],
                              'role':'Header or row label outside sensitivity result body'})
            elif tid=='0137':
                matches=re.findall(r"'7\. Investor Econ'!\$([A-Z]+)\$(\d+)",item['nativeFormula'])
                assert matches
                targets=['7. Investor Econ!'+col+row for col,row in matches]
                assert any(native.formulas.get(target,'').startswith('=XIRR(') for target in targets)
                for target in targets:
                    assert native.formulas.get(target,'').startswith('=XIRR(') or native.cells.get(target) is None
                    assert frozen.cells.get(target) is None
                proof.append({'cell':cell,'calculatedSources':{target:native.formulas.get(target,'blank fallback') for target in targets}})
            elif tid=='0211':
                formula=item['nativeFormula']
                if cell.startswith('Sens!'):
                    target=formula[1:].replace("'",'').replace('$','')
                    confirmed={v['cell'] for v in receipt['confirmed']}
                    assert target in native.formulas or target in confirmed
                    proof.append({'cell':cell,'sourceSensitivityOutput':target,'sourceFormula':native.formulas.get(target),
                                  'sourceDataTableConfirmed':target in confirmed})
                else:
                    match=re.search(r'\$D\$(\d+)',formula);assert match
                    target=cell.rsplit('!',1)[0]+'!D'+match[1]
                    assert native.formulas[target].startswith(('=XIRR(','=AVERAGE(','=SUMIF('))
                    assert frozen.cells.get(target) is None
                    proof.append({'cell':cell,'calculatedSource':target,'sourceFormula':native.formulas[target]})
            else:
                assert cell=='Amazon Unlevered DCF!O10'
                for target in ('Amazon Unlevered DCF!H8','Amazon Unlevered DCF!H20'):
                    assert target in native.formulas and frozen.cells.get(target) is None
                proof.append({'cell':cell,'sourceFormula':item['nativeFormula'],
                    'calculatedSources':['Amazon Unlevered DCF!H8','Amazon Unlevered DCF!H20']})
        confirmed=tid in ('0137','0211','0690')
        reason={'0074':'Copies of preserved literal coupon inputs; no financial calculation exposed.',
            '0389':'Sensitivity scenario-axis labels; preserve input axes and their interpretation.',
            '0137':'Formatted copies of calculated investor XIRRs.',
            '0211':'Formatted calculated project/investor IRRs, cash yields, cash pools, and their sensitivity outputs.',
            '0690':'Formatted calculated EBITDA and valuation multiple reveal model intermediates.'}[tid]
        out.append({'taskId':tid,'disposition':'confirmed_calculated_result_text' if confirmed else 'preserve_input_representation',
            'cells':[x['cell'] for x in leads],'reason':reason,'sourceEvidence':proof,
            'hashes':{str(path):sha(path),receipt['nativeSource']:sha(receipt['nativeSource']),
                      receipt['workbookPath']:sha(receipt['workbookPath'])}})
        print(tid,len(leads),'mask' if confirmed else 'preserve')
    save(WORK/'formatted-result-census/root-semantic-review.json',out)


if __name__=='__main__':review()

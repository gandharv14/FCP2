"""Read-only native-confirmed census of formatted calculated numeric strings."""
from __future__ import annotations
import collections,json,re,sys,time
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.recursion_small_literal_census import Book,WORK,ROOT,sha,readjson,save
from scripts.recursion_hardening_workbooks import sheets,NS
OUT=WORK/'formatted-result-census'
# Numeric expressions with a financial-result marker; dates/plain counters are not candidates.
FINANCIAL=re.compile(r'^\s*[-+$£€₹(]?\s*(?:\d|\.\d)[\d,.\s%/xX×()$£€₹+\-:]*\s*$',re.I)
def is_financial_result_text(value):
 return isinstance(value,str) and ((bool(FINANCIAL.fullmatch(value)) and bool(re.search(r'[%xX×$£€₹]',value))) or bool(re.fullmatch(r'\s*(?:NPV|IRR|MOIC|Equity value|Enterprise value)\s*[:=]?\s+[$£€₹(+-]?\s*\d[\d,.%xX()\s-]*',value,re.I)))
def source_for(task):
 found=[p for f in ['RawWorkbooks1','RawWorkbooks2','RawWorkbooks3'] for p in (ROOT.parent/f).glob(task+'.*') if p.is_file()]
 return found[0] if len(found)==1 else None

def main():
 rows=readjson(WORK/'final-assets.json');OUT.mkdir(exist_ok=True);allrows=[]
 for i,r in enumerate(rows):
  target=OUT/'tasks'/f'{r["taskId"]}.json'
  if target.exists():allrows.append(readjson(target));continue
  b=Book(r['workbookPath']);source=source_for(r['taskId']);candidates=[(k,v) for k,v in b.cells.items() if is_financial_result_text(v) and not k.startswith('Embedded Assumptions!')]
  receipt={'taskId':r['taskId'],'workbookPath':r['workbookPath'],'workbookSha256':sha(r['workbookPath']),'nativeSource':str(source) if source else None,'status':'no_formatted_financial_string_candidates','confirmed':[],'leads':[],'excluded':[],'sharedStringCandidates':[]}
  assert receipt['workbookSha256']==r['workbookSha256']
  with ZipFile(r['workbookPath']) as z:
   if 'xl/sharedStrings.xml' in z.namelist():
    receipt['sharedStringCandidates']=[{'index':j,'value':''.join(el.itertext())} for j,el in enumerate(ET.fromstring(z.read('xl/sharedStrings.xml'))) if is_financial_result_text(''.join(el.itertext()))]
  if candidates:
   if source and source.suffix.lower() in ['.xlsx','.xlsm']:
    n=Book(source);receipt['nativeSourceSha256']=sha(source)
    for k,v in candidates:
     form=n.formulas.get(k);item={'cell':k,'frozenValue':v,'nativeFormula':form,'nativeCachedValue':n.cells.get(k)}
     if form and isinstance(n.cells.get(k),str) and v.strip()==n.cells[k].strip():receipt['confirmed'].append(item)
     elif not form:receipt['excluded'].append({**item,'reason':'not_native_formula_cell'})
     else:receipt['leads'].append({**item,'reason':'native_formula_value_does_not_match_frozen'})
   else:receipt['leads']=[{'cell':k,'frozenValue':v,'reason':'native_source_unavailable_or_legacy'} for k,v in candidates]
   receipt['status']='confirmed_native_calculated_string_exposure' if receipt['confirmed'] else ('leads' if receipt['leads'] else 'only_nonformula_numeric_labels_or_inputs')
  save(target,receipt);allrows.append(receipt)
  if i%25==0:print(json.dumps({'tasks':i+1,'confirmedTasks':sum(bool(x['confirmed']) for x in allrows),'confirmedCells':sum(len(x['confirmed']) for x in allrows)}),flush=True)
 save(OUT/'manifest.json',allrows);summary={'tasks':len(allrows),'statuses':dict(collections.Counter(x['status'] for x in allrows)),'confirmedTasks':[x['taskId'] for x in allrows if x['confirmed']],'confirmedCells':sum(len(x['confirmed']) for x in allrows),'leadTasks':[x['taskId'] for x in allrows if x['leads']]};save(OUT/'summary.json',summary);print(json.dumps(summary))
if __name__=='__main__':main()

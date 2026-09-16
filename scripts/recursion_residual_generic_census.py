"""Extend the same read-only active population to generic numeric pair formats."""
from __future__ import annotations
import collections,datetime,json,re
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET
from scripts.recursion_residual_result_census import OUT,WORK,Book,TargetSource,active_assets,readjson,save,sha,MIXED,NS,in_ranges
NUMBER=r'[-+($£€₹\s]*\d[\d,.\s]*[%xX×)?\s]*'
GENERIC=re.compile(r'^\s*'+NUMBER+r'(?:\s*[/|]\s*'+NUMBER+r')+\s*$')
def generic(v):return isinstance(v,str) and bool(GENERIC.fullmatch(v))
def date_text(v):
 if not re.fullmatch(r'\s*\d{1,4}/\d{1,2}/\d{1,4}\s*',v):return False
 for fmt in ['%m/%d/%Y','%d/%m/%Y','%Y/%m/%d','%m/%d/%y','%d/%m/%y']:
  try:datetime.datetime.strptime(v.strip(),fmt);return True
  except ValueError:pass
 return False

def main():
 rows=list(active_assets());summary=[]
 for i,r in enumerate(rows):
  dest=OUT/'generic-tasks'/f'{r["taskId"]}.json'
  if dest.exists():
   q=readjson(dest);assert q['workbookSha256']==r['workbookSha256']==sha(r['workbookPath']);summary.append(q);continue
  base=readjson(OUT/'tasks'/f'{r["taskId"]}.json');assert base['workbookSha256']==r['workbookSha256']==sha(r['workbookPath']);b=Book(r['workbookPath']);seen={x['cell'] for kind in ('confirmed','leads','excluded') for x in base[kind]};keys={k for k,v in b.cells.items() if generic(v) and k not in seen and not k.startswith('Embedded Assumptions!')};dates={k for k in keys if date_text(b.cells[k])};keys-=dates;n=TargetSource(base['nativeSource'],keys) if base['sourceSupported'] and keys else None
  q={k:r[k] for k in ['taskId','workbookPath','workbookSha256','activeProblemVersionId']};q.update(nativeSource=base['nativeSource'],nativeSourceSha256=base['nativeSourceSha256'],originalSource=base['originalSource'],originalSourceSha256=sha(base['originalSource']) if base['originalSource'] else None,confirmed=[],leads=[],excluded=[{'cell':k,'activeValue':b.cells[k],'reason':'valid_three_component_calendar_date'} for k in sorted(dates)],sharedStringLeads=[])
  for k in sorted(keys):
   v=b.cells[k];native=n.cells.get(k) if n else None;form=n.formulas.get(k) if n else None;sh,co=k.rsplit('!',1);tables=[t for t in base['sourceDataTableScopes'].get(sh,[]) if in_ranges(co,[t['ref']])];item={'cell':k,'activeValue':v,'nativeValue':native,'nativeFormula':form,'sourceDataTables':tables,'screen':'generic_numeric_composite'}
   if not n:item['reason']='native_source_unavailable_or_legacy';q['leads'].append(item)
   elif native!=v:item['reason']='native_value_differs_requires_review';q['leads'].append(item)
   elif tables:item['reason']='exact_native_result_body';q['confirmed'].append(item)
   else:item['reason']='native_formula_generic_composite_needs_semantic_review' if form else 'native_literal_generic_composite_needs_semantic_review';q['leads'].append(item)
  with ZipFile(r['workbookPath']) as z:
   if 'xl/sharedStrings.xml' in z.namelist():
    # Existing live cells carry exact contents; an unmatched string has no live textual use.
    live=collections.Counter(v for v in b.cells.values() if isinstance(v,str))
    for j,el in enumerate(ET.fromstring(z.read('xl/sharedStrings.xml'))):
     v=''.join(el.itertext())
     if generic(v) and not date_text(v) and not MIXED.search(v) and not live[v]:q['sharedStringLeads'].append({'index':j,'value':v,'liveCellReferences':0,'reason':'orphan_generic_composite_needs_source_proof'})
  save(dest,q);summary.append(q)
  if i%50==0:print(json.dumps({'genericTasks':i+1,'leadTasks':sum(bool(x['leads']) for x in summary)}),flush=True)
 save(OUT/'generic-manifest.json',summary);report={'tasks':len(summary),'confirmedTasks':{x['taskId']:len(x['confirmed']) for x in summary if x['confirmed']},'leadTasks':{x['taskId']:len(x['leads']) for x in summary if x['leads']},'dateExcludedTasks':{x['taskId']:len(x['excluded']) for x in summary if x['excluded']},'orphanTasks':{x['taskId']:len(x['sharedStringLeads']) for x in summary if x['sharedStringLeads']}};save(OUT/'generic-summary.json',report);print(json.dumps(report))
if __name__=='__main__':main()

"""Adjudicate financial string candidates using original DataTable scopes and formulas."""
from __future__ import annotations
import collections,re,sys
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.recursion_small_literal_census import WORK,readjson,save,Book
from scripts.recursion_hardening_workbooks import sheets,NS,in_ranges
OUT=WORK/'formatted-result-census'
COMPOSITE=re.compile(r'[/|]')
def composite(s):return bool(COMPOSITE.search(s)) and len(re.findall(r'\d+(?:\.\d+)?',s))>=2

def source_metadata(path):
 tables={};formulas={};
 with ZipFile(path) as z:
  for sh,p in sheets(z).items():
   root=ET.fromstring(z.read(p));tables[sh]=[]
   for c in root.findall('.//s:sheetData/s:row/s:c',NS):
    f=c.find('s:f',NS)
    if f is not None:
     if f.attrib.get('t')=='dataTable' and f.attrib.get('ref'):tables[sh].append({'anchor':c.attrib['r'],'ref':f.attrib['ref'],'attributes':dict(f.attrib)})
     if f.text:formulas[sh+'!'+c.attrib['r']]='='+f.text
 return tables,formulas

def main():
 allrows=[]
 for p in sorted((OUT/'tasks').glob('*.json')):
  r=readjson(p);dest=OUT/'adjudicated'/p.name
  if dest.exists():allrows.append(readjson(dest));continue
  candidates=r['confirmed']+r['leads']+r['excluded'];sourcesupported=r['nativeSource'] and Path(r['nativeSource']).suffix.lower() in ['.xlsx','.xlsm']
  tables,formulas=source_metadata(r['nativeSource']) if sourcesupported and candidates else ({},{})
  q={k:r[k] for k in ['taskId','workbookPath','workbookSha256','nativeSource']};q['nativeSourceSha256']=r.get('nativeSourceSha256');q.update(confirmed=[],leads=[],excluded=[],orphanSharedStringLeads=r['sharedStringCandidates'])
  for c in candidates:
   sh,co=c['cell'].rsplit('!',1);matching=[t for t in tables.get(sh,[]) if in_ranges(co,[t['ref']])];form=formulas.get(c['cell'],c.get('nativeFormula'));v=c['frozenValue'];item={**c,'sourceFormula':form}
   if matching:
    item.update(reason='native_DataTable_calculated_result_body',sourceDataTables=matching);q['confirmed'].append(item)
   elif form and re.fullmatch(r'=\+?\s*"[^\"]*"\s*',form):
    item['reason']='native_literal_text_formula_input_or_label';q['excluded'].append(item)
   elif form and composite(v):
    item['reason']='native_formula_financial_composite_output';q['confirmed'].append(item)
   elif composite(v):
    item['reason']='pasted_composite_needs_sensitivity_context' if sourcesupported else 'composite_native_source_unavailable_or_legacy';q['leads'].append(item)
   elif form:
    item['reason']='single_formatted_value_or_axis_label_needs_semantic_review';q['leads'].append(item)
   else:q['excluded'].append(item)
  q['confirmedCount']=len(q['confirmed']);q['leadCount']=len(q['leads']);save(dest,q);allrows.append(q)
 save(OUT/'adjudicated-manifest.json',allrows);save(OUT/'adjudicated-summary.json',{'tasks':len(allrows),'confirmedTasks':[x['taskId'] for x in allrows if x['confirmed']],'confirmedCells':sum(len(x['confirmed']) for x in allrows),'leadTasks':{x['taskId']:len(x['leads']) for x in allrows if x['leads']}})
 print(readjson(OUT/'adjudicated-summary.json'))
if __name__=='__main__':main()

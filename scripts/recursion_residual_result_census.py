"""Read-only census of residual mixed/status result strings on active workbooks."""
from __future__ import annotations
import collections,json,re,sys,gzip
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.recursion_small_literal_census import Book,WORK,sha,readjson,save
from scripts.recursion_formatted_result_census import source_for
from scripts.recursion_formatted_result_adjudication import source_metadata
from scripts.recursion_hardening_workbooks import NS,in_ranges,sheets
from openpyxl.formula.translate import Translator
OUT=WORK/'content-amendment-review'/'residual-census'
# Nonnumeric/error/label first halves and numeric composites; require slash/pipe and a numeric financial suffix.
MIXED=re.compile(r'[/|]\s*[-+($£€]?\s*(?:\d[\d,]*(?:\.\d*)?|\.\d+)\s*(?:x|×|%)\s*\)?\s*$',re.I)
STATUS=re.compile(r'^\s*(?:NM|N/?M|N/?A|#(?:N/A|VALUE!|DIV/0!|NUM!|REF!|NAME\?|SPILL!)|not meaningful|not applicable|[-–—])\s*$',re.I)
def candidate(v):return isinstance(v,str) and bool(MIXED.search(v) or STATUS.fullmatch(v))
def active_assets():
 rows=readjson(WORK/'final-assets.json');act=readjson(WORK/'content-successor/activation.json');assert act['status']=='ready';assert sha(WORK/'final-assets.json')==act['baseAssetsSha256'];revs={x['taskId']:x for x in act['revisions']}
 for r in rows:
  r=dict(r);rev=revs.get(r['taskId'])
  if rev:r.update(workbookPath=rev['workbookPath'],workbookSha256=rev['workbookSha256'],activeProblemVersionId=rev['newProblemVersionId'])
  else:
   vp=WORK/'problems'/r['taskId']/'verified.json';r['activeProblemVersionId']=readjson(vp).get('id') if vp.exists() else None
  yield r

class TargetSource:
 def __init__(self,path,keys):
  self.cells={};self.formulas={};self.tables={}
  wanted=collections.defaultdict(set)
  for key in keys:sh,co=key.rsplit('!',1);wanted[sh].add(co)
  with ZipFile(path) as z:
   strings=[''.join(n.itertext()) for n in ET.fromstring(z.read('xl/sharedStrings.xml'))] if 'xl/sharedStrings.xml' in z.namelist() else []
   for sh,p in sheets(z).items():
    if sh not in wanted:continue
    root=ET.fromstring(z.read(p));anchors={};pending=[];self.tables[sh]=[]
    for c in root.findall('.//s:sheetData/s:row/s:c',NS):
     co=c.attrib['r'];f=c.find('s:f',NS)
     if f is not None:
      if f.attrib.get('t')=='dataTable' and f.attrib.get('ref'):self.tables[sh].append({'anchor':co,'ref':f.attrib['ref'],'attributes':dict(f.attrib)})
      if f.text and f.attrib.get('t')=='shared':anchors[f.attrib.get('si')]=(co,'='+f.text)
     if co not in wanted[sh]:continue
     key=sh+'!'+co;v=c.findtext('s:v',namespaces=NS);typ=c.attrib.get('t','n')
     if typ=='s' and v is not None:v=strings[int(v)]
     elif typ=='inlineStr':v=''.join(c.find('s:is',NS).itertext())
     elif typ=='b' and v is not None:v=v=='1'
     elif typ=='n' and v is not None:
      try:v=float(v)
      except ValueError:pass
     self.cells[key]=v
     if f is not None:
      if f.text:self.formulas[key]='='+f.text
      elif f.attrib.get('t')=='shared':pending.append((key,co,f.attrib.get('si')))
      else:self.formulas[key]='=UNSUPPORTED_NATIVE_TABLE()'
    for key,co,si in pending:
     try:anchor,form=anchors[si];self.formulas[key]=Translator(form,origin=anchor).translate_formula(co)
     except Exception:self.formulas[key]='=UNSUPPORTED_SHARED_FORMULA()'

def all_source_tables(path):
 tables={}
 with ZipFile(path) as z:
  for sh,p in sheets(z).items():
   tables[sh]=[];co=None
   with z.open(p) as f:
    for event,el in ET.iterparse(f,events=('start','end')):
     tag=el.tag.rsplit('}',1)[-1]
     if event=='start' and tag=='c':co=el.attrib.get('r')
     elif event=='end':
      if tag=='f' and el.attrib.get('t')=='dataTable' and el.attrib.get('ref'):tables[sh].append({'anchor':co,'ref':el.attrib['ref'],'attributes':dict(el.attrib)})
      if tag=='f' and el.text and re.match(r'^\+?(?:_xlfn\.)?TABLE\(',el.text,re.I):tables[sh].append({'anchor':co,'ref':co,'attributes':{'convertedTableFormula':el.text}})
      if tag in ('c','row'):el.clear()
 return tables

def main():
 OUT.mkdir(parents=True,exist_ok=True);rows=list(active_assets());allrows=[];supplemental={x['task']:x for x in readjson(WORK.parent/'supplemental-masking-manifest.json')}
 for i,r in enumerate(rows):
  dest=OUT/'tasks'/f'{r["taskId"]}.json'
  if dest.exists() and readjson(dest).get('scopeVersion')==4:
   cached=readjson(dest);assert cached['workbookSha256']==r['workbookSha256']==sha(r['workbookPath']);assert cached['activeProblemVersionId']==r['activeProblemVersionId'];assert cached['nativeSource'] is None or sha(cached['nativeSource'])==cached['nativeSourceSha256'];assert cached['originalSource'] is None or sha(cached['originalSource'])==cached['originalSourceSha256'];allrows.append(cached);continue
  b=Book(r['workbookPath']);assert sha(r['workbookPath'])==r['workbookSha256'];src=source_for(r['taskId']);supported=bool(src and src.suffix.lower() in ('.xlsx','.xlsm'))
  oldp=WORK/'formatted-result-census/adjudicated'/f'{r["taskId"]}.json';old=readjson(oldp) if oldp.exists() else {};ranges=collections.defaultdict(set)
  originalSource=src
  if not supported and old.get('originalLegacySource') and old.get('nativeSource') and Path(old['nativeSource']).exists():
   assert sha(old['nativeSource'])==old['nativeSourceSha256'] and sha(originalSource)==old['originalLegacySourceSha256'];src=Path(old['nativeSource']);supported=True
  allTables=all_source_tables(src) if supported else {}
  for sh,tableRows in allTables.items():ranges[sh].update(t['ref'] for t in tableRows)
  for c in old.get('confirmed',[]):
   sh=c['cell'].rsplit('!',1)[0]
   for t in c.get('sourceDataTables',[]):ranges[sh].add(t['ref'])
  for sh,refs in supplemental.get(r['taskId'],{}).get('remove_calculated_ranges',{}).items():ranges[sh].update(refs)
  keys={k for k,v in b.cells.items() if isinstance(v,str) and MIXED.search(v) and not k.startswith('Embedded Assumptions!')}
  # Every surviving string in a previously native-proven result body, irrespective of regex.
  for k,v in b.cells.items():
   sh,co=k.rsplit('!',1)
   if v is not None and (not isinstance(v,str) or v.strip()) and ranges.get(sh) and in_ranges(co,list(ranges[sh])):keys.add(k)
  q={k:r[k] for k in ['taskId','workbookPath','workbookSha256','activeProblemVersionId']};q.update(scopeVersion=4,sourceDataTableScopes=allTables,originalSource=str(originalSource) if originalSource else None,originalSourceSha256=sha(originalSource) if originalSource else None,conversionProvenance=old.get('conversionProvenance') if originalSource!=src else None,nativeSource=str(src) if src else None,nativeSourceSha256=sha(src) if src else None,sourceSupported=supported,confirmed=[],leads=[],excluded=[],sharedStringLeads=[])
  n=TargetSource(src,keys) if supported and keys else None;tables=allTables
  for k in sorted(keys):
   sh,co=k.rsplit('!',1);v=b.cells[k];form=n.formulas.get(k) if n else None;native=n.cells.get(k) if n else None;matching=[t for t in tables.get(sh,[]) if in_ranges(co,[t['ref']])];item={'cell':k,'activeValue':v,'nativeValue':native,'nativeFormula':form,'sourceDataTables':matching,'screen':'mixed_financial_composite' if isinstance(v,str) and MIXED.search(v) else ('status_text' if isinstance(v,str) and STATUS.fullmatch(v) else 'source_verified_result_region')}
   if not supported:item['reason']='native_source_unavailable_or_legacy';q['leads'].append(item)
   elif native!=v:item['reason']='native_value_differs_requires_review';q['leads'].append(item)
   elif matching:item['reason']='exact_native_DataTable_result_body';q['confirmed'].append(item)
   elif form and re.fullmatch(r'=\+?\s*"[^\"]*"\s*',form):item['reason']='native_literal_text_formula';q['excluded'].append(item)
   elif form and isinstance(v,str) and MIXED.search(v):item['reason']='exact_native_formula_composite_needs_semantic_confirmation';q['leads'].append(item)
   elif form and isinstance(v,str) and STATUS.fullmatch(v):item['reason']='native_formula_status_needs_semantic_confirmation';q['leads'].append(item)
   else:item['reason']='native_literal_or_label_not_proven_calculated';q['excluded'].append(item)
  with ZipFile(r['workbookPath']) as z:
   refs=collections.Counter()
   for sh,p in sheets(z).items():
    for c in ET.fromstring(z.read(p)).findall('.//s:sheetData/s:row/s:c',NS):
     if c.attrib.get('t')=='s':
      val=c.findtext('s:v',namespaces=NS)
      if val is not None:refs[int(val)]+=1
   if 'xl/sharedStrings.xml' in z.namelist():
    for j,el in enumerate(ET.fromstring(z.read('xl/sharedStrings.xml'))):
     v=''.join(el.itertext())
     if MIXED.search(v):q['sharedStringLeads'].append({'index':j,'value':v,'liveCellReferences':refs[j],'reason':'orphan_mixed_result_candidate' if not refs[j] else 'live_shared_string_also_review_cells'})
  q['status']='confirmed_calculated_result_disclosure' if q['confirmed'] else ('requires_source_or_semantic_review' if q['leads'] or any(not x['liveCellReferences'] for x in q['sharedStringLeads']) else ('only_preserved_literals_or_labels' if keys else 'no_screen_candidates'))
  save(dest,q);allrows.append(q)
  if i%25==0:print(json.dumps({'tasks':i+1,'confirmedCells':sum(len(x['confirmed']) for x in allrows),'leadTasks':sum(bool(x['leads']) for x in allrows)}),flush=True)
 save(OUT/'manifest.json',allrows);summary={'tasks':len(allrows),'statuses':dict(collections.Counter(x['status'] for x in allrows)),'sourceSupportedTasks':sum(x['sourceSupported'] for x in allrows),'sourceUnsupportedTasks':sum(not x['sourceSupported'] for x in allrows),'confirmedTasks':{x['taskId']:len(x['confirmed']) for x in allrows if x['confirmed']},'leadTasks':{x['taskId']:len(x['leads']) for x in allrows if x['leads']},'orphanMixedStringTasks':{x['taskId']:sum(not a['liveCellReferences'] for a in x['sharedStringLeads']) for x in allrows if any(not a['liveCellReferences'] for a in x['sharedStringLeads'])},'sourceRosterSha256':sha(WORK/'final-assets.json'),'activationSha256':sha(WORK/'content-successor/activation.json')};save(OUT/'summary.json',summary);print(json.dumps(summary))
if __name__=='__main__':main()

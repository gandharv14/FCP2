"""Read-only source census of small economic constants in arithmetic inputs."""
from __future__ import annotations
import argparse,collections,copy,gzip,hashlib,json,math,posixpath,re,sys,time,signal
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET
from openpyxl.formula.translate import Translator
from openpyxl.utils.cell import range_boundaries,column_index_from_string,get_column_letter
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from xl_ast_graph import parse_formula
from scripts.recursion_hardening_workbooks import sheets,NS
ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'reports/recursion/gemini-high-score-audit-20260915/refactor'
OUT=WORK/'small-literal-census'
STRUCTURAL={0,1,12,100,365}
ARITH=re.compile(r'^[\d\s.eE+*/^()%\-]+$')
NUM=re.compile(r'(?<![A-Za-z])(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?%?')

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def canon(k):
 sh,c=k.rsplit('!',1);return sh.strip("'").replace("''", "'")+"!"+c.replace('$','').upper()
def save(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,indent=2,default=str)+'\n')
def readjson(p):return json.loads(Path(p).read_text())
def flat(v):
 if isinstance(v,list):
  for a in v:yield from flat(a)
 else:yield v
class Unsupported(Exception):pass
class AuditDeadline(BaseException):pass
def deadline_handler(*args):raise AuditDeadline()

class Book:
 def __init__(self,path):
  self.path=Path(path);self.cells={};self.formulas={};self.sheetcells=collections.defaultdict(list);self.names={};self.ast={}
  with ZipFile(path) as z:
   strings=[]
   if 'xl/sharedStrings.xml' in z.namelist():
    strings=[''.join(n.itertext()) for n in ET.fromstring(z.read('xl/sharedStrings.xml'))]
   for n in ET.fromstring(z.read('xl/workbook.xml')).findall('s:definedNames/s:definedName',NS):
    self.names[n.attrib['name']]=n.text or ''
   for sheet,path in sheets(z).items():
    root=ET.fromstring(z.read(path));shared={};pending=[]
    for c in root.findall('.//s:sheetData/s:row/s:c',NS):
     co=c.attrib['r'];key=sheet+'!'+co;value=c.findtext('s:v',namespaces=NS);typ=c.attrib.get('t','n');f=c.find('s:f',NS)
     if typ=='s' and value is not None:value=strings[int(value)]
     elif typ=='inlineStr':value=''.join(c.find('s:is',NS).itertext())
     elif typ=='n' and value is not None:
      try:value=float(value)
      except ValueError:pass
     elif typ=='b' and value is not None:value=value=='1'
     if value is None and f is None:continue
     self.cells[key]=value
     m=re.fullmatch('([A-Z]+)([0-9]+)',co)
     if m:self.sheetcells[sheet].append((column_index_from_string(m[1]),int(m[2]),key))
     if f is not None:
      if f.text:
       self.formulas[key]='='+f.text
       if f.attrib.get('t')=='shared':shared[f.attrib.get('si')]=(co,'='+f.text)
      elif f.attrib.get('t')=='shared':pending.append((key,co,f.attrib.get('si')))
      else:self.formulas[key]='=UNSUPPORTED_NATIVE_TABLE()'
    for key,co,si in pending:
     try:anchor,text=shared[si];self.formulas[key]=Translator(text,origin=anchor).translate_formula(co)
     except Exception:self.formulas[key]='=UNSUPPORTED_SHARED_FORMULA()'
 def refs(self,ref,sheet,seen=()):
  ref=ref.replace('$','')
  if ref in self.names:
   if ref in seen:raise Unsupported('cyclic_defined_name')
   return self.refs(self.names[ref],sheet,seen+(ref,))
  if '!' in ref:sheet,ref=ref.rsplit('!',1);sheet=sheet.strip("'").replace("''", "'")
  if '[' in sheet or '[' in ref or ':' in sheet:raise Unsupported('external_or_3d_reference')
  if sheet not in self.sheetcells:raise Unsupported('unknown_sheet:'+sheet)
  if re.fullmatch('[A-Z]+[0-9]+',ref):return [sheet+'!'+ref]
  try:
   c1,r1,c2,r2=range_boundaries(ref)
   if not any([c1,r1,c2,r2]):raise ValueError()
   return [k for c,r,k in self.sheetcells[sheet] if (c1 or 1)<=c<=(c2 or 16384) and (r1 or 1)<=r<=(r2 or 1048576)]
  except Exception:raise Unsupported('unresolved_reference:'+ref)
 def tree(self,key):
  if key not in self.ast:self.ast[key]=parse_formula(self.formulas[key])
  return self.ast[key]
 def cone(self,targets):
  todo=list(targets);seen=set();reverse=collections.defaultdict(set);unresolved=[];started=time.monotonic()
  while todo:
   key=todo.pop()
   if time.monotonic()-started>10:unresolved.append({'cell':key,'reason':'dependency_time_budget'});break
   if key in seen:continue
   seen.add(key)
   if len(seen)>300000:unresolved.append({'cell':key,'reason':'dependency_budget'});break
   if key not in self.formulas:continue
   try:
    nodes=[self.tree(key)]
    while nodes:
     n=nodes.pop();nodes.extend(n.args)
     if n.kind in ('ref','name'):
      for dep in self.refs(n.name,key.rsplit('!',1)[0]):reverse[dep].add(key);todo.append(dep)
     if n.kind=='func' and n.name.upper() in ('INDIRECT','OFFSET'):unresolved.append({'cell':key,'reason':'dynamic_reference'})
   except Exception as e:unresolved.append({'cell':key,'reason':str(e)[:120]})
  return seen,reverse,unresolved
 def numeric(self,key):
  v=self.cells.get(key)
  if v is None:return 0.
  if isinstance(v,(int,float)):return v
  raise Unsupported('nonnumeric_cached_cell:'+key)
 def calculate(self,targets,host,replacement,reverse,overrides=None):
  overrides=overrides or {host:replacement}
  affected=set(overrides);todo=list(overrides)
  while todo:
   for k in reverse.get(todo.pop(),()):
    if k not in affected:affected.add(k);todo.append(k)
  memo=dict(overrides);active=set()
  def cell(k):
   if k in memo:return memo[k]
   if k not in affected:return self.numeric(k)
   if k in active:raise Unsupported('circular_affected_path')
   if k not in self.formulas:return self.numeric(k)
   active.add(k)
   try:v=ev(self.tree(k),k.rsplit('!',1)[0]);memo[k]=v;return v
   finally:active.remove(k)
  def ev(n,sheet):
   if n.kind=='const':return n.shape
   if n.kind in ('ref','name'):
    values=[cell(k) for k in self.refs(n.name,sheet)];return values[0] if len(values)==1 else values
   if n.kind=='prefix':return (-1 if n.name=='-' else 1)*ev(n.args[0],sheet)
   if n.kind=='postfix' and n.name=='%':return ev(n.args[0],sheet)/100
   if n.kind=='infix':
    a,b=[ev(x,sheet) for x in n.args]
    return {'+':lambda:a+b,'-':lambda:a-b,'*':lambda:a*b,'/':lambda:a/b,'^':lambda:a**b,'=':lambda:a==b,'<>':lambda:a!=b,'>':lambda:a>b,'<':lambda:a<b,'>=':lambda:a>=b,'<=':lambda:a<=b}[n.name]()
   if n.kind=='func':
    name=n.name.upper()
    if name=='IF':return ev(n.args[1 if ev(n.args[0],sheet) else 2],sheet)
    if name=='IFERROR':
     try:return ev(n.args[0],sheet)
     except Exception:return ev(n.args[1],sheet)
    a=list(flat([ev(x,sheet) for x in n.args]));num=[x for x in a if isinstance(x,(int,float))]
    if name=='SUM':return sum(num)
    if name=='AVERAGE':return sum(num)/len(num)
    if name=='MAX':return max(num)
    if name=='MIN':return min(num)
    if name=='ABS':return abs(a[0])
    if name=='ROUND':return round(a[0],int(a[1]))
    if name=='POWER':return a[0]**a[1]
    if name=='SQRT':return math.sqrt(a[0])
    if name=='AND':return all(a)
    if name=='OR':return any(a)
    if name=='SUMPRODUCT':raise Unsupported('sumproduct_affected')
   raise Unsupported('unsupported_affected_expression:'+n.name)
  return {k:cell(k) for k in targets if k in affected}

def embedded(book):
 out=collections.defaultdict(list)
 rows=collections.defaultdict(dict)
 for key,v in book.cells.items():
  sh,co=key.rsplit('!',1)
  if sh=='Embedded Assumptions':
   m=re.fullmatch('([A-Z]+)([0-9]+)',co);rows[int(m[2])][m[1]]=v
 for row,cols in rows.items():
  if row==1 or not cols.get('A') or not cols.get('B'):continue
  sh=str(cols['A']);cells=str(cols['B']).replace('$','')
  for part in re.split('[,; ]+',cells):
   try:
    c1,r1,c2,r2=range_boundaries(part)
    if not all((c1,r1,c2,r2)):raise ValueError('Unbounded embedded scope')
    if (c2-c1+1)*(r2-r1+1)>100000:raise ValueError('Embedded scope too large')
    for ci in range(c1,c2+1):
     for ri in range(r1,r2+1):
      key=sh+'!'+get_column_letter(ci)+str(ri)
      out[key].append({'row':row,'value':cols.get('D'),'formula':cols.get('E'),'label':cols.get('C')})
   except Exception:pass
 return out

def normformula(s):return re.sub(r'\s+','',str(s or '').lstrip("'")).upper()
def constant_arithmetic(f):
 try:
  nodes=[parse_formula(f)]
  while nodes:
   node=nodes.pop();nodes.extend(node.args)
   if node.kind not in ('const','prefix','postfix','infix') or (node.kind=='const' and node.name!='number'):return False
  return True
 except Exception:return False

def literals(f):
 out=[]
 for m in NUM.finditer(f):
  v=float(m[0].rstrip('%'))/(100 if m[0].endswith('%') else 1)
  out.append({'text':m[0],'value':v,'span':[m.start(),m.end()]})
 return out

def records_for(root,hosts,missing_values):
 matching=collections.defaultdict(list);elsewhere=collections.defaultdict(list)
 if not root:return matching,elsewhere
 root=Path(root);p=root/'runtime/records.jsonl.gz';op=gzip.open
 if not p.exists():p=root/'runtime/records.jsonl';op=open
 if not p.exists():return matching,elsewhere
 with op(p,'rt') as f:
  for line in f:
   r=json.loads(line)
   if str(r.get('status','')).casefold()!='approved' or str(r.get('scenario','')).casefold()!='base case' or r.get('superseded_by'):continue
   if r.get('basis')!='Externally sourced assumption':continue
   text=r.get('metric','');entry={k:r.get(k) for k in ('id','metric','value','status','scenario','basis','document_id','period')}
   for host in hosts:
    if host in text or "'"+host.rsplit('!',1)[0]+"'!"+host.rsplit('!',1)[1] in text:matching[host].append(entry)
   if isinstance(r.get('value'),(int,float)) and abs(r['value']) in missing_values:elsewhere[abs(r['value'])].append(entry)
 return matching,elsewhere

def census(row,source):
 tid=row['taskId'];result={'taskId':tid,'problemVersionId':row['problemVersionId'],'frozenWorkbookSha256':row['workbookSha256'],'nativeSource':str(source) if source else None,'status':'uncheckable','candidates':[]}
 if not source:result['reason']='verified_native_source_unavailable';return result
 if source.suffix.lower() not in ('.xlsx','.xlsm'):result['reason']='native_legacy_format_not_converted_for_this_census';return result
 result['nativeSourceSha256']=sha(source)
 try:native=Book(source);final=Book(row['workbookPath'])
 except Exception as e:result['reason']='native_or_final_parse_error:'+type(e).__name__;return result
 requirehash=sha(row['workbookPath'])==row['workbookSha256']
 if not requirehash:raise RuntimeError('Frozen asset changed:'+tid)
 em=embedded(final);result['embeddedHostCount']=len(em)
 key=readjson(row['answerKeyPath']);targets={canon(k):v for k,v in key['targets'].items()}
 if any(t not in native.cells for t in targets):result['reason']='graded_output_not_present_in_native_source';return result
 potential=[];counts=collections.Counter()
 for host,f in native.formulas.items():
  body=f.lstrip('=')
  if not ARITH.fullmatch(body):continue
  if not constant_arithmetic(f):continue
  nums=literals(f)
  if len(nums)<2:continue
  small=[n for n in nums if n['value'].is_integer() and 0<abs(n['value'])<100 and abs(n['value']) not in STRUCTURAL]
  if not small:continue
  counts['constantCompoundSmallIntegerHosts']+=1
  ev=em.get(host,[])
  if any(normformula(x['formula'])==normformula(f) for x in ev):counts['operationSuppliedInEmbeddedFormula']+=1;continue
  if isinstance(final.cells.get(host),(int,float)) and host not in final.formulas:counts['aggregateRetainedInFinalWorkbook']+=1;continue
  visible={abs(float(x['value'])) for x in ev if isinstance(x['value'],(int,float))}
  missing=[n for n in small if abs(n['value']) not in visible]
  if not missing:counts['smallOperandsSuppliedInEmbeddedValues']+=1;continue
  potential.append((host,f,nums,missing,ev))
 result['screeningCounts']=dict(counts)
 if not potential:result['status']='no_candidate_in_supported_static_screen';return result
 cone,reverse,unresolved=native.cone(targets);result['dependencyConeCells']=len(cone);result['dependencyUnresolvedCount']=len(unresolved);result['dependencyUnresolvedExamples']=unresolved[:10]
 if unresolved:result['unresolvedReachabilityCandidates']=[{'host':p[0],'formula':p[1]} for p in potential if p[0] not in cone]
 potential=[p for p in potential if p[0] in cone];result['reachableCandidateHosts']=len(potential)
 research,elsewhere=records_for(row['researchRoot'],[p[0] for p in potential],{n['value'] for p in potential for n in p[3]}) if potential else ({},{})
 prompt=Path(row['promptPath']).read_text();assumptions=Path(row['additionalAssumptionsPath']).read_text() if row['additionalAssumptionsPath'] else '';public_text=prompt+'\n'+assumptions
 tol=key.get('tolerance',{});abstol=tol.get('numeric_abs',1e-6);reltol=tol.get('numeric_rel',1e-6)
 for host,f,nums,missing,ev in potential:
  hostcache=native.cells.get(host);sr=research.get(host,[])
  if sr and all(isinstance(x['value'],(int,float)) and isinstance(hostcache,(int,float)) and abs(x['value']-hostcache)<1e-9 for x in sr):counts['hostAggregateSuppliedInApprovedResearch']+=1;continue
  missingvals={n['value'] for n in missing}
  rownum=int(re.search(r'\d+$',host)[0]);sh=host.rsplit('!',1)[0]
  labels=[{'cell':k,'text':v} for k,v in native.cells.items() if k.startswith(sh+'!') and re.search(r'\d+$',k)[0]==str(rownum) and isinstance(v,str) and not v.startswith('#')][:10]
  c={'host':host,'nativeFormula':f,'nativeCachedValue':hostcache,'missingSmallOperands':missing,'embeddedEntries':ev,'rowLabels':labels,'approvedHostRecords':sr,
     'otherApprovedRecordsWithSameValue':{str(v):elsewhere.get(v,[])[:10] for v in missingvals},
     'sameValueFinalCells':{str(v):[k for k,x in final.cells.items() if isinstance(x,(int,float)) and abs(x)==v][:25] for v in missingvals},
     'publicTextMentionsHost':host in public_text,'publicTextContainsNativeExpression':normformula(f) in normformula(public_text),'status':'lead_requires_semantic_review',
     'proposedRemediation':'If confirmed, disclose the source calculation convention and semantic operand references; retain original economic inputs and targets. Do not publish the computed host or graded answer.'}
  if c['publicTextContainsNativeExpression']:c['status']='operation_supplied_in_public_text';result['candidates'].append(c);continue
  # Counterfactual zeros only the small literal occurrences. It measures whether
  # omitted information matters, not the correct treatment of multiplication.
  removed=f
  for n in sorted(missing,key=lambda x:x['span'][0],reverse=True):a,b=n['span'];removed=removed[:a]+'0'+removed[b:]
  try:
   tree=parse_formula(removed)
   def arithmetic(n):
    if n.kind=='const':return float(n.shape)
    if n.kind=='prefix':return (-1 if n.name=='-' else 1)*arithmetic(n.args[0])
    if n.kind=='postfix':return arithmetic(n.args[0])/100
    a,b=[arithmetic(x) for x in n.args]
    return {'+':lambda:a+b,'-':lambda:a-b,'*':lambda:a*b,'/':lambda:a/b,'^':lambda:a**b}[n.name]()
   replacement=arithmetic(tree);base=arithmetic(parse_formula(f));c['hostCounterfactualValue']=replacement;c['hostNativeArithmeticValue']=base
   changed=native.calculate(list(targets),host,replacement,reverse);impact=[]
   for t,value in changed.items():
    sourcevalue=native.numeric(t);delta=abs(value-sourcevalue);threshold=abstol+reltol*abs(targets[t])
    impact.append({'target':t,'sourceCachedValue':sourcevalue,'frozenExpectedValue':targets[t],'counterfactualValue':value,'absoluteChange':delta,'tolerance':threshold,'exceedsTolerance':delta>threshold,'sourceMatchesFrozenExpected':abs(sourcevalue-targets[t])<=threshold})
   c['downstreamCounterfactual']=impact;c['beyondToleranceImpact']=any(x['exceedsTolerance'] for x in impact)
   c['sourceTargetsMatchFrozen']=all(x['sourceMatchesFrozenExpected'] for x in impact)
  except Exception as e:c['counterfactualUncheckable']=str(e)[:160]
  result['candidates'].append(c)
 result['screeningCounts']=dict(counts)
 result['status']='leads' if any(c['status']=='lead_requires_semantic_review' for c in result['candidates']) else ('uncheckable_partial_dependency_cone' if unresolved else 'no_candidate_in_supported_static_screen')
 return result

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--start-index',type=int,default=0);args=parser.parse_args()
 OUT.mkdir(parents=True,exist_ok=True);rows=readjson(WORK/'final-assets.json');save(OUT/'population.json',{'assetsSha256':sha(WORK/'final-assets.json'),'tasks':[{k:r[k] for k in ('taskId','problemVersionId','workbookSha256','answerKeySha256','promptSha256')} for r in rows]})
 sources=collections.defaultdict(list)
 for folder in ('RawWorkbooks1','RawWorkbooks2','RawWorkbooks3'):
  for p in (ROOT.parent/folder).iterdir():
   if p.is_file():sources[p.stem].append(p)
 results=[]
 for i,row in enumerate(rows):
  if i<args.start_index:continue
  target=OUT/'tasks'/(row['taskId']+'.json')
  if target.exists():r=readjson(target)
  else:
   found=sources[row['taskId']];signal.signal(signal.SIGALRM,deadline_handler);signal.alarm(35)
   try:r=census(row,found[0] if len(found)==1 else None)
   except AuditDeadline:r={'taskId':row['taskId'],'problemVersionId':row['problemVersionId'],'frozenWorkbookSha256':row['workbookSha256'],'nativeSource':str(found[0]) if len(found)==1 else None,'status':'uncheckable','reason':'bounded_native_audit_exceeded_35_seconds','candidates':[]}
   finally:signal.alarm(0)
   save(target,r)
  results.append(r)
  if i%20==0:print(json.dumps({'reviewed':i+1,'tasks':len(rows),'last':row['taskId'],'statuses':dict(collections.Counter(r['status'] for r in results))}),flush=True)
 if args.start_index:
  print(json.dumps({'rangeFinished':args.start_index,'processed':len(results)}),flush=True);return
 save(OUT/'manifest.json',results);save(OUT/'summary.json',{'tasks':len(results),'statuses':dict(collections.Counter(r['status'] for r in results)),'candidateHosts':sum(len(r['candidates']) for r in results),'tasksWithLeads':[r['taskId'] for r in results if r['status']=='leads']})
 print(json.dumps(readjson(OUT/'summary.json')),flush=True)
if __name__=='__main__':main()

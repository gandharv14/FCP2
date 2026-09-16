"""Exact selected size-hold source/evidence/code closure; no authoring mutations."""
import argparse,json
from pathlib import Path
from scripts.fleet import build_0190_successor as B
from scripts.fleet.build_size_hold_successor import CATALOG
from scripts.fleet.migration import export_files

def closure(workbook,successor):
 B.require(workbook in CATALOG,'unreviewed workbook');home=B.Q/'authoring'/(workbook+'-'+CATALOG[workbook][0]);reconstruction=B.Q/('reconstruction-'+workbook+'-attempt-001');code=Path(B.read(B.record(home/'frozen-code.json'))['root']);generated=Path(B.read(B.record(B.Q/'generated-curation-snapshot-v1.json'))['root']);paths=set();pending=[]
 def add(path,tree=False):
  path=Path(path)
  if not path.exists():return
  if any(p.is_symlink() for p in (path,*path.parents)):raise ValueError('symlink custody forbidden')
  if path.name.startswith('.env') or path.name in {'credentials.json','application_default_credentials.json'} or path.suffix in {'.pem','.key'}:return
  if path.is_dir():
   if tree:
    for p in path.rglob('*'):
     if p.is_file() and not {'__pycache__','.pytest_cache'}&set(p.parts):add(p)
   return
  if path in paths:return
  paths.add(path)
  if path.suffix=='.json' and not path.is_relative_to(code) and path.name not in {'inventory.json','queue.json','workbooks.json','survey.json'} and (path.is_relative_to(home) or path.is_relative_to(reconstruction) or path.name.startswith(workbook+'-')):pending.append(path)
 def walk(value,key=None):
  if key in {'env_file','token_file'}:return
  if isinstance(value,str) and value.startswith('/Users/gandharvmahajan/'):add(value)
  elif isinstance(value,dict):
   for k,v in value.items():walk(v,k)
  elif isinstance(value,list):
   for v in value:walk(v)
 for p in (home,reconstruction,code,generated,successor):add(p,True)
 add(B.Q/'generated-curation-snapshot-v1.json');add(B.ROOT/'scripts/fleet/inspect_size_hold.py')
 while pending:
  try:walk(json.loads(pending.pop().read_bytes()))
  except (ValueError,UnicodeDecodeError):continue
 return sorted(paths)
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--workbook',choices=sorted(CATALOG),required=True);p.add_argument('--successor',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();out=a.output.absolute();out.mkdir();paths=closure(a.workbook,a.successor.absolute());print(json.dumps(dict(manifest=export_files(paths,out/'inputs.tar.gz',out/'manifest.json'),files=len(paths),bytes=sum(p.stat().st_size for p in paths))))
if __name__=='__main__':main()

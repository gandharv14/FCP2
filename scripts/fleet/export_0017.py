"""Preserve exact0017historical closure without exporting unrelated surveys."""
import argparse,json,sys
from pathlib import Path
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from scripts.fleet.build_0017_successor import HOME
from scripts.fleet.migration import export_files,binding


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);a=p.parse_args();out=a.output.absolute();out.mkdir(parents=True,exist_ok=False)
    q=HOME.parents[1];reconstruction=q/'reconstruction-0017-attempt-001'
    code=Path(json.loads((HOME/'frozen-code.json').read_text())['root'])
    paths=set();pending=[]
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
        if path.suffix=='.json' and not path.is_relative_to(code) and path.name not in {'inventory.json','queue.json','workbooks.json','survey.json'} and (path.is_relative_to(HOME) or path.is_relative_to(reconstruction) or path.name.startswith('0017-') or path.is_relative_to(Path(__file__).resolve().parents[2]/'diagnostics/vm-fleet-20260912/holds-0017')):pending.append(path)
    def walk(v,k=None):
        if k in {'env_file','token_file'}:return
        if isinstance(v,str) and v.startswith('/Users/gandharvmahajan/'):add(v)
        elif isinstance(v,dict):
            for key,x in v.items():walk(x,key)
        elif isinstance(v,list):
            for x in v:walk(x)
    for p in (HOME,reconstruction,code,HOME.parents[1]/'code'/json.loads((HOME.parents[1]/'generated-curation-snapshot-v1.json').read_text())['root'].split('/')[-1]):add(p,True)
    add(HOME.parents[1]/'generated-curation-snapshot-v1.json')
    successor=Path(__file__).resolve().parents[2]/'diagnostics/vm-fleet-20260912/holds-0017/gated-successor-003'
    add(successor,True);add(successor.parent/'scope-repair-001',True);add(successor.parent/'assessment-001',True)
    while pending:
        try:walk(json.loads(pending.pop().read_bytes()))
        except (ValueError,UnicodeDecodeError):continue
    result=export_files(paths,out/'inputs.tar.gz',out/'manifest.json')
    print(json.dumps({'manifest':result,'file_count':len(paths),'bytes':sum(p.stat().st_size for p in paths)}))

if __name__=='__main__':main()

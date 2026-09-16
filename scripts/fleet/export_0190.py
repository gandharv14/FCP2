"""Enumerate and transfer the exact retained 0190 continuation evidence closure."""
import argparse
import json
from pathlib import Path
import sys
if __package__ in (None, ''):sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from scripts.fleet import recover_0190 as R
from scripts.fleet.migration import export_files


def closure():
    selected=set();pending=[];seen=set();expanded=set()
    required_snapshots=[Path(R.SNAPSHOT['path']),R.BASE/'generated-curation-snapshot-v1.json',R.BASE/'0190-existing-review-snapshot-v4.json']
    required_roots={Path(json.loads(p.read_text())['root']) for p in required_snapshots}
    def add(path,*,tree=False):
        p=Path(path)
        if not p.exists():return
        if any(x.is_symlink() for x in (p,*p.parents)):raise ValueError('symlink evidence not portable')
        if p.name.startswith('.env') or p.name in {'credentials.json','application_default_credentials.json'} or p.suffix in {'.pem','.key'}:return
        if p.is_dir():
            if (tree or p in required_roots) and p not in expanded:
                expanded.add(p)
                for child in p.rglob('*'):
                    if child.is_file() and not {'__pycache__','.pytest_cache'}&set(child.parts):add(child)
            return
        if p not in selected:
            selected.add(p)
            if p.suffix=='.json' and '/code/' not in str(p) and p.name not in {'inventory.json','queue.json','workbooks.json','survey.json'} and (p.is_relative_to(R.HOME) or p.is_relative_to(R.BASE/'reconstruction-0190-attempt-001') or p.name.startswith('0190-') or p.name in {'authority-inputs-v4.json','profile.json','generated-curation-snapshot-v1.json'}):pending.append(p)
    def walk(value,key=None):
        if key in {'env_file','token_file'}:return
        if isinstance(value,str) and value.startswith('/Users/gandharvmahajan/'):
            add(value)
        elif isinstance(value,dict):
            for k,v in value.items():walk(v,k)
        elif isinstance(value,list):
            for v in value:walk(v)
    add(R.HOME,tree=True);add(R.BASE/'reconstruction-0190-attempt-001',tree=True)
    for p in [R.SNAPSHOT['path'],R.INPUTS['path'],R.BASE/'0190-existing-review-snapshot-v4.json',R.BASE/'0190-indexed-review-repair/completed-lanes-development-v2/profile.json']:add(p)
    while pending:
        p=pending.pop()
        if p in seen:continue
        seen.add(p)
        try:walk(json.loads(p.read_bytes()))
        except (ValueError,UnicodeDecodeError):continue
    for p in required_roots:add(p,tree=True)
    return sorted(selected)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--inventory-only',action='store_true');a=p.parse_args()
    a.output=a.output.absolute();paths=closure();a.output.mkdir(parents=True,exist_ok=False)
    inventory={'files':[R.record(p) for p in paths],'file_count':len(paths),'bytes':sum(p.stat().st_size for p in paths),'namespace':'original_absolute_paths','credential_files':0}
    (a.output/'closure.json').write_text(json.dumps(inventory,indent=2,sort_keys=True)+'\n')
    if not a.inventory_only:inventory['manifest']=export_files(paths,a.output/'inputs.tar.gz',a.output/'manifest.json')
    print(json.dumps({k:v for k,v in inventory.items() if k!='files'}))

if __name__=='__main__':main()

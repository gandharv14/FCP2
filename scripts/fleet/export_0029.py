"""Export0029 immutable code and historical closure at original absolute paths."""
import argparse,json,sys
from pathlib import Path
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from scripts.fleet import build_0029_successor as B
from scripts.fleet.migration import export_files,binding
from scripts.fleet import migration_symlinks as A

def closure(successor):
    home=B.TASK;q=B.Q;reconstruction=q/'reconstruction-0029-attempt-001';private=q/'0029-dialogue-period-repair'
    selected=set();pending=[];expanded=set();skipped=set();missing=set();aliases={};deferred={}
    roots={Path(json.loads((home/'frozen-code.json').read_text())['root']),Path(json.loads((q/'generated-curation-snapshot-v1.json').read_text())['root'])}
    def add(value,tree=False):
        p=Path(value)
        if p.name.startswith('.env') or p.name.endswith('.env') or p.name in {'credentials.json','application_default_credentials.json'} or p.suffix in {'.pem','.key'}:
            skipped.add(str(p));return
        if not p.exists():missing.add(str(p));return
        if p.is_symlink() and p.is_relative_to(reconstruction):
            aliases[str(p)]=A.describe(p,reconstruction);add(p.resolve(strict=True),tree=True);return
        broad=B.ROOT/'seg_out'
        if broad in p.parents and broad.is_symlink() and str(broad.readlink())=='../seg_out':
            target=p.resolve(strict=True)
            if not target.is_relative_to(B.ROOT.parent/'seg_out'):raise ValueError('historical seg_out alias escaped root')
            deferred[str(p)]={'alias':str(broad),'link_text':'../seg_out','referenced_real_target':str(target),'reason':'Full16GiB alias target not transferred or claimed verified; actual owning probe will determine need.'}
            if target.is_file():add(target)
            return
        links=[x for x in (p,*p.parents) if x.is_symlink() and x.parent==B.ROOT]
        if links:
            alias=links[0];target=p.resolve(strict=True)
            if not target.is_relative_to(B.ROOT.parent):raise ValueError('broad historical alias escaped authorized root')
            deferred[str(p)]={'alias':str(alias),'link_text':str(alias.readlink()),'referenced_real_target':str(target),'reason':'Broad historical alias deferred; no full-tree claim or alias creation.'}
            if target.is_file():add(target)
            return
        if p.parent==B.ROOT/'.venv/bin' and p.name in {'python','python3','python3.12'} and p.is_symlink():
            target=p.resolve(strict=True)
            if not target.is_relative_to(Path('/Users/gandharvmahajan/.local/share/uv/python')):raise ValueError('historical interpreter target outside managed runtime')
            deferred[str(p)]={'alias':str(p),'link_text':str(p.readlink()),'referenced_real_target':str(target),'reason':'Historical Mac interpreter alias is not executable Linux configuration; exact real binary retained only as evidence.'}
            add(target);return
        if any(x.is_symlink() for x in (p,*p.parents)):
            # Interpreter symlinks are not evidence substitutes. An exact real
            # Mach-O executable named in a historical guard is exported as data.
            raise ValueError('0029 symlink path cannot enter closure: '+str(p))
        if p.is_dir():
            if (tree or p in roots) and p not in expanded:
                expanded.add(p)
                for f in p.rglob('*'):
                    if (f.is_file() or f.is_symlink()) and not {'__pycache__','.pytest_cache'}&set(f.parts):add(f)
            return
        if p in selected:return
        selected.add(p)
        if p.suffix=='.json' and '/code/' not in str(p) and p.name not in {'inventory.json','queue.json','workbooks.json','survey.json'}:
            if p.is_relative_to(home) or p.is_relative_to(reconstruction) or p.is_relative_to(private) or p.is_relative_to(successor) or '0029' in str(p) or 'snapshot' in p.name or p.name in {'frozen-code.json','profile.json'}:pending.append(p)
    def walk(v,key=None):
        if key in {'env_file','token_file','credential_file','api_key'}:return
        if isinstance(v,str) and v.startswith('/Users/gandharvmahajan/'):add(v)
        elif isinstance(v,dict):
            if isinstance(v.get('root'),str) and ('files' in v or 'files_count' in v) and '/code/' in v['root']:
                roots.add(Path(v['root']));add(v['root'],True)
            for k,x in v.items():walk(x,k)
        elif isinstance(v,list):
            for x in v:walk(x)
    for p in [home,reconstruction,private,successor,*roots]:add(p,True)
    for p in [q/'generated-curation-snapshot-v1.json',Path(__file__).with_name('probe_0029_successor.py'),Path(A.__file__)]:add(p)
    while pending:
        p=pending.pop()
        try:v=json.loads(p.read_bytes())
        except (ValueError,UnicodeDecodeError):continue
        walk(v)
    return sorted(selected),sorted(skipped),sorted(missing),dict(schema_version=A.SCHEMA,root=str(reconstruction),aliases=list(aliases.values())),list(deferred.values())

def main():
    p=argparse.ArgumentParser();p.add_argument('--successor',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    out=a.output.absolute();out.mkdir(parents=True,exist_ok=False)
    paths,skipped,missing,aliases,deferred=closure(a.successor.absolute())
    inventory=dict(schema_version='harbor-0029-closure/v1',files=[binding(p) for p in paths],files_count=len(paths),bytes=sum(p.stat().st_size for p in paths),credential_files=0,excluded_credential_locators=skipped,missing_references=missing,historical_bytes_rewritten=False,executable_policy='Historical macOS executable bytes are evidence only; Linux execution uses isolated native Python.')
    (out/'closure.json').write_text(json.dumps(inventory,indent=2,sort_keys=True)+'\n')
    (out/'aliases.json').write_text(json.dumps(aliases,indent=2,sort_keys=True)+'\n')
    (out/'deferred-aliases.json').write_text(json.dumps(deferred,indent=2,sort_keys=True)+'\n')
    manifest=export_files(paths,out/'inputs.tar.gz',out/'manifest.json')
    print(json.dumps(dict(manifest=manifest,files=len(paths),bytes=inventory['bytes'],excluded_credential_files=len(skipped),missing_references=len(missing))))

if __name__=='__main__':main()

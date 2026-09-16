"""Export exact 0270 reconstruction/evidence bytes for its first authoring run."""
from pathlib import Path
import json
import sys
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.fleet.migration import export_files

def main():
    root=Path(__file__).resolve().parents[1]
    q=root/'diagnostics/authoring-queue-extension-20260911T185027Z'
    out=root/'diagnostics/vm-fleet-20260912/inputs-0270'
    out.mkdir(exist_ok=False)
    original=root.parent/'RawWorkbooks3/0270.xlsx'
    selected={p for p in (q/'reconstruction-0270-attempt-001').rglob('*') if p.is_file() and not p.is_symlink()}
    selected.update([original,q/'0270-generated-curation-adoption.json',q/'0270-reconstruction-handoff-001.json',q/'queue.json',
        root/'diagnostics/authoring-resume-20260910/root/inventory.json',root/'taxonomy_out/workbooks.json',
        root/'diagnostics/vm-fleet-20260912/control/history-001.json',root/'diagnostics/vm-fleet-20260912/local-dispatch-pause-001.json'])
    runners=root/'diagnostics/authoring-resume-20260910/runner'
    selected.update(runners/name for name in ['author_workbook.py','author_workbook_v3.py','advance_authoring_v7.py','verify_published.py'])
    # Traverse only the selected reconstruction's evidence closure. The complete
    # original inventory/queue stay intact, but unrelated workbook bytes needn't
    # be copied merely because they occur in that denominator.
    inspect=[p for p in selected if p.suffix=='.json' and ('reconstruction-0270' in str(p) or p.name.startswith('0270-'))]
    visited=set()
    def paths(value):
        if isinstance(value,str) and value.startswith(str(root.parent)+'/'):
            p=Path(value)
            if p.is_file() and not any(x.is_symlink() for x in (p,*p.parents)):
                if p.name.startswith('.env') or p.suffix in {'.pem','.key'}:return
                yield p
        elif isinstance(value,dict):
            for v in value.values():yield from paths(v)
        elif isinstance(value,list):
            for v in value:yield from paths(v)
    while inspect:
        p=inspect.pop()
        if p in visited:continue
        visited.add(p)
        try:value=json.loads(p.read_bytes())
        except (ValueError,UnicodeDecodeError):continue
        for child in paths(value):
            if child not in selected:
                selected.add(child)
                if child.suffix=='.json' and child.name not in {'queue.json','inventory.json'}:inspect.append(child)
    result=export_files(selected,out/'inputs.tar.gz',out/'manifest.json')
    print(json.dumps({'manifest':result,'files':len(selected),'bytes':sum(p.stat().st_size for p in selected),'archive_bytes':(out/'inputs.tar.gz').stat().st_size}))

if __name__=='__main__':main()

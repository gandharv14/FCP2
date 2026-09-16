"""Seal the reviewed historical custody successor without mutable live imports."""
from __future__ import annotations
import ast,hashlib,json
from pathlib import Path
ROOT=Path('/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline')
PRIOR=ROOT/'diagnostics/vm-fleet-20260912/control/first-custody-003/implementation-manifest.json'
RECOVERED=ROOT/'diagnostics/vm-fleet-20260912/control/source-first-custody-004/implementation/scripts/fleet/prepare_queue.py'
RECOVERY_ROOTS=(
    ROOT/'diagnostics/vm-fleet-20260912/control/first-custody-004/implementation/scripts/fleet',
    ROOT/'diagnostics/vm-fleet-20260912/control/source-first-custody-004/implementation/scripts/fleet',
)

def binding(path):
    p=Path(path).absolute()
    if any(x.is_symlink() for x in (p,*p.parents)) or not p.is_file():raise ValueError('regular frozen custody file required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def recover_dependency(row):
    """Locate retained bytes by the predecessor hash, never by version guesses."""
    original=Path(row['path'])
    candidates=[original,*(root/original.name for root in RECOVERY_ROOTS)]
    if original.name=='prepare_queue.py':candidates.append(RECOVERED)
    for path in candidates:
        try:found=binding(path)
        except (OSError,ValueError):continue
        if found['sha256']==row['sha256']:return path
    raise ValueError('retained dependency unavailable')

def expected_successor(source):
    def replace(old,new):
        nonlocal source
        if source.count(old)!=1:raise ValueError('closed selected-smoke patch anchor differs')
        source=source.replace(old,new)
    replace("SCHEMA='harbor-fleet-first-historical-custody/v1'", "SCHEMA='harbor-fleet-first-historical-custody/v2'")
    replace("'smoke_verification','smoke_collection','worker','new_owner','remote_receipt','expected_reason'", "'smoke_verification','smoke_collection','smoke_workbook','worker','new_owner','remote_receipt','expected_reason'")
    replace("    job=checked(p['job']);a=authority(p,job)","    require(p['smoke_workbook']=='0029','this successor selects the independently qualified 0029 smoke')\n    job=checked(p['job']);a=authority(p,job)")
    node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='smoke_gate');old=''.join(source.splitlines(keepends=True)[node.lineno-1:node.end_lineno])
    new=old.replace("    operation_admission(status,job)","    require(p['smoke_workbook']=='0029','exact selected smoke required')\n    operation_admission(status,job)").replace("=='0270'","==p['smoke_workbook']")
    replace(old,new);return source

def freeze(output,selected_source):
    output=Path(output).absolute()
    if output.exists() or any(x.is_symlink() for x in (output,*output.parents)):raise ValueError('new immutable custody namespace required')
    prior=json.loads(PRIOR.read_bytes());originals=[];copies={}
    for row in prior['files']:
        p=recover_dependency(row)
        raw=p.read_bytes();originals.append(dict(original=row,recovered_from=binding(p)))
        copies['scripts/fleet/'+Path(row['path']).name]=raw
    old=copies['scripts/fleet/first_custody_concurrent.py'].decode();selected_source=Path(selected_source)
    if selected_source.read_text()!=expected_successor(old):raise ValueError('unreviewed authority or operation-policy change')
    copies['scripts/fleet/first_custody_selected_smoke.py']=selected_source.read_bytes()
    copies['scripts/__init__.py']=b'';copies['scripts/fleet/__init__.py']=b''
    output.mkdir(parents=True);root=output/'implementation';files=[]
    for relative,raw in copies.items():
        p=root/relative;p.parent.mkdir(parents=True,exist_ok=True)
        with p.open('xb') as f:f.write(raw)
        files.append(binding(p))
    manifest=dict(schema_version='frozen-selected-historical-custody/v1',root=str(root),entry_point=binding(root/'scripts/fleet/first_custody_selected_smoke.py'),files=files,originals=originals,predecessor=binding(PRIOR),selected_source=binding(selected_source),smoke_workbook='0029',operation_policy_unchanged=True,claims_changed=0,models_dispatched=0)
    path=output/'manifest.json';path.write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n');mb=binding(path)
    code='''import hashlib,json,runpy,sys
from pathlib import Path
BOUND=SELECTED
p=Path(BOUND['path'])
if p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest()!=BOUND['sha256']:raise ValueError('custody manifest changed')
m=json.loads(p.read_bytes());root=Path(m['root'])
if any(n=='scripts' or n.startswith('scripts.') for n in sys.modules):raise ValueError('foreign scripts already loaded')
expected={x['path'] for x in m['files']};actual={str(p) for p in root.rglob('*') if p.is_file()}
if actual!=expected or any(p.is_symlink() for p in root.rglob('*')):raise ValueError('custody import inventory changed')
for row in m['files']:
 p=Path(row['path'])
 if any(x.is_symlink() for x in (p,*p.parents)) or hashlib.sha256(p.read_bytes()).hexdigest()!=row['sha256']:raise ValueError('custody code changed')
if m['entry_point'] not in m['files']:raise ValueError('unbound entry')
sys.path.insert(0,str(root));sys.argv[0]=m['entry_point']['path'];runpy.run_path(sys.argv[0],run_name='__main__')
'''.replace('SELECTED',repr(mb))
    runner=output/'run_verified.py';runner.write_text(code)
    return dict(manifest=mb,wrapper=binding(runner),entry_point=manifest['entry_point'])

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--selected-source',required=True);a=p.parse_args();print(json.dumps(freeze(a.output,a.selected_source),indent=2))

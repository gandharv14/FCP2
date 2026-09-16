"""Freeze a closed root control implementation and all its local imports.

Only code is copied. This does not prepare a task, claim work, or send signals.
The resulting isolated wrapper remains independent of future repository edits.
"""
from pathlib import Path
import argparse
import hashlib
import json

PROFILES = {
    'held-job-launch': ('launch_job.py', ['migration.py', 'coordinator.py', 'pool.py', 'prepare_queue.py']),
    'oneoff': ('oneoff_control.py', ['migration.py', 'pool.py', 'prepare_queue.py']),
    'fleet-v2': ('fleet_control_v2.py', ['publish.py', 'coordinator.py', 'prepare_queue.py', 'migration.py']),
    '0270-refresh-launch': ('smoke_refresh_successor_launch.py',
        ['smoke_refresh_successor_layout.py', 'migration.py', 'coordinator.py', 'pool.py', 'prepare_queue.py']),
}


def binding(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
        raise ValueError('regular code file required')
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def freeze(source_root, output, profile, *, layout_sha256=None):
    source_root = Path(source_root).absolute(); output = Path(output).absolute()
    if profile not in PROFILES: raise ValueError('closed control profile required')
    if any(p.is_symlink() for p in (output, *output.parents)) or output.exists():
        raise ValueError('new regular control namespace required')
    entry, dependencies = PROFILES[profile]
    if profile == '0270-refresh-launch':
        if not isinstance(layout_sha256, str) or len(layout_sha256) != 64 or any(c not in '0123456789abcdef' for c in layout_sha256):
            raise ValueError('exact reviewed layout digest required')
    elif layout_sha256 is not None: raise ValueError('unexpected layout binding')
    originals = []
    for name in [entry, *dependencies]:
        path = source_root/'scripts/fleet'/name
        originals.append((name, binding(path), path.read_bytes()))
    # Verify the complete source list before creating the output.
    output.mkdir(parents=True); root = output/'implementation'
    package = root/'scripts/fleet'; package.mkdir(parents=True)
    files = []
    for path in [root/'scripts/__init__.py', package/'__init__.py']:
        path.write_bytes(b''); files.append(binding(path))
    for name, original, raw in originals:
        if name == entry and layout_sha256 is not None:
            needle = b'LAYOUT_SHA256=""'
            if raw.count(needle) != 1: raise ValueError('unrecognized launcher layout slot')
            raw = raw.replace(needle, ('LAYOUT_SHA256='+repr(layout_sha256)).encode())
        target = package/name; target.write_bytes(raw); files.append(binding(target))
    manifest = {'schema_version': 'harbor-frozen-root-control/v1', 'profile': profile,
        'root': str(root), 'entry_point': binding(package/entry), 'files': files,
        'originals': [b for _, b, _ in originals], 'layout_sha256': layout_sha256}
    path = output/'manifest.json'; path.write_text(json.dumps(manifest, sort_keys=True, indent=2)+'\n')
    selected = binding(path)
    wrapper = '''import hashlib,json,runpy,sys
from pathlib import Path
BOUND = SELECTED
def verify(row):
 p=Path(row['path'])
 if any(x.is_symlink() for x in (p,*p.parents)) or hashlib.sha256(p.read_bytes()).hexdigest()!=row['sha256']:
  raise ValueError('frozen control changed')
 return p
m=json.loads(verify(BOUND).read_bytes()); root=Path(m['root'])
if m['schema_version']!='harbor-frozen-root-control/v1':raise ValueError('unknown frozen control')
expected={row['path'] for row in m['files']}
actual={str(p) for p in root.rglob('*') if p.is_file()}
if actual!=expected or any(p.is_symlink() for p in root.rglob('*')):raise ValueError('frozen import inventory changed')
for row in m['files']:verify(row)
if m['entry_point'] not in m['files']:raise ValueError('unbound entry point')
if any(n=='scripts' or n.startswith('scripts.') for n in sys.modules):raise ValueError('foreign preloaded scripts package')
sys.path.insert(0,str(root));sys.argv[0]=m['entry_point']['path']
runpy.run_path(sys.argv[0],run_name='__main__')
'''.replace('SELECTED', repr(selected))
    runner = output/'run_verified.py'; runner.write_text(wrapper)
    return {'manifest': selected, 'wrapper': binding(runner), 'entry_point': manifest['entry_point']}


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source-root', required=True)
    p.add_argument('--output', required=True);p.add_argument('--profile', choices=PROFILES, required=True)
    p.add_argument('--layout-sha256');a=p.parse_args()
    print(json.dumps(freeze(a.source_root,a.output,a.profile,layout_sha256=a.layout_sha256),indent=2))

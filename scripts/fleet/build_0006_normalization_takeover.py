"""Derive one explicit external environment guard reader from retained T2 bytes."""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import sys


def require(ok, message):
    if not ok:
        raise ValueError(message)


def binding(path):
    p = Path(path).absolute()
    require(not any(x.is_symlink() for x in (p, *p.parents)), 'symlink takeover evidence')
    return dict(path=str(p), sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def read(row):
    require(binding(row['path']) == row, 'takeover evidence changed')
    return json.loads(Path(row['path']).read_bytes())


def retain(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(raw); f.flush(); os.fsync(f.fileno())
    return binding(path)

PARENT = 'b12f4b3f7fd6f299c8a3079f519e96028bcac6a2f745ba83de649146aae3fad6'
GUARD = '53929796e0eac7b925a7d5e2af1d42e3e402a517c4eae4e8adaa2acff1cf4714'


def rendered(original, helper, profile):
    require(hashlib.sha256(original.encode()).hexdigest() == PARENT, 'exact retained T2 reader required')
    patches = {
        "type(guard['returncode']) is int or _fleet_retained_walltime(s,guard)":
        "type(guard['returncode']) is int or (_fleet_normalization_reader().allowed(s,guard,_FLEET_NORMALIZATION_PROFILE) if s.guard['sha256']==_FLEET_NORMALIZATION_GUARD else _fleet_retained_walltime(s,guard))",
        "not external or (s.config is not None and s.worker_kind=='instruction')":
        "not external or (s.config is not None and (s.worker_kind=='instruction' or (s.worker_kind=='environment' and s.guard['sha256']==_FLEET_NORMALIZATION_GUARD)))",
        "external_records=_external_instruction_guard(s,intent,run,path) if external else []":
        "external_records=(_fleet_normalization_reader().external_environment(s,intent,run,path,_FLEET_NORMALIZATION_PROFILE) if s.worker_kind=='environment' else _external_instruction_guard(s,intent,run,path)) if external else []",
    }
    for old, new in patches.items():
        require(original.count(old) == 1, 'retained takeover predicate changed')
        original = original.replace(old, new, 1)
    return original + '\n\nHISTORICAL_IMPLEMENTATIONS=HISTORICAL_IMPLEMENTATIONS | {' + repr(PARENT) + '}\n' + \
        '_FLEET_NORMALIZATION_PROFILE=' + repr(profile) + '\n_FLEET_NORMALIZATION_GUARD=' + repr(GUARD) + '''

def _fleet_normalization_reader():
    import hashlib,importlib.util,sys
    helper=__HELPER__
    path=Path(helper['path'])
    require(not any(p.is_symlink() for p in (path,*path.parents)) and hashlib.sha256(path.read_bytes()).hexdigest()==helper['sha256'],'normalization timeout reader changed')
    name='selected_0006_normalization_timeout_reader'
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
    return module
'''.replace('__HELPER__', repr(helper))


def build(parent, helper, profile, directory):
    for row in (parent, helper, profile):
        require(binding(row['path']) == row, 'selected takeover dependency changed')
    require(parent['sha256'] == PARENT, 'unreviewed takeover parent')
    directory = Path(directory)
    require(not directory.exists(), 'takeover reader version already exists')
    directory.mkdir(parents=True)
    entry = retain(directory/'takeover.py', rendered(Path(parent['path']).read_text(), helper, profile).encode())
    import json
    return retain(directory/'manifest.json', (json.dumps(dict(schema_version='0006-normalization-timeout-takeover-reader/v1',
        parent=parent, helper=helper, profile=profile, entry=entry, expired_replacement_limit=3,
        semantic_budget_reset=False), sort_keys=True, indent=2)+'\n').encode())


def install(selected):
    meta = read(selected)
    require(set(meta) == {'schema_version', 'parent', 'helper', 'profile', 'entry',
                         'expired_replacement_limit', 'semantic_budget_reset'}
            and meta['schema_version'] == '0006-normalization-timeout-takeover-reader/v1'
            and meta['expired_replacement_limit'] == 3 and meta['semantic_budget_reset'] is False,
            'closed normalization takeover manifest required')
    for key in ('parent', 'helper', 'profile', 'entry'):
        require(binding(meta[key]['path']) == meta[key], 'selected takeover implementation changed')
    require(Path(meta['entry']['path']).read_text() == rendered(Path(meta['parent']['path']).read_text(),
            meta['helper'], meta['profile']), 'unreviewed takeover implementation delta')
    name = 'scripts.batch.harbor_lease_takeover'
    spec = importlib.util.spec_from_file_location(name, meta['entry']['path'])
    module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
    import scripts.batch
    scripts.batch.harbor_lease_takeover = module
    require(module.MAX_REPLACEMENTS == 3 and module.MAX_DURATION_NS == 7200*10**9,
            'original lease lifecycle bounds changed')
    return module

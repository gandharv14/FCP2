"""Cold, hash-bound runtime loader for the closed 0200 source correction.

Loading this runtime creates no owning permit or request. The guarded parent and
child select the same loader plan and independently check its full inventory.
"""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import sys

SOURCE_SHA = 'bf807aa63628a337e6a333764fdac8216e201f2980b42b57a177406051ce23c8'
VERSION = '3ea7e135b1b4dc2acaa88b5bff985ccf49f98a3aa3c46f3c886ce06b22ba1c11'
STDLIB_SHA = '5bb59bfa3010bc1ba156b20ba12ae602881b590877c39e63fd98b6a6dda588a8'
GATE_SHA = '8e284b66b43b8acd215700a812d242ae3eb8d6cb964122addfdb27c8441baf92'
POLICY_SHA = '17d52d922dc4141f33e9627ece86762e8ebb664233278956a9485b8a2b52b3a4'
AUTHOR = {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/authoring-resume-20260910/runner/author_workbook.py',
          'sha256': '414a278fe2bc52858788e1253af18d4989f0275869e86fbab6b071a13b241666'}
ADAPTERS = ('source_card_authoring', 'source_card_reader', 'source_card_readiness',
            'source_card_producer', 'source_card_worker', 'source_card_authorize', 'source_card_install')


def require(value, message):
    if not value: raise ValueError(message)


def binding(path):
    path = Path(path)
    require(path.is_absolute() and not any(p.is_symlink() for p in (path, *path.parents)),
            'exact source-card runtime path required')
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def checked(row):
    require(type(row) is dict and set(row) == {'path', 'sha256'} and binding(row['path']) == row,
            'source-card runtime evidence changed')
    return json.loads(Path(row['path']).read_bytes())


def module(name, row):
    require(binding(row['path']) == row, 'source-card runtime implementation changed')
    previous = sys.modules.get(name)
    require(previous is None or binding(previous.__file__) == row, 'foreign cached runtime module: '+name)
    if previous is not None: return previous
    spec = importlib.util.spec_from_file_location(name, row['path'])
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    try: spec.loader.exec_module(value)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    if '.' in name:
        parent, leaf = name.rsplit('.', 1)
        setattr(sys.modules[parent], leaf, value)
    return value


def load(plan_binding, *, probe):
    require(sys.flags.isolated == 1 and sys.dont_write_bytecode, 'fresh -I -B source-card interpreter required')
    plan = checked(plan_binding)
    require(set(plan) == {'schema_version', 'workbook', 'source_plan', 'reader_plan',
                         'implementation_manifest', 'stdlib', 'model_gate', 'capacity_policy',
                         'original_authoring_job'} and
            plan['schema_version'] == 'fleet-0200-source-card-runtime/v1' and plan['workbook'] == '0200' and
            plan['source_plan']['sha256'] == SOURCE_SHA and type(probe) is bool,
            'closed source-card runtime plan required')
    require(plan['stdlib']['sha256'] == STDLIB_SHA and plan['model_gate']['sha256'] == GATE_SHA and
            plan['capacity_policy']['sha256'] == POLICY_SHA, 'unreviewed runtime or model-capacity policy')
    stdlib = module('source_card_runtime_stdlib', plan['stdlib'])
    inspect = stdlib.preload_inspect()
    import asyncio, dataclasses
    require(callable(inspect.isgenerator) and callable(dataclasses.dataclass), 'cold runtime dependencies incomplete')
    os.environ['PATH'] = str(Path(sys.executable).absolute().parent)+os.pathsep+os.environ.get('PATH', '')
    source = checked(plan['source_plan']); snapshot = checked(source['snapshot']); root = Path(snapshot['root'])
    require(root.name == VERSION and snapshot['fingerprint'] == VERSION and
            checked(binding(root/'.pipeline-code.json')) == snapshot['files'], 'original task manifest differs')
    require(hashlib.sha256(json.dumps(snapshot['files'], sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest() == VERSION,
            'original task fingerprint differs')
    full = {**snapshot['files'], '.pipeline-code.json': binding(root/'.pipeline-code.json')['sha256']}
    actual = {}
    for path in root.rglob('*'):
        require(not path.is_symlink(), 'symlink in original task snapshot')
        if '__pycache__' not in path.parts and path.is_file(): actual[str(path.relative_to(root))] = binding(path)['sha256']
    require(actual == full, 'full original source-card task inventory changed')
    manifest = checked(plan['implementation_manifest'])
    require(set(manifest) == {'schema_version', 'modules'} and manifest['schema_version'] == 'fleet-source-card-runtime-inventory/v1' and
            set(manifest['modules']) == set(ADAPTERS)|{'source_card_bootstrap'}, 'closed runtime adapter inventory required')
    for row in manifest['modules'].values(): require(binding(row['path']) == row, 'runtime inventory changed')
    require(binding(__file__) == manifest['modules']['source_card_bootstrap'], 'unselected cold runtime loader')
    require(binding(source['preparer']['path']) == source['preparer'], 'actual source preparer changed before import')
    module('author_workbook', AUTHOR)
    module('source_card_original_preparer', source['preparer'])
    sys.path.insert(0, str(root))
    import scripts.fleet
    module('scripts.fleet.capacity_policy', plan['capacity_policy'])
    module('scripts.fleet.model_gate', plan['model_gate'])
    os.environ.update(HARBOR_FLEET_WORKBOOK='0200', HARBOR_FLEET_SOURCE_HISTORY_PATH=source['history']['path'],
                      HARBOR_FLEET_SOURCE_HISTORY_SHA256=source['history']['sha256'])
    if probe:
        os.environ['HARBOR_FLEET_CONTINUATION_READ_ONLY'] = '1'
        from scripts.fleet import source_authoring_history as history
        def retained(roots, known):
            value = history.validate_certificate(source['history'], workbook='0200', root=root, live=False)
            require(list(map(str, roots)) == value['history_roots'] and sorted(known) == value['known_ids'],
                    'read-only source history namespace differs')
            return value
        history.configured = retained
    else:
        require(os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY') != '1', 'probe cannot enter owning runtime')
    driver = module('source_card_original_dispatcher', source['dispatcher'])
    job = checked(plan['original_authoring_job'])
    require(job['driver'] == source['dispatcher'] and checked(job['config'])['workbook'] == '0200', 'foreign original authoring job')
    env = driver.setup(job)
    for name in ADAPTERS: module('scripts.fleet.'+name, manifest['modules'][name])
    installer = sys.modules['scripts.fleet.source_card_install']
    originals = {key: __import__(name, fromlist=['selected']) for key, name in installer.NAMES.items()}
    from scripts.batch import harbor_lease_takeover as takeover
    reader_module = sys.modules['scripts.fleet.source_card_reader']
    reader_plan = checked(plan['reader_plan']); permit = Path(reader_plan['directory'])/'permit.json'
    authority = binding(permit) if permit.exists() else {'path': str(permit), 'sha256': '0'*64}
    reader = reader_module.Reader(plan=plan['reader_plan'], authority=authority,
        producer=originals['producer'], worker=originals['worker'], campaign=originals['campaign'],
        renewal=originals['renewal'], takeover=takeover)
    for row in [plan['implementation_manifest'], plan['stdlib'], plan['model_gate'], plan['capacity_policy'], *manifest['modules'].values()]:
        require(row in reader.dependencies(), 'undeclared cold runtime dependency')
    selected = installer.install(originals, reader)
    return plan, reader, selected, driver, env

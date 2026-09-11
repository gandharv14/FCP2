"""Capture native, currently qualified release evidence without changing bytes.

This inventories evidence; it neither establishes portable verification nor
authorizes deletion. A separate isolated verifier must re-enter the owning
validators with the resulting exact files before any cleanup is possible.
"""
from dataclasses import asdict
from pathlib import Path, PurePosixPath

from scripts.batch import harbor_promotion_evidence as promotion
from scripts.batch import harbor_ready_transitions as transitions
from scripts.batch import harbor_runtime_readiness as runtime
from scripts.batch import harbor_tracker as schema
from scripts.batch.harbor_admission import read, record, require
from scripts.batch.harbor_transition_codec import decode

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 'harbor-verification-capture/v1'
MAX_JSON_BYTES = 32 * 1024 * 1024
CODE_SUFFIXES = {'.py', '.sh', '.md', '.json', '.txt', '.toml', '.yaml', '.yml', '.ini'}
CODE_DIRECTORIES = ('scripts', 'xl_seg', 'mcp_env', 'grader', '.cursor/skills', 'verification_manifests')
OMIT_CODE_DIRECTORIES = {'__pycache__', 'tests', 'test', 'fixtures', '.git', '.venv',
                         'node_modules', 'runs', 'diagnostics', 'artifacts', 'outputs'}


def _path(value):
    require(type(value) in (str, Path) or isinstance(value, Path), 'invalid evidence path type')
    text = str(value)
    lexical = PurePosixPath(text)
    require(text.startswith('/') and not text.startswith('//') and str(lexical) == text
            and '..' not in lexical.parts and not any(ord(c) < 32 for c in text),
            'evidence paths must be canonical absolute paths')
    require(not any(part == '.env' or part.startswith('.env.') for part in lexical.parts),
            'private environment files cannot be bundled')
    return schema.safe_path(text)


class _Inventory:
    def __init__(self):
        self.files = {}
        self.directories = set()
        self.trees = {}
        self.json_pending = []
        self.json_seen = set()
        self.follow_roots = set()

    def file(self, value, expected=None):
        path = _path(value)
        require(path.is_file(), 'missing declared verification dependency: ' + str(path))
        bound = record(path)
        require(expected is None or bound['sha256'] == expected, 'verification dependency hash changed')
        previous = self.files.get(str(path))
        require(previous is None or previous == bound, 'verification dependency changed during capture')
        if previous is None:
            self.files[str(path)] = bound
            if path.suffix == '.json':
                self.json_pending.append(path)
        return bound

    def tree(self, value):
        path = _path(value)
        require(path.is_dir(), 'missing selected verification tree')
        members = {}
        self.directories.add(str(path))
        for child in sorted(path.rglob('*')):
            child = _path(child)
            if child.is_dir():
                self.directories.add(str(child))
                members[str(child.relative_to(path))] = None
            else:
                bound = self.file(child)
                members[str(child.relative_to(path))] = bound['sha256']
        previous = self.trees.get(str(path))
        require(previous is None or previous == members, 'selected verification tree changed')
        self.trees[str(path)] = members
        self.follow_roots.add(path)

    def binding(self, bound):
        require(isinstance(bound, dict) and set(bound) == {'path', 'sha256'}, 'invalid file evidence binding')
        path = _path(bound['path'])
        if path.is_dir():
            import xl_release_publication as release
            require(release._tree_hash(path)[0] == bound['sha256'], 'declared evidence tree changed')
            self.tree(path)
        else:
            self.file(path, bound['sha256'])

    def selected(self, value):
        """Follow closed path/hash forms inside already selected metadata roots."""
        if isinstance(value, list):
            for child in value:
                self.selected(child)
            return
        if not isinstance(value, dict):
            return
        pairs = []
        if isinstance(value.get('path'), str) and value['path'].startswith('/'):
            if isinstance(value.get('sha256'), str):
                pairs.append((value['path'], value['sha256']))
            elif isinstance(value.get('manifest_sha256'), str):
                pairs.append((str(Path(value['path']) / 'generation-manifest.json'), value['manifest_sha256']))
        for key, child in value.items():
            if isinstance(child, str) and child.startswith('/'):
                digest = value.get(key + '_sha256', value.get('expected_' + key + '_sha256'))
                if isinstance(digest, str):
                    pairs.append((child, digest))
            if isinstance(key, str) and key.startswith('/') and isinstance(child, str) and schema.HEX.fullmatch(child):
                pairs.append((key, child))
        for filename, digest in pairs:
            path = Path(filename)
            if str(path) in self.files or any(path == root or root in path.parents for root in self.follow_roots):
                require(schema.HEX.fullmatch(digest), 'invalid nested evidence hash')
                if path.is_dir():
                    self.binding({'path': filename, 'sha256': digest})
                else:
                    self.file(filename, digest)
        for child in value.values():
            self.selected(child)

    def close_json(self):
        while self.json_pending:
            path = self.json_pending.pop()
            if path in self.json_seen:
                continue
            self.json_seen.add(path)
            require(path.stat().st_size <= MAX_JSON_BYTES,
                    'verification JSON exceeds supported closure inspection bound: ' + str(path))
            document = read(self.files[str(path)])
            # This source is an actual direct read by the trusted-recalculation
            # validator, including when it lies outside the published generation.
            if isinstance(document, dict) and document.get('schema_version') == 'source-recalc-request/v2':
                source = document.get('source')
                require(isinstance(source, dict), 'recalculation source binding missing')
                self.file(source['path'], source['sha256'])
            self.selected(document)

    def recheck(self):
        for name, expected in self.trees.items():
            root = _path(name)
            require(root.is_dir(), 'selected tree removed during capture')
            actual = {}
            for child in sorted(root.rglob('*')):
                child = _path(child)
                actual[str(child.relative_to(root))] = None if child.is_dir() else record(child)['sha256']
            require(actual == expected, 'selected tree membership changed during capture')
        for bound in self.files.values():
            require(record(bound['path']) == bound, 'verification bytes changed during capture')


def _code_files(root):
    """Fixed installed code roots, never a walk over corpus/artifact roots."""
    root = _path(root)
    found = {p for p in root.iterdir() if p.is_file() and p.suffix in CODE_SUFFIXES
             and not p.name.startswith('.env')}
    for name in ('.python-version',):
        if (root / name).exists():
            found.add(root / name)
    for name in CODE_DIRECTORIES:
        directory = root / name
        if not directory.exists():
            continue
        _path(directory)
        for path in directory.rglob('*'):
            relative = path.relative_to(directory)
            if set(relative.parts[:-1]) & OMIT_CODE_DIRECTORIES:
                continue
            if path.is_symlink():
                raise ValueError('symlink in installed verifier code inventory')
            if path.is_file() and path.suffix in CODE_SUFFIXES:
                found.add(path)
    return sorted(found)


def collect(source: dict, tracker_path: Path, trusted_public_key: str, *, runtime_producer=None) -> dict:
    """Select one currently verified immutable release; never publish or delete."""
    require(isinstance(source, dict), 'original source record required')
    tracker_path = _path(tracker_path)
    anchor = _path(trusted_public_key)
    if runtime_producer is None:
        identity = runtime.capture_runtime_producer_identity()
    else:
        require(type(runtime_producer) is runtime.RuntimeProducerIdentity,
                'typed runtime producer identity required')
        runtime.RuntimeProducerIdentity.__post_init__(runtime_producer)
        identity = runtime_producer
    explicit_identity = asdict(identity) if runtime_producer is not None else None
    producer_options = {'runtime_producer': identity} if runtime_producer is not None else {}
    tracker_binding = record(tracker_path)
    qualified = promotion.validate(source, tracker_path, trusted_public_key=str(anchor), **producer_options)
    require(qualified.get('state') == 'PROMOTED' and qualified.get('verified_promoted') is True
            and qualified.get('revalidation_required') is False,
            'current complete promotion qualification required before evidence capture')
    require(record(tracker_path) == tracker_binding, 'tracker changed during qualification')
    tracker = read(tracker_binding)
    workbook = tracker['workbook_id']
    journal_root = tracker_path.parent / (workbook + '-notes-transactions')
    candidates = []
    for path in sorted(journal_root.glob('*/transaction.json')):
        descriptor = read(record(path))
        if descriptor.get('schema_version') == transitions.SCHEMA and descriptor.get('action') == 'promoted':
            candidates.append((path, descriptor))
    require(len(candidates) == 1, 'missing or ambiguous strict promotion journal')
    journal, descriptor = candidates[0]
    selection, published = decode(descriptor['selection']), decode(descriptor['published'])
    before = read(descriptor['before'])
    proof = transitions._proof(before, selection, **producer_options)
    require(proof.get('publication_ready') is True and proof.get('blockers') == []
            and proof.get('checks') and all(row.get('checked') is True for row in proof['checks'].values()),
            'canonical complete readiness proof required')
    require(published.release_id == qualified['release_id'] == tracker['promotion_ids']['release_id'],
            'qualified release identity changed')
    content = selection.content
    inventory = _Inventory()
    for bound in proof['evidence']:
        inventory.binding(bound)
    for bound in (tracker_binding, record(source['path']), record(anchor),
                  published.pointer, published.manifest, record(tracker['agent_notes']['path'])):
        inventory.binding(bound)
    require(inventory.files[str(_path(source['path']))]['sha256'] == source['sha256'],
            'original source changed during capture')
    final_task = published.task_root / 'task-generations' / tracker['promotion_ids']['task_generation_id']
    roots = [journal_root, content.stage, content.normalization_run, content.naturalization_root,
             Path(selection.observation.execution['path']).parent, final_task,
             published.publication_root / 'releases' / published.release_id]
    roots.extend(Path(content.bindings[key]['path']) for key in ('source_generation', 'segmentation_generation'))
    roots.extend(path for path in (content.mcp_build_a, content.mcp_build_b) if path is not None)
    if content.pre_dialogue_witness is not None:
        roots.append(content.pre_dialogue_witness.stage)
    for path in roots:
        inventory.tree(path)
    # Metadata records may refer to sibling submitted/completed/budget files.
    # A workbook's original parent is protected, never recursively captured.
    for binding in list(inventory.files.values()):
        path = Path(binding['path'])
        if path.suffix == '.json' and ROOT not in path.parents:
            inventory.follow_roots.add(path.parent)
        elif path.suffix == '.json' and any(part in {'.corpus-runs', 'runs'} for part in path.parts):
            inventory.follow_roots.add(path.parent)
    for bound in content.normalization_records:
        inventory.binding(bound)
        normalization = read(bound)
        for key in ('candidate', 'plan', 'manifest', 'curation', 'inventory'):
            if normalization.get(key):
                inventory.file(normalization[key], normalization.get(key + '_sha256'))
                inventory.follow_roots.add(Path(normalization[key]).parent)
    inventory.close_json()
    code_paths = _code_files(ROOT)
    for path in code_paths:
        inventory.file(path)
    # Code-owned JSON is configuration, not permission to traverse arbitrary
    # task data. Any extra read must pass the later isolated owning verifier.
    inventory.recheck()
    if runtime_producer is None:
        require(record(tracker_path) == tracker_binding and runtime.capture_runtime_producer_identity() == identity,
                'native verification context changed during capture')
    else:
        runtime.RuntimeProducerIdentity.__post_init__(identity)
        require(record(tracker_path) == tracker_binding and asdict(identity) == explicit_identity,
                'explicit verification context changed during capture')
    protected_files = {str(_path(source['path'])), str(anchor), str(tracker_path),
                       published.pointer['path'], published.manifest['path'], tracker['agent_notes']['path']}
    protected_files.update(str(path) for path in code_paths)
    protected_files.update(name for name in inventory.files if journal_root in Path(name).parents)
    if content.bindings.get('curation'):
        protected_files.add(content.bindings['curation']['path'])
    protected_roots = {str(Path(source['path']).parent),
        str(content.bindings['source_generation']['path']), str(content.bindings['segmentation_generation']['path']),
        str(final_task)}
    scratch_candidates = {content.stage, content.normalization_run, content.naturalization_root,
                          Path(selection.observation.execution['path']).parent}
    scratch_candidates.update(path for path in (content.mcp_build_a, content.mcp_build_b) if path is not None)
    scratch_roots = []
    for candidate in sorted(scratch_candidates):
        candidate = _path(candidate)
        overlap = any(candidate == Path(name) or candidate in Path(name).parents or Path(name) in candidate.parents
                      for name in protected_roots)
        contains_protected_file = any(candidate == Path(name) or candidate in Path(name).parents for name in protected_files)
        if not overlap and not contains_protected_file:
            scratch_roots.append(str(candidate))
    return {'schema_version': SCHEMA, 'source': dict(source), 'tracker': tracker_binding,
            'release_pointer': dict(published.pointer), 'release_manifest': dict(published.manifest),
            'release_id': published.release_id, 'final_task_root': str(final_task),
            'verifier_root': str(ROOT), 'producer_identity': asdict(identity),
            'files': [inventory.files[name] for name in sorted(inventory.files)],
            'directories': sorted(inventory.directories),
            'protected_files': sorted(protected_files),
            'protected_roots': sorted(protected_roots), 'scratch_roots': scratch_roots}

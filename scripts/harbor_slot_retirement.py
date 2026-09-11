"""Root-selected terminal retirement evidence; no reassignment or tracker writes.

Creation runs the current owning terminal validator. A root must independently
select the returned seal's exact path/hash. Historical validation proves that
selected observation's byte integrity and canonical terminal/release projection;
it does not reclassify source validity, requalify prose, or execute archived code.
Only exhausted-dialogue STOPPED is supported here. Other terminal owners need a
separate concrete adapter before they can release capacity.
"""
from contextlib import contextmanager
from pathlib import Path
import fcntl
import os
import time
import shutil
import subprocess
import sys
from scripts.corpus import digest

from scripts import harbor_parallel_policy as P
from scripts import harbor_dialogue_terminal as T
from scripts.batch import harbor_lane_notes as N
from scripts.batch import harbor_tracker as S
from scripts.batch import harbor_tracker_lifecycle as L
from scripts.batch.harbor_promotion_evidence import after_releases

SCHEMA = 'harbor-slot-retirement/v1'
require = P.require
def record(path):
    path = P.safe(path)
    require(path.is_file(), 'regular evidence file required')
    return dict(path=str(path), sha256=digest(path))


def _copy(binding, target):
    source = P.safe(binding['path'])
    require(source.name not in {'private.env', '.env'} and not source.name.endswith('.pem'),
            'credential material cannot enter retirement archive')
    require(record(source) == binding, 'archive source changed')
    before = source.stat()
    if not target.exists():
        require(shutil.disk_usage(target.parent).free - before.st_size >= 8 * 1024**3,
                'retirement archive would breach eight GiB disk reserve')
        if sys.platform == 'darwin':
            result = subprocess.run(['/bin/cp', '-c', '-p', str(source), str(target)],
                                    capture_output=True)
            if result.returncode:
                target.unlink(missing_ok=True)
                shutil.copy2(source, target)
        else:
            shutil.copy2(source, target)
        with target.open('rb') as stream:
            os.fsync(stream.fileno())
        N._fsync_directory(target.parent)
    retained = record(target)
    require(retained['sha256'] == binding['sha256'] and (source.stat().st_dev, source.stat().st_ino, source.stat().st_size, source.stat().st_mtime_ns) == (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            and record(source) == binding and source.stat().st_ino != target.stat().st_ino,
            'archive copy differs or source changed')
    return retained
read = P.read


def _policy(contract, activation, workbook):
    p = P.validate(contract, activation)
    rows = [r for r in p['lanes'] if r['workbook'] == workbook]
    require(len(rows) == 1, 'original activated lane owner required')
    return p, rows[0]


@contextmanager
def _locked(contract, activation, workbook):
    p, lane = _policy(contract, activation, workbook)
    fds = []
    try:
        # Same ordering as strict publication; nonblocking, never steal a lane.
        for path in (p['publication_queue_lock'], lane['lock']):
            fd = P._open(path)
            fds.append(fd)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _policy(contract, activation, workbook)
        yield p, lane
    finally:
        for fd in reversed(fds):
            os.close(fd)


def _current(tracker, workbook, original, tracker_root):
    path = Path(tracker_root) / (workbook + '.json')
    require(tracker == record(path), 'terminal tracker CAS differs')
    current = S.validate_tracker(read(tracker))
    L.validate_lifecycle(current)
    require(current['workbook_id'] == workbook and
            current['bindings']['original_raw_source'] == original,
            'terminal original identity differs')
    L.checked_record(original)
    require(current['execution_state'] == 'STOPPED', 'supported terminal STOPPED required')
    require(all(v is None for v in current['leases'].values()), 'old owner still holds a lease')
    N.LaneNotes(N.RootContext(Path(tracker_root))).require_synchronized(workbook)
    N.validate_notes_pair(current, Path(current['agent_notes']['path']).read_bytes())
    return current


def inspect(*, transaction, tracker, workbook, original, tracker_root):
    """Read-only current owning proof plus exact released terminal tracker."""
    current = _current(tracker, workbook, original, tracker_root)
    d, _, before, after, _ = T._material(transaction)
    require(d['tracker_path'] == tracker['path'] and before['workbook_id'] == workbook,
            'foreign terminal transaction')
    require(before['bindings']['original_raw_source'] == original,
            'terminal transaction original differs')
    completion = record(Path(transaction['path']).parent / 'complete.json')
    require(read(completion) == dict(tracker_sha256=d['after']['sha256'],
                                    notes_sha256=d['notes']['sha256']),
            'terminal transaction incomplete')
    after_releases(after, current)
    evidence = read(d['proof'])['evidence'] + [transaction, completion, tracker, original]
    evidence += [d[k] for k in ('before', 'after', 'notes', 'selection', 'proof')]
    evidence += [record(current['agent_notes']['path']), d['adapter']]
    evidence += [dict(path=p,sha256=h) for p,h in d['code'].items()]
    if d['prior_notes'] is not None:
        evidence.append(d['prior_notes'])
    return N.evidence_list(evidence)


def seal(*, contract, activation, transaction, tracker, workbook, original, output, _fault=lambda _: None):
    """Root-only explicit archival operation, after supported lease releases.

    Output must be fresh. An interruption leaves no seal and cannot be blindly
    resumed; inspect the retained incomplete directory and choose a fresh one.
    """
    output = P.safe(output)
    with _locked(contract, activation, workbook) as (p, lane):
        root = Path(p['campaign_root'])
        require(output.parent == root / 'parallel-policy' / 'retirements', 'foreign retirement output')
        require(not output.exists(), 'retained retirement attempt exists')
        evidence = inspect(transaction=transaction, tracker=tracker, workbook=workbook,
                           original=original, tracker_root=root / 'workbooks')
        output.mkdir(parents=True, mode=0o700)
        (output / 'files').mkdir()
        copies = []
        for index, binding in enumerate(evidence):
            target = output / 'files' / binding['sha256']
            copies.append(dict(original=binding, retained=_copy(binding, target)))
            _fault('copy')
        # Recheck every observed byte immediately before committing the seal.
        require(inspect(transaction=transaction, tracker=tracker, workbook=workbook,
                        original=original, tracker_root=root / 'workbooks') == evidence,
                'terminal evidence changed while archiving')
        _fault('validated')
        result = dict(schema_version=SCHEMA, contract=contract, activation=activation,
                      workbook=workbook, lane=lane, original=original, tracker=tracker,
                      transaction=transaction, copies=copies, observed_at_ns=time.time_ns(),
                      implementation=record(__file__))
        binding = P.put(output / 'seal.json', result)
        validate(binding, contract=contract, activation=activation, workbook=workbook,
                 original=original, tracker=tracker)
        return binding


def validate(binding, *, contract, activation, workbook, original, tracker):
    """Verify a root-selected prior observation, including all archived bytes.

    No current authoring code replay: the selected hash is root authority over
    the previously observed full proof. Canonical terminal/release semantics
    are checked again, and changes to any archived original invalidate the seal.
    """
    p, lane = _policy(contract, activation, workbook)
    root = Path(p['campaign_root'])
    seal = read(binding)
    require(set(seal) == {'schema_version', 'contract', 'activation', 'workbook', 'lane',
                         'original', 'tracker', 'transaction', 'copies', 'observed_at_ns', 'implementation'},
            'closed retirement seal required')
    require(seal['schema_version'] == SCHEMA and seal['contract'] == contract and
            seal['activation'] == activation and seal['workbook'] == workbook and
            seal['original'] == original and seal['tracker'] == tracker and seal['lane'] == lane,
            'retirement selection differs')
    require(seal['implementation'] == record(__file__), 'retirement validator changed')
    directory = Path(binding['path']).parent
    require(directory.parent == root / 'parallel-policy' / 'retirements'
            and Path(binding['path']).name == 'seal.json', 'foreign retirement namespace')
    require(type(seal['observed_at_ns']) is int and seal['observed_at_ns'] > 0,
            'invalid retirement observation time')
    archive = {}
    require(type(seal['copies']) is list and seal['copies'], 'missing retirement archive')
    for index, item in enumerate(seal['copies']):
        require(set(item) == {'original', 'retained'}, 'closed archive mapping required')
        old, retained = item['original'], item['retained']
        require(set(old) == set(retained) == {'path', 'sha256'} and old['path'] not in archive,
                'duplicate or malformed archive identity')
        require(Path(retained['path']) == directory / 'files' / old['sha256']
                and old['sha256'] == retained['sha256'], 'archive path/hash differs')
        require(record(retained['path']) == retained, 'retained archive bytes changed')
        archive[old['path']] = (old, retained)

    def raw(selected):
        require(selected['path'] in archive and archive[selected['path']][0] == selected,
                'required archived evidence missing or detached')
        return Path(archive[selected['path']][1]['path']).read_bytes()

    def obj(selected):
        return S.strict_json(raw(selected))

    tx = seal['transaction']; d = obj(tx)
    require(d['schema_version'] == T.SCHEMA and d['tracker_path'] == tracker['path']
            and d['contract'] == contract and d['activation'] == activation,
            'archived terminal descriptor differs')
    txroot = Path(tx['path']).parent
    require(txroot.parent == root / 'workbooks' / (workbook + '-notes-transactions'),
            'foreign archived terminal transaction')
    for field in ('before', 'after', 'notes', 'selection', 'proof'):
        require(Path(d[field]['path']) == txroot / (field + ('.md' if field == 'notes' else '.json')),
                'archived terminal artifact path differs')
    for path, sha in d['code'].items():
        raw(dict(path=path, sha256=sha))
    raw(d['adapter'])
    before, after, proof = obj(d['before']), obj(d['after']), obj(d['proof'])
    for value in (before, after):
        S.validate_tracker(value); L.validate_lifecycle(value)
    require(before['workbook_id'] == workbook and before['bindings']['original_raw_source'] == original
            and proof['classification'] == T.CLASSIFICATION and proof['attempt'] == 2
            and proof['passed'] is False and proof['terminal'] is True,
            'archived owning terminal identity differs')
    require(d['lease'] == dict(owner=before['leases']['task_agent']['owner'],
                              token=before['leases']['task_agent']['token'], revision=before['revision'],
                              sha256=d['before']['sha256']), 'archived lease detached')
    require(before['leases']['task_agent']['acquired_at'] <= d['now'] <
            before['leases']['task_agent']['expires_at'], 'archived terminal lease expired')
    require(N.evidence_list(proof['evidence']) == proof['evidence'], 'archive proof inventory malformed')
    for item in proof['evidence']:
        raw(item)
    raw(d['selection'])
    result = dict(proof, evidence=N.evidence_list(proof['evidence'] + [d['proof'], d['selection']]))
    expected, expected_notes = N._failure_after(before, result, d['now'], Path(d['notes_path']))
    require(after == expected and raw(d['notes']) == expected_notes, 'archived terminal projection differs')
    complete_path = str(txroot / 'complete.json')
    require(complete_path in archive, 'archived completion absent')
    require(obj(archive[complete_path][0]) == dict(tracker_sha256=d['after']['sha256'],
                                                notes_sha256=d['notes']['sha256']),
            'archived terminal completion differs')
    current = _current(tracker, workbook, original, root / 'workbooks')
    require(obj(tracker) == current, 'released terminal tracker changed')
    after_releases(after, current)
    return dict(workbook=workbook, lane=lane, tracker=tracker, seal=binding,
                terminal='STOPPED', source_invalidity=False, mutation_performed=False)

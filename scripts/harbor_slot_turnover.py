"""Root-selected capacity turnover; no admission, model work or tracker edits.

The original parallel contract, four lock inodes and single model slot remain
unchanged. Historical descriptors for untouched lanes continue to validate.
A new descriptor must select its exact assignment and pass ordinary admission.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import copy
import fcntl
import hashlib
import json
import os
import time
import uuid

from scripts import harbor_parallel_policy as policy
from scripts.batch import harbor_tracker as schema
from scripts.batch import harbor_tracker_lifecycle as lifecycle
from scripts.batch import harbor_lane_notes as notes
from scripts.corpus import read_inventory

SCHEMA = 'harbor-slot-turnover/v1'
SEAL = 'harbor-slot-retirement/v1'
require = policy.require
read = policy.read
record = policy.record


@dataclass(frozen=True)
class Selection:
    contract: dict
    activation: dict
    candidates: dict
    inventory: dict


def config(selection):
    require(type(selection) is Selection, 'root-selected turnover configuration required')
    p = policy.validate(selection.contract, selection.activation)
    root = Path(p['campaign_root'])
    require(Path(selection.candidates['path']).is_relative_to(root/'intake-v1'), 'foreign candidate campaign')
    manifest = read(selection.candidates)
    require(manifest.get('schema_version') == 'new50-qualified-task-candidate-proposal/v1'
            and manifest.get('target_new_qualified_tasks') == 50
            and manifest.get('denominator_for_original_rebuild') == 700, 'selected new50 manifest required')
    survey = read(manifest['survey'])
    require(survey.get('inventory') == selection.inventory and not survey.get('unmapped_prior_artifacts'),
            'candidate original/history survey differs')
    policy.checked(selection.inventory)
    inventory = read_inventory(selection.inventory['path'])['sources']
    require(len(inventory) == 700, 'original700 denominator must remain intact')
    originals = {r['workbook_id']: r for r in inventory}
    rows = manifest.get('selected_candidates')
    require(type(rows) is list and len(rows) == 50, 'exact fifty selected originals required')
    selected = {}
    for row in rows:
        source = row.get('original', {})
        wb = source.get('workbook_id')
        require(wb in originals and originals[wb] == source and wb not in selected,
                'duplicate/foreign selected original')
        require(not row.get('exclusion_evidence'), 'excluded prior task cannot enter new50')
        selected[wb] = row
    return p, root, selected


def identity(selection, wb):
    _, _, candidates = config(selection)
    require(wb in candidates, 'workbook absent from selected new50')
    row = candidates[wb]
    source = row['original']
    require(record(source['path']) == {k: source[k] for k in ('path', 'sha256')}
            and Path(source['path']).stat().st_size == source['size_bytes'], 'original source bytes changed')
    # Preserve every known prior budget artifact. Capacity does not reset or
    # pronounce eligibility over these; admission must reconcile them again.
    for item in row.get('history', []) + row.get('prior_audit_metadata', []):
        policy.checked(item)
    return copy.deepcopy(row)


def home(selection):
    return config(selection)[1] / 'parallel-policy' / 'turnover'


def _put(path, value):
    path = policy.safe(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        require(path.read_bytes() == policy.canonical(value), 'immutable turnover evidence changed')
        return record(path)
    return policy.put(path, value)


def _bytes(path, raw):
    path = policy.safe(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        require(path.read_bytes() == raw, 'immutable retained bytes changed')
    else:
        notes._immutable(path, raw)
    return record(path)


@contextmanager
def _locks(selection, lane=None):
    p, _, _ = config(selection)
    paths = [p['publication_queue_lock']]
    if lane is not None:
        require(type(lane) is int and 1 <= lane <= 4, 'one of four physical lanes required')
        paths.append(p['lanes'][lane-1]['lock'])
    held = []
    try:
        for path in paths:
            fd = policy._open(path)
            held.append(fd)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        policy.validate(selection.contract, selection.activation)
        yield held
    finally:
        for fd in reversed(held):
            os.close(fd)


def _configuration(s):
    return dict(contract=s.contract,activation=s.activation,candidates=s.candidates,
                inventory=s.inventory,implementation=record(Path(__file__).resolve()))


def _retirement(selection, binding, assignment):
    # First supported turnover is rooted in the independently selected current
    # terminal retirement seal. Other terminal owners remain closed.
    from scripts import harbor_retirement_history
    seal = read(binding)
    require(seal['workbook'] == assignment['workbook'], 'foreign retirement original')
    result = harbor_retirement_history.validate(binding, contract=selection.contract,
        activation=selection.activation, workbook=assignment['workbook'],
        original={k: assignment['candidate']['original'][k] for k in ('path','sha256')},
        tracker=seal['tracker'])
    require(result['lane']['lock'] == assignment['lock'] and
            result['lane']['lane'] == assignment['lane'], 'retirement physical lane differs')
    return result


def _validate_state(selection, state):
    require(set(state) == {'schema_version','configuration','sequence','previous','assignments','retired','operation'},
            'closed assignment state required')
    require(type(state['sequence']) is int and state['sequence'] >= 0 and type(state['retired']) is list, 'invalid assignment sequence/history')
    require(state['schema_version'] == SCHEMA and state['configuration'] == _configuration(selection),
            'foreign assignment configuration')
    rows = state['assignments']
    require(len(rows) == 4 and [r['lane'] for r in rows] == [1,2,3,4], 'exactly four physical lanes required')
    p, _, selected = config(selection)
    for row, physical in zip(rows,p['lanes']):
        require(set(row) == {'lane','lock','workbook','candidate'} and row['lock'] == physical['lock'],
                'physical lock identity changed')
        require(row['workbook'] in selected and row['candidate'] == selected[row['workbook']], 'assignment candidate changed')
    require(len({r['workbook'] for r in rows}) == 4
            and len({r['candidate']['original']['path'] for r in rows}) == 4, 'duplicate active original')


def _initial(selection, state):
    require(state['sequence'] == 0 and state['previous'] is None and
            state['operation'] == 'initialize' and state['retired'] == [] and
            [r['workbook'] for r in state['assignments']] == list(policy.WORKBOOKS),
            'initial roster changed')


def _transition(selection, previous, old, new):
    require(new['sequence'] == old['sequence'] + 1 and new['previous'] == previous,
            'broken assignment sequence')
    operation = new['operation']
    require(type(operation) is dict and set(operation) == {'lane','retirement'},
            'closed turnover operation required')
    lane = operation['lane']
    require(type(lane) is int and 1 <= lane <= 4, 'invalid turnover lane')
    old_row, new_row = old['assignments'][lane-1], new['assignments'][lane-1]
    require(new['retired'] == old['retired'] + [old_row], 'retirement history changed')
    require(new_row['workbook'] not in {r['workbook'] for r in old['assignments'] + old['retired']},
            'original assignment/budget reset')
    require(all(a == b for i, (a,b) in enumerate(zip(old['assignments'],new['assignments']),1)
                if i != lane), 'other running lane changed')
    _retirement(selection, operation['retirement'], old_row)


def _pointer(binding, state):
    return dict(sequence=state['sequence'],head=binding)


def _transaction(selection, binding):
    tx = read(binding)
    require(set(tx) == {'configuration','before','after'} and
            tx['configuration'] == _configuration(selection), 'foreign or malformed transaction')
    directory = Path(binding['path']).parent
    require(directory.parent == home(selection)/'events' and
            Path(binding['path']).name == 'transaction.json' and
            tx['after'] == record(directory/'state.json'), 'foreign transaction artifact')
    state = read(tx['after']); _validate_state(selection,state)
    if state['previous'] is None:
        _initial(selection,state)
        require(tx['before'] is None, 'initial transaction has prior pointer')
    else:
        old = read(state['previous']); _validate_state(selection,old)
        _transition(selection,state['previous'],old,state)
        expected = dict(path=str(home(selection)/'current.json'),
                        sha256=hashlib.sha256(policy.canonical(_pointer(state['previous'],old))).hexdigest())
        require(tx['before'] == expected, 'transaction prior pointer detached')
    return tx,state


def _complete_history(selection, binding):
    """Validate a committed chain independently of the mutable current pointer."""
    seen = set()
    latest = None
    while binding is not None:
        require(binding['path'] not in seen and len(seen) < 51, 'cyclic/oversized history')
        seen.add(binding['path'])
        directory = Path(binding['path']).parent
        tx,state = _transaction(selection,record(directory/'transaction.json'))
        require(tx['after'] == binding, 'history state detached')
        require(read(record(directory/'complete.json')) == dict(
            pointer_sha256=hashlib.sha256(policy.canonical(_pointer(binding,state))).hexdigest()),
            'assignment commit incomplete')
        if latest is None:
            latest=state
        binding=state['previous']
    require(latest is not None, 'missing initial assignment')
    return latest


def validate(selection, *, expected=None):
    """Validate current CAS and every complete immutable assignment event."""
    config(selection)
    pointer = record(home(selection)/'current.json')
    if expected is not None:
        require(pointer == expected, 'stale assignment CAS')
    current = read(pointer)
    require(set(current) == {'sequence','head'} and type(current['sequence']) is int,
            'closed current pointer required')
    latest = _complete_history(selection, current['head'])
    require(current == _pointer(current['head'],latest), 'current pointer differs')
    return {**latest,'pointer':pointer}


def _pending(selection):
    directory=home(selection)/'events'
    return [p for p in directory.glob('*/transaction.json') if not (p.parent/'complete.json').exists()]


def _commit(selection,state,expected,*,_fault=lambda _:None):
    require(not _pending(selection),'pending assignment requires explicit recovery')
    directory=home(selection)/'events'/str(uuid.uuid4());directory.mkdir(parents=True,mode=0o700)
    for parent in (directory.parent, directory.parent.parent, directory.parent.parent.parent):
        notes._fsync_directory(parent)
    after=_put(directory/'state.json',state)
    tx=_put(directory/'transaction.json',dict(configuration=_configuration(selection),before=expected,after=after))
    _fault('prepared')
    return _finish(selection,tx,_fault=_fault)


def _finish(selection,transaction,*,_fault=lambda _:None):
    tx,state = _transaction(selection,transaction)
    if state['previous'] is not None:
        # After pointer replacement the live pointer names this still-pending
        # event. Its complete predecessor chain must nevertheless be validated
        # before a recovery completion marker can be published.
        _complete_history(selection, state['previous'])
        row = state['assignments'][state['operation']['lane']-1]
        _unadmitted(selection, row['workbook'], identity(selection,row['workbook']))
    directory=Path(transaction['path']).parent
    target=home(selection)/'current.json';raw=policy.canonical(_pointer(tx['after'],state))
    if not target.exists():
        require(tx['before'] is None and state['sequence']==0,'missing prior pointer')
    elif target.read_bytes()!=raw:
        require(tx['before']==record(target),'concurrent assignment CAS')
        validate(selection,expected=tx['before'])
    if not target.exists() or target.read_bytes()!=raw:
        notes._replace(target,raw)
    _fault('pointer')
    _put(directory/'complete.json',{'pointer_sha256':hashlib.sha256(raw).hexdigest()})
    _fault('complete')
    return record(target)


def initialize(selection,*,_fault=lambda _:None):
    with _locks(selection):
        p,_,_=config(selection)
        require(not (home(selection)/'current.json').exists(),'turnover already initialized')
        rows=[dict(lane=x['lane'],lock=x['lock'],workbook=x['workbook'],candidate=identity(selection,x['workbook'])) for x in p['lanes']]
        state=dict(schema_version=SCHEMA,configuration=_configuration(selection),sequence=0,previous=None,
                   assignments=rows,retired=[],operation='initialize')
        return _commit(selection,state,None,_fault=_fault)


def _unadmitted(selection, workbook, candidate):
    root = config(selection)[1]
    require(not (root/'workbooks'/(workbook+'.json')).exists() and
            not (root/'work'/workbook).exists(), 'new original has existing/uncertain admission state')
    # A stranded executor plan still consumes or reserves its original identity.
    # Capacity allocation may not erase that ambiguity by creating a new task.
    for path in (root/'model-journal').glob('*/attempt-*/plan.json'):
        value = read(record(path)); scope = value.get('scope')
        require(type(scope) is dict and type(scope.get('workbook_id')) is str and
                type(scope.get('original_sha256')) is str, 'ambiguous campaign model identity')
        require(scope['workbook_id'] != workbook and
                scope['original_sha256'] != candidate['original']['sha256'],
                'new original has retained campaign model attempts')


def assign(selection,*,expected,lane,workbook,retirement,_fault=lambda _:None):
    with _locks(selection,lane):
        before=validate(selection,expected=expected);old=before['assignments'][lane-1]
        _retirement(selection,retirement,old)
        candidate=identity(selection,workbook);root=config(selection)[1]
        require(workbook not in {r['workbook'] for r in before['assignments']+before['retired']}, 'original already assigned; no budget reset')
        _unadmitted(selection, workbook, candidate)
        require(all(r['candidate']['original']['path']!=candidate['original']['path'] for r in before['assignments']), 'duplicate active original source')
        state={k:copy.deepcopy(v) for k,v in before.items() if k!='pointer'}
        state.update(sequence=before['sequence']+1,previous=read(expected)['head'],
                     operation=dict(lane=lane,retirement=retirement))
        state['retired'].append(copy.deepcopy(old));state['assignments'][lane-1]=dict(old,workbook=workbook,candidate=candidate)
        return _commit(selection,state,expected,_fault=_fault)


def recover(selection,transaction):
    tx=read(transaction);state=read(tx['after']);operation=state['operation']
    lane=None if operation=='initialize' else operation['lane']
    with _locks(selection,lane):
        require(_pending(selection)==[Path(transaction['path'])], 'ambiguous/incomplete recovery selection')
        return _finish(selection,transaction)


def assignment(selection, *, expected, workbook):
    state = validate(selection, expected=expected)
    rows = [r for r in state['assignments'] if r['workbook'] == workbook]
    require(len(rows) == 1, 'workbook has no active physical assignment')
    return dict(state=read(expected)['head'], row=copy.deepcopy(rows[0]))


def validate_assignment(selection, selected):
    require(type(selected) is dict and set(selected) == {'state','row'},
            'exact immutable assignment selection required')
    current = validate(selection)
    require(selected['row'] in current['assignments'], 'workbook assignment is retired or changed')
    cursor = read(current['pointer'])['head']; found = False
    while cursor is not None:
        state = read(cursor)
        if cursor == selected['state']:
            require(selected['row'] in state['assignments'], 'assignment was not in selected history')
            found = True
        cursor = state['previous']
    require(found, 'assignment selection is outside committed history')
    return selected['row']


@contextmanager
def lane_lock(selection, *, selected):
    row = validate_assignment(selection, selected)
    fd = policy._open(row['lock'])
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(validate_assignment(selection, selected) == row, 'assignment changed before lock')
        policy.lock_identity(fd, row['lock'])
        yield fd  # Inherit into the existing guardian; no fifth lock or model slot.
    finally:
        os.close(fd)


def controls(selection, *, selected, env_file=None):
    """For a new root descriptor; preserve assignment selection in its origin.

    Every dispatch must validate that selection under lane_lock, then inherit
    its FD through the maintained guardian. Existing descriptors are untouched.
    """
    from scripts import harbor_campaign as campaign
    row = validate_assignment(selection, selected)
    p = config(selection)[0]
    require(campaign.MEMORY == p['memory_limit_bytes'] and campaign.SECONDS == p['timeout_seconds'],
            'coordinator guard limits differ')
    return campaign.Controls(Path(row['lock']), Path(env_file) if env_file is not None else None)

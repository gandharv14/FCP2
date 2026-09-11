"""One explicit root recovery after a coordinator pause; not a semantic retry.

Ordinary lifecycle replacement remains limited to one. This separate operation
requires root authority, complete stopped-guard evidence, the exact current
tracker and an immutable authorization naming that tracker. No CLI auto-retry.
"""
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, asdict
from pathlib import Path
import copy, fcntl, os, time, uuid
from scripts.batch import harbor_tracker as S, harbor_tracker_lifecycle as L
from scripts.batch import harbor_instruction_producer as P
from scripts.batch.harbor_admission import read, record, require
from scripts.batch.harbor_workbook_bootstrap import Lease
from scripts.batch.harbor_lease_takeover import _lock_identity

AUTH = 'harbor-coordinator-pause-authorization/v1'
STOP = 'harbor-coordinator-pause-stopped/v1'
COMPLETE = 'harbor-coordinator-pause-completion/v1'
OPERATION = 'recover_coordinator_pause'
MAX_DURATION_NS = 7200 * 10**9

@dataclass(frozen=True)
class Selection:
    campaign_root: Path
    workbook: str
    tracker: dict
    checkpoint: dict
    guard: dict
    authorization: dict
    owner: str
    worker_kind: str
    duration_ns: int = MAX_DURATION_NS
    parallel_contract: dict | None = None
    parallel_activation: dict | None = None


def _value(s): return {**asdict(s), 'campaign_root': str(s.campaign_root)}
def _selection(v): return Selection(**{**v, 'campaign_root': Path(v['campaign_root'])})


def _checkpoint(binding):
    value = read(binding)
    lease = value['lease']
    if isinstance(lease, dict) and set(lease) == {'type', 'value'}:
        from scripts.harbor_campaign import decode
        lease = asdict(decode(lease))
    require(isinstance(lease, dict) and set(lease) == {'owner','token','revision','sha256'}, 'exact checkpoint lease required')
    return lease


def _guard(binding, home):
    g = read(binding); records = [binding]
    require(Path(binding['path']).is_relative_to(home), 'foreign guard')
    if set(g) == {'automatic_retry','resource','source_invalidity_claimed','status','terminal_verdict'}:
        require(g['automatic_retry'] is False and g['source_invalidity_claimed'] is False and g['terminal_verdict'] is None and g['status'] in {'RECONCILIATION_REQUIRED','COMPLETE'}, 'unsupported coordinator outcome')
        records.append(g['resource']); g = read(g['resource'])
    require(set(g) in ({'intent','log','metrics','returncode'}, {'intent','log','metrics','returncode','failure'}) and type(g['returncode']) is int, 'completed reaped guard required')
    require(isinstance(g['metrics'], dict) and type(g['metrics'].get('peak_rss_bytes')) is int and g['metrics']['peak_rss_bytes'] >= 0, 'guard process observation missing')
    if 'failure' in g: require(g['failure'] is None, 'unresolved process termination')
    intent = read(g['intent']); L.checked_record(g['log']); records += [g['intent'], g['log']]
    require(all(Path(x['path']).is_relative_to(home) for x in records), 'foreign guard evidence')
    if 'driver' in intent:
        L.checked_record(intent['driver']); records.append(intent['driver'])
    else:
        require(intent.get('schema_version') == 'harbor-coordinator-step/v1' and intent.get('timeout_seconds') == 900 and intent.get('memory_limit_bytes') == 8*1024**3, 'unsupported coordinator guard intent')
        L.checked_record(intent['code']); L.checked_record(intent['executable']); records += [intent['code'], intent['executable']]
    return records


def _recipe(s, historical=None):
    require(type(s) is Selection and s.worker_kind in {'instruction','environment'}, 'typed root selection required')
    require(type(s.duration_ns) is int and 0 < s.duration_ns <= MAX_DURATION_NS, 'bounded lease duration required')
    require(isinstance(s.owner,str) and bool(s.owner.strip()), 'root-selected owner required')
    c = S.safe_path(s.campaign_root); path = c/'workbooks'/(s.workbook+'.json')
    require(c.is_absolute() and c.is_dir() and s.tracker['path'] == str(path), 'canonical tracker required')
    before = S.validate_tracker(read(s.tracker) if historical is None else historical)
    require(S.sha(L.canonical(before)) == s.tracker['sha256'] and before['workbook_id'] == s.workbook, 'stale/foreign tracker')
    require(L.validate_lifecycle(before) == 1 and not any(x.get('schema_version') == L.PAUSE_EVENT_SCHEMA for x in before['handoffs'] if isinstance(x,dict)), 'coordinator-pause recovery budget unavailable')
    require(before['execution_state'] not in {'STOPPED','PROMOTED','publication_queued'}, 'terminal tracker requires its owning recovery')
    old = before['leases']['task_agent']; require(old is not None and old['scope'] == 'workbook:'+s.workbook, 'old workbook lease missing')
    home = c/'work'/s.workbook; run = home/(s.worker_kind+'-worker')
    require(run.is_dir(), 'existing worker directory required')
    # Absence is a live stopped-owner precondition, not a permanent ban on
    # later authorized lanes. validate_event rechecks the immutable root stop
    # observation, selected evidence, lock identities and exact event chronology.
    if historical is None and s.worker_kind == 'environment':
        require(not (home/'instruction-worker').exists(), 'existing instruction worker requires its lock')
    require(Path(s.checkpoint['path']).is_relative_to(home), 'foreign checkpoint')
    lease = _checkpoint(s.checkpoint)
    require((lease['owner'],lease['token']) == (old['owner'],old['token']) and type(lease['revision']) is int and lease['revision'] <= before['revision'] and isinstance(lease['sha256'],str) and S.HEX.fullmatch(lease['sha256']), 'checkpoint does not identify stopped owner')
    auth = read(s.authorization)
    require(set(auth) == {'schema_version','root_actor','operation','campaign_root','workbook','prior_tracker','prior_lease','continuation_request','reason','additional_recoveries','semantic_budget_reset'}, 'authorization fields changed')
    require(auth['schema_version'] == AUTH and auth['root_actor'] == 'harbor-orchestrator' and auth['operation'] == OPERATION and auth['campaign_root'] == str(c) and auth['workbook'] == s.workbook and auth['prior_tracker'] == s.tracker and auth['prior_lease'] == old and auth['reason'] == 'coordinator_pause' and type(auth['additional_recoveries']) is int and auth['additional_recoveries'] == 1 and auth['semantic_budget_reset'] is False, 'explicit exact root pause authorization required')
    L.checked_record(auth['continuation_request'])
    evidence = [s.authorization, auth['continuation_request'], s.checkpoint, *_guard(s.guard,home)]
    _lock_paths(s)
    if s.parallel_contract is not None: evidence += [s.parallel_contract, s.parallel_activation]
    return before, old, evidence


def _lock_paths(s):
    if s.parallel_contract is None:
        require(s.parallel_activation is None and not (s.campaign_root/'parallel-policy/activation.json').exists(), 'activated parallel policy must be selected')
        paths = [s.campaign_root/'actual-workbook-slot.lock']
    else:
        from scripts import harbor_parallel_policy as policy
        require(s.parallel_activation is not None and policy.contract(s.parallel_contract)['campaign_root']==str(s.campaign_root), 'foreign or incomplete parallel policy')
        paths = [policy.validate(s.parallel_contract,s.parallel_activation,s.workbook)]
    if s.worker_kind == 'instruction': paths.append(s.campaign_root/'work'/s.workbook/'instruction-worker/worker.lock')
    return paths


@contextmanager
def _locks(s):
    paths = _lock_paths(s)
    with ExitStack() as stack:
        held = []
        for path in paths:
            S.safe_path(path); require(path.is_file(), 'existing stopped-owner lock required')
            fd = os.open(path, os.O_RDWR|os.O_NOFOLLOW); stack.callback(os.close,fd)
            fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB); held.append((path,fd))
        yield held


def validate_event(event):
    require(set(event) == {'schema_version','operation','lease_kind','revision','root_actor','at','before','after','prior_tracker_sha256','stopped_owner_proof','authorization'}, 'pause event fields changed')
    require(event['schema_version'] == L.PAUSE_EVENT_SCHEMA and event['operation'] == OPERATION and event['lease_kind'] == 'task_agent' and event['root_actor'] == 'harbor-orchestrator', 'unknown pause event')
    stop = read(event['stopped_owner_proof'])
    require(set(stop) == {'schema_version','selection','before','prior_lease','evidence','locks','observed_at','implementation','semantic_budget_reset'} and stop['schema_version'] == STOP and stop['semantic_budget_reset'] is False, 'stopped proof fields changed')
    # Retain producer identity, without requiring historical executable reuse.
    L.checked_record(stop['implementation'])
    s = _selection(stop['selection']); before = read(stop['before']); prior,old,evidence = _recipe(s,before)
    require(stop['before']['sha256'] == s.tracker['sha256'] == event['prior_tracker_sha256'] and stop['prior_lease'] == old == event['before'] and event['authorization'] == s.authorization and stop['evidence'] == evidence, 'pause proof context differs')
    expected_paths = [str(p) for p in _lock_paths(s)]
    require([v['path'] for v in stop['locks']] == expected_paths and all(set(v)=={'path','device','inode'} and type(v['device']) is int and type(v['inode']) is int and v['inode']>0 for v in stop['locks']), 'stopped lock observation differs')
    at = event['at']; new = event['after']
    require(type(at) is int and type(stop['observed_at']) is int and old['expires_at'] <= stop['observed_at'] <= at and event['revision'] == before['revision']+1, 'pause chronology differs')
    require(isinstance(new,dict) and set(new)==S.LEASE_KEYS and new['owner']==s.owner and new['scope']==old['scope'] and isinstance(new['token'],str) and bool(new['token']) and new['token']!=old['token'] and new['acquired_at']==new['heartbeat_at']==at and new['expires_at']==at+s.duration_ns and new['acquisition_revision']==event['revision'], 'pause lease identity differs')
    return before


def validate_completion(binding):
    v = read(binding); require(set(v)=={'schema_version','event','after','lease'} and v['schema_version']==COMPLETE, 'completion fields changed')
    event = v['event']; before = validate_event(event); after = S.validate_tracker(read(v['after']))
    expected = copy.deepcopy(before); expected['handoffs'].append(event); expected['leases']['task_agent']=event['after']; expected['revision']=event['revision']; expected['updated_at']=event['at']
    require(after==expected and L.validate_lifecycle(after)==1, 'pause recovery changed nonlease history')
    new=event['after']; lease=Lease(new['owner'],new['token'],after['revision'],v['after']['sha256'])
    require(v['lease']==asdict(lease), 'completion token differs')
    return before,after,lease


def _seal(directory,event,after):
    # Validate before publishing completion, so interrupted/foreign CAS cannot seal.
    validate_event(event)
    expected=copy.deepcopy(read(read(event['stopped_owner_proof'])['before']))
    expected['handoffs'].append(event);expected['leases']['task_agent']=event['after'];expected['revision']=event['revision'];expected['updated_at']=event['at']
    require(after==expected, 'pause CAS absent or tracker advanced')
    a=P.put(directory/'after.json',L.canonical(after)); new=after['leases']['task_agent'];lease=Lease(new['owner'],new['token'],after['revision'],a['sha256'])
    completion=P.put(directory/'complete.json',dict(schema_version=COMPLETE,event=event,after=a,lease=asdict(lease)))
    validate_completion(completion);return lease,completion


def recover_pause(s,directory,*,authority,clock=time.time_ns,_fault=lambda _:None):
    """Root-authorized one additional recovery, holding stopped-owner and CAS locks."""
    directory=S.safe_path(directory)
    require(directory.parent==s.campaign_root/'work'/s.workbook/'pause-recoveries' and not directory.exists(), 'fresh dedicated pause transaction required')
    with _locks(s) as held, L.locked(s.tracker['path']) as path:
        raw=path.read_bytes();before,old,evidence=_recipe(s);now=L.instant(clock)
        require(now>=old['expires_at'], 'cannot replace active lease')
        request=dict(selection=_value(s),prior_lease=old,authorization=s.authorization)
        L.authorize(authority,OPERATION,path,before,request)
        require(s.duration_ns<=authority.max_duration_ns, 'root duration exceeded')
        directory.mkdir(parents=True)
        prior=P.put(directory/'before.json',raw)
        # Copy current producer, retaining identity across future unrelated code changes.
        implementation=P.put(directory/'producer.py',Path(__file__).read_bytes())
        proof=P.put(directory/'stopped.json',dict(schema_version=STOP,selection=_value(s),before=prior,prior_lease=old,evidence=evidence,locks=[_lock_identity(p,fd) for p,fd in held],observed_at=now,implementation=implementation,semantic_budget_reset=False))
        new=dict(owner=s.owner,token=str(uuid.uuid4()),scope=old['scope'],acquired_at=now,heartbeat_at=now,expires_at=now+s.duration_ns,acquisition_revision=before['revision']+1)
        event=dict(schema_version=L.PAUSE_EVENT_SCHEMA,operation=OPERATION,lease_kind='task_agent',revision=before['revision']+1,root_actor=authority.actor,at=now,before=old,after=new,prior_tracker_sha256=s.tracker['sha256'],stopped_owner_proof=proof,authorization=s.authorization)
        P.put(directory/'event.json',event);_fault('prepared')
        after=copy.deepcopy(before);after['handoffs'].append(event);after['leases']['task_agent']=new;after['revision']=event['revision'];after['updated_at']=now
        L.validate_lifecycle(after)
        for p,fd in held:_lock_identity(p,fd)
        result=L.commit(path,raw,after,records=[prior,proof,implementation,*evidence],clock=clock,deadline=new['expires_at'],fault_hook=_fault)
        _fault('committed');return _seal(directory,event,result['tracker'])


def reconcile(event_record):
    """Seal only the exact already-committed event; no new token or CAS."""
    event=read(event_record);before=validate_event(event);stop=read(event['stopped_owner_proof']);s=_selection(stop['selection'])
    directory=Path(event_record['path']).parent
    require(directory==Path(event['stopped_owner_proof']['path']).parent, 'foreign recovery transaction')
    with _locks(s), L.locked(s.tracker['path']):
        after=read(record(s.tracker['path']));return _seal(directory,event,after)


def continuation_chain(original_record,original_lease,current,current_lease,*,completion,tracker_path):
    from scripts.batch.harbor_nat_retry import _renewal_chain
    before,after,new=validate_completion(completion);v=read(completion);stop=read(v['event']['stopped_owner_proof'])
    require(stop['selection']['tracker']['path']==str(tracker_path) and before['workbook_id']==current['workbook_id'], 'foreign pause continuation')
    old=before['leases']['task_agent'];old_lease=Lease(old['owner'],old['token'],before['revision'],S.sha(L.canonical(before)))
    _renewal_chain(original_record,original_lease,before,old_lease)
    _renewal_chain(v['after'],asdict(new),current,current_lease)

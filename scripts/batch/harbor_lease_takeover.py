"""Explicit one-time expired root lease replacement after observed worker stop.

No model/workbook/runtime work or successful lane is inferred. Root must select
all old evidence; existing lifecycle owns replacement and token minting.
"""
from contextlib import ExitStack,contextmanager
from dataclasses import dataclass,asdict
from pathlib import Path
import copy,fcntl,json,os,time
from scripts.batch import harbor_tracker as schema,harbor_tracker_lifecycle as life
from scripts.batch import harbor_instruction_producer as producer
from scripts.batch.harbor_workbook_bootstrap import Lease
from scripts.batch.harbor_admission import read,record,require
SCHEMA='harbor-expired-lease-takeover/v1'
STOP='harbor-stopped-root-worker/v1'
MAX_REPLACEMENTS=3
HISTORICAL_IMPLEMENTATIONS={'c015d14ff789c15c9d9599919765d7d1c6dd6878712f7f1fce0483236bfacb50', '08aa2a34efb36abe10b6e4c88d05b6f5ddf14529c71d3e236d46bbffffafed00', '5e3616510c2979731447290b8b4163c3d4e1551501c88cd6902ebc174aa2a919'}
MAX_DURATION_NS=7200*10**9

@dataclass(frozen=True)
class Selection:
    campaign_root: Path
    workbook: str
    tracker: dict
    worker_kind: str
    guard: dict
    checkpoint: dict
    owner: str
    duration_ns: int = MAX_DURATION_NS
    config: dict | None = None
    prior_completion: dict | None = None


def _layout(s):
    c=schema.safe_path(s.campaign_root)
    if s.config is None:
        home=c/'work'/s.workbook
        return c/'workbooks'/(s.workbook+'.json'),home,home/(s.worker_kind+'-worker'),c/'actual-workbook-slot.lock'
    config=read(s.config)
    require(config['schema_version']=='authoring-root-driver/v1' and config['workbook']==s.workbook
            and config['run_root']==str(c) and s.config['path']==str(c/'config.json'),
            'exact authoring driver configuration required')
    from scripts.harbor_campaign import decode
    controls=decode(config['controls'])
    slot=schema.safe_path(controls.slot_lock)
    require(slot.is_absolute(),'absolute configured workbook slot required')
    return c/'trackers/workbooks'/(s.workbook+'.json'),c,c/s.worker_kind,slot


def _lock_paths(s):
    _,_,run,slot=_layout(s)
    return [slot,*([run/'worker.lock'] if s.worker_kind=='instruction' else [])]


def _implementation(binding):
    life.checked_record(binding)
    require(binding['sha256']==record(__file__)['sha256'] or binding['sha256'] in HISTORICAL_IMPLEMENTATIONS,
            'unreviewed stopped-owner implementation')


def _prior_chain(s,before,*,seen=()):
    count=life.validate_lifecycle(before)
    if s.prior_completion is None:
        require(count==0,'prior takeover completion required; replacement cannot reset by namespace')
        return []
    require(count>0 and count<MAX_REPLACEMENTS,'replacement chain budget exhausted')
    _,after,lease=validate_completion(s.prior_completion,_seen=seen)
    proof=read(read(s.prior_completion)['stopped_owner_proof'])
    require(proof['selection']['tracker']['path']==s.tracker['path'] and after['workbook_id']==before['workbook_id'],
            'prior completion belongs to another tracker')
    require(life.validate_lifecycle(after)==count and before['handoffs'][:len(after['handoffs'])]==after['handoffs'],
            'prior replacement omitted or history changed')
    old=after['leases']['task_agent'];current=before['leases']['task_agent']
    require(all(current[k]==old[k] for k in schema.LEASE_KEYS-{'heartbeat_at','expires_at'}),
            'prior completion owner/token/scope detached')
    require(before['bindings']['original_raw_source']==after['bindings']['original_raw_source']
            and before['revision']>=after['revision'],'prior original or revision changed')
    for lane,row in after['lane_state'].items():
        require(int(before['lane_state'][lane].get('attempt') or 0)>=int(row.get('attempt') or 0),
                'semantic consumption regressed across lease chain')
    # Ordinary lease renewals retain their exact hash chain. Lane transitions
    # may independently advance tracker fields; their owning quality gates are
    # deliberately not inferred by this stopped-owner proof.
    if before['revision']==after['revision']:
        require(before==after,'same revision tracker changed')
    elif all(e.get('schema_version')==life.EVENT_SCHEMA and e.get('operation')=='renew'
             for e in before['handoffs'][len(after['handoffs']):]) and before['revision']-after['revision']==len(before['handoffs'])-len(after['handoffs']):
        from scripts.batch.harbor_nat_retry import _renewal_chain
        _renewal_chain(read(s.prior_completion)['after'],asdict(lease),before,
            Lease(current['owner'],current['token'],before['revision'],schema.sha(life.canonical(before))))
    return completion_records(s.prior_completion,_seen=seen)


def _external_instruction_guard(s,intent,run,tracker_path):
    """Authenticate a configured coordinator guard outside its workbook home.

    The actual paths remain evidence. Historical coordinator bytes are checked,
    while only the installed closed readers inspect the descriptor/checkpoint.
    """
    from scripts import harbor_instruction_campaign as C
    require(s.config is not None and s.worker_kind=='instruction',
            'external guard requires configured instruction worker')
    require(intent.get('schema_version')==C.SCHEMA and intent.get('action')=='step',
            'external guard is not an instruction coordinator step')
    args=intent.get('arguments')
    require(type(args) is dict and set(args)=={'descriptor','checkpoint'} and args['checkpoint']==s.checkpoint,
            'external guard checkpoint differs')
    descriptor=read(args['descriptor'])
    require(set(descriptor)=={'schema_version','worker','controls','dependencies','origin','code','initial'}
            and descriptor['schema_version']==C.SCHEMA,'external guard descriptor differs')
    worker=C.worker_decode(descriptor['worker'])
    require(worker.run==run and worker.context.tracker_root==tracker_path.parent,
            'external guard belongs to another worker')
    require(descriptor['controls']==read(s.config)['controls']==intent['controls'],
            'external guard controls differ')
    require(descriptor['code']==intent['code']==record(worker.root/'scripts/harbor_instruction_campaign.py')
            and intent['base_code']==record(worker.root/'scripts/harbor_campaign.py'),
            'external guard coordinator identity differs')
    # Current schema reader checks exact selected lease, selection and checkpoint
    # ancestry without importing or running the historical implementation.
    prior,selection,lease=C._prior(s.checkpoint,args['descriptor'])
    require(selection.content.workbook==s.workbook,'external guard workbook differs')
    evidence=[args['descriptor'],*descriptor['origin'],*prior['evidence']]
    for binding in evidence:life.checked_record(binding)
    return evidence


def _recipe(s,*,historical=None):
    require(type(s) is Selection and s.worker_kind in {'instruction','environment'},'typed root takeover selection required')
    require(type(s.duration_ns) is int and 0<s.duration_ns<=MAX_DURATION_NS,'root takeover duration invalid')
    c=schema.safe_path(s.campaign_root);require(c.is_absolute() and c.is_dir(),'existing campaign root required')
    path,home,run,slot=_layout(s)
    require(s.tracker['path']==str(path),'canonical root-selected tracker required')
    before=schema.validate_tracker(read(s.tracker) if historical is None else historical);life.validate_lifecycle(before)
    require(schema.sha(life.canonical(before))==s.tracker['sha256'],'root-selected prior tracker differs')
    require(before['workbook_id']==s.workbook and before['execution_state'] not in {'STOPPED','PROMOTED','publication_queued'},'terminal or foreign tracker cannot be taken over')
    old=before['leases']['task_agent'];require(old is not None and old['scope']=='workbook:'+s.workbook,'existing exact task lease required')
    require(isinstance(s.owner,str) and s.owner.strip(),'explicit replacement owner required')
    require(run.is_dir(),'selected existing worker directory required')
    if s.worker_kind=='environment' and historical is None:
        require(not (home/('instruction' if s.config else 'instruction-worker')).exists(),'environment-only takeover is limited to before instruction startup')
    guard=read(s.guard)
    expected={'intent','log','metrics','returncode'}
    require(set(guard)==(expected|{'failure'} if s.config else expected) and type(guard['returncode']) is int,
            'completed root guard record required')
    external=not Path(s.guard['path']).is_relative_to(home)
    require(not external or (s.config is not None and s.worker_kind=='instruction'),'guard belongs to another workbook')
    intent=read(guard['intent']);life.checked_record(guard['log'])
    external_records=_external_instruction_guard(s,intent,run,path) if external else []
    code_records=[intent['driver']] if s.config is None else [intent[key] for key in ('code','base_code','executable') if key in intent]
    require(bool(code_records),'guard producer identity missing')
    for code in code_records:life.checked_record(code)
    if s.config:
        config=read(s.config)
        require(intent['controls']==config['controls'],'guard workbook slot differs from configured driver')
    require(Path(guard['intent']['path']).parent==Path(s.guard['path']).parent and Path(guard['log']['path']).parent==Path(s.guard['path']).parent,'guard evidence directory differs')
    checkpoint=read(s.checkpoint)
    if s.config:
        if s.prior_completion is not None and s.checkpoint==s.prior_completion:
            prior_proof=read(checkpoint['stopped_owner_proof'])
            require(checkpoint['schema_version']==SCHEMA and prior_proof['selection'].get('config')==s.config
                    and prior_proof['selection']['tracker']['path']==s.tracker['path'],
                    'prior completion checkpoint belongs to another configured worker')
        elif s.worker_kind=='instruction':
            from scripts.batch import harbor_transition_codec as codec
            selected=codec.decode(checkpoint['selection']).content
            require(selected.workbook==s.workbook and selected.bindings['original_raw_source']==before['bindings']['original_raw_source'],
                    'checkpoint workbook or original differs')
        else:
            from scripts.harbor_campaign import decode
            descriptor=read(checkpoint['descriptor']);environment=decode(descriptor['worker'])
            require(environment.bootstrap.workbook==s.workbook and environment.bootstrap.policy.tracker_root==path.parent
                    and environment.run==run and descriptor['controls']==read(s.config)['controls'],
                    'environment checkpoint belongs to another configured worker')
    else:
        require(Path(s.checkpoint['path']).is_relative_to(home),'checkpoint belongs to another workbook')
    lease=checkpoint['lease']
    if s.config and set(lease)=={'type','value'}:
        from scripts.harbor_campaign import decode
        lease=asdict(decode(lease))
    require(set(lease)=={'owner','token','revision','sha256'} and (lease['owner'],lease['token'])==(old['owner'],old['token']) and type(lease['revision']) is int and lease['revision']<=before['revision'] and schema.HEX.fullmatch(lease['sha256']),'checkpoint does not retain this old owner/token')
    # A stopped-worker observation is not a semantic success or retry approval;
    # any later unknown call/failed attempt remains in its owning journal.
    return before,old,run,[s.guard,guard['intent'],guard['log'],*code_records,s.checkpoint,*([s.config] if s.config else []),*external_records]


def _lock_identity(path,fd):
    st=os.fstat(fd);actual=path.stat()
    require(st.st_dev==actual.st_dev and st.st_ino==actual.st_ino,'held worker lock was replaced')
    return dict(path=str(path),device=st.st_dev,inode=st.st_ino)


@contextmanager
def _stopped_locks(s):
    paths=_lock_paths(s)
    with ExitStack() as stack:
        locks=[]
        for path in paths:
            schema.safe_path(path);require(path.is_file(),'existing root/worker lock required; do not invent one')
            fd=os.open(path,os.O_RDWR|getattr(os,'O_NOFOLLOW',0));stack.callback(os.close,fd)
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);locks.append((path,fd))
        yield locks


def _stop_validate(value,before,old,s,locks):
    require(value['schema_version']==STOP and value['implementation']==record(__file__),'stopped-worker producer changed')
    require(read(value['before'])==before and value['prior_lease']==old and _selection(value['selection'])==s,'stopped owner context differs')
    actual,lease,run,evidence=_recipe(s)
    require(actual==before and lease==old and value['evidence']==evidence,'stopped evidence changed')
    require(value['locks']==[_lock_identity(p,fd) for p,fd in locks],'stopped-worker locks changed')
    for b in evidence:life.checked_record(b)
    return True


def _selection_value(s):return {**asdict(s),'campaign_root':str(s.campaign_root)}


def _selection(value):return Selection(**{**value,'campaign_root':Path(value['campaign_root'])})


def validate_completion(binding,*,_seen=()):
    require(binding["path"] not in _seen and len(_seen)<MAX_REPLACEMENTS,"cyclic or overlong takeover chain")
    _seen=(*_seen,binding["path"])
    v=read(binding);require(v['schema_version']==SCHEMA,'unknown takeover completion')
    stop=read(v['stopped_owner_proof']);before=read(stop['before']);after=read(v['after']);s=_selection(stop['selection'])
    require(stop['schema_version']==STOP,'stopped-owner proof schema changed')
    _implementation(stop['implementation'])
    require(set(stop)=={'schema_version','selection','before','prior_lease','evidence','locks','observed_at','implementation','semantic_budget_reset'} and stop['semantic_budget_reset'] is False,'stopped proof fields changed')
    historical,prior,run,evidence=_recipe(s,historical=before)
    require(stop['evidence']==evidence,'stopped proof omitted or changed guard/checkpoint evidence')
    paths=list(map(str,_lock_paths(s)))
    require([x['path'] for x in stop['locks']]==paths and all(set(x)=={'path','device','inode'} and type(x['device']) is int and type(x['inode']) is int and x['inode']>0 for x in stop['locks']),'stopped lock observation changed')
    require(stop['prior_lease']==before['leases']['task_agent'],'stopped proof changed prior lease')
    require(s.tracker['sha256']==stop['before']['sha256'] and s.tracker['path']==str(_layout(s)[0]),'takeover prior snapshot changed')
    for b in stop['evidence']:life.checked_record(b)
    prior_count=life.validate_lifecycle(before)
    _prior_chain(s,before,seen=_seen)
    require(life.validate_lifecycle(after)==prior_count+1<=MAX_REPLACEMENTS,'exactly one bounded lifecycle replacement required')
    event=after['handoffs'][-1];old=stop['prior_lease'];new=event['after']
    require(event['schema_version']==life.EVENT_SCHEMA and event['operation']=='replace_expired' and event['lease_kind']=='task_agent' and event['root_actor']=='harbor-orchestrator' and event['prior_tracker_sha256']==stop['before']['sha256'] and event['before']==old and event['stopped_owner_proof']==v['stopped_owner_proof'],'replacement event differs from stopped proof')
    require(type(stop['observed_at']) is int and old['expires_at']<=stop['observed_at']<=event['at'],'stopped observation chronology changed')
    require(type(event['at']) is int and event['at']>=old['expires_at'] and new['token']!=old['token'] and new['owner']==s.owner and new['scope']==old['scope'] and new['acquired_at']==new['heartbeat_at']==event['at'] and new['acquisition_revision']==before['revision']+1 and new['expires_at']==event['at']+s.duration_ns,'replacement identity/chronology changed')
    expected=copy.deepcopy(before);expected['handoffs'].append(event);expected['leases']['task_agent']=new;expected['revision']+=1;expected['updated_at']=event['at']
    require(after==expected,'takeover altered nonlease state or history')
    lease=Lease(new['owner'],new['token'],after['revision'],schema.sha(life.canonical(after)))
    require(v['lease']==asdict(lease),'new lease differs from exact replacement')
    return before,after,lease


def _seal(directory,proof,after):
    path=producer.put(directory/'after.json',life.canonical(after));new=after['leases']['task_agent']
    lease=Lease(new['owner'],new['token'],after['revision'],schema.sha(life.canonical(after)))
    complete=producer.put(directory/'complete.json',dict(schema_version=SCHEMA,stopped_owner_proof=proof,after=path,lease=asdict(lease)))
    validate_completion(complete);return lease,complete


def replace_expired(s,directory,*,clock=time.time_ns,_fault=lambda _:None):
    """Observe both locks then use the existing root lifecycle once; no retry."""
    directory=schema.safe_path(directory)
    require(directory.parent==_layout(s)[1]/'lease-takeovers','dedicated root takeover directory required')
    require(not directory.exists(),'takeover transaction exists; select exact stopped proof for recovery')
    with _stopped_locks(s) as locks:
        before,old,run,evidence=_recipe(s);now=clock()
        require(now>=old['expires_at'],'active owner cannot be replaced')
        prior_records=_prior_chain(s,before)
        require(life.validate_lifecycle(before)<MAX_REPLACEMENTS,'replacement budget exhausted')
        directory.mkdir(parents=True)
        retained=producer.put(directory/'before.json',Path(s.tracker['path']).read_bytes())
        proof=producer.put(directory/'stopped-owner.json',dict(schema_version=STOP,selection=_selection_value(s),before=retained,prior_lease=old,evidence=evidence,locks=[_lock_identity(p,fd) for p,fd in locks],observed_at=now,implementation=record(__file__),semantic_budget_reset=False))
        _fault('prepared')
        def authority(operation,path,observed,request):
            return operation=='replace_expired' and path==s.tracker['path'] and observed==before and request==dict(operation='replace_expired',lease_kind='task_agent',owner=s.owner,scope=old['scope'],token=None,duration_ns=s.duration_ns,prior_lease=old,stopped_owner_proof=proof)
        def stopped(observed,prior,chosen):
            require(chosen==proof,'unexpected stopped proof')
            return _stop_validate(read(chosen),observed,prior,s,locks)
        result=life.lease_operation(s.tracker['path'],authority=life.RootAuthority('harbor-orchestrator',MAX_DURATION_NS,authority,max_replacements=MAX_REPLACEMENTS),operation='replace_expired',lease_kind='task_agent',owner=s.owner,scope=old['scope'],expected_revision=before['revision'],expected_sha256=s.tracker['sha256'],duration_ns=s.duration_ns,prior_lease=old,stopped_owner_proof=proof,prior_owner_stopped_validator=stopped,clock=clock,fault_hook=_fault)
        _fault('committed');return _seal(directory,proof,result['tracker'])


def recover(stopped_owner_proof):
    """Only seal an already committed exact replacement; never repeat its CAS."""
    stop=read(stopped_owner_proof);s=_selection(stop['selection']);directory=Path(stopped_owner_proof['path']).parent
    with _stopped_locks(s):
        after=read(record(s.tracker['path']));before=read(stop['before'])
        require(after['revision']==before['revision']+1,'replacement did not commit or tracker advanced')
        # _seal's complete validator checks every field before accepting reuse.
        return _seal(directory,stopped_owner_proof,after)


def continuation_chain(original_record,original_lease,current,current_lease,*,takeover=None,tracker_path=None):
    """Ordinary renewals stay unchanged; one explicit root replacement bridges."""
    from scripts.batch.harbor_nat_retry import _renewal_chain
    if takeover is None:return _renewal_chain(original_record,original_lease,current,current_lease)
    before,after,new=validate_completion(takeover)
    proof=read(read(takeover)['stopped_owner_proof'])
    require(tracker_path is not None and proof['selection']['tracker']['path']==str(tracker_path) and proof['selection']['workbook']==current['workbook_id'],'takeover belongs to another live tracker')
    old=before['leases']['task_agent'];old_lease=Lease(old['owner'],old['token'],before['revision'],schema.sha(life.canonical(before)))
    prior=proof['selection'].get('prior_completion')
    if prior is not None and (original_lease['owner'],original_lease['token'])!=(old_lease.owner,old_lease.token):
        continuation_chain(original_record,original_lease,before,old_lease,takeover=prior,tracker_path=tracker_path)
    else:
        _renewal_chain(original_record,original_lease,before,old_lease)
    completed=read(takeover)
    _renewal_chain(completed['after'],asdict(new),current,current_lease)
    require(life.validate_lifecycle(current)==life.validate_lifecycle(after),'replacement count changed')


def completion_records(binding,*,_seen=()):
    validate_completion(binding,_seen=_seen);v=read(binding);stop=read(v['stopped_owner_proof'])
    prior=stop['selection'].get('prior_completion')
    return [binding,v['stopped_owner_proof'],v['after'],stop['before'],stop['implementation'],*stop['evidence'],
            *(completion_records(prior,_seen=(*_seen,binding['path'])) if prior else [])]

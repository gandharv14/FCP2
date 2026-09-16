"""One separately journalled dispatch of a proven untransmitted prepared request.

The original incomplete execution remains spent. Acquired operations require an
explicit terminal reconciliation; unacquired requests require exact central absence.
This certificate neither retries an unknown HTTP effect nor creates a review round.
"""
from dataclasses import asdict,replace
from pathlib import Path
import json,re
from scripts.batch import harbor_model_executor as E

SCHEMA='harbor-model-pretransport-recovery/v1'
FIELDS={'schema_version','implementation','authorization','proof','reconciliation','original_prepared','campaign','physical_count','semantic_budget_reset'}

def read(binding):return json.loads(E.checked_bytes(binding))

def original(permit):
    p=read(permit);v=p['original_prepared']
    E.require(set(p)==FIELDS and p['schema_version']==SCHEMA,'closed pretransport certificate required')
    return E.PreparedCall(**{**v,'policy':E.Policy(**v['policy']),'scope':E.SemanticScope(**v['scope']),'budget_evidence':tuple(v['budget_evidence'])})

def inventory(directory):
    root=E.safe(directory)
    E.require(root.is_dir() and all(p.is_file() and not p.is_symlink() for p in root.iterdir()),'closed flat execution journal required')
    return [E.record(p)for p in sorted(root.iterdir())]

def validate_original(permit):
    p=read(permit);old=original(permit)
    E.require(p['implementation']==E.record(__file__) and p['physical_count']==1 and p['semantic_budget_reset'] is False,'pretransport implementation or ceiling differs')
    E.checked_bytes(p['authorization']);proof=read(p['proof'])
    unacquired=proof.get('schema_version')=='fleet-unacquired-pretransport-stop/v1'
    E.require(old.physical_repair is None and old.audit_context is None and getattr(old,'review_reservation',None) is None
              and ((type(old.ordinal)is int and type(old.limit)is int and 1<=old.ordinal<=old.limit<=2)if unacquired else old.ordinal==old.limit==1) and old.response_mode=='json_object','only an original single-use JSON request is supported')
    c=p['campaign'];E.require(set(c)=={'root','slot_root','model_slots'} and c['model_slots']==1,'original fleet campaign required')
    campaign=E.Campaign(Path(c['root']),Path(c['slot_root']),c['model_slots'])
    root=E.location(old,campaign);proof=read(p['proof'])
    E.require(proof['schema_version']in('fleet-pretransport-stop-proof/v1','fleet-unacquired-pretransport-stop/v1') and proof['operation']==old.attempt_id
        and proof['executor']==str(root) and proof['no_transport'] is True and proof['pid_exists'] is False
        and proof['matching_processes']==[],'exact stopped pretransport proof required')
    E.require(inventory(root)==proof['executor_inventory'],'original executor inventory changed')
    expected={'identity.json','outcome.json','plan.json','prior-attempts.json','request.json','submitted.json'}|{'budget-%02d.snapshot'%i for i in range(len(old.budget_evidence))}
    E.require({x.name for x in root.iterdir()}==expected,'any transport or unknown execution file forbids recovery')
    plan=read(E.record(root/'plan.json'));raw=E.checked_bytes(old.request)
    E.require(plan['max_transport_retries']==0 and plan['attempt_id']==old.attempt_id
        and plan['request']==old.request and plan['scope']==asdict(old.scope) and plan['policy']==asdict(old.policy)
        and plan['ordinal']==old.ordinal and plan['limit']==old.limit and plan['budget_evidence']==list(old.budget_evidence)
        and plan['campaign_root']==str(campaign.root) and plan['slot_root']==str(campaign.slot_root)
        and plan.get('physical_repair') is None,'original prepared identity differs')
    E.require((root/'request.json').read_bytes()==raw,'original request bytes differ')
    for i,b in enumerate(old.budget_evidence):
        E.require((root/('budget-%02d.snapshot'%i)).read_bytes()==E.checked_bytes(b),'original budget evidence changed')
    checked=E.inspect_locked(root,plan,json.loads(raw))
    outcome=read(checked.outcome)
    E.require(checked.status=='incomplete' and checked.call is None and outcome==proof['executor_outcome']
        and outcome['details']=={'automatic_replay':False,'reason_type':'RuntimeError'if unacquired else'ValueError'},'only sealed pretransport refusal is supported')
    if not unacquired:
        refused=read(proof['refused'])
        E.require(set(refused)=={'automatic_retry','failure_type','message_sha256','transport_started'}
            and refused['automatic_retry'] is False and refused['transport_started'] is False
            and refused['failure_type']=='ValueError' and re.fullmatch('[0-9a-f]{64}',refused['message_sha256']), 'refusal evidence differs')
    E.require(E.canonical(read(proof['prepared'])['prepared'])==E.canonical(asdict(old)),'prepared request witness differs')
    process=read(proof['process']);outer=read(proof['worker_outcome']);E.checked_bytes(proof['worker_intent'])
    E.require(process['pid']==proof['pid'] and process['start_ticks']==proof['start_ticks']
        and outer['returncode']==1 and outer['failure_type'] is None and outer.get('result') is None
        and outer['status']=='RECONCILIATION_REQUIRED','stopped outer custody differs')
    worker=Path(proof['worker']);journal=worker/'model-operations'/E.sha(old.attempt_id.encode())
    rows=proof['model_operations']
    if unacquired:
        validate_unacquired(p,old,proof,root,journal,rows,process,outer,raw)
        return p,old,campaign
    E.require({x['path'] for x in rows}=={str(journal/x)for x in('intent.json','acquired.json','released.json')},'closed acquired operation journal required')
    E.require(inventory(journal)==sorted(rows,key=lambda b:b['path']),'acquired operation inventory changed')
    for b in rows:E.checked_bytes(b)
    intent=read(E.record(journal/'intent.json'));released=read(E.record(journal/'released.json'));read(E.record(journal/'acquired.json'))
    E.require(intent['operation_id']==old.attempt_id and intent['request_sha256']==E.sha(raw)
        and intent['workbook']==old.scope.workbook_id and released['result']=={'state':'hold','operation_id':old.attempt_id},'acquired operation identity differs')
    receipt=read(p['reconciliation']);E.require(receipt['proof']==p['proof'] and receipt['result']=={'state':'abandoned','operation_id':old.attempt_id},'explicit abandoned reconciliation required')
    from scripts.fleet.coordinator import digest
    ops=[x for x in receipt['after']['operations']if x['kind']=='model'and x['operation_id']==old.attempt_id]
    E.require(len(ops)==1,'unique reconciled operation required');op=ops[0]
    E.require(op['state']=='abandoned' and op['workbook']==intent['workbook'] and op['owner']==intent['owner'] and op['fence']==intent['fence']
        and op['identity']==digest(dict(workbook=intent['workbook'],request_sha256=intent['request_sha256']))
        and json.loads(op['evidence'])==reconciliation_evidence(proof,receipt),'reconciled operation evidence differs')
    return p,old,campaign

def reconciliation_evidence(proof,receipt):
    """Authenticate an independently sealed no-HTTP report without rewriting it."""
    alias=receipt.get('evidence_alias')
    if alias is None:return receipt['proof']
    E.require(set(alias)=={'proof','before','middle','after','operation_response','claim_response'},'closed prior settlement history required')
    other=read(alias['proof']);before=read(alias['before']);middle=read(alias['middle']);after=read(alias['after'])
    E.require(other.get('schema_version')=='raw4-stopped-pretransport-proof/v1'
        and other.get('no_transport')is True and other.get('matching_processes')==[]
        and other.get('provider_calls')==0 and other.get('automatic_replay')is False
        and other.get('consumed_budget_preserved')is True,'independent stopped pretransport report required')
    E.require(other['operation_id']==proof['operation'] and other['executor']==proof['executor']
        and other['executor_inventory']==proof['executor_inventory']
        and other['outcome']==proof['executor_outcome']
        and other['refused']['binding']==proof['refused']
        and other['worker_intent']['binding']==proof['worker_intent']
        and other['worker_outcome']['binding']==proof['worker_outcome'],
        'independent report differs from original sealed evidence')
    for name in ('refused','worker_intent','worker_outcome'):
        E.require(other[name]['value']==read(other[name]['binding']),'independent captured bytes changed')
    operation=proof['operation'];wb=other['workbook']
    def row(status,kind,key,value):
        rows=[x for x in status[kind]if x[key]==value]
        E.require(len(rows)==1,'unique settlement predecessor required');return rows[0]
    oldop=row(before,'operations','operation_id',operation);midop=row(middle,'operations','operation_id',operation)
    old=row(before,'workbooks','workbook',wb);mid=row(middle,'workbooks','workbook',wb);end=row(after,'workbooks','workbook',wb)
    from scripts.fleet.coordinator import digest
    E.require(oldop['state']=='hold'and oldop['kind']=='model'and oldop['workbook']==wb
        and oldop['owner']==old['owner']==other['owner']and oldop['fence']==old['fence']==other['fence']
        and old['state']=='hold'and old['source_sha256']==other['source_sha256']
        and oldop['identity']==digest(dict(workbook=wb,request_sha256=other['request_sha256'])), 'original held identity differs')
    E.require(mid==old and midop==dict(oldop,state='abandoned',ended=midop['ended'],evidence=midop['evidence'])
        and json.loads(midop['evidence'])==alias['proof']
        and read(alias['operation_response'])==dict(state='abandoned',operation_id=operation), 'operation settlement changed original custody')
    E.require(end==dict(old,owner=None,claim_id=None,expires=None,state='hold',reason=end['reason'],updated=end['updated'])
        and json.loads(end['reason'])==alias['proof'] and read(alias['claim_response'])==end
        and before['at']<=middle['at']<=after['at'] and midop['ended']<=end['updated'], 'held author settlement changed source, fence or checkpoint')
    E.require(middle['operations']==after['operations'] and receipt['after']==after,
        'settled operation history changed')
    for key in ('quarantines','quarantine_host_capacity','physical_capacity_extensions','author_capacity_policy','model_policy','model_limit','publication_limit'):
        E.require(before.get(key)==middle.get(key)==after.get(key),'settlement changed unrelated policy')
    for key,target,identity in [('operations',operation,'operation_id'),('workbooks',wb,'workbook')]:
        E.require([x for x in before[key]if x[identity]!=target]==[x for x in middle[key]if x[identity]!=target]
            ==[x for x in after[key]if x[identity]!=target], 'settlement changed unrelated ledger identity')
    E.require(before['author_reservations']==middle['author_reservations'],'operation settlement changed author reservations')
    E.require(len(after['author_reservations'])==len(before['author_reservations']),'author reservation history lost')
    for row in before['author_reservations']:
        current=next((x for x in after['author_reservations']if x['claim_id']==row['claim_id']),None)
        if row['claim_id']==old['claim_id']:
            E.require(current is not None and current==dict(row,state='released',ended=current['ended'],evidence=current['evidence'])
                and json.loads(current['evidence'])==alias['proof'] and middle['at']<=current['ended']<=end['updated']<=after['at'],'stopped author reservation not exactly released')
        else:E.require(current==row,'unrelated author reservation changed')
    return alias['proof']


def validate_unacquired(p,old,proof,root,journal,rows,process,outer,raw):
    """Require authentic stopped intent-only native custody and central absence."""
    E.require(set(proof)=={'schema_version','operation','executor','no_transport','pid_exists','matching_processes',
        'executor_inventory','executor_outcome','prepared','process','worker_outcome','worker_intent','pid','start_ticks',
        'worker','model_operations','native_inventory'},'closed unacquired stop proof required')
    E.require(inventory(journal)==rows and rows==[E.record(journal/'intent.json')],'unacquired operation must contain only its original intent')
    intent=read(rows[0]);wi=read(proof['worker_intent']);job=read(wi['job'])
    E.require(intent['operation_id']==old.attempt_id and intent['request_sha256']==E.sha(raw)
        and intent['workbook']==old.scope.workbook_id and wi['owner']==intent['owner']
        and job['workbook']==old.scope.workbook_id and job['source']['sha256']==old.scope.original_sha256
        and outer['intent']==proof['worker_intent']and process['job']==wi['job'], 'unacquired worker/source identity differs')
    central=read(p['reconciliation'])
    E.require(central['schema_version']=='fleet-central-operation-absence/v1'and central['status']=='CENTRAL_OPERATION_ABSENCE_VERIFIED'
        and central['central_operations']==central['central_events']==[]and central['native_processes']==[]
        and central['automatic_replay']is False and central['owning_recovery_performed']is False
        and central['semantic_budget_reset']is False and central['operation_id']==old.attempt_id
        and central['request_sha256']==E.sha(raw)and central['model_intent']==rows[0]
        and central['native_inventory']==proof['native_inventory'],'exact authenticated central absence required')
    held=central['workbook']
    E.require(held['state']=='hold'and held['owner']==intent['owner']and held['fence']==intent['fence']
        and held['workbook']==old.scope.workbook_id and held['source_sha256']==old.scope.original_sha256
        and held['version']==job['version'],'absence proof custody changed')
    native=read(proof['native_inventory'])
    E.require(native['processes']==[]and native['at']<=central['at'],'absence proof is not after stopped native inspection')
    import base64
    observed={x['path']:x for x in native['files']}
    for row in[rows[0],proof['worker_intent'],proof['worker_outcome'],proof['process'],E.record(root/'outcome.json')]:
        captured=observed.get(row['path'])
        E.require(captured is not None and captured['sha256']==row['sha256']
            and base64.b64decode(captured['base64'],validate=True)==E.checked_bytes(row),'native stopped evidence does not authenticate original bytes')


def original_identity(permit):
    _,old,campaign=validate_original(permit)
    return read(read(E.record(E.location(old,campaign)/'identity.json')))

def validate_prepared(prepared,campaign):
    p,old,original_campaign=validate_original(prepared.physical_repair)
    E.require(campaign==original_campaign and prepared==replace(old,physical_repair=prepared.physical_repair),'pretransport continuation changed request, UUID or budget')
    marker=E.location(old,campaign).parent/'physical-repair.json'
    E.require(not marker.exists() or read(E.record(marker))==prepared.physical_repair,'one physical continuation already reserved elsewhere')
    E.require(not E.location(prepared,campaign).exists() or marker.exists(),'physical continuation lacks scope reservation')
    return p

def prepare(old,campaign,directory,*,authorization,proof,reconciliation):
    directory=E.safe(directory);E.require(not directory.exists(),'fresh certificate namespace required')
    value=dict(schema_version=SCHEMA,implementation=E.record(__file__),authorization=authorization,proof=proof,reconciliation=reconciliation,
        original_prepared=asdict(old),campaign=dict(root=str(campaign.root),slot_root=str(campaign.slot_root),model_slots=campaign.model_slots),physical_count=1,semantic_budget_reset=False)
    directory.mkdir(parents=True);E.save(directory/'permit.json',value)
    result=replace(old,physical_repair=E.record(directory/'permit.json'));validate_prepared(result,campaign);return result

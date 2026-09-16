"""Explicit bounded partition allowance after one sealed normalization HTTP 400.

The original attempted UUID, request and rejection remain in their original
scope. A new scope delegation consumes this permit once. A terminal rejection
is not a semantic pass, nor evidence that the provider did no internal work.
Incomplete/uncertain/complete responses never qualify for this transition.
"""
from dataclasses import asdict
from pathlib import Path
import time
from scripts.batch import harbor_model_executor as E
from scripts.batch.harbor_admission import read, record, require
from scripts.batch.harbor_model_review_provenance import Policy

SCHEMA = 'harbor-normalization-terminal-partition-allowance/v1'
CAPACITY_SCHEMA = 'harbor-normalization-terminal-capacity/v1'
EXTENDED_SCHEMA = 'harbor-normalization-terminal-partition-allowance/v2'
AUTHORIZATION_SHA256 = 'acffe75c8ed401f66484a5bb6c39a15a0a3e209fc976967da0bddd6125126fd0'
FIELDS = {'schema_version','implementation','authorization','previous_selection',
          'execution_plan','outcome','identity','campaign','partitions',
          'maximum_additional_physical_calls','original_consumed_physical_calls',
          'partition_round_limit','semantic_budget_reset','prior_response_reused',
          'issued_at_ns','expires_at_ns'}


def capacity(binding,selection,execution_plan,authorization,*,at=None):
    """Explicit source/request-bound expansion; no transport or semantic retry."""
    p=read(binding)
    fields={'schema_version','authorization','workbook','source','execution_plan',
        'maximum_partitions','maximum_additional_physical_calls',
        'original_consumed_physical_calls','partition_round_limit','transport_retries',
        'issued_at_ns','expires_at_ns'}
    require(set(p)==fields and p['schema_version']==CAPACITY_SCHEMA,
        'closed terminal partition capacity required')
    E.checked_bytes(p['authorization']);E.checked_bytes(p['source']);E.checked_bytes(p['execution_plan'])
    require(p['authorization']==authorization and authorization['sha256']==AUTHORIZATION_SHA256
        and p['workbook']==selection.workbook and p['source']==selection.bindings['original_raw_source']
        and p['execution_plan']==execution_plan,'terminal capacity source/request authority differs')
    require(type(p['maximum_partitions']) is int and 1<=p['maximum_partitions']<=32
        and type(p['maximum_additional_physical_calls']) is int
        and p['maximum_additional_physical_calls']==2*p['maximum_partitions']
        and type(p['original_consumed_physical_calls']) is int and p['original_consumed_physical_calls']==1
        and type(p['partition_round_limit']) is int and p['partition_round_limit']==2
        and type(p['transport_retries']) is int and p['transport_retries']==0,
        'finite source-bound partition capacity required')
    require(type(p['issued_at_ns']) is int and type(p['expires_at_ns']) is int
        and 0<p['issued_at_ns']<p['expires_at_ns']<=p['issued_at_ns']+86400*10**9,
        'bounded terminal capacity window required')
    if at is not None:require(p['issued_at_ns']<=at<p['expires_at_ns'],'terminal capacity expired')
    return p


def prepared(value):
    """Decode only the closed original ordinary normalization execution plan."""
    require(value.get('physical_repair') is None and value.get('audit_context') is None
            and value.get('review_reservation') is None, 'only original normalization transport supported')
    require(value['scope']['lane']=='harbor-normalization-engineer'
            and value['scope']['budget_id']=='normalization-review'
            and value['ordinal']==1 and value['limit']==2
            and value['response_mode']=='json_object', 'original first normalization request required')
    return E.PreparedCall(value['request'],Policy(**value['policy']),
        E.SemanticScope(**value['scope']),value['ordinal'],value['limit'],
        value['attempt_id'],tuple(value['budget_evidence']),response_mode='json_object')


def rejection(plan_binding,campaign,*,scope_locked=False):
    value=read(plan_binding);call=prepared(value);root=E.location(call,campaign)
    require(plan_binding==record(root/'plan.json'),'original execution namespace changed')
    if scope_locked:
        expected,_,request,_=E.prepared_plan(call,campaign)
        observed=E.inspect_locked(root,expected,request)
    else:observed=E.inspect(call,campaign)
    require(observed.status=='http_rejected' and observed.call is None,
            'only sealed terminal HTTP rejection; uncertain or complete calls cannot restart')
    outcome=read(observed.outcome)
    require(outcome['details']=={'http_status':400,'automatic_replay':False},'exact terminal HTTP 400 required')
    require(not any((root/name).exists() for name in
        ('response.raw.json','completed.json','parsed.json','content.txt')),
        'response evidence prevents terminal transport continuation')
    require([p.name for p in sorted(root.glob('transport-*.started.json'))]==['transport-00.started.json']
            and [p.name for p in sorted(root.glob('transport-*.http-error.json'))]==['transport-00.http-error.json']
            and not list(root.glob('transport-*.connection-error.json')),
            'multiple or uncertain physical transports require reconciliation')
    error=read(record(root/'transport-00.http-error.json'));body=read(error['body'])
    require(error['status']==400 and error['retry'] is False and isinstance(body,dict)
            and set(body)=={'error'} and isinstance(body['error'],dict), 'explicit provider rejection body required')
    # The retained executor plan must be exactly the submitted configuration,
    # including fleet transport policy and all consumed evidence.
    require(value==E.prepared_plan(call,campaign)[0], 'original execution policy changed')
    files={str(root/name):digest for name,digest in outcome['evidence'].items()}
    files[observed.outcome['path']]=observed.outcome['sha256']
    for path,digest in files.items():E.checked_bytes(dict(path=path,sha256=digest))
    return call,observed.outcome,record(root/'identity.json'),files


def validate(binding,selection,campaign,*,at=None,scope_locked=False):
    p=read(binding)
    extended=p.get('schema_version')==EXTENDED_SCHEMA
    require(set(p)==FIELDS|({'partition_capacity'} if extended else set())
            and p['schema_version'] in (SCHEMA,EXTENDED_SCHEMA)
            and p['implementation']==record(Path(__file__).absolute()),'closed current terminal partition authority required')
    E.checked_bytes(p['authorization'])
    require(p['authorization']['sha256']==AUTHORIZATION_SHA256,'explicit revised deployment authorization required')
    require(p['campaign']==dict(root=str(campaign.root),slot_root=str(campaign.slot_root),model_slots=campaign.model_slots),
            'original campaign must retain consumed transport')
    call,outcome,identity,files=rejection(p['execution_plan'],campaign,scope_locked=scope_locked)
    require(outcome==p['outcome'] and identity==p['identity'],'terminal evidence substituted')
    require(call.scope==E.SemanticScope(selection.workbook,selection.bindings['original_raw_source']['sha256'],
            'harbor-normalization-engineer','normalization-review') and call.policy==selection.policy
            and (call.ordinal,call.limit,call.attempt_id,call.budget_evidence)==
                (selection.budget.ordinal,selection.budget.limit,selection.budget.attempt_id,selection.budget.evidence),
            'original workbook, policy or consumed budget changed')
    previous=read(p['previous_selection'])
    require(p['previous_selection']==record(Path(previous['run'])/'production-selection.json')
            and previous['workbook']==selection.workbook,'original producer selection detached')
    old_prepared=read(record(Path(previous['run'])/'prepared.json'))
    require(old_prepared['selection']==p['previous_selection'] and old_prepared['request']==call.request
            and old_prepared['budget']['attempt_id']==call.attempt_id,'original producer/request identity changed')
    maximum=4
    if extended:
        policy=capacity(p['partition_capacity'],selection,p['execution_plan'],p['authorization'],at=at)
        maximum=policy['maximum_partitions']
        require(p['partitions']==maximum,'selected partitions must exactly consume the declared count')
        require(policy['issued_at_ns']<=p['issued_at_ns']<p['expires_at_ns']<=policy['expires_at_ns'],
            'partition permit outlives source-bound capacity')
        for row in (p['partition_capacity'],policy['authorization']):files[row['path']]=row['sha256']
    require(type(p['partitions']) is int and 1<=p['partitions']<=maximum
            and p['maximum_additional_physical_calls']==2*p['partitions']
            and p['partition_round_limit']==2 and p['original_consumed_physical_calls']==1
            and p['semantic_budget_reset'] is False and p['prior_response_reused'] is False,
            'finite additive physical allowance required')
    require(type(p['issued_at_ns']) is int and type(p['expires_at_ns']) is int
            and 0<p['issued_at_ns']<p['expires_at_ns']<=p['issued_at_ns']+86400*10**9,
            'bounded terminal repair admission window required')
    if at is not None:require(p['issued_at_ns']<=at<p['expires_at_ns'],'terminal repair admission expired')
    root=E.location(call,campaign).parent
    require({x.name for x in root.iterdir()}<={'scope.lock','attempt-01','scope-delegation.json'},
            'another original normalization attempt exists; reconcile before continuation')
    return p,files


def propose(selection,campaign,*,previous_selection,execution_plan,authorization,partitions,directory,partition_capacity=None):
    """Write an explicit proposal only; P.prepare owns atomic scope delegation."""
    from scripts.batch.harbor_normalization_producer import keep
    call,outcome,identity,_=rejection(execution_plan,campaign)
    now=time.time_ns();expires=now+86400*10**9
    if partition_capacity is not None:
        selected=capacity(partition_capacity,selection,execution_plan,authorization,at=now)
        require(partitions==selected['maximum_partitions'],'partition proposal exceeds selected capacity')
        expires=min(expires,selected['expires_at_ns'])
    else:require(type(partitions) is int and 1<=partitions<=4,'explicit capacity required above four partitions')
    directory=E.safe(directory);require(not directory.exists(),'repair proposal namespace consumed')
    directory.mkdir(parents=True,mode=0o700)
    value=dict(schema_version=EXTENDED_SCHEMA if partition_capacity is not None else SCHEMA,
        implementation=record(Path(__file__).absolute()),authorization=authorization,
        previous_selection=previous_selection,execution_plan=execution_plan,outcome=outcome,identity=identity,
        campaign=dict(root=str(campaign.root),slot_root=str(campaign.slot_root),model_slots=campaign.model_slots),
        partitions=partitions,maximum_additional_physical_calls=2*partitions,
        original_consumed_physical_calls=1,partition_round_limit=2,
        semantic_budget_reset=False,prior_response_reused=False,
        issued_at_ns=now,expires_at_ns=expires)
    if partition_capacity is not None:value['partition_capacity']=partition_capacity
    row=keep(directory/'allowance.json',value)
    validate(row,selection,campaign,at=now)
    return row

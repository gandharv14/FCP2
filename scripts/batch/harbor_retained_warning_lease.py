"""Reuse a validated warning permit across an explicit completed lease chain.

No warning authorization or semantic verdict is created here. The original Q
validator is always called on its exact historical tracker and worker. Only
custody is projected through shared T's fully validated completion/renewal chain.
The caller installs this declared reader in warm and cold owning namespaces.
"""
from dataclasses import asdict, replace
from pathlib import Path
import hashlib,json,importlib.util,sys
SCHEMA='harbor-retained-warning-lease/v1'


def require(ok,message):
    if not ok:raise ValueError(message)


def record(path):
    p=Path(path).absolute();require(p.is_file() and not any(x.is_symlink() for x in (p,*p.parents)), 'regular retained warning file required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def read(row):
    require(set(row)=={'path','sha256'} and record(row['path'])==row,'retained warning evidence changed')
    return json.loads(Path(row['path']).read_bytes())


def install(profile,*,I,W,Q,R,T,original_history_reader):
    p=read(profile)
    fields={'schema_version','adapter','context','original_permit','takeover','module_inventory','instruction_reader','takeover_runtime'}
    require(set(p) in (fields,fields|{'completed_lane_history','completed_lane'}),'closed retained warning profile required')
    require(p['schema_version']==SCHEMA and p['adapter']==record(__file__), 'unknown warning lease adapter')
    for name,module in [('I',I),('W',W),('Q',Q),('R',R)]:
        require(p['module_inventory'][name]==record(module.__file__),'owning warning implementation changed')
    require(set(p['module_inventory'])=={'I','W','Q','R'} and p['instruction_reader']==record(original_history_reader.__file__),'historical instruction reader changed')
    runtime=read(p['takeover_runtime'])
    require(runtime['shared']['takeover']==record(T.__file__) and runtime['shared']['lifecycle']==record(T.life.__file__), 'selected shared takeover dependencies changed')
    original_history_reader.install(I)
    original_validate=Q.validate;original_historical=Q.historical_lease;original_warning=I._warning_history;original_retained=R._retained_lease_adapter

    def lineage(worker,before):
        side=read(p['context']);old=read(p['original_permit']);prior=read(old['tracker'])
        require(set(side)=={'schema_version','original_warning','permit','blocked','takeover','selection','lease','semantic_budget_reset','Q_authorization_replayed'},'closed retained warning custody required')
        require(worker.warning_requalification==p['context'] and side['schema_version']==SCHEMA
                and side['original_warning']==side['permit']==p['original_permit'] and side['takeover']==p['takeover']
                and side['blocked']==old['blocked'] and side['selection']==old['selection'] and side['semantic_budget_reset'] is False
                and side['Q_authorization_replayed'] is False,'warning authority changed during custody transfer')
        require(old['schema_version']==Q.SCHEMA and old['semantic_budget_reset'] is False
                and old['model_calls']==0,'original zero-model warning permit required')
        _,after,lease=T.validate_completion(p['takeover'])
        require(side['lease']==asdict(lease),'warning custody lease differs from actual takeover')
        path=str(worker.context.tracker_root/(before['workbook_id']+'.json'))
        # Proves old permit lease through every intervening takeover/renewal;
        # counts and allowance records are validated by the selected lifecycle.
        T.continuation_chain(old['tracker'],old['lease'],after,lease,takeover=p['takeover'],tracker_path=path)
        current=before['leases']['task_agent']
        current_lease=T.Lease(current['owner'],current['token'],before['revision'],T.schema.sha(T.life.canonical(before)))
        anchor=read(p['takeover'])['after'];anchor_lease=asdict(lease)
        if 'completed_lane' in p:
            event=p['completed_lane'];require(set(event)=={'kind','completion','after'},'closed completed lane witness required')
            dependency=p['completed_lane_history'];require(record(dependency['path'])==dependency,'completed lane reader changed')
            spec=importlib.util.spec_from_file_location('retained_completed_lane_history',dependency['path'])
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            module.validate(W=W,T=T,worker=worker,before_binding=anchor,after_binding=event['after'],completion=event['completion'],kind=event['kind'])
            anchor=event['after'];anchor_lease=read(event['completion'])['lease']
        T.continuation_chain(anchor,anchor_lease,before,current_lease,tracker_path=path)
        original=replace(worker,warning_requalification=p['original_permit'])
        original_validate(original,prior)
        return side,old,prior,original

    def validate(worker,before):
        if worker.warning_requalification!=p['context']:return original_validate(worker,before)
        side,old,_,_=lineage(worker,before)
        return dict(old,lease=side['lease'])

    def historical(worker,before):
        if worker.warning_requalification!=p['context']:return original_historical(worker,before)
        _,old,prior,original=lineage(worker,before)
        proof=original_historical(original,prior)
        # Preserve the existing ordinary renewed-first-round protocol; do not
        # relabel warnings or replace the original rejected review.
        require(proof==old['ordinary_first_round'] and proof['renewal']==original.lease_renewal
                and proof['renewal_permit']==read(original.lease_renewal)['permit'],'original renewed warning history changed')
        return read(proof['renewal_permit'])

    def retained(worker,before):
        if worker.warning_requalification==p['context']:return historical(worker,before)
        return original_retained(worker,before)

    def warning(semantic,repair,binding,revision=None,disclosure=None,renewal=None):
        if binding!=p['context']:return original_warning(semantic,repair,binding,revision,disclosure,renewal)
        side=read(p['context']);require(side['original_warning']==p['original_permit'] and side['takeover']==p['takeover'],'warning custody sidecar changed')
        return original_warning(semantic,repair,p['original_permit'],revision,disclosure,renewal)

    Q.validate=validate;Q.historical_lease=historical;R._retained_lease_adapter=retained;I._warning_history=warning
    return dict(profile=profile,takeover_runtime=p['takeover_runtime'],original_permit=p['original_permit'],
                authorizations_replayed=0,model_calls=0,original_permit_bytes_unchanged=True)

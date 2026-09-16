"""Complete one exact prepared enqueue through original ReadyTransitions recovery.

The original failed worker journal remains unchanged. No lane, runtime or model
operation is repeated; the only mutation is the previously prepared transaction.
"""
from pathlib import Path
from dataclasses import asdict
import fcntl

SCHEMA='harbor-prepared-enqueue-recovery/v1'

def validate(W,worker,selection,lease,*,transaction,failed_intent,failed_exception,failed_readiness,predecessor):
    from scripts.batch import harbor_ready_transitions as R
    old=W.read(failed_intent);previous=W.read(predecessor);d=W.read(transaction)
    directory=Path(failed_intent['path']).parent
    W.require(failed_intent==W.record(directory/'intent.json') and failed_exception==W.record(directory/'exception.json') and failed_readiness==W.record(directory/'readiness.json') and not(directory/'complete.json').exists(),'exact unresolved enqueue required')
    W.require(W.read(failed_exception)['automatic_replay']is False,'automatic replay forbidden')
    proof=W.read(failed_readiness)
    W.require(proof['publication_ready']is True and proof['blockers']==[] and proof['mutation_performed']is False,'complete readiness must precede prepared transaction')
    W.require(old['step']=='enqueue' and old['previous']==predecessor and old['initial']==W.record(worker.run/'initial.json') and old['code']==W.code(worker) and old['selection']==W.codec.encode(selection) and old['lease']==asdict(lease),'failed enqueue identity differs')
    W.require(previous['step']=='dialogue-commit' and previous['status']=='completed' and previous['selection']==old['selection'] and previous['lease']==old['lease'],'completed commit predecessor differs')
    tracker=worker.context.tracker_root/(selection.content.workbook+'.json')
    W.require(Path(transaction['path']).parent.parent==tracker.parent/(tracker.stem+'-notes-transactions') and Path(transaction['path']).name=='transaction.json','foreign prepared transaction')
    W.require(d['schema_version']==R.SCHEMA and d['action']=='enqueue' and d['workbook']==tracker.stem and d['selection']==old['selection'] and d['lease_kind']=='task_agent' and d['root_authorized']is False and d['published']==R.encode(None),'prepared enqueue authority differs')
    before=W.read(d['before']);after=W.read(d['after'])
    W.require(d['before']['sha256']==lease.sha256 and before['revision']==lease.revision and d['lease']==before['leases']['task_agent'] and (d['lease']['owner'],d['lease']['token'])==(lease.owner,lease.token),'prepared enqueue lease differs')
    W.require(tracker.read_bytes() in (Path(d['before']['path']).read_bytes(),Path(d['after']['path']).read_bytes()),'tracker diverged from prepared enqueue')
    return d,before,after,directory,R


def recover(W,worker,selection,lease,*,transaction,failed_intent,failed_exception,failed_readiness,predecessor,directory):
    """Caller owns fleet custody and the original stage guard; lock worker here."""
    args=dict(transaction=transaction,failed_intent=failed_intent,failed_exception=failed_exception,failed_readiness=failed_readiness,predecessor=predecessor)
    with (worker.run/'worker.lock').open('a+b')as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        d,before,after,failed,R=validate(W,worker,selection,lease,**args)
        directory=Path(directory);W.require(not directory.exists(),'prepared enqueue recovery already attempted');directory.mkdir(parents=True)
        origin=W.keep(directory/'intent.json',dict(schema_version=SCHEMA,worker_run=str(worker.run),selection=W.codec.encode(selection),lease=asdict(lease),code=W.code(worker),**args,models_dispatched=0,automatic_retry=False))
        result=R.ReadyTransitions(worker.context).recover_ready(selection.content.workbook,transaction,owner=lease.owner,token=lease.token)
        W.require(result['tracker']==after and result['sha256']==d['after']['sha256'],'original recovered enqueue differs')
        updated=W.Lease(lease.owner,lease.token,result['revision'],result['sha256'])
        # Exact original W enqueue checkpoint shape, appended only after the
        # original recovery seals its real transaction. Old exception is kept.
        checkpoint=W.keep(failed/'complete.json',dict(step='enqueue',status='completed',selection=W.codec.encode(selection),lease=asdict(updated),intent=failed_intent))
        receipt=W.keep(directory/'complete.json',dict(schema_version=SCHEMA,status='PUBLICATION_READY',intent=origin,transaction=transaction,transaction_completion=W.record(Path(transaction['path']).parent/'complete.json'),checkpoint=checkpoint,tracker=W.record(worker.context.tracker_root/(selection.content.workbook+'.json')),models_dispatched=0,publication_performed=False,automatic_retry=False))
        return updated,checkpoint,receipt

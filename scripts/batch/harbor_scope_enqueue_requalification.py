"""Requalify an exact failed enqueue after a discovery-code-only mismatch.

No worker operation or semantic call is replayed. All current gates and runtime
run afresh before the owning lifecycle enqueues the same authored task.
"""
from dataclasses import replace,asdict
from pathlib import Path
import fcntl
from scripts.batch.harbor_admission import read,record,require
from scripts.batch import harbor_instruction_worker as w,harbor_transition_codec as codec
from scripts.batch import harbor_dialogue_scope_context as scope
from scripts.batch.harbor_complete_readiness import complete_readiness

SCHEMA='harbor-scope-enqueue-requalification/v1'


def enqueue(worker,selection,lease,*,failed_intent,predecessor,directory,takeover=None):
    """Caller supplies guarded process and workbook slot; own worker lock here."""
    with (worker.run/'worker.lock').open('a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return _enqueue(worker,selection,lease,failed_intent=failed_intent,
                        predecessor=predecessor,directory=directory,takeover=takeover)


def _enqueue(worker,selection,lease,*,failed_intent,predecessor,directory,takeover=None):
    facade,before,tracker=w.active(worker,selection,lease)
    old=read(failed_intent);previous=read(predecessor)
    require(old['step']=='enqueue' and old['previous']==predecessor
            and old['selection']==codec.encode(selection),
            'exact failed enqueue selection and predecessor required')
    takeover_records=[]
    if takeover is None:
        require(old['lease']==asdict(lease),'exact failed enqueue lease required')
    else:
        from scripts.batch.harbor_lease_takeover import continuation_chain,completion_records
        completion=read(takeover);stop=read(completion['stopped_owner_proof'])
        starting=stop['before']
        # This adapter starts from the failed enqueue CAS, not an older round.
        require(starting['sha256']==old['lease']['sha256'] and read(starting)['revision']==old['lease']['revision'],
                'takeover does not start at exact failed enqueue tracker')
        continuation_chain(starting,old['lease'],before,lease,takeover=takeover,tracker_path=tracker['path'])
        takeover_records=completion_records(takeover)
    require(previous['step']=='dialogue-commit' and previous['status']=='completed'
            and previous['selection']==old['selection'] and previous['lease']==old['lease'],
            'canonical completed dialogue checkpoint required')
    require(old['initial']==record(worker.run/'initial.json'),'failed enqueue foreign worker')
    for path,digest in old['code'].items():require(record(path)['sha256']==digest,'historical enqueue code changed')
    failed=Path(failed_intent['path']).parent
    require(failed_intent==record(failed/'intent.json') and not (failed/'complete.json').exists(),
            'failed enqueue already completed')
    exception=record(failed/'exception.json');require(read(exception)['automatic_replay'] is False,'not retained failed operation')
    readiness=record(failed/'readiness.json');proof=read(readiness)
    require(proof['publication_ready'] is False and set(proof['blockers'])=={'context_bindings','context_bindings_final'},
            'scope requalification cannot waive another readiness failure')
    # Recheck the original receipt, including source/header/claim equality,
    # then add a separately bound current-code equivalence receipt.
    directory=Path(directory);require(not directory.exists(),'scope enqueue already attempted')
    directory.mkdir(parents=True)
    w.keep(directory/'origin.json',dict(schema_version=SCHEMA,failed=failed_intent,
        predecessor=predecessor,exception=exception,readiness=readiness,
        tracker=w.snapshot(directory/'tracker.before.json',tracker),code=w.code(worker),
        takeover=takeover,takeover_records=takeover_records,
        semantic_attempts_added=0,semantic_budget_reset=False))
    content,receipt=scope.requalify_selected(selection.content,directory/'scope',root=worker.root)
    selected=replace(selection,content=content)
    selected=w._runtime(worker,selected,directory/'runtime')
    current=complete_readiness(selected,root=worker.root)
    proof_record=w.keep(directory/'readiness.json',current)
    require(current['publication_ready'] is True and not current['blockers'],'current scope enqueue readiness failed')
    require(w.active(worker,selection,lease)[1]==before,'tracker changed during scope requalification')
    intent=w.keep(directory/'intent.json',dict(step='enqueue',selection=codec.encode(selected),lease=asdict(lease),
        code=w.code(worker),initial=old['initial'],previous=predecessor,
        requalification=record(directory/'origin.json'),scope_equivalence=receipt,readiness=proof_record))
    result=facade.transition(selected.content.workbook,'enqueue',selected,expected_revision=lease.revision,
        expected_sha256=lease.sha256,owner=lease.owner,token=lease.token)
    updated=w.Lease(lease.owner,lease.token,result['revision'],result['sha256'])
    complete=w.keep(directory/'complete.json',dict(step='enqueue',status='completed',selection=codec.encode(selected),
        lease=asdict(updated),intent=intent))
    return selected,updated,complete

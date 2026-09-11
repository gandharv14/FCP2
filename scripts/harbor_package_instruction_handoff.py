"""Root-selected instruction handoff from a fully checked completed R recovery.

No commands, model calls, tracker mutations or replacement of old F evidence.
"""
from pathlib import Path
import copy
from scripts import harbor_package_recovery as R,harbor_instruction_campaign as I
from scripts.batch.harbor_admission import read,record,require
from scripts.batch.harbor_transition_codec import decode,encode


def completed(prepared):
    p,s,current,oldlease,receipt,proof=R.completed_package(prepared,active=False)
    directory=Path(prepared['path']).parent
    validated=record(directory/'package-validated.json')
    require(read(validated)==dict(prepared=prepared,selection=encode(current),receipt=receipt,evidence=proof['evidence']),'package validation changed')
    require(read(record(directory/'gate-intent.json'))==dict(prepared=prepared,package=validated,before=p['before']),'owning CAS intent changed')
    before=read(p['before']);tracker=s.bootstrap.policy.tracker_root/(s.bootstrap.workbook+'.json');actual=read(record(tracker))
    expected=copy.deepcopy(before);lane=proof['lane'];level=proof.get('confidence','high')
    expected['bindings'].update(proof['bindings']);row=expected['lane_state'][lane]
    row.update(state='passed',current_confidence=level,current_gate=proof['gate'],evidence_paths=R.notes.evidence_list(proof['evidence']))
    if proof.get('attempt') is not None:row['attempt']=proof['attempt']
    if proof.get('phase') is not None:row['phase']=proof['phase']
    if proof.get('disposition') is not None:row['disposition']=proof['disposition']
    row['confidence_history'].append(dict(level=level,reason='current_independent_gate_pass',revision=before['revision']+1))
    expected.update(last_gate=proof['gate'],current_gate=None,execution_state='running')
    levels=[(k,v['current_confidence']) for k,v in expected['lane_state'].items() if v.get('current_confidence')]
    expected['current_confidence']=dict(level=min((v for _,v in levels),key={'low':0,'medium':1,'high':2}.get),reason_codes=['current_independent_gate_pass'],contributors=[k for k,_ in levels],computed_at_revision=before['revision']+1)
    expected['confidence_history'].append(copy.deepcopy(expected['current_confidence']))
    expected['revision']+=1;expected['updated_at']=actual['updated_at'];expected['leases']['task_agent']['heartbeat_at']=actual['updated_at']
    require(actual==expected and actual['updated_at']>=before['updated_at'],'tracker is not exact completed environment CAS')
    binding=record(directory/'result.json');result=read(binding);lease=I.base.decode(result['lease'])
    require(result['status']=='ENVIRONMENT_READY' and result['step']=='environment' and result['selection']==encode(current) and result['descriptor']==p['descriptor'],'completed root handoff differs')
    require(lease==R.replace(oldlease,revision=actual['revision'],sha256=record(tracker)['sha256']),'exact environment lease differs')
    continuation=record(Path(p['refresh']['path']).parent/'continuation/environment.json')
    require(read(record(directory/'complete.json'))==dict(result=binding,checkpoint=continuation),'root completed checkpoint differs')
    require(read(continuation)==dict(selection=result['selection'],evidence=[receipt,prepared,validated]),'owning environment checkpoint differs')
    for item in result['evidence']:R.E.checked_bytes(item)
    return s,current,lease,[prepared,binding,validated,receipt,continuation,record(directory/'complete.json')]


def start(prepared,run,directory,*,parallel,lane_fd):
    from scripts import harbor_parallel_policy as policy
    controls=R.parallel_controls(read(prepared)['descriptor'],parallel)
    policy.lock_identity(lane_fd,controls.slot_lock)
    with controls.slot_lock.open('r+b') as probe:
        try:R.fcntl.flock(probe.fileno(),R.fcntl.LOCK_EX|R.fcntl.LOCK_NB)
        except BlockingIOError:pass
        else:
            R.fcntl.flock(probe.fileno(),R.fcntl.LOCK_UN)
            raise ValueError('persistent workbook guard lock is not held')
    s,selection,lease,evidence=completed(prepared)
    original=I.base.selected(read(prepared)['descriptor'])[2]
    controls=policy.controls(parallel['contract'],parallel['activation'],s.bootstrap.workbook,env_file=original.env_file)
    run=R.E.safe(Path(run));directory=R.E.safe(Path(directory))
    protected=(s.run,s.bootstrap.run,s.bootstrap.policy.tracker_root,selection.content.stage,s.campaign.root,s.campaign.slot_root)
    require(all(run!=p and run not in p.parents and p not in run.parents for p in protected),'instruction run overlaps protected evidence')
    require(not run.exists() and not directory.exists(),'existing instruction work requires exact checkpoint recovery')
    require(directory!=run and directory not in run.parents and run not in directory.parents and all(directory!=p and directory not in p.parents and p not in directory.parents for p in protected),'handoff journal overlaps protected evidence')
    # Same preflight, active ownership and full current environment checks as
    # ordinary I._start. No old F validation is skipped: completed() validates
    # the explicit R current-prefix proof, original F history, and full gates.
    directory.mkdir(parents=True)
    intent=R.keep(directory/'intent.json',dict(prepared=prepared,parallel=parallel,run=str(run),code=record(__file__),owning_code=R.code()))
    dep=I.base._dependency(s,directory)
    require(R.read(record(s.bootstrap.policy.tracker_root/(s.bootstrap.workbook+'.json')))['leases']['task_agent']['expires_at']-I.time.time_ns()>(I.base.SECONDS+30)*10**9,'explicit root lease continuation required before instructions')
    w=I.worker.Worker(R.W._context(s),run,s.campaign)
    _,before,_=I.worker.active(w,selection,lease)
    I.check_completed_gate(before,I.CompletionEvidence('environment',selection),w.context)
    bound=R.keep(directory/'descriptor.json',dict(schema_version=I.SCHEMA,worker=I.worker_encode(w),controls=I.base.encode(controls),dependencies=I.base.encode(s.bootstrap.policy.dependencies),origin=[intent,dep,*evidence,record(__file__),*R.code(),parallel['contract'],parallel['activation']],code=record(I.__file__),initial=dict(selection=encode(selection),lease=I.base.encode(lease))))
    result=I._result('INSTRUCTION_READY',bound,selection,lease,evidence=[intent,dep,*evidence],next_step=I.worker.STEPS[0])
    return R.keep(directory/'result.json',result)
